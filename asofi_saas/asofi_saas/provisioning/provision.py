"""Automated provisioning of a new company site.

End-to-end: ``bench new-site`` -> install apps -> set the control-plane secret in
the new site's config -> create the product's manager user -> mark the Managed
Company Active -> push its initial subscription.

Every product-specific detail — which bench, which apps, which site_config key
the secret goes into, which role the manager gets — comes from the company's
SaaS Product via `provisioning.drivers`. Nothing here knows what Rased is.

Design choices (learned from an earlier SaaS app):
- The background worker accepts **keyword** arguments, so no RQ-compat wrapper is
  needed — ``frappe.enqueue`` passes kwargs straight through.
- Live progress is buffered in **Redis** (read by the Flutter console via
  ``get_provision_progress``) and mirrored to ``publish_realtime`` for Desk — no
  temporary log files under ``sites/``.
- Command lines are **redacted** before they are logged or published, so no
  password or secret ever leaks into the progress stream.
"""

import subprocess

import frappe
from frappe import _

from asofi_saas.asofi_saas.provisioning import drivers
from asofi_saas.asofi_saas.subscription.push import push_subscription

logger = frappe.logger("asofi_saas.provision", allow_site=True)

PROGRESS_TTL = 3600  # seconds a provisioning progress buffer lives in Redis
# Tokens whose *following* value must be masked before logging/publishing.
_REDACT_AFTER = {
    "--mariadb-root-password",
    "--admin-password",
    "--password",
    "rased_control_plane_secret",
    "control_plane_secret",
    "edupulse_control_plane_secret",
}


class ProvisionError(Exception):
    pass


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------
def _settings():
    return frappe.get_single("SaaS Settings")


def _bench_path(product=None):
    """Per product. Rased on v15 and EduPulse on v16 cannot share a bench, so
    the global in SaaS Settings survives only for rows with no product yet."""
    if product:
        path = (drivers.for_product(product).cwd or "").strip()
        if path:
            return path

    p = (_settings().bench_path or "").strip()
    if not p:
        frappe.throw(_("SaaS Settings: Bench Path is not set."))
    return p


def _bench_exec():
    return (_settings().bench_executable or "bench").strip()


def _root_password():
    pw = _settings().get_password("db_root_password")
    if not pw:
        frappe.throw(_("SaaS Settings: MariaDB Root Password is not set."))
    return pw


def _apps_to_install():
    raw = _settings().apps_to_install or "utility_billing"
    return [a.strip() for a in raw.replace(",", "\n").splitlines() if a.strip()]


# ---------------------------------------------------------------------------
# Progress transport (Redis buffer + realtime), no temp files
# ---------------------------------------------------------------------------
def _progress_key(operation_id):
    return f"asofi_provision:{operation_id}"


def _redact(cmd):
    out, mask_next = [], False
    for tok in cmd:
        if mask_next:
            out.append("***")
            mask_next = False
            continue
        out.append(tok)
        if tok in _REDACT_AFTER:
            mask_next = True
    return out


def _publish(operation_id, step, status, message, user=None, final_status=None):
    event = {"operation_id": operation_id, "step": step, "status": status, "message": message}
    if final_status:
        event["final_status"] = final_status
    try:
        key = _progress_key(operation_id)
        buf = frappe.cache().get_value(key) or []
        buf.append(event)
        frappe.cache().set_value(key, buf, expires_in_sec=PROGRESS_TTL)
    except Exception:
        logger.exception("failed to buffer provision progress")
    try:
        frappe.publish_realtime(
            event="asofi_provision_progress",
            message=event,
            user=user or frappe.session.user,
        )
    except Exception:
        logger.exception("failed to publish provision progress")


def _run_streaming(cmd, cwd, operation_id, step, user):
    _publish(operation_id, step, "info", "$ " + " ".join(_redact(cmd)), user)
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    for line in iter(proc.stdout.readline, ""):
        line = line.rstrip("\n")
        if line:
            _publish(operation_id, step, "progress", line, user)
            logger.info(f"[{operation_id}] {step}: {line}")
    proc.wait()
    if proc.returncode != 0:
        raise ProvisionError(f"{step} failed (exit code {proc.returncode})")


def _run(cmd, cwd, operation_id, step, user):
    _publish(operation_id, step, "info", "$ " + " ".join(_redact(cmd)), user)
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip() or "")[:300]
        raise ProvisionError(f"{step} failed: {detail}")
    return result


# ---------------------------------------------------------------------------
# Shared core (no permission gate — callers authorize)
# ---------------------------------------------------------------------------
def _create_company_and_enqueue(
    *,
    company_name,
    site_name,
    manager_email,
    manager_password,
    subscription_plan,
    site_url=None,
    subscription_status="Trial",
    subscription_start=None,
    subscription_end=None,
    contact_person=None,
    contact_phone=None,
    contact_email=None,
    manager_first_name=None,
    manager_last_name=None,
    admin_password=None,
    is_trial=0,
    signup_ip=None,
    requested_plan=None,
):
    """Register the Managed Company and queue the provisioning worker.

    Deliberately performs **no** permission check: the admin console
    (``enqueue_provision``, System Manager) and the public trial flow
    (``public.tenant.create_trial_tenant``, guest + rate-limited) each enforce
    their own access rules, then share this creation/enqueue logic so the two
    paths never drift. Returns ``{operation_id, company}``.
    """
    if not (company_name and site_name and manager_email and manager_password and subscription_plan):
        frappe.throw(
            _(
                "company_name, site_name, manager_email, manager_password and "
                "subscription_plan are required."
            )
        )

    settings = _settings()
    if not settings.bench_path or not settings.get_password("db_root_password"):
        frappe.throw(
            _("Provisioning is not configured: set Bench Path and MariaDB Root Password in SaaS Settings.")
        )
    if not frappe.db.exists("SaaS Subscription Plan", subscription_plan):
        frappe.throw(_("Unknown plan: {0}").format(subscription_plan))
    if frappe.db.exists("Managed Company", site_name):
        frappe.throw(_("A Managed Company for site {0} already exists.").format(site_name))

    site_url = (site_url or ("https://" + site_name)).rstrip("/")
    secret = frappe.generate_hash(length=32)
    admin_password = (
        admin_password
        or settings.get_password("default_admin_password")
        or frappe.generate_hash(length=16)
    )

    doc = frappe.get_doc(
        {
            "doctype": "Managed Company",
            "company_name": company_name,
            "site_name": site_name,
            "site_url": site_url,
            "control_plane_secret": secret,
            "subscription_plan": subscription_plan,
            "subscription_status": subscription_status or "Trial",
            "subscription_start": subscription_start,
            "subscription_end": subscription_end,
            "contact_person": contact_person,
            "contact_phone": contact_phone,
            "contact_email": contact_email,
            "is_trial": 1 if is_trial else 0,
            "signup_ip": signup_ip,
            # A record of what the visitor was actually shopping for. Never
            # used to decide entitlements — see create_trial_tenant.
            "requested_plan": requested_plan,
            "provision_status": "Queued",
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()

    operation_id = frappe.generate_hash(length=12)
    frappe.enqueue(
        "asofi_saas.asofi_saas.provisioning.provision.provision_worker",
        queue="long",
        timeout=3600,
        operation_id=operation_id,
        company=doc.name,
        admin_password=admin_password,
        manager_email=manager_email,
        manager_password=manager_password,
        manager_first_name=manager_first_name,
        manager_last_name=manager_last_name,
        user=frappe.session.user,
    )
    logger.info(f"queued provision op={operation_id} site={site_name} trial={bool(is_trial)}")
    return {"operation_id": operation_id, "company": doc.name}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
@frappe.whitelist()
def enqueue_provision(**kwargs):
    """Validate inputs, register the Managed Company, and queue the worker.

    Returns ``{operation_id, company}``. The Flutter console polls
    ``get_provision_progress(operation_id)`` for live status.
    """
    frappe.only_for("System Manager")
    return _create_company_and_enqueue(
        company_name=kwargs.get("company_name"),
        site_name=kwargs.get("site_name"),
        manager_email=kwargs.get("manager_email"),
        manager_password=kwargs.get("manager_password"),
        subscription_plan=kwargs.get("subscription_plan"),
        site_url=kwargs.get("site_url"),
        subscription_status=kwargs.get("subscription_status") or "Trial",
        subscription_start=kwargs.get("subscription_start"),
        subscription_end=kwargs.get("subscription_end"),
        contact_person=kwargs.get("contact_person"),
        contact_phone=kwargs.get("contact_phone"),
        contact_email=kwargs.get("contact_email"),
        manager_first_name=kwargs.get("manager_first_name"),
        manager_last_name=kwargs.get("manager_last_name"),
        admin_password=kwargs.get("admin_password"),
    )


@frappe.whitelist()
def provision_existing(
    company,
    manager_email=None,
    manager_password=None,
    manager_first_name=None,
    manager_last_name=None,
    admin_password=None,
):
    """Provision the site for an EXISTING Managed Company record (Desk button).

    Unlike enqueue_provision (which creates a new record), this uses the record's
    own site_name / plan / dates, and only needs the manager credentials that are
    never stored. Returns ``{operation_id, company}``.
    """
    frappe.only_for("System Manager")
    doc = frappe.get_doc("Managed Company", company)

    if doc.provision_status == "Active":
        frappe.throw(_("Site {0} is already Active.").format(doc.site_name))
    if not (manager_email and manager_password):
        frappe.throw(_("Manager email and password are required."))

    settings = _settings()
    if not settings.bench_path or not settings.get_password("db_root_password"):
        frappe.throw(
            _("Provisioning is not configured: set Bench Path and MariaDB Root Password in SaaS Settings.")
        )

    # A record registered by hand may not carry a secret yet — mint one so the
    # post-creation push can authenticate against the new site.
    if not doc.control_plane_secret:
        doc.db_set("control_plane_secret", frappe.generate_hash(length=32))

    admin_password = (
        admin_password
        or settings.get_password("default_admin_password")
        or frappe.generate_hash(length=16)
    )
    operation_id = frappe.generate_hash(length=12)
    doc.db_set("provision_status", "Queued")
    frappe.db.commit()

    frappe.enqueue(
        "asofi_saas.asofi_saas.provisioning.provision.provision_worker",
        queue="long",
        timeout=3600,
        operation_id=operation_id,
        company=doc.name,
        admin_password=admin_password,
        manager_email=manager_email,
        manager_password=manager_password,
        manager_first_name=manager_first_name,
        manager_last_name=manager_last_name,
        user=frappe.session.user,
    )
    logger.info(f"queued provision (existing) op={operation_id} site={doc.site_name}")
    return {"operation_id": operation_id, "company": doc.name}


def provision_worker(
    operation_id=None,
    company=None,
    admin_password=None,
    manager_email=None,
    manager_password=None,
    manager_first_name=None,
    manager_last_name=None,
    user=None,
):
    """Background worker (queue=long). Creates the site end-to-end."""
    doc = frappe.get_doc("Managed Company", company)
    site = doc.site_name

    try:
        # Read settings inside the try so a misconfiguration (missing bench path,
        # root password, etc.) surfaces as a visible "Failed" with a message,
        # instead of leaving the company stuck on "Queued" with no trace.
        driver = drivers.for_product(doc.product)
        bench_path = driver.cwd
        root_pw = _root_password()
        secret = doc.get_password("control_plane_secret")
        apps = driver.apps()

        doc.db_set("provision_status", "Creating")
        frappe.db.commit()
        _publish(operation_id, "start", "info", f"بدء إنشاء الموقع {site}", user)

        _run_streaming(
            driver.create_site(site, root_pw, admin_password),
            bench_path,
            operation_id,
            "new-site",
            user,
        )

        for app in apps:
            _run_streaming(
                driver.install_app(site, app),
                bench_path,
                operation_id,
                f"install:{app}",
                user,
            )

        # A freshly created site has the setup wizard incomplete, which traps the
        # (non-System-Manager) manager on /app/setup-wizard with a "Not permitted"
        # error on first web login. Mark setup complete so the site is usable
        # immediately; the manager's day-to-day interface is the Rased mobile app.
        _run(
            driver.finalize_setup(site),
            bench_path,
            operation_id,
            "finalize-setup",
            user,
        )

        _run(
            driver.set_secret(site, secret),
            bench_path,
            operation_id,
            "set-secret",
            user,
        )

        _run(
            driver.add_manager(
                site, manager_email, manager_password,
                manager_first_name, manager_last_name,
            ),
            bench_path,
            operation_id,
            "add-manager",
            user,
        )

        doc.reload()
        doc.db_set("provision_status", "Active")
        frappe.db.commit()

        _publish(operation_id, "push", "info", "دفع الاشتراك الأولي", user)
        push_subscription(doc.name, action="Provision")

        # Optional, non-fatal: wire up nginx + SSL so the new site is reachable at
        # its domain. A failure here never fails the provision (site is Active).
        if _settings().enable_domain_ssl:
            try:
                _setup_domain_ssl(doc, operation_id, user)
            except Exception as e:
                logger.warning(f"domain/ssl setup skipped for {site}: {e}")
                _publish(operation_id, "ssl", "warning", f"تخطّي النطاق/SSL: {e}", user)

        _publish(
            operation_id, "done", "success", f"تم إنشاء الموقع {site} بنجاح", user, final_status="SUCCESS"
        )
        logger.info(f"provision {site} completed op={operation_id}")
    except Exception as e:
        frappe.db.rollback()
        logger.exception(f"provision {site} failed op={operation_id}")
        frappe.db.set_value("Managed Company", company, "provision_status", "Failed")
        frappe.db.set_value("Managed Company", company, "last_push_error", str(e)[:140])
        frappe.db.commit()
        _publish(operation_id, "error", "error", f"فشل الإنشاء: {e}", user, final_status="ERROR")


def _read_progress(operation_id, since=0):
    """Read the Redis-buffered progress events for an operation since `since`.

    No permission gate — the guest trial page reads the same buffer via the
    unguessable operation_id (capability token). Admin callers wrap this in
    ``get_provision_progress`` with a System Manager check.
    """
    buf = frappe.cache().get_value(_progress_key(operation_id)) or []
    since = int(since or 0)
    finished = any(e.get("final_status") for e in buf)
    final = next((e.get("final_status") for e in buf if e.get("final_status")), None)
    return {
        "events": buf[since:],
        "next": len(buf),
        "finished": finished,
        "final_status": final,
    }


@frappe.whitelist()
def get_provision_progress(operation_id, since=0):
    """Return progress events buffered since index `since` (polling endpoint)."""
    frappe.only_for("System Manager")
    return _read_progress(operation_id, since)


# ---------------------------------------------------------------------------
# Domain + SSL automation (production only, optional, non-fatal)
# ---------------------------------------------------------------------------
def _setup_domain_ssl(doc, operation_id, user):
    """Regenerate nginx, reload it, and issue a Let's Encrypt certificate.

    Production-only and non-fatal: the site is already Active, so any failure here
    (no nginx, no sudo, DNS not pointed yet) is surfaced as a warning, not a
    provisioning failure. Requires nginx + certbot on the host and passwordless
    sudo for the bench user (`bench setup sudoers <user>`).
    """
    driver = drivers.for_product(doc.product)
    bench_path = driver.cwd
    bench = driver.executable
    site = doc.site_name
    custom_domain = (doc.get("custom_domain") or "").strip()
    target = custom_domain or site

    _run([bench, "setup", "nginx", "--yes"], bench_path, operation_id, "nginx", user)
    _run([bench, "setup", "reload-nginx"], bench_path, operation_id, "reload-nginx", user)

    le_cmd = ["sudo", "-n", "-H", bench, "setup", "lets-encrypt", site]
    if custom_domain:
        le_cmd += ["--custom-domain", custom_domain]
    le_cmd += ["-n"]  # non-interactive certbot + nginx restart
    _publish(operation_id, "ssl", "info", f"إصدار شهادة SSL لـ {target}", user)
    _run(le_cmd, bench_path, operation_id, "ssl", user)


@frappe.whitelist()
def enqueue_domain_ssl(company):
    """Queue domain + SSL setup for an already-Active company (Desk/Flutter)."""
    frappe.only_for("System Manager")
    doc = frappe.get_doc("Managed Company", company)
    if doc.provision_status != "Active":
        frappe.throw(_("The site must be Active before setting up domain/SSL."))
    operation_id = frappe.generate_hash(length=12)
    frappe.enqueue(
        "asofi_saas.asofi_saas.provisioning.provision.domain_ssl_worker",
        queue="long",
        timeout=1800,
        operation_id=operation_id,
        company=doc.name,
        user=frappe.session.user,
    )
    logger.info(f"queued domain/ssl op={operation_id} site={doc.site_name}")
    return {"operation_id": operation_id, "company": doc.name}


def domain_ssl_worker(operation_id=None, company=None, user=None):
    doc = frappe.get_doc("Managed Company", company)
    try:
        _publish(operation_id, "start", "info", f"إعداد النطاق وSSL لـ {doc.site_name}", user)
        _setup_domain_ssl(doc, operation_id, user)
        _publish(
            operation_id, "done", "success", "تم إعداد النطاق وSSL بنجاح", user, final_status="SUCCESS"
        )
        logger.info(f"domain/ssl completed op={operation_id} site={doc.site_name}")
    except Exception as e:
        logger.exception(f"domain/ssl worker failed for {doc.site_name}")
        _publish(operation_id, "error", "error", f"فشل إعداد النطاق/SSL: {e}", user, final_status="ERROR")
