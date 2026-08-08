"""اختبارات قُمع التسجيل الذاتي.

The rule these hold: **a signup provisions the product whose page it came
from**, configured entirely by that product's own record. Every value the flow
needs — the trial switch, the plan it grants, its length, the domain suffix,
the bench — used to come from SaaS Settings, a single doc holding one set of
them, which is what made every signup a Rased signup no matter which storefront
the visitor filled in.

Two products are set up in every test, deliberately differing on every one of
those values, so a helper that reaches for the global instead of the product
fails here rather than in production.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from asofi_saas.asofi_saas.provisioning import provision as prov
from asofi_saas.asofi_saas.public import storefront, tenant

OURS = "_test_tenant_ours"
THEIRS = "_test_tenant_theirs"


def _plan(code, product, active=1):
    if frappe.db.exists("SaaS Subscription Plan", code):
        frappe.delete_doc("SaaS Subscription Plan", code, force=True)

    frappe.get_doc(
        {
            "doctype": "SaaS Subscription Plan",
            "plan_code": code,
            "plan_name": code,
            "product": product,
            "is_active": active,
        }
    ).insert(ignore_permissions=True)
    return code


def _product(code, **overrides):
    """A product with a working trial, unless a test says otherwise."""
    if frappe.db.exists("SaaS Product", code):
        frappe.delete_doc("SaaS Product", code, force=True)

    doc = frappe.new_doc("SaaS Product")
    doc.update(
        {
            "product_code": code,
            "product_name": f"منتج {code}",
            "is_active": 1,
            "bench_path": "/tmp/bench_" + code,
            "bench_executable": "bench",
            "apps_to_install": "test_app",
            "secret_config_key": "test_control_plane_secret",
            "manager_role": "Test Manager",
            "apply_path": "/api/method/test.apply",
            "usage_path": "/api/method/test.usage",
            "enable_public_trial": 1,
            "trial_days": 14,
        }
    )
    doc.update(overrides)
    doc.insert(ignore_permissions=True)

    # The trial plan has to exist and belong to this product; the DocType now
    # refuses the alternative, which is the point of `test_a_products_trial_plan_
    # must_be_its_own`.
    if doc.enable_public_trial and "trial_plan" not in overrides:
        doc.trial_plan = _plan(f"{code}_trial", code)
        doc.save(ignore_permissions=True)

    frappe.clear_cache()
    return doc


def _company(site_name, product, **overrides):
    values = {
        "doctype": "Managed Company",
        "product": product,
        "company_name": site_name,
        "site_name": site_name,
        "site_url": "https://" + site_name,
        "subscription_plan": f"{product}_trial",
        "subscription_status": "Trial",
        "provision_status": "Active",
    }
    values.update(overrides)
    return frappe.get_doc(values).insert(ignore_permissions=True)


class StorefrontTestCase(FrappeTestCase):
    """Two products, configured differently on purpose.

    `OURS` is the page under test. `THEIRS` exists so that anything read from a
    global instead of from the product shows up as the wrong value rather than
    as a passing test.
    """

    def setUp(self):
        super().setUp()
        self.ours = _product(
            OURS,
            default_site_domain="ours.test",
            trial_site_url_template="http://{site}:8100",
            trial_days=21,
        )
        self.theirs = _product(
            THEIRS,
            default_site_domain="theirs.test",
            trial_site_url_template="https://{site}",
            trial_days=7,
        )

        # Set the globals to something different again. If one ever leaks
        # through, the assertions say so instead of the flow quietly agreeing.
        settings = frappe.get_single("SaaS Settings")
        settings.default_site_domain = "global.test"
        settings.trial_days = 30
        settings.trial_plan = None
        settings.enable_public_trial = 0
        settings.bench_path = "/tmp/bench_global"
        settings.db_root_password = "x"
        settings.save(ignore_permissions=True)


class TestSubdomainValidation(FrappeTestCase):
    def test_accepts_good_names(self):
        self.assertIsNone(tenant._subdomain_error("my-company"))
        self.assertIsNone(tenant._subdomain_error("abc"))

    def test_rejects_bad_shapes(self):
        self.assertIsNotNone(tenant._subdomain_error(""))
        self.assertIsNotNone(tenant._subdomain_error("ab"))          # too short
        self.assertIsNotNone(tenant._subdomain_error("-abc"))        # leading hyphen
        self.assertIsNotNone(tenant._subdomain_error("abc-"))        # trailing hyphen
        self.assertIsNotNone(tenant._subdomain_error("Abc"))         # uppercase
        self.assertIsNotNone(tenant._subdomain_error("a b"))         # space

    def test_rejects_reserved(self):
        self.assertIsNotNone(tenant._subdomain_error("www"))
        self.assertIsNotNone(tenant._subdomain_error("admin"))

    def test_every_product_code_is_reserved(self):
        """A tenant called `edupulse` would sit at the address the product's own
        site wants, and no rename undoes a handed-out subdomain."""
        for code in frappe.get_all("SaaS Product", pluck="product_code"):
            if code and not code.startswith("_test"):
                self.assertIn(code, tenant.RESERVED_SUBDOMAINS)


class TestPerProductAddressing(StorefrontTestCase):
    def test_the_suffix_comes_from_the_product(self):
        self.assertEqual(tenant._domain_suffix(self.ours), ".ours.test")
        self.assertEqual(tenant._domain_suffix(self.theirs), ".theirs.test")
        self.assertEqual(tenant._site_name_for("acme", self.ours), "acme.ours.test")

    def test_the_form_shows_the_address_the_endpoint_will_create(self):
        """The suffix the page prints under the subdomain box is a promise.

        The field holds whatever the operator typed, and the two products'
        were saved differently — one with a leading dot, one without. The page
        printed it raw, so it offered `acmeours.test` for a site the endpoint
        would have created as `acme.ours.test`.
        """
        self.ours.default_site_domain = "ours.test"     # no leading dot
        self.ours.save(ignore_permissions=True)
        frappe.clear_cache()

        ctx = frappe._dict()
        storefront.product_context(ctx, OURS)

        self.assertEqual(ctx.domain_suffix, ".ours.test")
        self.assertEqual(
            "acme" + ctx.domain_suffix, tenant._site_name_for("acme", self.ours)
        )

    def test_the_url_template_comes_from_the_product(self):
        """Two benches answer on two ports; one template cannot describe both."""
        self.assertEqual(
            tenant._site_url_for("acme.ours.test", self.ours), "http://acme.ours.test:8100"
        )
        self.assertEqual(
            tenant._site_url_for("acme.theirs.test", self.theirs), "https://acme.theirs.test"
        )

    def test_a_template_without_the_placeholder_falls_back(self):
        self.ours.trial_site_url_template = "broken-no-placeholder"
        self.ours.save(ignore_permissions=True)
        self.assertEqual(
            tenant._site_url_for("acme.ours.test", self.ours), "https://acme.ours.test"
        )


class TestProductResolution(StorefrontTestCase):
    def test_an_unknown_product_is_refused(self):
        with self.assertRaises(frappe.exceptions.ValidationError):
            tenant._product("no-such-product")

    def test_an_inactive_product_is_refused(self):
        self.ours.is_active = 0
        self.ours.save(ignore_permissions=True)
        frappe.clear_cache()
        with self.assertRaises(frappe.exceptions.ValidationError):
            tenant._product(OURS)

    def test_a_nameless_request_is_refused_while_two_trials_are_open(self):
        """Guessing would hand a school a water-utility site. No message
        afterwards undoes that, so the request has to name its product."""
        with self.assertRaises(frappe.exceptions.ValidationError):
            tenant._product(None)

    def test_a_nameless_request_resolves_while_only_one_trial_is_open(self):
        """Which is what keeps a page cached before the argument existed working."""
        self.theirs.enable_public_trial = 0
        self.theirs.save(ignore_permissions=True)
        for code in frappe.get_all(
            "SaaS Product", filters={"is_active": 1, "enable_public_trial": 1}, pluck="name"
        ):
            if code not in (OURS, THEIRS):
                frappe.db.set_value("SaaS Product", code, "enable_public_trial", 0)
        frappe.clear_cache()

        self.assertEqual(tenant._product(None).name, OURS)


class TestCheckSubdomain(StorefrontTestCase):
    def test_available_when_free(self):
        res = tenant.check_subdomain(subdomain="free-name-xyz", product=OURS)
        self.assertTrue(res["available"])
        self.assertEqual(res["site_name"], "free-name-xyz.ours.test")
        self.assertEqual(res["product"], OURS)

    def test_the_same_name_is_free_on_the_other_product(self):
        """Two products, two domain suffixes — so two different site names."""
        _company("taken.ours.test", OURS)

        self.assertFalse(tenant.check_subdomain(subdomain="taken", product=OURS)["available"])
        self.assertTrue(tenant.check_subdomain(subdomain="taken", product=THEIRS)["available"])

    def test_reserved_is_unavailable(self):
        res = tenant.check_subdomain(subdomain="admin", product=OURS)
        self.assertFalse(res["available"])
        self.assertTrue(res["reason"])


class TestCreateTrialTenant(StorefrontTestCase):
    """Each test signs up under its own subdomain, and its own email.

    FrappeTestCase rolls back once per class, not once per test, so a value
    reused by a later test collides with the row an earlier one left behind:
    the subdomain as a duplicate key, the email as the one-live-trial rule.
    Both read as a bug in the flow rather than as shared fixture state.
    """

    def _create(self, **overrides):
        payload = {
            "product": OURS,
            "company_name": "ACME",
            "phone": "777123456",
            "password": "secret123",
        }
        payload.update(overrides)
        payload.setdefault("admin_email", f"{payload.get('subdomain')}@acme.com")
        with patch.object(frappe, "enqueue") as enq, patch.object(frappe.db, "commit"):
            result = tenant.create_trial_tenant(**payload)
        return result, enq

    def test_the_site_is_created_for_the_product_the_page_named(self):
        """The single fact this whole module exists for."""
        res, enq = self._create(subdomain="acme-named")

        doc = frappe.get_doc("Managed Company", "acme-named.ours.test")
        self.assertEqual(doc.product, OURS)
        self.assertEqual(doc.subscription_plan, f"{OURS}_trial")
        self.assertEqual(res["site_name"], "acme-named.ours.test")
        self.assertEqual(res["site_url"], "http://acme-named.ours.test:8100")
        self.assertTrue(enq.called)  # provisioning worker + welcome email

    def test_the_trial_length_comes_from_the_product(self):
        self._create(subdomain="acme-length")
        doc = frappe.get_doc("Managed Company", "acme-length.ours.test")
        self.assertEqual(doc.subscription_status, "Trial")
        self.assertEqual(doc.is_trial, 1)
        # 21 from the product, not 30 from SaaS Settings and not the 14 default.
        self.assertEqual(str(doc.subscription_end), str(add_days(today(), 21)))

    def test_blocked_when_the_product_has_no_trial(self):
        self.ours.enable_public_trial = 0
        self.ours.save(ignore_permissions=True)
        frappe.clear_cache()

        with self.assertRaises(frappe.exceptions.ValidationError):
            self._create(subdomain="acme-notrial")

    def test_blocked_when_the_product_names_no_trial_plan(self):
        """The switch alone used to be enough, with the plan falling back to a
        literal "trial" — which on a second product grants another product's."""
        frappe.db.set_value("SaaS Product", OURS, "trial_plan", None)
        frappe.clear_cache()

        with self.assertRaises(frappe.exceptions.ValidationError):
            self._create(subdomain="acme-noplan")

    def test_a_trial_plan_belonging_to_another_product_is_refused(self):
        """Set behind the DocType's back, the way stale data arrives."""
        frappe.db.set_value("SaaS Product", OURS, "trial_plan", f"{THEIRS}_trial")
        frappe.clear_cache()

        with self.assertRaises(frappe.exceptions.ValidationError):
            self._create(subdomain="acme-foreign")

    def test_a_visitors_plan_choice_is_recorded_but_grants_nothing(self):
        # The pricing page sends whichever card the visitor clicked. This
        # endpoint is guest-callable, so treating that value as an entitlement
        # would let anyone self-provision the most expensive tier for free.
        _plan(f"{OURS}_premium", OURS)
        self._create(subdomain="acme-choice", requested_plan=f"{OURS}_premium")

        doc = frappe.get_doc("Managed Company", "acme-choice.ours.test")
        self.assertEqual(doc.subscription_plan, f"{OURS}_trial")   # what they GET
        self.assertEqual(doc.requested_plan, f"{OURS}_premium")    # what they ASKED FOR

    def test_another_products_plan_is_not_even_recorded(self):
        """Hiding a plan from the page does not stop it being posted here, and a
        lead carrying another product's plan describes a site this one cannot be."""
        self.assertIsNone(tenant._sanitize_requested_plan(f"{THEIRS}_trial", self.ours))

    def test_an_unknown_or_inactive_plan_is_dropped_not_stored(self):
        # The value lands in a Link field, so unchecked text would either break
        # the record or park junk on it.
        _plan(f"{OURS}_retired", OURS, active=0)

        self.assertIsNone(tenant._sanitize_requested_plan("no-such-plan", self.ours))
        self.assertIsNone(tenant._sanitize_requested_plan(f"{OURS}_retired", self.ours))
        self.assertIsNone(tenant._sanitize_requested_plan("  ", self.ours))
        self.assertIsNone(tenant._sanitize_requested_plan(None, self.ours))
        self.assertEqual(
            tenant._sanitize_requested_plan(f" {OURS}_trial ", self.ours), f"{OURS}_trial"
        )

    def test_a_signup_with_no_plan_click_still_works(self):
        self._create(subdomain="acme-noclick")
        doc = frappe.get_doc("Managed Company", "acme-noclick.ours.test")
        self.assertFalse(doc.requested_plan)

    def test_rejects_short_password(self):
        with self.assertRaises(frappe.exceptions.ValidationError):
            self._create(subdomain="acme-short", password="short")

    def test_rejects_bad_subdomain(self):
        with self.assertRaises(frappe.exceptions.ValidationError):
            self._create(subdomain="ab")

    def test_blocks_a_second_live_trial_on_the_same_product(self):
        _company("held.ours.test", OURS, contact_email="held@acme.com")

        with self.assertRaises(frappe.exceptions.ValidationError):
            self._create(subdomain="held-again", admin_email="held@acme.com")

    def test_allows_the_same_email_to_try_a_different_product(self):
        """One operator evaluating two of our systems is a good outcome."""
        _company("both.ours.test", OURS, contact_email="both@acme.com")

        self._create(product=THEIRS, subdomain="both", admin_email="both@acme.com")

        self.assertEqual(
            frappe.db.get_value("Managed Company", "both.theirs.test", "product"), THEIRS
        )


class TestTrialProgress(FrappeTestCase):
    def test_reads_capability_buffer(self):
        op = "opTOKEN12345"
        frappe.cache().set_value(
            prov._progress_key(op),
            [{"step": "done", "status": "success", "message": "done", "final_status": "SUCCESS"}],
            expires_in_sec=60,
        )
        res = tenant.get_trial_progress(operation_id=op)
        self.assertTrue(res["finished"])
        self.assertEqual(res["final_status"], "SUCCESS")

    def test_requires_operation_id(self):
        with self.assertRaises(frappe.exceptions.ValidationError):
            tenant.get_trial_progress(operation_id=None)
