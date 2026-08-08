"""Shared context for the product storefronts under /asofisaas.

One shell, one page per product. The alternative — a single page with a
product switcher — was rejected because the half that actually sells is the
hero and the feature copy, and that half cannot be generated from a catalogue
without going bland for every product at once. So the catalogue drives what is
*structural* (plans, limits, trial configuration) and each product keeps its
own words in its own template.

Everything here is read with permission-free helpers: the visitor is Guest and
holds no Desk role.
"""

import frappe
from frappe.utils import cint

# The public method paths the templates call via frappe.call, kept here so the
# markup and the backend cannot drift apart.
CHECK_METHOD = "asofi_saas.asofi_saas.public.tenant.check_subdomain"
CREATE_METHOD = "asofi_saas.asofi_saas.public.tenant.create_trial_tenant"
PROGRESS_METHOD = "asofi_saas.asofi_saas.public.tenant.get_trial_progress"

UNLIMITED = "غير محدود"


def products():
    """Active products, for the platform page's cards."""
    return frappe.get_all(
        "SaaS Product",
        filters={"is_active": 1},
        fields=["name", "product_code", "product_name", "description",
                "enable_public_trial"],
        order_by="creation asc",
    )


def _advertised(product):
    """The metrics this product sells, in the order its catalogue lists them.

    `public_on_pricing` carries an editorial decision that used to live in
    Python: Rased meters `max_ai_tokens` and has never advertised it, so
    showing every metric would start selling something nobody priced.
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


def plan_cards(product):
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


def product_context(context, product_code):
    """Everything a product storefront needs, from the product's own record.

    Reads the product, never SaaS Settings, for anything a second product
    would answer differently — the trial switch, its plan, its length, its
    domain suffix. SaaS Settings holds one set of those values, so consulting
    it here is what made the trial path single-product no matter how many
    products the catalogue knew about.
    """
    product = frappe.get_cached_doc("SaaS Product", product_code)

    context.no_cache = 1
    context.product = product_code
    context.product_name = product.product_name
    context.product_tagline = product.description or ""
    context.title = f"{product.product_name} — {product.description or ''}".strip(" —")

    # A product whose trial is not configured must not advertise one. Offering
    # a signup that lands on a half-built site is worse than offering none.
    context.enable_public_trial = bool(
        cint(product.enable_public_trial) and product.trial_plan
    )
    context.trial_days = cint(product.trial_days) or 14
    context.domain_suffix = (product.default_site_domain or "").strip()
    context.mobile_app_url = (product.mobile_app_url or "").strip()

    # Where a paid-plan enquiry goes. Sales contact stays platform-wide: it is
    # the same people answering, whichever product asked.
    context.sales_whatsapp = (
        frappe.db.get_single_value("SaaS Settings", "sales_whatsapp") or ""
    ).strip()
    context.sales_email = (
        frappe.db.get_single_value("SaaS Settings", "sales_email") or ""
    ).strip()

    context.plans = plan_cards(product_code)

    context.check_method = CHECK_METHOD
    context.create_method = CREATE_METHOD
    context.progress_method = PROGRESS_METHOD

    return context
