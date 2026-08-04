"""Where a tenant physically lives.

Today every tenant is a site on a bench, one bench per product — Rased on
Frappe v15, EduPulse on v16, which is not a preference but a hard constraint:
two Frappe majors cannot share a bench.

Tomorrow some tenants may want a container instead — for contractual isolation,
or to hold one school on an older app version through a term while everyone else
upgrades. That change is expensive to retrofit and cheap to prepare for, so the
provisioning steps talk to a driver rather than to `bench` directly.

`BenchDriver` deliberately holds no logic of its own. It answers "what command,
run where" and the worker still runs and streams them, so introducing the seam
changed no behaviour on any live tenant.
"""

import frappe
from frappe import _

from asofi_saas.asofi_saas.doctype.saas_product import saas_product


class BenchDriver:
    """One site per tenant on the product's own bench."""

    name = "bench"

    def __init__(self, product):
        self.product = saas_product.get(product)

        if not self.product.bench_path:
            frappe.throw(
                _("Product {0} has no Bench Path set.").format(self.product.name)
            )

    @property
    def cwd(self):
        return self.product.bench_path

    @property
    def executable(self):
        return (self.product.bench_executable or "bench").strip()

    def apps(self):
        """Install order matters — edupulse_core requires lms to exist first."""
        raw = self.product.apps_to_install or ""
        return [a.strip() for a in raw.replace(",", "\n").splitlines() if a.strip()]

    # -- commands ----------------------------------------------------------
    def create_site(self, site, root_password, admin_password):
        return [
            self.executable, "new-site", site,
            "--mariadb-root-password", root_password,
            "--admin-password", admin_password,
        ]

    def install_app(self, site, app):
        return [self.executable, "--site", site, "install-app", app]

    def finalize_setup(self, site):
        # A fresh site has the setup wizard incomplete, which traps a
        # non-System-Manager on /app/setup-wizard with "Not permitted" at first
        # web login. The manager's real interface is the mobile app anyway.
        return [
            self.executable, "--site", site, "execute", "frappe.db.set_single_value",
            "--kwargs",
            "{'doctype': 'System Settings', 'fieldname': 'setup_complete', 'value': 1}",
        ]

    def set_secret(self, site, secret):
        return [
            self.executable, "--site", site, "set-config",
            self.secret_config_key, secret,
        ]

    def add_manager(self, site, email, password, first_name, last_name):
        cmd = [
            self.executable, "--site", site, "add-user", email,
            "--first-name", (first_name or "Manager"),
            "--last-name", (last_name or ""),
            "--password", password,
            "--user-type", "System User",
        ]

        if self.product.manager_role:
            cmd += ["--add-role", self.product.manager_role]

        return cmd

    @property
    def secret_config_key(self):
        return (self.product.secret_config_key or "control_plane_secret").strip()


#: Registry so a second driver is a dict entry, not a scattered `if`.
DRIVERS = {BenchDriver.name: BenchDriver}


def for_product(product):
    """The driver a product's tenants are created with."""
    doc = saas_product.get(product)
    driver = DRIVERS.get(getattr(doc, "driver", None) or BenchDriver.name)

    if not driver:
        frappe.throw(_("Unknown provisioning driver for product {0}.").format(doc.name))

    return driver(doc)
