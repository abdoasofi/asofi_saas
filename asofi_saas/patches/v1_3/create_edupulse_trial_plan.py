"""Give نبض التعلم a free plan, so its storefront has a trial to grant.

Rased's funnel has always granted a plan named `trial`. That plan belongs to
Rased, carries Rased's limits, and granting it to a school would provision a
site entitled to a collector cap it cannot name — which is why the signup
endpoint now refuses a trial plan from another product outright. So a second
product cannot open its trial until it has a free plan of its own.

The limits are a smaller `edupulse_standard`, and the modules are exactly the
ones that plan enables. A trial card advertising *more* than the plan it
upgrades to reads as a pricing mistake, and the pricing page shows the two
side by side.
"""

import frappe

PRODUCT = "edupulse"
PLAN = "edupulse_trial"
MODEL = "edupulse_standard"

#: A school can run a real pilot inside these: a couple of classes, the
#: teachers who own them, the courses those teachers actually author.
LIMITS = {
    "max_students": 50,
    "max_teachers": 5,
    "max_courses": 10,
    "max_storage_gb": 5,
}

#: Fallback only. The modules are read from the paid plan when it exists, so
#: the two cards cannot drift apart as its packaging changes.
FEATURES = (
    "allow_remedial_engine",
    "allow_parent_portal",
    "allow_offline_library",
)


def execute():
    if not frappe.db.exists("SaaS Product", PRODUCT):
        return

    if frappe.db.exists("SaaS Subscription Plan", PLAN):
        return

    doc = frappe.new_doc("SaaS Subscription Plan")
    doc.plan_code = PLAN
    doc.plan_name = "التجريبية"
    doc.product = PRODUCT
    doc.is_active = 1
    doc.monthly_price = 0
    doc.description = "جرّب المنصّة كاملة على فصل واحد قبل أن تقرّر."

    for key, value in LIMITS.items():
        doc.append("limits", {"metric_key": key, "value": value})

    for key in _modules():
        doc.append("features", {"metric_key": key, "enabled": 1})

    doc.insert(ignore_permissions=True)
    frappe.db.commit()


def _modules():
    """Whatever the paid plan includes — the trial is a smaller one, not a
    different one."""
    enabled = frappe.get_all(
        "SaaS Plan Feature",
        filters={"parent": MODEL, "enabled": 1},
        pluck="metric_key",
    )
    return enabled or list(FEATURES)
