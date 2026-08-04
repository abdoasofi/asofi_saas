"""Tests for the multi-product control plane.

The console started as Rased and only Rased. Generalising it touches the two
code paths that reach a live tenant — the subscription push and the usage pull —
so most of what follows exists to prove that a Rased company still gets exactly
what it got before, byte for byte.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from asofi_saas.asofi_saas.doctype.saas_product import saas_product
from asofi_saas.asofi_saas.doctype.saas_subscription_plan.saas_subscription_plan import (
    LEGACY_RASED_FIELDS,
)
from asofi_saas.patches.v1_1 import create_products
from asofi_saas.asofi_saas.provisioning import drivers
from asofi_saas.asofi_saas.subscription import push

TEST_PRODUCT = "_test_product"


def _product(**overrides):
    if frappe.db.exists("SaaS Product", TEST_PRODUCT):
        frappe.delete_doc("SaaS Product", TEST_PRODUCT, force=True)

    doc = frappe.new_doc("SaaS Product")
    doc.update(
        {
            "product_code": TEST_PRODUCT,
            "product_name": "منتج اختباري",
            "bench_path": "/tmp/bench_test",
            "bench_executable": "bench",
            "apps_to_install": "app_one\napp_two",
            "secret_config_key": "test_control_plane_secret",
            "manager_role": "Test Manager",
            "apply_path": "/api/method/test.apply",
            "usage_path": "/api/method/test.usage",
            **overrides,
        }
    )
    doc.append("metrics", {"metric_key": "max_widgets", "label_ar": "أدوات", "metric_kind": "Limit"})
    doc.append("metrics", {"metric_key": "allow_gizmo", "label_ar": "أداة", "metric_kind": "Feature"})
    doc.append("metrics", {"metric_key": "widgets", "label_ar": "أدوات", "metric_kind": "Usage"})
    doc.insert(ignore_permissions=True)
    return doc


class TestProductCatalogue(FrappeTestCase):
    def setUp(self):
        super().setUp()
        self.product = _product()

    def test_duplicate_metric_key_is_refused(self):
        """A duplicate makes the wire payload ambiguous — last row silently wins,
        so an operator would see a limit set that never travels."""
        self.product.append(
            "metrics",
            {"metric_key": "max_widgets", "label_ar": "مكرّر", "metric_kind": "Limit"},
        )

        with self.assertRaises(frappe.ValidationError):
            self.product.save(ignore_permissions=True)

    def test_paths_are_normalised(self):
        self.product.apply_path = "api/method/test.apply"
        self.product.bench_path = "/tmp/bench_test/"
        self.product.save(ignore_permissions=True)

        self.assertEqual(self.product.apply_path, "/api/method/test.apply")
        self.assertEqual(self.product.bench_path, "/tmp/bench_test")

    def test_keys_are_partitioned_by_kind(self):
        self.assertEqual(self.product.keys_of("Limit"), ["max_widgets"])
        self.assertEqual(self.product.keys_of("Feature"), ["allow_gizmo"])
        self.assertEqual(self.product.keys_of("Usage"), ["widgets"])

    def test_unknown_product_raises_rather_than_defaulting(self):
        """Defaulting would provision a tenant onto whatever bench happens to be
        configured — the wrong Frappe version, silently."""
        with self.assertRaises(frappe.ValidationError):
            saas_product.get(None)


class TestPlanRows(FrappeTestCase):
    def setUp(self):
        super().setUp()
        self.product = _product()

    def _plan(self):
        name = f"{TEST_PRODUCT}_plan"
        if frappe.db.exists("SaaS Subscription Plan", name):
            frappe.delete_doc("SaaS Subscription Plan", name, force=True)

        doc = frappe.new_doc("SaaS Subscription Plan")
        doc.update(
            {
                "plan_code": name,
                "plan_name": "خطة اختبارية",
                "product": TEST_PRODUCT,
                "is_active": 1,
            }
        )
        return doc

    def test_a_key_outside_the_catalogue_is_refused(self):
        """The failure this prevents is invisible otherwise: the tenant ignores
        keys it does not know, so a typo just quietly does nothing."""
        plan = self._plan()
        plan.append("limits", {"metric_key": "max_widgetz", "value": 10})

        with self.assertRaises(frappe.ValidationError):
            plan.insert(ignore_permissions=True)

    def test_a_feature_key_cannot_be_used_as_a_limit(self):
        plan = self._plan()
        plan.append("limits", {"metric_key": "allow_gizmo", "value": 1})

        with self.assertRaises(frappe.ValidationError):
            plan.insert(ignore_permissions=True)

    def test_labels_are_denormalised_from_the_catalogue(self):
        plan = self._plan()
        plan.append("limits", {"metric_key": "max_widgets", "value": 10})
        plan.insert(ignore_permissions=True)

        self.assertEqual(plan.limits[0].label_ar, "أدوات")

    def test_definition_carries_the_products_own_vocabulary(self):
        plan = self._plan()
        plan.append("limits", {"metric_key": "max_widgets", "value": 42})
        plan.append("features", {"metric_key": "allow_gizmo", "enabled": 1})
        plan.insert(ignore_permissions=True)

        definition = plan.definition()

        self.assertEqual(definition["max_widgets"], 42)
        self.assertEqual(definition["allow_gizmo"], 1)
        self.assertEqual(definition["plan_code"], plan.plan_code)


class TestRasedIsUnchanged(FrappeTestCase):
    """The migration must be invisible to every tenant already in production."""

    def test_plan_definition_is_unchanged_by_the_migration(self):
        """Compare `definition()` against the payload the removed PLAN_FIELDS
        loop produced. Every Rased plan on this site must match exactly."""
        plans = frappe.get_all(
            "SaaS Subscription Plan", filters={"product": create_products.RASED}, pluck="name"
        )

        if not plans:
            self.skipTest("no Rased plans on this site")

        for name in plans:
            with self.subTest(plan=name):
                plan = frappe.get_doc("SaaS Subscription Plan", name)

                expected = {"plan_code": name}
                for field in ("plan_name", "is_active", "monthly_price") + LEGACY_RASED_FIELDS + ("description",):
                    value = plan.get(field)
                    expected[field] = value if value is not None else 0

                actual = plan.definition()

                for key, value in expected.items():
                    self.assertEqual(
                        actual.get(key), value, f"{name}.{key} changed on the wire"
                    )

    def test_every_company_and_plan_has_a_product(self):
        """An unassigned row would fall back to the global bench path — which is
        exactly the ambiguity this phase removes."""
        for doctype in ("Managed Company", "SaaS Subscription Plan"):
            with self.subTest(doctype=doctype):
                orphans = frappe.get_all(
                    doctype, filters={"product": ("in", [None, ""])}, pluck="name"
                )
                self.assertEqual(orphans, [], f"{doctype} rows with no product")

    def test_rased_still_pushes_to_its_original_endpoint(self):
        if not frappe.db.exists("SaaS Product", create_products.RASED):
            self.skipTest("rased product not created")

        doc = frappe.get_doc(
            {"doctype": "Managed Company", "product": create_products.RASED}
        )

        self.assertEqual(push._apply_path(doc), push.APPLY_PATH)

    def test_a_company_with_no_product_falls_back_instead_of_failing(self):
        """Belt and braces for a row the patch somehow missed."""
        doc = frappe.get_doc({"doctype": "Managed Company", "product": None})

        self.assertEqual(push._apply_path(doc), push.APPLY_PATH)


class TestBenchDriver(FrappeTestCase):
    def setUp(self):
        super().setUp()
        self.product = _product()
        self.driver = drivers.for_product(TEST_PRODUCT)

    def test_apps_keep_their_declared_order(self):
        """edupulse_core requires lms to already exist — order is not cosmetic."""
        self.assertEqual(self.driver.apps(), ["app_one", "app_two"])

    def test_commands_run_on_the_products_own_bench(self):
        self.assertEqual(self.driver.cwd, "/tmp/bench_test")

    def test_secret_goes_to_the_products_own_config_key(self):
        """Rased reads `rased_control_plane_secret`; EduPulse reads its own. One
        hard-coded key would leave the other product unauthenticated."""
        cmd = self.driver.set_secret("site.test", "s3cr3t")

        self.assertIn("test_control_plane_secret", cmd)
        self.assertIn("s3cr3t", cmd)

    def test_manager_gets_the_products_role(self):
        cmd = self.driver.add_manager("site.test", "m@x.com", "pw", "Man", "Ager")

        self.assertIn("Test Manager", cmd)

    def test_no_role_flag_when_the_product_declares_none(self):
        """`bench add-user --add-role ''` fails; omitting the flag does not."""
        self.product.manager_role = ""
        self.product.save(ignore_permissions=True)
        frappe.clear_document_cache("SaaS Product", TEST_PRODUCT)

        cmd = drivers.for_product(TEST_PRODUCT).add_manager(
            "site.test", "m@x.com", "pw", "Man", "Ager"
        )

        self.assertNotIn("--add-role", cmd)

    def test_a_product_without_a_bench_cannot_provision(self):
        """EduPulse ships with a blank bench_path on purpose — better a loud
        failure than a school created on the Rased v15 bench."""
        self.product.bench_path = ""
        self.product.db_set("bench_path", "", update_modified=False)
        frappe.clear_document_cache("SaaS Product", TEST_PRODUCT)

        with self.assertRaises(frappe.ValidationError):
            drivers.for_product(TEST_PRODUCT)
