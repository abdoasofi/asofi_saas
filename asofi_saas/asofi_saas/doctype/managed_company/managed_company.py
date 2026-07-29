import frappe
from frappe.model.document import Document

# Subscription fields whose change should be pushed to the company site.
PUSH_FIELDS = (
    "subscription_plan",
    "subscription_status",
    "subscription_start",
    "subscription_end",
    "company_name",
)


class ManagedCompany(Document):
    def validate(self):
        self.normalize_site_url()

    def on_update(self):
        # Auto-push subscription changes to the company site, in the background so
        # the save stays snappy. Skip while the site is still being provisioned
        # (no reachable site / secret yet) — provisioning does its own initial push.
        if self.provision_status != "Active":
            return
        if not self.control_plane_secret:
            return
        if not self._has_subscription_change():
            return
        frappe.enqueue(
            "asofi_saas.asofi_saas.subscription.push.push_subscription",
            queue="short",
            enqueue_after_commit=True,
            company=self.name,
            action="Push Subscription",
        )

    def _has_subscription_change(self):
        # On a fresh insert `_doc_before_save` is None and has_value_changed() is
        # True, so a newly-activated company pushes once.
        return any(self.has_value_changed(f) for f in PUSH_FIELDS)

    def normalize_site_url(self):
        # site_url is used verbatim to build the push endpoint; make sure it has a
        # scheme and no trailing slash so callers never have to second-guess it.
        url = (self.site_url or "").strip()
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        self.site_url = url.rstrip("/")
