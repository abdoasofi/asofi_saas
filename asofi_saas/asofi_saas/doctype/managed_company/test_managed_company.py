import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from asofi_saas import api
from asofi_saas.asofi_saas.provisioning import provision as prov
from asofi_saas.asofi_saas.subscription import push as push_mod
from asofi_saas.asofi_saas.sync import usage as usage_mod

# Every company and plan belongs to a product now — the console serves
# more than Rased. These fixtures pin the one they were written for.
TEST_PRODUCT_FIXTURE = "rased"


def _make_plan(code="standard"):
    if not frappe.db.exists("SaaS Subscription Plan", code):
        frappe.get_doc(
            {"doctype": "SaaS Subscription Plan", "plan_code": code, "plan_name": code,
             "product": TEST_PRODUCT_FIXTURE}
        ).insert()


def _make_company(site, provision="Draft", status="Active", plan="standard", **kw):
    _make_plan(plan)
    return frappe.get_doc(
        {
            "doctype": "Managed Company",
            "product": TEST_PRODUCT_FIXTURE,
            "company_name": kw.get("company_name", "Test Co"),
            "site_name": site,
            "site_url": kw.get("site_url", f"http://{site}"),
            "control_plane_secret": kw.get("secret", "s3cr3t"),
            "subscription_plan": plan,
            "subscription_status": status,
            "provision_status": provision,
            "subscription_end": kw.get("subscription_end"),
        }
    ).insert()


class TestManagedCompany(FrappeTestCase):
    def test_normalize_site_url_adds_scheme_and_strips_slash(self):
        doc = _make_company("norm.example", site_url="norm.example/")
        self.assertEqual(doc.site_url, "https://norm.example")

    def test_on_update_skips_push_when_not_active(self):
        doc = _make_company("draft.example", provision="Draft")
        with patch.object(frappe, "enqueue") as enq:
            doc.subscription_status = "Trial"
            doc.save()
            enq.assert_not_called()

    def test_on_update_enqueues_push_when_active_and_changed(self):
        doc = _make_company("active.example", provision="Active", status="Active")
        with patch.object(frappe, "enqueue") as enq:
            doc.subscription_status = "Suspended"
            doc.save()
            self.assertTrue(enq.called)


class TestSubscriptionPush(FrappeTestCase):
    def test_extract_error_parses_server_messages(self):
        resp = MagicMock(status_code=417)
        resp.json.return_value = {
            "_server_messages": json.dumps([json.dumps({"message": "limit reached"})])
        }
        self.assertEqual(push_mod._extract_error(resp), "limit reached")

    def test_extract_error_fallback_non_json(self):
        resp = MagicMock(status_code=500)
        resp.json.side_effect = ValueError()
        self.assertEqual(push_mod._extract_error(resp), "HTTP 500")

    def test_push_success_records_log_and_status(self):
        doc = _make_company("push-ok.example", provision="Draft")
        fake = MagicMock(status_code=200, text="{}", ok=True)
        with patch.object(push_mod.requests, "post", return_value=fake) as post:
            res = push_mod.push_subscription(doc.name, action="Push Subscription")
        self.assertTrue(res["ok"])
        self.assertEqual(res["http_status"], 200)
        self.assertEqual(
            frappe.db.get_value("Managed Company", doc.name, "last_push_status"), "Success"
        )
        self.assertTrue(
            frappe.db.exists("Subscription Push Log", {"company": doc.name, "status": "Success"})
        )
        post.assert_called_once()

    def test_push_failure_captures_error(self):
        doc = _make_company("push-fail.example", provision="Draft")
        fake = MagicMock(status_code=403, ok=False, text="{}")
        fake.json.return_value = {"exception": "PermissionError"}
        with patch.object(push_mod.requests, "post", return_value=fake):
            res = push_mod.push_subscription(doc.name)
        self.assertFalse(res["ok"])
        self.assertEqual(
            frappe.db.get_value("Managed Company", doc.name, "last_push_status"), "Failed"
        )

    def test_push_missing_secret_raises(self):
        doc = _make_company("nosecret.example", provision="Draft", secret="")
        with self.assertRaises(frappe.exceptions.ValidationError):
            push_mod.push_subscription(doc.name)


class TestConsoleApi(FrappeTestCase):
    def test_list_and_get_company_hides_secret(self):
        doc = _make_company("api-list.example", provision="Active")
        names = [r["name"] for r in api.list_companies()]
        self.assertIn(doc.name, names)
        got = api.get_company(doc.name)
        self.assertNotIn("control_plane_secret", got)
        self.assertIn("recent_logs", got)

    def test_create_and_update_company(self):
        _make_plan("basic")
        res = api.create_company(
            company_name="ACME",
            site_name="api-create.example",
            site_url="http://api-create.example",
            control_plane_secret="x",
            subscription_plan="basic",
        )
        self.assertEqual(res["name"], "api-create.example")
        api.update_company("api-create.example", subscription_status="Suspended")
        self.assertEqual(
            frappe.db.get_value("Managed Company", "api-create.example", "subscription_status"),
            "Suspended",
        )

    def test_dashboard_has_real_shape(self):
        _make_company("api-dash.example", provision="Active", status="Active", plan="standard")
        d = api.get_dashboard()
        self.assertIn("total_companies", d)
        self.assertIn("by_status", d)
        self.assertIsInstance(d["estimated_mrr"], float)


class TestDomainSsl(FrappeTestCase):
    def _configure_bench(self):
        frappe.db.set_single_value("SaaS Settings", "bench_path", "/tmp/bench")

    def test_setup_domain_ssl_builds_commands(self):
        doc = _make_company("ssl.example", provision="Active")
        self._configure_bench()
        calls = []
        with patch.object(prov, "_run", side_effect=lambda cmd, *a, **k: calls.append(cmd)), patch.object(
            prov, "_publish"
        ):
            prov._setup_domain_ssl(doc, "op1", "Administrator")
        self.assertTrue(any(c[1:3] == ["setup", "nginx"] for c in calls))
        self.assertTrue(any("reload-nginx" in c for c in calls))
        le = next(c for c in calls if "lets-encrypt" in c)
        self.assertIn("ssl.example", le)
        self.assertNotIn("--custom-domain", le)

    def test_setup_domain_ssl_uses_custom_domain(self):
        doc = _make_company("ssl2.example", provision="Active")
        doc.db_set("custom_domain", "vanity.example")
        self._configure_bench()
        calls = []
        with patch.object(prov, "_run", side_effect=lambda cmd, *a, **k: calls.append(cmd)), patch.object(
            prov, "_publish"
        ):
            prov._setup_domain_ssl(
                frappe.get_doc("Managed Company", doc.name), "op", "Administrator"
            )
        le = next(c for c in calls if "lets-encrypt" in c)
        self.assertIn("--custom-domain", le)
        self.assertIn("vanity.example", le)

    def test_enqueue_domain_ssl_requires_active(self):
        doc = _make_company("ssl3.example", provision="Draft")
        with self.assertRaises(frappe.exceptions.ValidationError):
            prov.enqueue_domain_ssl(doc.name)

    def test_enqueue_domain_ssl_enqueues_when_active(self):
        doc = _make_company("ssl4.example", provision="Active")
        with patch.object(frappe, "enqueue") as enq:
            res = prov.enqueue_domain_ssl(doc.name)
        self.assertTrue(enq.called)
        self.assertIn("operation_id", res)


class TestUsageSync(FrappeTestCase):
    def test_pull_usage_success_stores_snapshot(self):
        doc = _make_company("usage-ok.example", provision="Active")
        payload = {
            "message": {
                "collectors_active": 4,
                "zones": 6,
                "beneficiaries": {
                    "active": 100,
                    "inactive": 5,
                    "suspended": 2,
                    "total": 107,
                },
                "last_reading_date": "2026-07-20",
            }
        }
        fake = MagicMock(status_code=200, ok=True, content=b"{}")
        fake.json.return_value = payload
        with patch.object(usage_mod.requests, "post", return_value=fake):
            res = usage_mod.pull_usage(doc.name)
        self.assertTrue(res["ok"])
        self.assertEqual(
            frappe.db.get_value("Managed Company", doc.name, "usage_collectors"), 4
        )
        self.assertEqual(
            frappe.db.get_value("Managed Company", doc.name, "usage_beneficiaries"), 107
        )
        self.assertEqual(
            frappe.db.get_value("Managed Company", doc.name, "usage_beneficiaries_active"),
            100,
        )

    def test_pull_usage_failure_records_error(self):
        doc = _make_company("usage-fail.example", provision="Active")
        fake = MagicMock(status_code=403, ok=False, content=b"{}")
        fake.json.return_value = {"exception": "PermissionError"}
        with patch.object(usage_mod.requests, "post", return_value=fake):
            res = usage_mod.pull_usage(doc.name)
        self.assertFalse(res["ok"])
        self.assertTrue(
            frappe.db.get_value("Managed Company", doc.name, "usage_error")
        )
