"""Public, guest-facing self-service: free-trial site provisioning.

These endpoints are exposed to anonymous website visitors (the storefronts under
/asofisaas), so every one is hardened before any real site is created:

- an explicit opt-in gate on the product (``enable_public_trial`` + a trial plan),
- strict input validation (subdomain shape, email, password length),
- per-IP rate limiting on both the availability probe and the create call,
- a one-live-trial-per-email rule.

Every value this flow needs — the trial switch, the plan it grants, its length,
the domain suffix, the bench the site is created on — comes from the **SaaS
Product** the visitor is signing up for. SaaS Settings holds exactly one set of
those values, so reading them here is what made this flow provision Rased no
matter which storefront the visitor filled in.

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
from frappe.utils import add_days, cint, today, validate_email_address

from asofi_saas.asofi_saas.provisioning import provision as prov
from asofi_saas.asofi_saas.public.storefront import domain_suffix as _domain_suffix

logger = frappe.logger("asofi_saas.public_tenant", allow_site=True)

# Subdomains we never hand out (infrastructure / brand / obvious confusables).
RESERVED_SUBDOMAINS = {
    "www", "api", "app", "admin", "administrator", "saas", "asofi", "asofisaas",
    "rased", "edupulse", "mail", "smtp", "imap", "pop", "ftp", "ns", "ns1", "ns2",
    "dns", "portal", "dashboard", "console", "billing", "pay", "payment", "status",
    "support", "help", "docs", "cdn", "static", "assets", "test", "staging",
    "dev", "demo", "trial", "signup", "login", "root", "system", "site", "sites",
}
# 3–30 chars, lowercase letters/digits/hyphen, not starting or ending with a hyphen.
_SUBDOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$")
_MIN_PASSWORD = 8


# ---------------------------------------------------------------------------
# Which product is being signed up for
# ---------------------------------------------------------------------------
def _product(product=None):
    """The product this signup provisions.

    A request that names no product is only unambiguous while exactly one
    product offers a trial — which is what keeps a page cached before the
    argument existed working. Once two do, guessing would hand a school a
    water-utility site, and no later message undoes that, so ask instead.
    """
    code = (product or "").strip()

    if code:
        if not frappe.db.exists("SaaS Product", code):
            frappe.throw(_("النظام المطلوب غير معروف."))

        doc = frappe.get_cached_doc("SaaS Product", code)
        if not cint(doc.is_active):
            frappe.throw(_("هذا النظام غير متاح حالياً."))

        return doc

    open_trials = frappe.get_all(
        "SaaS Product",
        filters={"is_active": 1, "enable_public_trial": 1},
        pluck="name",
    )
    if len(open_trials) == 1:
        return frappe.get_cached_doc("SaaS Product", open_trials[0])

    frappe.throw(_("يرجى تحديد النظام الذي ترغب بتجربته."))


# ---------------------------------------------------------------------------
# Helpers — all of them read the product, never SaaS Settings
# ---------------------------------------------------------------------------
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


def _site_name_for(subdomain, product):
    return subdomain + _domain_suffix(product)


def _site_dir_exists(site_name, product):
    """True if a site directory already exists on the product's bench.

    Belt-and-braces against a site that exists on disk but was never registered
    here — and it must look on the *product's* bench, since a name free on v15
    says nothing about v16.
    """
    bench_path = (product.bench_path or "").strip()
    if not bench_path:
        return False
    return os.path.isdir(os.path.join(bench_path, "sites", site_name))


def _site_url_for(site_name, product):
    """Build a trial site's reachable URL from the product's URL template.

    Production uses ``https://{site}``; a local bench serving on a port uses
    ``http://{site}:8000`` so the initial subscription push actually reaches the
    new site instead of failing on a non-existent HTTPS endpoint. The template
    is per product because two products on two benches answer on two ports.
    """
    tpl = (product.trial_site_url_template or "").strip() or "https://{site}"
    if "{site}" not in tpl:
        tpl = "https://{site}"
    return tpl.format(site=site_name).rstrip("/")


def _availability(subdomain, product):
    """Return (available: bool, reason: str|None, site_name, full_url)."""
    err = _subdomain_error(subdomain)
    if err:
        return False, err, None, None

    site_name = _site_name_for(subdomain, product)
    if frappe.db.exists("Managed Company", site_name) or _site_dir_exists(site_name, product):
        return False, _("هذا الاسم محجوز مسبقًا."), site_name, None

    return True, None, site_name, _site_url_for(site_name, product)


# ---------------------------------------------------------------------------
# Endpoints (guest)
# ---------------------------------------------------------------------------
@frappe.whitelist(allow_guest=True)
@rate_limit(key="subdomain", limit=30, seconds=60)
def check_subdomain(subdomain=None, product=None):
    """Live availability probe for the onboarding form (AJAX)."""
    doc = _product(product)
    subdomain = _normalize_subdomain(subdomain)
    available, reason, site_name, full_url = _availability(subdomain, doc)
    return {
        "subdomain": subdomain,
        "product": doc.name,
        "available": available,
        "reason": reason,
        "site_name": site_name,
        "full_url": full_url,
    }


def _assert_trial_open(product):
    """The same rule the storefront renders by: switch on AND a plan set.

    The switch alone used to be enough here, with the plan falling back to a
    literal ``"trial"``. On a second product that fallback grants *another*
    product's plan, and the site comes up entitled to things it cannot name.
    """
    if not cint(product.enable_public_trial):
        frappe.throw(_("التسجيل الذاتي غير مُفعّل لهذا النظام حالياً. يرجى التواصل معنا."))
    if not product.trial_plan:
        frappe.throw(_("لم تُضبط الخطة التجريبية لهذا النظام. يرجى التواصل معنا."))


def _trial_plan(product):
    """The plan a trial grants — always the product's own, never the visitor's.

    `SaaS Product.trial_plan` is an unfiltered Link, so an operator can pick a
    plan from a different product. Caught here as well as on save, because a
    plan's product can move after the product was configured.
    """
    plan = (product.trial_plan or "").strip()
    owner = frappe.db.get_value("SaaS Subscription Plan", plan, "product")

    if owner and owner != product.name:
        frappe.throw(_("الخطة التجريبية المضبوطة لهذا النظام تعود لنظام آخر. يرجى التواصل معنا."))

    return plan


def _assert_email_free(email, product):
    """One live trial per email, per product.

    Scoped to the product on purpose: the same operator evaluating two of our
    systems is a good outcome, not an abuse to block.
    """
    if frappe.get_all(
        "Managed Company",
        filters={
            "contact_email": email,
            "product": product.name,
            "subscription_status": ["in", ["Trial", "Active"]],
        },
        limit=1,
    ):
        frappe.throw(_("توجد تجربة قائمة بالفعل لهذا البريد الإلكتروني على هذا النظام."))


def _sanitize_requested_plan(plan_code, product):
    """Which advertised plan the visitor clicked, or None.

    Anything that is not a currently ACTIVE public plan **of this product** is
    dropped rather than stored: the value arrives from an anonymous form post
    and ends up in a Link field, so an unchecked string would either break the
    record or park arbitrary text on it.

    The product check is not a duplicate of the storefront's filter. Hiding a
    plan from the page does not stop it being posted here, and a lead carrying
    another product's plan describes something this tenant can never deliver.
    """
    plan_code = (plan_code or "").strip()
    if not plan_code:
        return None

    if not frappe.db.exists(
        "SaaS Subscription Plan",
        {"name": plan_code, "is_active": 1, "product": product.name},
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
    product=None,
):
    """Provision a free-trial site for a website visitor.

    Returns ``{operation_id, company, product, site_name, site_url}``. The
    onboarding page polls ``get_trial_progress(operation_id)`` for live status.
    """
    doc = _product(product)
    _assert_trial_open(doc)

    company_name = (company_name or "").strip()
    subdomain = _normalize_subdomain(subdomain)
    admin_email = (admin_email or "").strip().lower()
    phone = (phone or "").strip()
    password = password or ""

    if not company_name:
        frappe.throw(_("يرجى إدخال اسم الجهة."))
    if not validate_email_address(admin_email):
        frappe.throw(_("يرجى إدخال بريد إلكتروني صحيح."))
    if not phone:
        frappe.throw(_("يرجى إدخال رقم الجوال."))
    if len(password) < _MIN_PASSWORD:
        frappe.throw(_("كلمة المرور يجب ألا تقل عن {0} أحرف.").format(_MIN_PASSWORD))

    available, reason, site_name, site_url = _availability(subdomain, doc)
    if not available:
        frappe.throw(reason)

    _assert_email_free(admin_email, doc)

    # The plan actually granted is ALWAYS the product's configured trial plan.
    # The visitor's pick is recorded separately and deliberately never feeds
    # this line: this endpoint is guest-callable, so treating a client-supplied
    # plan as an entitlement would let anyone self-provision the premium tier.
    plan = _trial_plan(doc)
    requested_plan = _sanitize_requested_plan(requested_plan, doc)
    days = cint(doc.trial_days) or 14
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
            product=doc.name,
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
            product_name=doc.product_name,
            site_url=site_url,
            trial_end=str(end),
            app_url=(doc.mobile_app_url or ""),
        )
    finally:
        frappe.set_user(guest_user)

    result["product"] = doc.name
    result["site_name"] = site_name
    result["site_url"] = site_url
    logger.info(
        f"trial signup product={doc.name} site={site_name} "
        f"email={admin_email} ip={signup_ip}"
    )
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
    email=None,
    company_name=None,
    site_url=None,
    trial_end=None,
    app_url=None,
    product_name=None,
):
    """Queue a welcome email with the mobile-app link, site address and trial terms.

    Runs in the background; any mail misconfiguration is logged, never surfaced to
    the visitor (their site is already being created regardless). The product is
    named by the caller — this used to say "راصد" to everyone who signed up.
    """
    if not email:
        return

    product_name = frappe.utils.escape_html(product_name or "أسوفي")
    login_url = (site_url or "").rstrip("/") + "/login"
    subject = _("مرحبًا بك في {0} — تم إنشاء حسابك التجريبي").format(product_name)
    app_line = ""
    if app_url:
        app_line = _('<li>حمّل تطبيق {0} للجوال: <a href="{1}">{1}</a></li>').format(
            product_name, frappe.utils.escape_html(app_url)
        )
    message = _(
        """
        <div dir="rtl" style="font-family:Cairo,Tahoma,sans-serif;line-height:1.9">
            <h2>مرحبًا {company}،</h2>
            <p>شكرًا لتجربتك <strong>{product}</strong>.
               تم إنشاء حسابك وموقعك التجريبي بنجاح.</p>
            <p><strong>بيانات الدخول:</strong></p>
            <ul>
                {app_line}
                <li>عنوان موقعك: <strong>{url}</strong></li>
                <li>بريد الدخول: <strong>{email}</strong> وكلمة المرور التي اخترتها</li>
            </ul>
            <p>أو الدخول عبر المتصفح: <a href="{login}">{login}</a></p>
            <p>فترتك التجريبية مجانية وتنتهي بتاريخ <strong>{end}</strong>. يمكنك الترقية في أي وقت.</p>
            <hr>
            <p style="color:#64748b;font-size:12px">هذه رسالة تلقائية من منصّة أسوفي.</p>
        </div>
        """
    ).format(
        company=frappe.utils.escape_html(company_name or ""),
        product=product_name,
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
