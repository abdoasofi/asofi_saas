from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from asofi_saas.asofi_saas.provisioning import provision as prov
from asofi_saas.asofi_saas.public import tenant


def _make_plan(code="trial"):
    if not frappe.db.exists("SaaS Subscription Plan", code):
        frappe.get_doc(
            {"doctype": "SaaS Subscription Plan", "plan_code": code, "plan_name": code}
        ).insert()


def _configure(enable=1, suffix="", plan="trial", days=14):
    """Point SaaS Settings at a harmless, deterministic config for the tests.

    Uses the doc API (not set_single_value) so the Password field round-trips
    through encryption and ``get_password`` can read it back within the test txn.
    """
    _make_plan(plan)  # trial_plan is a Link; the target must exist to save
    s = frappe.get_single("SaaS Settings")
    s.enable_public_trial = enable
    s.trial_plan = plan
    s.trial_days = days
    s.default_site_domain = suffix
    s.bench_path = "/tmp/bench"
    s.db_root_password = "x"
    s.save(ignore_permissions=True)


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

    def test_domain_suffix_is_leading_dot_normalized(self):
        _configure(suffix="rased.com")
        self.assertEqual(tenant._domain_suffix(), ".rased.com")
        self.assertEqual(tenant._site_name_for("acme"), "acme.rased.com")

    def test_site_url_template_prod_dev_and_fallback(self):
        _configure(suffix="rased.com")
        s = frappe.get_single("SaaS Settings")
        s.trial_site_url_template = "https://{site}"
        s.save(ignore_permissions=True)
        self.assertEqual(tenant._site_url_for("acme.rased.com"), "https://acme.rased.com")
        s.trial_site_url_template = "http://{site}:8000"
        s.save(ignore_permissions=True)
        self.assertEqual(tenant._site_url_for("acme.rased.com"), "http://acme.rased.com:8000")
        # A template missing the {site} placeholder falls back to the safe default.
        s.trial_site_url_template = "broken-no-placeholder"
        s.save(ignore_permissions=True)
        self.assertEqual(tenant._site_url_for("acme.rased.com"), "https://acme.rased.com")


class TestCheckSubdomain(FrappeTestCase):
    def test_available_when_free(self):
        _configure(suffix="")
        res = tenant.check_subdomain(subdomain="free-name-xyz")
        self.assertTrue(res["available"])
        self.assertEqual(res["site_name"], "free-name-xyz")

    def test_taken_when_company_exists(self):
        _configure(suffix="")
        _make_plan("trial")
        frappe.get_doc(
            {
                "doctype": "Managed Company",
                "company_name": "Taken Co",
                "site_name": "taken-name-xyz",
                "site_url": "https://taken-name-xyz",
                "subscription_plan": "trial",
                "subscription_status": "Active",
                "provision_status": "Active",
            }
        ).insert()
        res = tenant.check_subdomain(subdomain="taken-name-xyz")
        self.assertFalse(res["available"])

    def test_reserved_is_unavailable(self):
        _configure(suffix="")
        res = tenant.check_subdomain(subdomain="admin")
        self.assertFalse(res["available"])
        self.assertTrue(res["reason"])


class TestCreateTrialTenant(FrappeTestCase):
    def test_blocked_when_disabled(self):
        _configure(enable=0)
        with self.assertRaises(frappe.exceptions.ValidationError):
            tenant.create_trial_tenant(
                company_name="ACME",
                subdomain="acme-trial",
                admin_email="a@example.com",
                phone="777000111",
                password="secret12",
            )

    def test_happy_path_creates_trial_and_enqueues(self):
        _configure(enable=1, suffix="", plan="trial", days=14)
        _make_plan("trial")
        with patch.object(frappe, "enqueue") as enq, patch.object(frappe.db, "commit"):
            res = tenant.create_trial_tenant(
                company_name="ACME Water",
                subdomain="acme-water",
                admin_email="owner@acme.com",
                phone="777123456",
                password="secret123",
            )
        self.assertIn("operation_id", res)
        self.assertEqual(res["site_name"], "acme-water")
        doc = frappe.get_doc("Managed Company", "acme-water")
        self.assertEqual(doc.subscription_status, "Trial")
        self.assertEqual(doc.is_trial, 1)
        self.assertEqual(doc.contact_email, "owner@acme.com")
        self.assertEqual(str(doc.subscription_end), str(add_days(today(), 14)))
        self.assertTrue(enq.called)  # provisioning worker + welcome email

    def test_a_visitors_plan_choice_is_recorded_but_grants_nothing(self):
        # The pricing page sends whichever card the visitor clicked. This
        # endpoint is guest-callable, so treating that value as an entitlement
        # would let anyone self-provision the most expensive tier for free.
        _configure(enable=1, suffix="", plan="trial", days=14)
        _make_plan("trial")
        _make_plan("premium")

        with patch.object(frappe, "enqueue"), patch.object(frappe.db, "commit"):
            tenant.create_trial_tenant(
                company_name="ACME Premium",
                subdomain="acme-premium",
                admin_email="boss@acme.com",
                phone="777123456",
                password="secret123",
                requested_plan="premium",
            )

        doc = frappe.get_doc("Managed Company", "acme-premium")
        self.assertEqual(doc.subscription_plan, "trial")  # what they GET
        self.assertEqual(doc.requested_plan, "premium")  # what they ASKED FOR

    def test_an_unknown_or_inactive_plan_is_dropped_not_stored(self):
        # The value lands in a Link field, so unchecked text would either break
        # the record or park junk on it.
        _configure(enable=1, suffix="", plan="trial", days=14)
        _make_plan("trial")
        _make_plan("retired")
        frappe.db.set_value("SaaS Subscription Plan", "retired", "is_active", 0)

        self.assertIsNone(tenant._sanitize_requested_plan("no-such-plan"))
        self.assertIsNone(tenant._sanitize_requested_plan("retired"))
        self.assertIsNone(tenant._sanitize_requested_plan("  "))
        self.assertIsNone(tenant._sanitize_requested_plan(None))
        self.assertEqual(tenant._sanitize_requested_plan(" trial "), "trial")

    def test_a_signup_with_no_plan_click_still_works(self):
        _configure(enable=1, suffix="", plan="trial", days=14)
        _make_plan("trial")
        with patch.object(frappe, "enqueue"), patch.object(frappe.db, "commit"):
            tenant.create_trial_tenant(
                company_name="ACME Direct",
                subdomain="acme-direct",
                admin_email="direct@acme.com",
                phone="777123456",
                password="secret123",
            )
        doc = frappe.get_doc("Managed Company", "acme-direct")
        self.assertFalse(doc.requested_plan)
        self.assertEqual(doc.subscription_plan, "trial")

    def test_rejects_short_password(self):
        _configure(enable=1, suffix="")
        _make_plan("trial")
        with self.assertRaises(frappe.exceptions.ValidationError):
            tenant.create_trial_tenant(
                company_name="ACME",
                subdomain="acme-two",
                admin_email="a@example.com",
                phone="777",
                password="short",
            )

    def test_rejects_bad_subdomain(self):
        _configure(enable=1, suffix="")
        _make_plan("trial")
        with self.assertRaises(frappe.exceptions.ValidationError):
            tenant.create_trial_tenant(
                company_name="ACME",
                subdomain="ab",
                admin_email="a@example.com",
                phone="777",
                password="secret12",
            )

    def test_blocks_duplicate_email(self):
        _configure(enable=1, suffix="")
        _make_plan("trial")
        frappe.get_doc(
            {
                "doctype": "Managed Company",
                "company_name": "First",
                "site_name": "first-site",
                "site_url": "https://first-site",
                "subscription_plan": "trial",
                "subscription_status": "Trial",
                "provision_status": "Active",
                "contact_email": "dup@acme.com",
            }
        ).insert()
        with self.assertRaises(frappe.exceptions.ValidationError):
            tenant.create_trial_tenant(
                company_name="Second",
                subdomain="second-site",
                admin_email="dup@acme.com",
                phone="777",
                password="secret12",
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
