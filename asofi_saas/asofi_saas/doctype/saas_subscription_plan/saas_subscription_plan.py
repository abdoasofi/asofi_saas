import frappe
from frappe.model.document import Document


class SaaSSubscriptionPlan(Document):
    def validate(self):
        # Plan code is the key the control plane pushes to each site, where it is
        # matched against a Rased Subscription Plan name. Keep it clean and stable.
        if self.plan_code:
            self.plan_code = self.plan_code.strip()
