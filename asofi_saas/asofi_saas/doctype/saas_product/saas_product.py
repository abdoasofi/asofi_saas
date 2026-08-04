"""A product this console sells — Rased, EduPulse, whatever comes next.

The console's machinery (provisioning, subscription push, usage pull, domain and
SSL, trials) was always product-neutral. What was hard-coded was the *vocabulary*:
`max_collectors`, `allow_meter_ocr`, `usage_zones` — words that mean nothing to a
school. This DocType holds the vocabulary and the addresses, so one console can
serve products that share no concepts at all.

The one thing that is NOT negotiable: products on different Frappe major versions
cannot share a bench. Rased is on v15 and EduPulse on v16, so `bench_path` lives
here and there is no global fallback worth having.
"""

import frappe
from frappe import _
from frappe.model.document import Document

LIMIT = "Limit"
FEATURE = "Feature"
USAGE = "Usage"


class SaaSProduct(Document):
	def validate(self):
		self.product_code = (self.product_code or "").strip()
		self.normalise_paths()
		self.validate_metrics()

	def normalise_paths(self):
		for field in ("apply_path", "usage_path"):
			value = (self.get(field) or "").strip()
			if value and not value.startswith("/"):
				value = "/" + value
			self.set(field, value)

		self.bench_path = (self.bench_path or "").strip().rstrip("/")
		self.secret_config_key = (self.secret_config_key or "").strip()

	def validate_metrics(self):
		"""A duplicate key would make the wire payload ambiguous — last row wins
		silently, and a plan would push a limit the operator never set."""
		seen = set()

		for row in self.metrics or []:
			row.metric_key = (row.metric_key or "").strip()

			if not row.metric_key:
				frappe.throw(_("Row {0}: metric key is required.").format(row.idx))

			if row.metric_key in seen:
				frappe.throw(
					_("Row {0}: duplicate metric key {1}.").format(row.idx, row.metric_key)
				)

			seen.add(row.metric_key)

	def keys_of(self, kind):
		return [r.metric_key for r in self.metrics or [] if r.metric_kind == kind]

	def label_for(self, metric_key):
		for row in self.metrics or []:
			if row.metric_key == metric_key:
				return row.label_ar or metric_key

		return metric_key

	def catalogue(self):
		"""The vocabulary, ready for the console to render forms and tiles from."""
		return [
			{
				"key": row.metric_key,
				"label_ar": row.label_ar,
				"kind": row.metric_kind,
				"icon": row.icon,
				"unit_ar": row.unit_ar,
			}
			for row in self.metrics or []
		]


def get(product):
	"""Resolve a product reference to its document.

	Raises rather than defaulting: a company whose product is unknown must not
	be silently provisioned onto whatever bench happens to be configured.
	"""
	if not product:
		frappe.throw(_("No product set. Every company and plan belongs to one."))

	if hasattr(product, "doctype"):
		return product

	return frappe.get_cached_doc("SaaS Product", product)


def for_company(company):
	doc = company if hasattr(company, "doctype") else frappe.get_doc("Managed Company", company)
	return get(doc.product)
