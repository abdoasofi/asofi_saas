"""Daily subscription lifecycle for managed companies.

Wired from hooks.py ``scheduler_events["daily"]``. It expires overdue
subscriptions (and pushes the change to the company site) and emails a reminder a
configurable number of days before expiry. Every action is recorded so the Super
Admin can see what the scheduler did.
"""

import frappe
from frappe.utils import date_diff, getdate, today

from asofi_saas.asofi_saas.subscription.push import push_subscription

logger = frappe.logger("asofi_saas.lifecycle", allow_site=True)


def run_daily_subscription_check():
    settings = frappe.get_single("SaaS Settings")
    reminder_days = int(settings.reminder_days or 0)

    companies = frappe.get_all(
        "Managed Company",
        filters={
            "provision_status": "Active",
            "subscription_status": ["in", ["Trial", "Active"]],
        },
        fields=["name", "company_name", "subscription_end", "contact_email"],
    )

    today_d = getdate(today())
    expired = reminded = 0
    for c in companies:
        if not c.subscription_end:
            continue
        end = getdate(c.subscription_end)
        if end < today_d:
            _expire(c)
            expired += 1
        elif reminder_days and date_diff(end, today_d) == reminder_days:
            _remind(c, settings, reminder_days)
            reminded += 1

    logger.info(
        f"daily_subscription_check: {len(companies)} active, {expired} expired, {reminded} reminded"
    )
    return {"checked": len(companies), "expired": expired, "reminded": reminded}


def _expire(c):
    # Flip the status without a full save so we don't double-fire the controller's
    # auto-push; we push explicitly below with the correct audit action.
    frappe.db.set_value("Managed Company", c.name, "subscription_status", "Expired")
    logger.info(f"expiring {c.name} (ended {c.subscription_end})")
    try:
        push_subscription(c.name, action="Expire")
    except Exception:
        logger.exception(f"failed to push expiry for {c.name}")


def _remind(c, settings, days):
    recipient = (settings.notification_email or c.contact_email or "").strip()
    status, error = "Success", ""
    if not recipient:
        status, error = "Failed", "no recipient email configured"
    else:
        try:
            frappe.sendmail(
                recipients=[recipient],
                subject=f"تنبيه: اشتراك {c.company_name} ينتهي خلال {days} أيام",
                message=(
                    f"<p>ينتهي اشتراك الشركة <b>{c.company_name}</b> بتاريخ "
                    f"{c.subscription_end} (خلال {days} أيام).</p>"
                    f"<p>يرجى التجديد من لوحة تحكم Asofi SaaS.</p>"
                ),
                now=True,
            )
        except Exception as e:
            logger.exception(f"failed to send reminder for {c.name}")
            status, error = "Failed", str(e)

    frappe.get_doc(
        {
            "doctype": "Subscription Push Log",
            "company": c.name,
            "action": "Reminder",
            "status": status,
            "subscription_end": c.subscription_end,
            "response": f"reminder to {recipient}" if recipient else "",
            "error": error,
        }
    ).insert(ignore_permissions=True)
