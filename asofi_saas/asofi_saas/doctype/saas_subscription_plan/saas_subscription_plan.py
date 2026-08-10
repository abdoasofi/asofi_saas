import frappe
from frappe import _
from frappe.model.document import Document

from asofi_saas.asofi_saas.doctype.saas_product import saas_product

#: The one product whose plans still carry typed columns. Scoping the fallback
#: matters: without it a school's plan travels with `max_collectors` and
#: `allow_meter_ocr` attached — harmless, since a tenant ignores keys it does
#: not know, but it puts a utility's vocabulary inside a school's contract.
LEGACY_PRODUCT = "rased"

#: Kept so Rased tenants provisioned before the metric catalogue keep receiving
#: exactly the payload they were built to parse. Delete this list — and the
#: matching columns — once every Rased plan has been moved onto the rows.
LEGACY_RASED_FIELDS = (
    "max_collectors",
    "max_zones",
    "max_beneficiaries",
    "max_branches",
    "max_employees",
    "max_ai_tokens",
    "allow_photo_capture",
    "allow_reports_export",
    "allow_branches",
    "allow_website",
    "allow_hr",
    "allow_incidents",
    "allow_tracking",
    "allow_messaging",
    "allow_ai_analytics",
    "allow_meter_ocr",
)


class SaaSSubscriptionPlan(Document):
    def validate(self):
        # Plan code is the key the control plane pushes to each site, where it is
        # matched against the tenant's own plan record. Keep it clean and stable.
        if self.plan_code:
            self.plan_code = self.plan_code.strip()

        self.validate_rows()

    def validate_rows(self):
        """Rows must name metrics the product actually declares.

        A typo'd key is the worst failure available here: it saves, it pushes,
        and the tenant ignores an unknown key — so the operator sees a limit set
        in the console that has no effect whatsoever on the site.
        """
        if not self.product:
            return

        product = saas_product.get(self.product)

        self._check(product, self.limits, saas_product.LIMIT, "Limits")
        self._check(product, self.features, saas_product.FEATURE, "Features")

    def _check(self, product, rows, kind, label):
        allowed = set(product.keys_of(kind))
        seen = set()

        for row in rows or []:
            row.metric_key = (row.metric_key or "").strip()

            if row.metric_key not in allowed:
                frappe.throw(
                    _("{0} row {1}: {2} is not a {3} metric of product {4}.").format(
                        label, row.idx, row.metric_key, kind.lower(), product.name
                    )
                )

            if row.metric_key in seen:
                frappe.throw(
                    _("{0} row {1}: duplicate key {2}.").format(
                        label, row.idx, row.metric_key
                    )
                )

            seen.add(row.metric_key)
            # Denormalised so the grid reads without opening the product.
            row.label_ar = product.label_for(row.metric_key)

    def definition(self):
        """The plan as this console defines it, ready to travel to the tenant.

        Generic rows win. The legacy Rased columns are a fallback so a plan that
        predates the table keeps pushing what it pushed yesterday — nothing on a
        live tenant changes until someone actually fills the rows in.
        """
        out = {
            "plan_code": self.plan_code or self.name,
            "plan_name": self.plan_name,
            "is_active": self.is_active,
            "monthly_price": self.monthly_price,
            "description": self.description,
        }

        for row in self.limits or []:
            out[row.metric_key] = row.value or 0

        for row in self.features or []:
            out[row.metric_key] = 1 if row.enabled else 0

        # Every feature the product sells, including the ones this plan does
        # not. A receiving site reads an unknown key as *allowed* — deliberately,
        # so a self-hosted school never loses functionality to a plan that
        # predates it — which means silence grants the module. A plan has to say
        # no out loud, and expecting an operator to add a disabled row for every
        # module they are withholding is expecting them to remember something
        # nothing reminds them of.
        for key in self._catalogue_features():
            out.setdefault(key, 0)

        if self.product == LEGACY_PRODUCT:
            for field in LEGACY_RASED_FIELDS:
                if field not in out:
                    value = self.get(field)
                    out[field] = value if value is not None else 0

        return out

    def _catalogue_features(self):
        """Feature keys this plan's product sells, or none if it has no product.

        A plan with no product predates the catalogue; it keeps travelling with
        exactly the keys it carried yesterday rather than being handed a
        vocabulary nobody chose for it.
        """
        if not self.product:
            return []

        return saas_product.get(self.product).keys_of(saas_product.FEATURE)
