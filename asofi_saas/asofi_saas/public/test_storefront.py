"""اختبارات واجهة البيع العامة.

This page is the one surface a stranger sees before paying, and it shipped two
faults at once: it listed every active plan on the control plane regardless of
product, and it rendered a missing limit and an unlimited limit identically.
Together those advertised a schools plan on a water-utility pricing page as
"unlimited collectors, zones and beneficiaries" for 500 a month — with public
trial signup switched on, so a visitor could act on it.

Both faults are invisible until a second product exists. These tests make the
second product exist.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from asofi_saas.www.asofisaas import index as storefront

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
    cards = [c for c in storefront._plan_cards(product) if c["plan_code"] == code]
    assert len(cards) == 1, f"توقّعت بطاقة واحدة لـ {code}، وجدت {len(cards)}"
    return cards[0]


class TestStorefrontScoping(FrappeTestCase):
    """One page sells one product."""

    def setUp(self):
        super().setUp()
        _product(
            OURS,
            [
                {"metric_key": "max_seats", "label_ar": "مقاعد", "metric_kind": "Limit",
                 "public_on_pricing": 1},
                {"metric_key": "allow_reports", "label_ar": "تقارير", "metric_kind": "Feature",
                 "public_on_pricing": 1},
                # Metered but never advertised — the editorial decision the
                # catalogue now carries instead of a hardcoded Python list.
                {"metric_key": "max_tokens", "label_ar": "رموز", "metric_kind": "Limit",
                 "public_on_pricing": 0},
            ],
        )
        _product(
            THEIRS,
            [{"metric_key": "max_collectors", "label_ar": "محصّلون", "metric_kind": "Limit",
              "public_on_pricing": 1}],
        )

    def test_another_products_plan_is_not_listed(self):
        """The fault that reached production. A plan belonging to a different
        product was priced on this page in this page's vocabulary."""
        _plan("_test_ours_basic", OURS, limits=[("max_seats", 30)])
        _plan("_test_theirs_basic", THEIRS, limits=[("max_collectors", 5)])

        codes = {p["plan_code"] for p in storefront._plan_cards(OURS)}

        self.assertIn("_test_ours_basic", codes)
        self.assertNotIn("_test_theirs_basic", codes)

    def test_a_metric_the_plan_does_not_carry_is_absent_not_unlimited(self):
        """The other half of the fault, and the one that made it dangerous.

        Absence used to read as zero, and zero has always meant unlimited here —
        so a plan that simply has no collector limit was sold as having an
        unlimited number of them.
        """
        _plan("_test_ours_seatless", OURS, limits=[])

        card = _card(OURS, "_test_ours_seatless")
        labels = [row["label"] for row in card["limits"]]

        self.assertEqual(card["limits"], [], f"ظهر حدّ لا تحمله الخطة: {labels}")

    def test_zero_on_a_metric_the_plan_does_carry_still_means_unlimited(self):
        """The fix must not overshoot: a top-tier plan states the limit and sets
        it to zero on purpose, and that has to keep reading as unlimited."""
        _plan("_test_ours_premium", OURS, limits=[("max_seats", 0)])

        card = _card(OURS, "_test_ours_premium")

        self.assertEqual(len(card["limits"]), 1)
        self.assertEqual(card["limits"][0]["value"], storefront.UNLIMITED)

    def test_a_metric_marked_private_is_not_advertised(self):
        """Metered but unpriced. Showing it would start selling something
        nobody set a price for."""
        _plan("_test_ours_tokens", OURS, limits=[("max_seats", 30), ("max_tokens", 900)])

        card = _card(OURS, "_test_ours_tokens")
        labels = [row["label"] for row in card["limits"]]

        self.assertEqual(labels, ["مقاعد"])

    def test_features_come_from_the_catalogue_not_a_fixed_list(self):
        """A gate added to a plan without a label used to be simply unadvertised.
        Now the label travels with the metric, so it cannot be forgotten."""
        _plan(
            "_test_ours_full", OURS,
            limits=[("max_seats", 30)], features=["allow_reports"],
        )

        card = _card(OURS, "_test_ours_full")

        self.assertEqual(card["modules"], ["تقارير"])
