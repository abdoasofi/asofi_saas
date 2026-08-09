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
        """Hand over a site whose setup wizard is already behind it.

        A manager arriving at the wizard is met by "ليس لديك الأذونات الكافية"
        — they are a System User holding the product's manager role and not
        System Manager, so they may not run it, and there is no way past it.
        That is the first screen after signing up.

        Writing `System Settings.setup_complete = 1` does not prevent it.
        Frappe **derives** that answer rather than reading the field:
        `frappe.is_setup_complete()` asks whether every row for frappe and
        erpnext in the `Installed Application` child table is flagged, and the
        System Settings field is written *from* that, not consulted. So the
        boot kept reporting `setup_complete: 0` next to a field saying 1.

        `enable_setup_wizard_complete` is how the wizard itself records
        completion, and it exists on both v15 and v16 — so this stays true for
        whichever Frappe a product's bench runs.

        The last write is separate and equally required: the Desk lands on
        whatever the `desktop:home_page` default says, and `bench new-site`
        puts "setup-wizard" there (frappe/utils/install.py). Nothing but
        finishing the wizard moves it.
        """
        def execute(method, kwargs):
            return [self.executable, "--site", site, "execute", method, "--kwargs", kwargs]

        wizard = "frappe.desk.page.setup_wizard.setup_wizard.enable_setup_wizard_complete"

        return [
            # The two apps `is_setup_complete()` actually inspects. A product
            # whose bench has no erpnext simply matches no row.
            execute(wizard, "{'app_name': 'frappe'}"),
            execute(wizard, "{'app_name': 'erpnext'}"),
            # Mirrored onto the field for code that still reads it directly.
            execute(
                "frappe.db.set_single_value",
                "{'doctype': 'System Settings', 'fieldname': 'setup_complete', 'value': 1}",
            ),
            execute(
                "frappe.db.set_default",
                "{'key': 'desktop:home_page', 'val': 'workspace'}",
            ),
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
