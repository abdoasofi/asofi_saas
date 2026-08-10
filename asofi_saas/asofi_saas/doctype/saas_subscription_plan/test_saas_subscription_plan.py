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


class TestAPlanSaysNoOutLoud(FrappeTestCase):
    """A module a plan withholds has to travel as a 0, not as silence.

    The receiving site reads an unknown key as *allowed* — deliberately, so a
    self-hosted school never loses functionality to a plan that predates the
    feature. That makes silence a grant. A trial that simply never mentioned
    the executive dashboard was handing it out.
    """

    def test_a_module_the_plan_does_not_grant_travels_as_zero(self):
        product = frappe.get_cached_doc("SaaS Product", "edupulse")
        keys = product.keys_of("Feature")
        if not keys:
            self.skipTest("edupulse has no feature metrics on this site")

        definition = frappe.get_doc(
            "SaaS Subscription Plan", "edupulse_trial"
        ).definition()

        for key in keys:
            with self.subTest(key=key):
                self.assertIn(
                    key,
                    definition,
                    f"{key} لم يُذكر في الخطة، فسيُقرأ على الموقع كمسموح",
                )

    def test_a_plan_with_no_product_is_left_alone(self):
        """Such a plan predates the catalogue. It keeps travelling with exactly
        the keys it carried yesterday rather than a vocabulary nobody chose."""
        plan = frappe.new_doc("SaaS Subscription Plan")
        plan.plan_code = "_test_no_product"
        plan.plan_name = "_test_no_product"

        definition = plan.definition()

        self.assertFalse([k for k in definition if k.startswith("allow_")])
