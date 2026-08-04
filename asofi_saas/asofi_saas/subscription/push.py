"""Push a company's subscription snapshot to its own Frappe site.

Each company runs its own site exposing the guest endpoint
`utility_billing.rased.api.subscription.apply_subscription`, authenticated by a
shared secret (the site's `rased_control_plane_secret`). This module POSTs the
plan / status / dates there over HTTP. Every attempt is recorded as a
`Subscription Push Log` so a failure is never silent, and the company's
`last_push_*` fields are updated for at-a-glance status.
"""

import json

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime

logger = frappe.logger("asofi_saas.push", allow_site=True)

#: Fallback only. Every product declares its own `apply_path`; this is what a
#: company created before SaaS Product existed used, so an un-migrated row keeps
#: reaching the same endpoint instead of silently pushing nowhere.
APPLY_PATH = "/api/method/utility_billing.rased.api.subscription.apply_subscription"
PUSH_TIMEOUT = 20  # seconds


def push_subscription(company, action="Push Subscription"):
    """Push the given company's subscription to its site.

    `company` may be a Managed Company name (str) or its doc. Returns
    ``{ok, http_status, message}``. A remote/HTTP failure is captured in the log
    and returned (not raised); programmer errors (unknown company, missing secret)
    are raised so they surface loudly.
    """
    doc = company if hasattr(company, "doctype") else frappe.get_doc("Managed Company", company)

    secret = doc.get_password("control_plane_secret") if doc.control_plane_secret else None
    if not secret:
        frappe.throw(_("Managed Company {0} has no control-plane secret set.").format(doc.name))

    plan_code = doc.subscription_plan
    payload = {
        "secret": secret,
        "plan_code": plan_code,
        "subscription_status": doc.subscription_status,
        "subscription_start": str(doc.subscription_start) if doc.subscription_start else None,
        "subscription_end": str(doc.subscription_end) if doc.subscription_end else None,
        "company_name": doc.company_name,
    }

    # Module 6: send the plan's DEFINITION, not only its code. The tenant used
    # to read limits from a fixture shipped inside utility_billing, so editing
    # a plan here changed nothing on any site. Now this console is the author
    # and every push carries the current limits and feature flags with it.
    plan_definition = _plan_definition(plan_code)
    if plan_definition:
        payload["plan"] = json.dumps(plan_definition)

    url = doc.site_url.rstrip("/") + _apply_path(doc)

    http_status = None
    ok = False
    response_text = ""
    error = ""
    try:
        # Route by site name, not just host: on a shared bench several company
        # sites answer on one address, and in production the header simply matches
        # the domain. This lets site_url be the reachable address (e.g. the bench
        # at 127.0.0.1:8000 in dev) while the push still lands on the right site.
        resp = requests.post(
            url,
            data=payload,
            headers={"X-Frappe-Site-Name": doc.site_name},
            timeout=PUSH_TIMEOUT,
        )
        http_status = resp.status_code
        response_text = resp.text[:500]
        ok = resp.ok
        if not ok:
            error = _extract_error(resp)
    except requests.RequestException as e:
        error = f"{type(e).__name__}: {e}"
        logger.error(f"push_subscription {doc.name} network error: {error}")

    doc.db_set("last_push_on", now_datetime(), update_modified=False)
    doc.db_set("last_push_status", "Success" if ok else "Failed", update_modified=False)
    doc.db_set(
        "last_push_error",
        "" if ok else (error or f"HTTP {http_status}"),
        update_modified=False,
    )

    _log(doc, action, ok, http_status, plan_code, response_text, error)
    logger.info(f"push {doc.name} action={action} ok={ok} http={http_status}")

    return {"ok": ok, "http_status": http_status, "message": error or "OK"}


def _apply_path(doc):
    """Where this company's product listens for a subscription push."""
    if not doc.product:
        return APPLY_PATH

    product = frappe.get_cached_doc("SaaS Product", doc.product)
    return product.apply_path or APPLY_PATH


def _plan_definition(plan_code):
    """The plan as this console defines it, ready to travel.

    The vocabulary now comes from the plan's product, so a school plan carries
    `max_students` and a utility plan carries `max_collectors` — over the same
    wire, in the same shape. The tenant ignores keys it does not know.
    """
    if not plan_code or not frappe.db.exists("SaaS Subscription Plan", plan_code):
        return None

    return frappe.get_doc("SaaS Subscription Plan", plan_code).definition()


def _extract_error(resp):
    """Pull a human message out of a Frappe error response.

    Frappe puts user-facing messages in ``_server_messages`` (a JSON string of
    JSON strings), not a top-level field. Fall back to exception/message/status.
    """
    try:
        data = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"

    raw = data.get("_server_messages")
    if raw:
        try:
            parts = []
            for m in json.loads(raw):
                try:
                    parts.append(json.loads(m).get("message", str(m)))
                except (ValueError, TypeError):
                    parts.append(str(m))
            if parts:
                return " | ".join(parts)
        except (ValueError, TypeError):
            pass
    return data.get("exception") or data.get("message") or f"HTTP {resp.status_code}"


def _log(doc, action, ok, http_status, plan_code, response_text, error):
    try:
        frappe.get_doc(
            {
                "doctype": "Subscription Push Log",
                "company": doc.name,
                "action": action,
                "status": "Success" if ok else "Failed",
                "site_url": doc.site_url,
                "plan_code": plan_code,
                "subscription_status": doc.subscription_status,
                "subscription_end": doc.subscription_end,
                "http_status": http_status or 0,
                "response": response_text,
                "error": error,
            }
        ).insert(ignore_permissions=True)
    except Exception:
        # Logging must never break the push; record why the log itself failed.
        logger.exception("failed to write Subscription Push Log")


@frappe.whitelist()
def push_now(company):
    """Synchronous push, for the Super Admin console / Desk button."""
    frappe.only_for("System Manager")
    return push_subscription(company, action="Push Subscription")
