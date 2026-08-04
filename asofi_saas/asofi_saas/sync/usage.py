"""Pull real usage from each company site into the control plane.

Each company site exposes `utility_billing.rased.api.subscription.get_usage`, a
secret-authenticated guest endpoint returning live counts (collectors, zones,
beneficiaries, last activity). We POST the shared secret, store the snapshot on
the Managed Company, and the Super Admin dashboard reads it — so the console shows
actual activity, not just the subscription metadata it pushed. Failures are
recorded in `usage_error`, never raised for the batch job.
"""

import frappe
import requests
from frappe.utils import now_datetime

from asofi_saas.asofi_saas.subscription.push import _extract_error

logger = frappe.logger("asofi_saas.usage", allow_site=True)

#: Fallback only — see push.APPLY_PATH for why it still exists.
USAGE_PATH = "/api/method/utility_billing.rased.api.subscription.get_usage"
USAGE_TIMEOUT = 20  # seconds


def _usage_path(doc):
    if not doc.product:
        return USAGE_PATH

    product = frappe.get_cached_doc("SaaS Product", doc.product)
    return product.usage_path or USAGE_PATH


#: Rased answers with a nested shape this console has always flattened. New
#: products are expected to answer with plain `{metric_key: value}` — so the
#: nesting is unwrapped here rather than pushed onto every future product.
_RASED_SHAPE = {
    "collectors": ("collectors_active", None),
    "zones": ("zones", None),
    "beneficiaries": ("beneficiaries", "total"),
    "beneficiaries_active": ("beneficiaries", "active"),
    "branches": ("branches", None),
    "employees": ("employees", None),
    "incidents_open": ("incidents_open", None),
    "violations": ("violations", None),
    "messages_30d": ("messages_30d", None),
    "ai_tokens": ("ai_tokens_this_month", None),
    "ai_calls": ("ai_calls_this_month", None),
    "ocr_reads": ("ocr_reads_30d", None),
}


def _store_metrics(doc, data):
    """Write the pulled counts into the product-neutral usage table.

    Only keys the product declares are stored. An undeclared key means either a
    tenant sending something new or a catalogue that has fallen behind — either
    way, silently persisting it would put an unlabelled number on the operator's
    dashboard.
    """
    if not doc.product:
        return

    product = frappe.get_cached_doc("SaaS Product", doc.product)
    declared = product.keys_of("Usage")

    if not declared:
        return

    doc.set("usage_metrics", [])
    for key in declared:
        source, nested = _RASED_SHAPE.get(key, (key, None))
        value = data.get(source)
        if nested is not None:
            value = (value or {}).get(nested)

        doc.append(
            "usage_metrics",
            {
                "metric_key": key,
                "label_ar": product.label_for(key),
                "value": _int(value),
            },
        )

    doc.save(ignore_permissions=True)


def _int(v):
    if v in (None, ""):
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def pull_usage(company):
    """Fetch usage for one company and store the snapshot. Returns
    ``{ok, usage|message}``. Raises only for programmer errors (missing secret)."""
    doc = company if hasattr(company, "doctype") else frappe.get_doc("Managed Company", company)

    secret = doc.get_password("control_plane_secret") if doc.control_plane_secret else None
    if not secret:
        frappe.throw(f"Managed Company {doc.name} has no control-plane secret set.")

    url = doc.site_url.rstrip("/") + _usage_path(doc)
    error = ""
    data = None
    try:
        resp = requests.post(
            url,
            data={"secret": secret},
            headers={"X-Frappe-Site-Name": doc.site_name},
            timeout=USAGE_TIMEOUT,
        )
        if resp.ok:
            body = resp.json() if resp.content else {}
            data = body.get("message") if isinstance(body, dict) else None
        else:
            error = _extract_error(resp)
    except requests.RequestException as e:
        error = f"{type(e).__name__}: {e}"
        logger.error(f"pull_usage {doc.name} network error: {error}")
    except ValueError:
        error = "invalid JSON in usage response"

    if isinstance(data, dict):
        ben = data.get("beneficiaries") or {}
        doc.db_set(
            {
                "usage_collectors": _int(data.get("collectors_active")),
                "usage_zones": _int(data.get("zones")),
                "usage_beneficiaries": _int(ben.get("total")),
                "usage_beneficiaries_active": _int(ben.get("active")),
                # Module 6: Modules 1-5 were invisible to this console until
                # now — an operator could neither bill on usage nor notice a
                # tenant burning through an operator-provided AI key.
                "usage_branches": _int(data.get("branches")),
                "usage_employees": _int(data.get("employees")),
                "usage_incidents_open": _int(data.get("incidents_open")),
                "usage_violations": _int(data.get("violations")),
                "usage_messages_30d": _int(data.get("messages_30d")),
                "usage_ai_tokens": _int(data.get("ai_tokens_this_month")),
                "usage_ai_calls": _int(data.get("ai_calls_this_month")),
                "usage_ocr_reads": _int(data.get("ocr_reads_30d")),
                "usage_last_activity": data.get("last_reading_date"),
                "usage_synced_on": now_datetime(),
                "usage_error": "",
            },
            update_modified=False,
        )
        _store_metrics(doc, data)
        return {"ok": True, "usage": data}

    doc.db_set(
        {"usage_error": error or "no data returned", "usage_synced_on": now_datetime()},
        update_modified=False,
    )
    logger.warning(f"pull_usage {doc.name} failed: {error}")
    return {"ok": False, "message": error or "no data"}


def sync_all_usage():
    """Scheduled: pull usage for every Active company. Never raises for one bad
    site — each failure is captured on its own record."""
    companies = frappe.get_all(
        "Managed Company",
        filters={"provision_status": "Active"},
        pluck="name",
    )
    ok = 0
    for name in companies:
        try:
            if pull_usage(name).get("ok"):
                ok += 1
        except Exception:
            logger.exception(f"sync_all_usage: {name} failed")
    logger.info(f"sync_all_usage: {ok}/{len(companies)} synced")
    return {"synced": ok, "total": len(companies)}


@frappe.whitelist()
def sync_usage(company):
    """On-demand usage sync for one company (Desk/console)."""
    frappe.only_for("System Manager")
    return pull_usage(company)
