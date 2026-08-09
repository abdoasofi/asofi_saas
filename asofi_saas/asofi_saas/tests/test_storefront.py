"""اختبارات إعداد واجهة البيع لكل منتج.

Plan-card rendering is covered next to the catalogue it is driven by, in
test_saas_product.py. What lives here is the other half: that each storefront
takes its *configuration* — trial switch, plan, length, domain suffix — from
its own product record rather than from SaaS Settings.

That distinction is the one most likely to creep back, because SaaS Settings
still holds a `trial_days` and a `default_site_domain` that look authoritative
and are simply the wrong scope now.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from asofi_saas.asofi_saas.public import storefront

OURS = "_test_store_ours"
THEIRS = "_test_store_theirs"


def _product(code, metrics):
    if frappe.db.exists("SaaS Product", code):
        frappe.delete_doc("SaaS Product", code, force=True)

    doc = frappe.new_doc("SaaS Product")
    doc.update(
        {
            "product_code": code,
            "product_name": f"منتج {code}",
            "bench_path": "/tmp/bench_test",
            "bench_executable": "bench",
            "apps_to_install": "test_app",
            "secret_config_key": "test_control_plane_secret",
            "manager_role": "Test Manager",
            "apply_path": "/api/method/test.apply",
            "usage_path": "/api/method/test.usage",
        }
    )
    for m in metrics:
        doc.append("metrics", m)
    doc.insert(ignore_permissions=True)
    return doc


def _plan(code, product, limits=(), features=()):
    if frappe.db.exists("SaaS Subscription Plan", code):
        frappe.delete_doc("SaaS Subscription Plan", code, force=True)

    doc = frappe.new_doc("SaaS Subscription Plan")
    doc.update(
        {
            "plan_code": code,
            "plan_name": code,
            "product": product,
            "is_active": 1,
            "monthly_price": 100,
        }
    )
    for key, value in limits:
        doc.append("limits", {"metric_key": key, "value": value})
    for key in features:
        doc.append("features", {"metric_key": key, "enabled": 1})
    doc.insert(ignore_permissions=True)
    return doc


def _card(product, code):
    """The rendered card for one plan.

    By code, never by position: every plan here is priced the same, so index 0
    is whichever row the database hands back first. A test that reads it passes
    or fails on what its neighbours left behind.
    """
    cards = [c for c in storefront.plan_cards(product) if c["plan_code"] == code]
    assert len(cards) == 1, f"توقّعت بطاقة واحدة لـ {code}، وجدت {len(cards)}"
    return cards[0]


class TestProductContext(FrappeTestCase):
    """Each storefront is configured by its own product record.

    The trial switch, its plan, its length and its domain suffix all used to be
    read from SaaS Settings — a single doc holding one set of values. That is
    what made the public surface single-product no matter how many products the
    catalogue knew about, and it is the thing most likely to creep back, since
    SaaS Settings still holds a `trial_days` that looks authoritative.
    """

    def setUp(self):
        super().setUp()
        _product(
            OURS,
            [{"metric_key": "max_seats", "label_ar": "مقاعد", "metric_kind": "Limit",
              "public_on_pricing": 1}],
        )
        _plan("_test_ours_trial", OURS, limits=[("max_seats", 3)])

    def _ctx(self, product):
        ctx = frappe._dict()
        storefront.product_context(ctx, product)
        return ctx

    def test_trial_settings_come_from_the_product_not_saas_settings(self):
        doc = frappe.get_doc("SaaS Product", OURS)
        doc.enable_public_trial = 1
        doc.trial_plan = "_test_ours_trial"
        doc.trial_days = 21
        doc.default_site_domain = ".ours.test"
        doc.save(ignore_permissions=True)
        frappe.clear_cache()

        # Set the global to something different. If it ever leaks through, the
        # assertions below say so instead of the page quietly showing 14.
        frappe.db.set_single_value("SaaS Settings", "trial_days", 14)

        ctx = self._ctx(OURS)

        self.assertTrue(ctx.enable_public_trial)
        self.assertEqual(ctx.trial_days, 21)
        self.assertEqual(ctx.domain_suffix, ".ours.test")

    def test_a_product_without_a_trial_plan_does_not_advertise_a_trial(self):
        """The switch alone is not enough. Turning it on without setting the
        plan would offer a signup that cannot resolve what to provision — the
        visitor fills the form and the request dies at the far end."""
        doc = frappe.get_doc("SaaS Product", OURS)
        doc.enable_public_trial = 1
        doc.trial_plan = None
        doc.save(ignore_permissions=True)
        frappe.clear_cache()

        self.assertFalse(self._ctx(OURS).enable_public_trial)

    def test_the_page_titles_itself_from_the_product(self):
        ctx = self._ctx(OURS)

        self.assertIn("منتج", ctx.title)
        self.assertEqual(ctx.product, OURS)

    def test_only_active_products_are_offered_on_the_platform_page(self):
        doc = frappe.get_doc("SaaS Product", THEIRS) if frappe.db.exists(
            "SaaS Product", THEIRS
        ) else _product(THEIRS, [])
        doc.is_active = 0
        doc.save(ignore_permissions=True)

        codes = {p["product_code"] for p in storefront.products()}

        self.assertIn(OURS, codes)
        self.assertNotIn(THEIRS, codes)
