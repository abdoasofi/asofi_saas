"""Whitelisted REST facade for the Asofi SaaS Super Admin console (Flutter).

Authentication is Frappe's standard session login (`/api/method/login`); every
endpoint here additionally requires the System Manager role. All figures returned
are derived from real records — there is no mock/demo data anywhere in this module.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, today

from asofi_saas.asofi_saas.doctype.saas_product import saas_product
from asofi_saas.asofi_saas.doctype.saas_subscription_plan.saas_subscription_plan import (
    LEGACY_RASED_FIELDS,
)


def _ensure_admin():
    frappe.only_for("System Manager")


def _default_product():
    """The product to assume when a caller did not name one.

    Unambiguous while exactly one product is active — which keeps console builds
    that predate the product picker working. The moment a second product goes
    live, guessing would put a school on a utility bench, so demand a choice.
    """
    active = frappe.get_all("SaaS Product", filters={"is_active": 1}, pluck="name")

    if len(active) == 1:
        return active[0]

    frappe.throw(
        _("`product` is required: {0} products are active.").format(len(active))
    )


# ---------------------------------------------------------------------------
# Session / context
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_console_context():
    """Identity + authorization probe the app calls right after login."""
    _ensure_admin()
    user = frappe.session.user
    return {
        "user": user,
        "full_name": frappe.utils.get_fullname(user),
        "roles": frappe.get_roles(user),
        "is_system_manager": True,
    }


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------
_COMPANY_LIST_FIELDS = [
    "name",
    "company_name",
    "site_name",
    "site_url",
    "subscription_plan",
    # What the visitor clicked on the pricing page before signing up for a
    # trial — the lead worth calling back. Never an entitlement.
    "requested_plan",
    "subscription_status",
    "subscription_start",
    "subscription_end",
    "provision_status",
    "last_push_on",
    "last_push_status",
    "contact_person",
    "contact_phone",
    "contact_email",
    "usage_collectors",
    "usage_zones",
    "usage_beneficiaries",
    "usage_last_activity",
    "usage_synced_on",
]

_COMPANY_EDITABLE = (
    "company_name",
    "site_url",
    "subscription_plan",
    "subscription_status",
    "subscription_start",
    "subscription_end",
    "contact_person",
    "contact_phone",
    "contact_email",
    "notes",
    "provision_status",
)


@frappe.whitelist()
def list_companies(status=None, search=None):
    _ensure_admin()
    filters = {}
    if status:
        filters["subscription_status"] = status
    or_filters = None
    if search:
        or_filters = {
            "company_name": ["like", f"%{search}%"],
            "site_name": ["like", f"%{search}%"],
        }
    rows = frappe.get_all(
        "Managed Company",
        filters=filters,
        or_filters=or_filters,
        fields=_COMPANY_LIST_FIELDS,
        order_by="modified desc",
    )
    for r in rows:
        end = r.get("subscription_end")
        r["days_remaining"] = date_diff(end, today()) if end else None
    return rows


@frappe.whitelist()
def get_company(name):
    _ensure_admin()
    doc = frappe.get_doc("Managed Company", name)
    d = doc.as_dict()
    d.pop("control_plane_secret", None)  # never expose the secret to the client
    d["days_remaining"] = (
        date_diff(doc.subscription_end, today()) if doc.subscription_end else None
    )
    d["recent_logs"] = frappe.get_all(
        "Subscription Push Log",
        filters={"company": name},
        fields=["action", "status", "http_status", "error", "creation"],
        order_by="creation desc",
        limit=10,
    )
    return d


@frappe.whitelist()
def create_company(**kwargs):
    """Register an EXISTING company site (manual). Provisioning uses a different path."""
    _ensure_admin()
    doc = frappe.get_doc(
        {
            "doctype": "Managed Company",
            "product": kwargs.get("product") or _default_product(),
            "company_name": kwargs.get("company_name"),
            "site_name": kwargs.get("site_name"),
            "site_url": kwargs.get("site_url"),
            "control_plane_secret": kwargs.get("control_plane_secret"),
            "subscription_plan": kwargs.get("subscription_plan"),
            "subscription_status": kwargs.get("subscription_status") or "Active",
            "subscription_start": kwargs.get("subscription_start"),
            "subscription_end": kwargs.get("subscription_end"),
            "contact_person": kwargs.get("contact_person"),
            "contact_phone": kwargs.get("contact_phone"),
            "contact_email": kwargs.get("contact_email"),
            "notes": kwargs.get("notes"),
            # A manually-registered site already exists, so it is push-ready.
            "provision_status": kwargs.get("provision_status") or "Active",
        }
    ).insert()
    return {"name": doc.name}


@frappe.whitelist()
def update_company(name, **kwargs):
    _ensure_admin()
    doc = frappe.get_doc("Managed Company", name)
    for f in _COMPANY_EDITABLE:
        if f in kwargs and kwargs[f] is not None:
            doc.set(f, kwargs[f])
    # The secret is only touched when a non-empty value is explicitly supplied.
    if kwargs.get("control_plane_secret"):
        doc.control_plane_secret = kwargs["control_plane_secret"]
    doc.save()
    return {"name": doc.name}


@frappe.whitelist()
def delete_company(name):
    _ensure_admin()
    frappe.db.delete("Subscription Push Log", {"company": name})
    frappe.delete_doc("Managed Company", name, force=1)
    return {"ok": True}


@frappe.whitelist()
def push_company(company):
    """Synchronous subscription push, returns the push result to the client."""
    _ensure_admin()
    from asofi_saas.asofi_saas.subscription.push import push_subscription

    return push_subscription(company, action="Push Subscription")


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
@frappe.whitelist()
def list_plans(active_only=0, product=None):
    """Plans, each carrying the exact payload it would push.

    The console renders limits and features from `definition()` rather than from
    a fixed column list, so a product whose vocabulary this console has never
    heard of still displays correctly — no app release required.
    """
    _ensure_admin()

    filters = {}
    if int(active_only or 0):
        filters["is_active"] = 1
    if product:
        filters["product"] = product

    out = []
    for row in frappe.get_all(
        "SaaS Subscription Plan",
        filters=filters,
        fields=["name", "plan_code", "product", "plan_name", "is_active",
                "monthly_price", "description"],
        order_by="monthly_price asc",
    ):
        plan = frappe.get_doc("SaaS Subscription Plan", row.name)
        row["limits"] = [
            {"key": r.metric_key, "label_ar": r.label_ar, "value": r.value}
            for r in plan.limits
        ]
        row["features"] = [
            {"key": r.metric_key, "label_ar": r.label_ar, "enabled": bool(r.enabled)}
            for r in plan.features
        ]
        row["definition"] = plan.definition()
        out.append(row)

    return out


@frappe.whitelist()
def upsert_plan(**kwargs):
    """Create or update a plan.

    `limits` and `features` accept `{key: value}` maps so the console can post
    whatever the product's catalogue told it to render. The legacy Rased columns
    are still writable for older console builds.
    """
    _ensure_admin()

    code = (kwargs.get("plan_code") or "").strip()
    if not code:
        frappe.throw(_("plan_code is required."))

    if frappe.db.exists("SaaS Subscription Plan", code):
        doc = frappe.get_doc("SaaS Subscription Plan", code)
    else:
        doc = frappe.new_doc("SaaS Subscription Plan")
        doc.plan_code = code

    for f in ("product", "plan_name", "is_active", "monthly_price", "description"):
        if kwargs.get(f) is not None:
            doc.set(f, kwargs[f])

    if not doc.product:
        doc.product = _default_product()

    for f in LEGACY_RASED_FIELDS:
        if kwargs.get(f) is not None:
            doc.set(f, kwargs[f])

    _apply_rows(doc, "limits", kwargs.get("limits"), "value")
    _apply_rows(doc, "features", kwargs.get("features"), "enabled")

    if not doc.plan_name:
        doc.plan_name = code

    doc.save()
    return {"name": doc.name}


def _apply_rows(doc, table, payload, value_field):
    """Replace a plan's rows from a `{key: value}` map, if one was sent."""
    if payload is None:
        return

    if isinstance(payload, str):
        payload = frappe.parse_json(payload)

    doc.set(table, [])
    for key, value in (payload or {}).items():
        doc.append(table, {"metric_key": key, value_field: value})


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
@frappe.whitelist()
def list_products(active_only=1):
    """The catalogue the console renders every product-specific form from."""
    _ensure_admin()

    filters = {"is_active": 1} if int(active_only or 0) else {}
    out = []

    for name in frappe.get_all("SaaS Product", filters=filters, pluck="name"):
        doc = frappe.get_cached_doc("SaaS Product", name)
        out.append(
            {
                "name": doc.name,
                "product_code": doc.product_code,
                "product_name": doc.product_name,
                "description": doc.description,
                "is_active": bool(doc.is_active),
                "mobile_app_url": doc.mobile_app_url,
                "enable_public_trial": bool(doc.enable_public_trial),
                "ready_to_provision": bool(doc.bench_path),
                "metrics": doc.catalogue(),
            }
        )

    return out


@frappe.whitelist()
def get_product(name):
    _ensure_admin()
    doc = saas_product.get(name)

    return {
        "name": doc.name,
        "product_name": doc.product_name,
        "bench_path": doc.bench_path,
        "apps_to_install": doc.apps_to_install,
        "apply_path": doc.apply_path,
        "usage_path": doc.usage_path,
        "ready_to_provision": bool(doc.bench_path),
        "metrics": doc.catalogue(),
    }


# ---------------------------------------------------------------------------
# Provisioning (thin facade over the provisioning module)
# ---------------------------------------------------------------------------
@frappe.whitelist()
def provision_company(**kwargs):
    _ensure_admin()
    from asofi_saas.asofi_saas.provisioning.provision import enqueue_provision

    return enqueue_provision(**kwargs)


@frappe.whitelist()
def provision_progress(operation_id, since=0):
    _ensure_admin()
    from asofi_saas.asofi_saas.provisioning.provision import get_provision_progress

    return get_provision_progress(operation_id, since)


@frappe.whitelist()
def setup_domain_ssl_company(company):
    _ensure_admin()
    from asofi_saas.asofi_saas.provisioning.provision import enqueue_domain_ssl

    return enqueue_domain_ssl(company)


@frappe.whitelist()
def sync_usage_company(company):
    """Pull live usage for one company on demand (synchronous)."""
    _ensure_admin()
    from asofi_saas.asofi_saas.sync.usage import pull_usage

    return pull_usage(company)


# ---------------------------------------------------------------------------
# Dashboard (real data only)
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_dashboard():
    _ensure_admin()

    total = frappe.db.count("Managed Company")

    by_status = {
        (row.subscription_status or "Unknown"): row.c
        for row in frappe.get_all(
            "Managed Company",
            fields=["subscription_status", "count(name) as c"],
            group_by="subscription_status",
        )
    }
    by_provision = {
        (row.provision_status or "Unknown"): row.c
        for row in frappe.get_all(
            "Managed Company",
            fields=["provision_status", "count(name) as c"],
            group_by="provision_status",
        )
    }

    soon = []
    for e in frappe.get_all(
        "Managed Company",
        filters={
            "subscription_status": ["in", ["Trial", "Active"]],
            "subscription_end": ["is", "set"],
        },
        fields=["name", "company_name", "subscription_end"],
        order_by="subscription_end asc",
    ):
        dr = date_diff(e.subscription_end, today())
        if dr is not None and 0 <= dr <= 7:
            soon.append(
                {
                    "name": e.name,
                    "company_name": e.company_name,
                    "subscription_end": str(e.subscription_end),
                    "days_remaining": dr,
                }
            )

    failures = frappe.get_all(
        "Subscription Push Log",
        filters={"status": "Failed"},
        fields=["company", "action", "error", "creation"],
        order_by="creation desc",
        limit=8,
    )

    # Estimated monthly recurring revenue: real sum of the plan prices of the
    # companies that are actually Active/Trial. Not a projection, not mocked.
    mrr = frappe.db.sql(
        """
        select coalesce(sum(p.monthly_price), 0)
        from `tabManaged Company` mc
        join `tabSaaS Subscription Plan` p on p.name = mc.subscription_plan
        where mc.subscription_status in ('Active', 'Trial')
        """
    )[0][0]

    # Aggregate real usage synced from the company sites (see sync.usage).
    usage = frappe.db.sql(
        """
        select
            coalesce(sum(usage_collectors), 0)    as collectors,
            coalesce(sum(usage_zones), 0)         as zones,
            coalesce(sum(usage_beneficiaries), 0) as beneficiaries,
            sum(case when usage_synced_on is not null then 1 else 0 end) as synced
        from `tabManaged Company`
        """,
        as_dict=True,
    )[0]

    return {
        "total_companies": total,
        "by_status": by_status,
        "by_provision": by_provision,
        "expiring_soon": soon,
        "recent_failures": failures,
        "estimated_mrr": float(mrr or 0),
        "usage": {
            "collectors": int(usage.collectors or 0),
            "zones": int(usage.zones or 0),
            "beneficiaries": int(usage.beneficiaries or 0),
            "synced": int(usage.synced or 0),
        },
    }
