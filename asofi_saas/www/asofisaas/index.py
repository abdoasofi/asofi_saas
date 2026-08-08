"""Context for the public marketing + trial-onboarding page.

Runs as the website visitor (Guest). Reads are done with permission-free helpers
(``frappe.get_all`` / ``frappe.db.get_single_value``) so anonymous visitors can see
the plans and the trial configuration without any Desk role.

Plan cards are built from the product's metric catalogue rather than from a
fixed set of columns. The previous version selected Rased's fields by name and
labelled them in the template, which is why a second product could not be
described here at all — and why, when one appeared, its plan was rendered
through Rased's vocabulary.
"""

import frappe
from frappe.utils import cint

from asofi_saas.asofi_saas.public.tenant import _domain_suffix

# The public method paths the page calls via frappe.call (kept here so the
# template and the backend never drift out of sync).
CHECK_METHOD = "asofi_saas.asofi_saas.public.tenant.check_subdomain"
CREATE_METHOD = "asofi_saas.asofi_saas.public.tenant.create_trial_tenant"
PROGRESS_METHOD = "asofi_saas.asofi_saas.public.tenant.get_trial_progress"

#: The product this page sells.
#:
#: The marketing copy in index.html is still Rased's, and the trial flow this
#: page posts to still provisions a Rased site, so the page sells one product.
#: Naming it keeps that honest — and keeps another product's plans off it.
PRODUCT = "rased"

UNLIMITED = "غير محدود"


def _advertised(product):
    """The metrics this product sells, in the order its catalogue lists them.

    `public_on_pricing` carries an editorial decision that used to live in
    Python: Rased meters `max_ai_tokens` and has never advertised it, so
    showing every metric would have started selling something nobody priced.
    """
    doc = frappe.get_cached_doc("SaaS Product", product)

    return [
        m
        for m in doc.metrics
        if m.public_on_pricing and m.metric_kind in ("Limit", "Feature")
    ]


def _limit_text(value, unit=None):
    """Zero means unlimited. A metric the plan never mentions is not shown.

    Conflating those two is what let a schools plan read as a water utility
    with unlimited collectors: it has no collector limit at all, the absence
    was read as zero, and zero has always meant unlimited here.
    """
    value = cint(value)
    if value <= 0:
        return UNLIMITED

    text = f"{value:,}"
    return f"{text} {unit}".strip() if unit else text


def _plan_cards(product):
    metrics = _advertised(product)
    limits = [m for m in metrics if m.metric_kind == "Limit"]
    features = [m for m in metrics if m.metric_kind == "Feature"]

    plans = frappe.get_all(
        "SaaS Subscription Plan",
        filters={"is_active": 1, "product": product},
        fields=["name", "plan_code", "plan_name", "monthly_price", "description"],
        order_by="monthly_price asc",
    )

    for plan in plans:
        values = {
            r.metric_key: r.value
            for r in frappe.get_all(
                "SaaS Plan Limit",
                filters={"parent": plan.name},
                fields=["metric_key", "value"],
            )
        }
        enabled = {
            r.metric_key
            for r in frappe.get_all(
                "SaaS Plan Feature",
                filters={"parent": plan.name, "enabled": 1},
                fields=["metric_key"],
            )
        }

        plan["limits"] = [
            {
                "label": m.public_label_ar or m.label_ar,
                "value": _limit_text(values[m.metric_key], m.unit_ar),
            }
            for m in limits
            if m.metric_key in values
        ]
        plan["modules"] = [
            m.public_label_ar or m.label_ar
            for m in features
            if m.metric_key in enabled
        ]

    return plans


def get_context(context):
    context.no_cache = 1

    product = frappe.get_cached_doc("SaaS Product", PRODUCT)
    context.product = PRODUCT
    context.title = f"{product.product_name} — {product.description or ''}".strip(" —")

    context.enable_public_trial = bool(
        frappe.db.get_single_value("SaaS Settings", "enable_public_trial")
    )
    context.trial_days = int(frappe.db.get_single_value("SaaS Settings", "trial_days") or 14)
    context.domain_suffix = _domain_suffix()
    context.mobile_app_url = frappe.db.get_single_value("SaaS Settings", "mobile_app_url") or ""

    # Where a paid-plan enquiry should go. Both blank means the page simply
    # does not offer a contact button — better than a dead link.
    context.sales_whatsapp = (
        frappe.db.get_single_value("SaaS Settings", "sales_whatsapp") or ""
    ).strip()
    context.sales_email = (
        frappe.db.get_single_value("SaaS Settings", "sales_email") or ""
    ).strip()

    context.plans = _plan_cards(PRODUCT)

    context.check_method = CHECK_METHOD
    context.create_method = CREATE_METHOD
    context.progress_method = PROGRESS_METHOD
    return context
