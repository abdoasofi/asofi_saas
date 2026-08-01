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


def get_context(context):
    context.no_cache = 1
    context.title = "راصد — منصّة إدارة تحصيل الخدمات"

    context.enable_public_trial = bool(
        frappe.db.get_single_value("SaaS Settings", "enable_public_trial")
    )
    context.trial_days = int(frappe.db.get_single_value("SaaS Settings", "trial_days") or 14)
    context.domain_suffix = _domain_suffix()
    context.mobile_app_url = frappe.db.get_single_value("SaaS Settings", "mobile_app_url") or ""

    # get_all bypasses user permissions, so Guests still see the public catalogue.
    context.plans = frappe.get_all(
        "SaaS Subscription Plan",
        filters={"is_active": 1},
        fields=[
            "plan_code",
            "plan_name",
            "monthly_price",
            "max_collectors",
            "max_zones",
            "max_beneficiaries",
            "allow_photo_capture",
            "allow_reports_export",
            "description",
        ],
        order_by="monthly_price asc",
    )

    context.check_method = CHECK_METHOD
    context.create_method = CREATE_METHOD
    context.progress_method = PROGRESS_METHOD
    return context
