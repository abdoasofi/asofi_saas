import frappe
from frappe.tests.utils import FrappeTestCase

from asofi_saas import api

# Every company and plan belongs to a product now — the console serves
# more than Rased. These fixtures pin the one they were written for.
TEST_PRODUCT_FIXTURE = "rased"


class TestSaaSSubscriptionPlan(FrappeTestCase):
    def test_plan_code_is_stripped(self):
        doc = frappe.get_doc(
            {
                "doctype": "SaaS Subscription Plan",
                "product": TEST_PRODUCT_FIXTURE,
                "plan_code": "  spaced  ",
                "plan_name": "Spaced",
            }
        ).insert()
        self.assertEqual(doc.plan_code, "spaced")

    def test_upsert_plan_creates_then_updates(self):
        api.upsert_plan(
            plan_code="gold", plan_name="Gold", monthly_price=99,
            max_zones=7, product=TEST_PRODUCT_FIXTURE,
        )
        self.assertEqual(frappe.db.get_value("SaaS Subscription Plan", "gold", "max_zones"), 7)
        api.upsert_plan(plan_code="gold", max_zones=9)
        self.assertEqual(frappe.db.get_value("SaaS Subscription Plan", "gold", "max_zones"), 9)

    def test_list_plans_active_only(self):
        codes = {p["plan_code"] for p in api.list_plans(active_only=1)}
        # the seeded fixtures are active and must appear
        self.assertIn("standard", codes)
