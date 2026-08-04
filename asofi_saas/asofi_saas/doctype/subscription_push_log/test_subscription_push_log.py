from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from asofi_saas.asofi_saas.subscription import lifecycle

# Every company and plan belongs to a product now — the console serves
# more than Rased. These fixtures pin the one they were written for.
TEST_PRODUCT_FIXTURE = "rased"


def _plan(code="standard"):
    if not frappe.db.exists("SaaS Subscription Plan", code):
        frappe.get_doc(
            {"doctype": "SaaS Subscription Plan", "plan_code": code, "plan_name": code,
             "product": TEST_PRODUCT_FIXTURE}
        ).insert()


def _company(site, end, status="Active"):
    _plan()
    return frappe.get_doc(
        {
            "doctype": "Managed Company",
            "product": TEST_PRODUCT_FIXTURE,
            "company_name": site,
            "site_name": site,
            "site_url": f"http://{site}",
            "control_plane_secret": "s",
            "subscription_plan": "standard",
            "subscription_status": status,
            "provision_status": "Active",
            "subscription_end": end,
        }
    ).insert()


class TestLifecycle(FrappeTestCase):
    def test_expires_overdue_and_pushes(self):
        c = _company("life-exp.example", add_days(today(), -1))
        with patch.object(lifecycle, "push_subscription") as push:
            res = lifecycle.run_daily_subscription_check()
            push.assert_called()
        self.assertEqual(
            frappe.db.get_value("Managed Company", c.name, "subscription_status"), "Expired"
        )
        self.assertGreaterEqual(res["expired"], 1)

    def test_reminder_at_window_sends_and_logs(self):
        frappe.db.set_single_value("SaaS Settings", "reminder_days", 3)
        frappe.db.set_single_value("SaaS Settings", "notification_email", "admin@example.com")
        c = _company("life-rem.example", add_days(today(), 3))
        with patch.object(frappe, "sendmail") as mail, patch.object(lifecycle, "push_subscription"):
            res = lifecycle.run_daily_subscription_check()
            mail.assert_called()
        self.assertGreaterEqual(res["reminded"], 1)
        self.assertTrue(
            frappe.db.exists("Subscription Push Log", {"company": c.name, "action": "Reminder"})
        )

    def test_future_active_untouched(self):
        c = _company("life-fut.example", add_days(today(), 60))
        with patch.object(lifecycle, "push_subscription"):
            lifecycle.run_daily_subscription_check()
        self.assertEqual(
            frappe.db.get_value("Managed Company", c.name, "subscription_status"), "Active"
        )
