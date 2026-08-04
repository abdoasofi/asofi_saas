import frappe

# Module 6 introduced per-module feature gates on subscription plans.
#
# A doctype field `default` applies when the ORM creates a NEW document; it is
# not a DDL default for rows that already exist. Frappe adds a Check column as
# `int NOT NULL DEFAULT 0`, so every plan that existed before this change came
# out of migrate with every gate CLOSED — and pushing such a plan would have
# switched off every module on every tenant using it.
#
# These plans were sold before the gates existed, so their customers are
# entitled to what they already had: open every gate once, here. Frappe records
# a patch as run, so a plan the operator narrows afterwards stays narrowed.

GATES = (
    "allow_branches",
    "allow_website",
    "allow_hr",
    "allow_incidents",
    "allow_tracking",
    "allow_messaging",
    "allow_ai_analytics",
    "allow_meter_ocr",
)


def execute():
    if not frappe.db.exists("DocType", "SaaS Subscription Plan"):
        return

    table = "tabSaaS Subscription Plan"
    columns = {c.get("Field") for c in frappe.db.sql(f"DESCRIBE `{table}`", as_dict=True)}
    present = [g for g in GATES if g in columns]
    if not present:
        return

    assignments = ", ".join(f"`{g}` = 1" for g in present)
    frappe.db.sql(f"UPDATE `{table}` SET {assignments}")
    frappe.db.commit()
    frappe.clear_cache(doctype="SaaS Subscription Plan")
