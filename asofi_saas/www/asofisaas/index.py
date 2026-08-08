"""Context for the public Rased / Asofi SaaS marketing + trial-onboarding page.

Runs as the website visitor (Guest). Reads are done with permission-free helpers
(``frappe.get_all`` / ``frappe.db.get_single_value``) so anonymous visitors can see
the plans and the trial configuration without any Desk role.
"""

import frappe

from asofi_saas.asofi_saas.public.tenant import _domain_suffix

# The public method paths the page calls via frappe.call (kept here so the
# template and the backend never drift out of sync).
CHECK_METHOD = "asofi_saas.asofi_saas.public.tenant.check_subdomain"
CREATE_METHOD = "asofi_saas.asofi_saas.public.tenant.create_trial_tenant"
PROGRESS_METHOD = "asofi_saas.asofi_saas.public.tenant.get_trial_progress"

#: The product this page sells.
#:
#: Every word on it is Rased's — the hero, the module labels below, the plan
#: columns the template reads. It is not a generic storefront, and until it is
#: it must not list another product's plans: `edupulse_standard` was appearing
#: between Rased's tiers, and because the template renders a zero limit as
#: "غير محدود", a 500-riyal schools plan was advertised as an unlimited water
#: utility plan. Naming the product here is also the seam a real multi-product
#: storefront will hang on.
PRODUCT = "rased"

#: The modules a buyer is actually choosing between, in selling order.
#: Kept here rather than in the template so the field list and the Arabic
#: labels stay in one place — a gate added to a plan without a label here is
#: simply not advertised, never rendered as a raw fieldname.
PUBLIC_MODULES = (
    ("allow_branches", "الفروع المتعددة"),
    ("allow_website", "الموقع الإلكتروني ونماذج الطلبات"),
    ("allow_hr", "الموارد البشرية والرواتب"),
    ("allow_incidents", "البلاغات والمخالفات"),
    ("allow_tracking", "التتبّع المباشر للمحصلين"),
    ("allow_messaging", "المراسلات الداخلية"),
    ("allow_ai_analytics", "تحليلات الذكاء والتنبؤ"),
    ("allow_meter_ocr", "قراءة العدّاد بالصورة"),
)


def get_context(context):
    context.no_cache = 1
    context.title = "راصد — منصّة إدارة تحصيل الخدمات"

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

    # get_all bypasses user permissions, so Guests still see the public catalogue.
    plans = frappe.get_all(
        "SaaS Subscription Plan",
        filters={"is_active": 1, "product": PRODUCT},
        fields=[
            "plan_code",
            "plan_name",
            "monthly_price",
            "max_collectors",
            "max_zones",
            "max_beneficiaries",
            "max_branches",
            "max_employees",
            "allow_photo_capture",
            "allow_reports_export",
            *[flag for flag, _label in PUBLIC_MODULES],
            "description",
        ],
        order_by="monthly_price asc",
    )
    for plan in plans:
        plan["modules"] = [label for flag, label in PUBLIC_MODULES if plan.get(flag)]

    context.plans = plans

    context.check_method = CHECK_METHOD
    context.create_method = CREATE_METHOD
    context.progress_method = PROGRESS_METHOD
    return context
