"""Mark which catalogue metrics the public pricing page advertises.

The storefront used to carry that editorial choice in Python: a hardcoded
tuple of Rased's module fields, plus a hardcoded list of Rased's limit columns
in the template. It could therefore only ever describe one product — and when
a second one arrived, its plan was rendered through Rased's vocabulary.

Which metrics are worth selling is a per-product decision, so it belongs on the
per-product catalogue. This seeds the flag so the page keeps showing exactly
what it showed before for Rased, and shows something sensible for the rest.
"""

import frappe

RASED = "rased"

# Exactly what the page listed before the catalogue drove it. `max_ai_tokens`
# is metered and has never been advertised — the omission is deliberate, and
# defaulting every metric to "public" would have started selling it.
#
# The values are the page's own wording, not the console's. A catalogue label
# is a column header — «التتبّع» — where the storefront was selling «التتبّع
# المباشر للمحصلين». Driving the page from the catalogue without preserving
# these would have quietly rewritten a live marketing page into console labels.
RASED_PUBLIC = {
    "max_collectors": "محصّلون",
    "max_zones": "مناطق",
    "max_beneficiaries": "مستفيدون",
    "max_branches": "فروع",
    "max_employees": "موظفون",
    "allow_photo_capture": "توثيق بالصور",
    "allow_reports_export": "تصدير التقارير",
    "allow_branches": "الفروع المتعددة",
    "allow_website": "الموقع الإلكتروني ونماذج الطلبات",
    "allow_hr": "الموارد البشرية والرواتب",
    "allow_incidents": "البلاغات والمخالفات",
    "allow_tracking": "التتبّع المباشر للمحصلين",
    "allow_messaging": "المراسلات الداخلية",
    "allow_ai_analytics": "تحليلات الذكاء والتنبؤ",
    "allow_meter_ocr": "قراءة العدّاد بالصورة",
}


def execute():
    for name in frappe.get_all("SaaS Product", pluck="name"):
        doc = frappe.get_doc("SaaS Product", name)
        changed = False

        for row in doc.metrics:
            # Usage metrics describe a live tenant, not a purchase. They have
            # no place on a pricing page whatever the product.
            if row.metric_kind == "Usage":
                wanted = 0
            elif name == RASED:
                wanted = 1 if row.metric_key in RASED_PUBLIC else 0
                if wanted and not row.public_label_ar:
                    row.public_label_ar = RASED_PUBLIC[row.metric_key]
                    changed = True
            else:
                # A newer product has no prior page to preserve, and every
                # limit and feature it declares was declared to be sold.
                wanted = 1

            if int(row.public_on_pricing or 0) != wanted:
                row.public_on_pricing = wanted
                changed = True

        if changed:
            doc.save(ignore_permissions=True)

    frappe.db.commit()
