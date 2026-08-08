"""Public, guest-facing self-service: free-trial site provisioning.

These endpoints are exposed to anonymous website visitors (the /asofisaas landing
page), so every one is hardened before any real site is created:

- an explicit opt-in gate in SaaS Settings (``enable_public_trial``),
- strict input validation (subdomain shape, email, password length),
- per-IP rate limiting on both the availability probe and the create call,
- a one-live-trial-per-email rule.

The heavy lifting reuses the existing provisioning worker (``provisioning.provision``)
and its Redis progress buffer — nothing is duplicated. Provisioning progress is read
back by the browser through ``get_trial_progress`` using the unguessable
``operation_id`` as a capability token (no guest realtime required).
"""

import os
import re

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, today, validate_email_address

from asofi_saas.asofi_saas.provisioning import provision as prov

logger = frappe.logger("asofi_saas.public_tenant", allow_site=True)

#: The product this self-service flow provisions.
#:
#: Every setting it reads — bench path, domain suffix, apps, trial plan — still
#: comes from SaaS Settings, which holds Rased's values. So this flow creates a
#: Rased site and nothing else, whatever the visitor believed they signed up
#: for. Naming that here keeps the limit visible instead of implied, and marks
#: the seam where a `product` argument belongs once the flow is generalised.
TRIAL_PRODUCT = "rased"

# Subdomains we never hand out (infrastructure / brand / obvious confusables).
RESERVED_SUBDOMAINS = {
    "www", "api", "app", "admin", "administrator", "saas", "asofi", "asofisaas",
    "rased", "mail", "smtp", "imap", "pop", "ftp", "ns", "ns1", "ns2", "dns",
    "portal", "dashboard", "console", "billing", "pay", "payment", "status",
    "support", "help", "docs", "cdn", "static", "assets", "test", "staging",
    "dev", "demo", "trial", "signup", "login", "root", "system", "site", "sites",
}
# 3–30 chars, lowercase letters/digits/hyphen, not starting or ending with a hyphen.
_SUBDOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$")
_MIN_PASSWORD = 8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _settings():
    return frappe.get_single("SaaS Settings")


def _domain_suffix(settings=None):
    """The site-name suffix (e.g. ``.rased.com``), always leading-dot normalized."""
    settings = settings or _settings()
    suffix = (settings.default_site_domain or "").strip().lower()
    if suffix and not suffix.startswith("."):
        suffix = "." + suffix
    return suffix


def _normalize_subdomain(subdomain):
    return (subdomain or "").strip().lower()


def _subdomain_error(subdomain):
    """Human message if the subdomain is syntactically unusable, else None."""
    if not subdomain:
        return _("يرجى إدخال اسم النطاق الفرعي.")
    if not _SUBDOMAIN_RE.match(subdomain):
        return _(
            "استخدم ٣ إلى ٣٠ حرفًا بالإنجليزية الصغيرة والأرقام والشرطات فقط — "
            "بدون نقاط أو امتداد، ودون البدء أو الانتهاء بشرطة."
        )
    if subdomain in RESERVED_SUBDOMAINS:
        return _("هذا الاسم محجوز. يرجى اختيار اسم آخر.")
    return None


def _site_name_for(subdomain, settings=None):
    return subdomain + _domain_suffix(settings)


def _site_dir_exists(site_name, settings=None):
    """True if a site directory already exists on the bench (belt-and-braces)."""
    settings = settings or _settings()
    bench_path = (settings.bench_path or "").strip()
    if not bench_path:
        return False
    return os.path.isdir(os.path.join(bench_path, "sites", site_name))


def _site_url_for(site_name, settings=None):
    """Build a trial site's reachable URL from the configurable template.

    Production uses ``https://{site}``; a local bench serving on a port uses
    ``http://{site}:8000`` so the initial subscription push actually reaches the
    new site instead of failing on a non-existent HTTPS endpoint.
    """
    settings = settings or _settings()
    tpl = (settings.trial_site_url_template or "").strip() or "https://{site}"
    if "{site}" not in tpl:
        tpl = "https://{site}"
    return tpl.format(site=site_name).rstrip("/")


def _availability(subdomain, settings=None):
    """Return (available: bool, reason: str|None, site_name, full_url)."""
    settings = settings or _settings()
    err = _subdomain_error(subdomain)
    if err:
        return False, err, None, None
    site_name = _site_name_for(subdomain, settings)
    if frappe.db.exists("Managed Company", site_name) or _site_dir_exists(site_name, settings):
        return False, _("هذا الاسم محجوز مسبقًا."), site_name, None
    return True, None, site_name, _site_url_for(site_name, settings)


# ---------------------------------------------------------------------------
# Endpoints (guest)
# ---------------------------------------------------------------------------
@frappe.whitelist(allow_guest=True)
@rate_limit(key="subdomain", limit=30, seconds=60)
def check_subdomain(subdomain=None):
    """Live availability probe for the onboarding form (AJAX)."""
    subdomain = _normalize_subdomain(subdomain)
    available, reason, site_name, full_url = _availability(subdomain)
    return {
        "subdomain": subdomain,
        "available": available,
        "reason": reason,
        "site_name": site_name,
        "full_url": full_url,
    }


def _assert_public_trial_enabled(settings):
    if not settings.enable_public_trial:
        frappe.throw(_("التسجيل الذاتي غير مُفعّل حاليًا. يرجى التواصل معنا."))


def _assert_email_free(email):
    """One live trial per email — block a second while one is still Trial/Active."""
    if frappe.get_all(
        "Managed Company",
        filters={"contact_email": email, "subscription_status": ["in", ["Trial", "Active"]]},
        limit=1,
    ):
        frappe.throw(_("توجد تجربة قائمة بالفعل لهذا البريد الإلكتروني."))


def _sanitize_requested_plan(plan_code):
    """Which advertised plan the visitor clicked, or None.

    Anything that is not a currently ACTIVE public plan is dropped rather than
    stored: the value arrives from an anonymous form post and ends up in a Link
    field, so an unchecked string would either break the record or park
    arbitrary text on it.

    The product check is not a duplicate of the storefront's filter. Hiding a
    plan from the page does not stop it being posted here, and this flow
    provisions a Rased site — a lead carrying another product's plan describes
    something the tenant it creates can never deliver.
    """
    plan_code = (plan_code or "").strip()
    if not plan_code:
        return None

    if not frappe.db.exists(
        "SaaS Subscription Plan",
        {"name": plan_code, "is_active": 1, "product": TRIAL_PRODUCT},
    ):
        return None

    return plan_code


@frappe.whitelist(allow_guest=True)
@rate_limit(key="admin_email", limit=5, seconds=3600)
def create_trial_tenant(
    company_name=None,
    subdomain=None,
    admin_email=None,
    phone=None,
    password=None,
    requested_plan=None,
):
    """Provision a free-trial site for a website visitor.

    Returns ``{operation_id, company, site_name, site_url}``. The onboarding page
    polls ``get_trial_progress(operation_id)`` for live status.
    """
    settings = _settings()
    _assert_public_trial_enabled(settings)

    company_name = (company_name or "").strip()
    subdomain = _normalize_subdomain(subdomain)
    admin_email = (admin_email or "").strip().lower()
    phone = (phone or "").strip()
    password = password or ""

    if not company_name:
        frappe.throw(_("يرجى إدخال اسم الشركة."))
    if not validate_email_address(admin_email):
        frappe.throw(_("يرجى إدخال بريد إلكتروني صحيح."))
    if not phone:
        frappe.throw(_("يرجى إدخال رقم الجوال."))
    if len(password) < _MIN_PASSWORD:
        frappe.throw(_("كلمة المرور يجب ألا تقل عن {0} أحرف.").format(_MIN_PASSWORD))

    available, reason, site_name, site_url = _availability(subdomain, settings)
    if not available:
        frappe.throw(reason)

    _assert_email_free(admin_email)

    # The plan actually granted is ALWAYS the configured trial plan. The
    # visitor's pick is recorded separately and deliberately never feeds this
    # line: this endpoint is guest-callable, so treating a client-supplied plan
    # as an entitlement would let anyone self-provision the premium tier.
    plan = (settings.trial_plan or "trial").strip()
    requested_plan = _sanitize_requested_plan(requested_plan)
    days = int(settings.trial_days or 14)
    start = today()
    end = add_days(start, days)
    signup_ip = getattr(frappe.local, "request_ip", None)

    # The provisioning worker runs as the *enqueuing* user (Frappe captures
    # frappe.session.user at enqueue time and re-applies it in the job). A public
    # trial is enqueued by an anonymous visitor, so elevate to Administrator around
    # the enqueue to give the worker the privileges it needs to create the site.
    # Restored in `finally` so the HTTP response continues as the guest.
    guest_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        result = prov._create_company_and_enqueue(
            company_name=company_name,
            site_name=site_name,
            site_url=site_url,
            manager_email=admin_email,
            manager_password=password,
            subscription_plan=plan,
            subscription_status="Trial",
            subscription_start=start,
            subscription_end=end,
            contact_person=company_name,
            contact_phone=phone,
            contact_email=admin_email,
            is_trial=1,
            signup_ip=signup_ip,
            requested_plan=requested_plan,
        )
        # Welcome email is best-effort and must never fail the signup.
        frappe.enqueue(
            "asofi_saas.asofi_saas.public.tenant.send_welcome_email",
            queue="short",
            enqueue_after_commit=True,
            email=admin_email,
            company_name=company_name,
            site_url=site_url,
            trial_end=str(end),
            app_url=(settings.mobile_app_url or ""),
        )
    finally:
        frappe.set_user(guest_user)

    result["site_name"] = site_name
    result["site_url"] = site_url
    logger.info(f"trial signup site={site_name} email={admin_email} ip={signup_ip}")
    return result


@frappe.whitelist(allow_guest=True)
@rate_limit(key="operation_id", limit=240, seconds=60)
def get_trial_progress(operation_id=None, since=0):
    """Guest-safe progress reader keyed by the unguessable operation_id.

    Only the browser that started the provision knows the 12-char token, so it can
    read its own progress buffer without any guest realtime/room plumbing.
    """
    if not operation_id:
        frappe.throw(_("معرّف العملية مطلوب."))
    return prov._read_progress(operation_id, since)


# ---------------------------------------------------------------------------
# Welcome email (background, non-fatal)
# ---------------------------------------------------------------------------
def send_welcome_email(
    email=None, company_name=None, site_url=None, trial_end=None, app_url=None
):
    """Queue a welcome email with the mobile-app link, site address and trial terms.

    Runs in the background; any mail misconfiguration is logged, never surfaced to
    the visitor (their site is already being created regardless).
    """
    if not email:
        return
    login_url = (site_url or "").rstrip("/") + "/login"
    subject = _("مرحبًا بك في راصد — تم إنشاء حسابك التجريبي")
    app_line = ""
    if app_url:
        app_line = _('<li>حمّل تطبيق راصد للجوال: <a href="{0}">{0}</a></li>').format(
            frappe.utils.escape_html(app_url)
        )
    message = _(
        """
        <div dir="rtl" style="font-family:Cairo,Tahoma,sans-serif;line-height:1.9">
            <h2>مرحبًا {company}،</h2>
            <p>شكرًا لتجربتك منصّة <strong>راصد</strong> لإدارة تحصيل الخدمات.
               تم إنشاء حسابك وموقعك التجريبي بنجاح.</p>
            <p><strong>لإدارة شركتك عبر تطبيق الجوال:</strong></p>
            <ul>
                {app_line}
                <li>عنوان موقعك: <strong>{url}</strong></li>
                <li>بريد الدخول: <strong>{email}</strong> وكلمة المرور التي اخترتها</li>
            </ul>
            <p>أو الدخول عبر المتصفح: <a href="{login}">{login}</a></p>
            <p>فترتك التجريبية مجانية وتنتهي بتاريخ <strong>{end}</strong>. يمكنك الترقية في أي وقت.</p>
            <hr>
            <p style="color:#64748b;font-size:12px">هذه رسالة تلقائية من منصّة راصد.</p>
        </div>
        """
    ).format(
        company=frappe.utils.escape_html(company_name or ""),
        url=site_url or "",
        login=login_url,
        email=frappe.utils.escape_html(email),
        end=trial_end or "",
        app_line=app_line,
    )
    try:
        frappe.sendmail(recipients=[email], subject=subject, message=message)
    except Exception:
        logger.exception(f"welcome email failed for {email}")
