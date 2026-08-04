"""Turn a single-product console into a multi-product one, without moving a
single tenant off what it already has.

Before this patch the console *was* Rased: the bench path, the apps to install,
the endpoint paths and the entire plan vocabulary were global or hard-coded.
This creates the `rased` product from those globals, points every existing
company and plan at it, and copies each plan's typed columns into the generic
rows so the console can render any product's vocabulary from one code path.

The copy uses the SAME key names the columns already had, so the pushed payload
is byte-identical before and after. `test_plan_definition_is_unchanged_by_the_
migration` in test_saas_product.py is what holds that promise.
"""

import frappe

RASED = "rased"
EDUPULSE = "edupulse"

# (key, label_ar, kind, icon)
RASED_METRICS = [
    ("max_collectors", "المحصّلون", "Limit", "badge_outlined"),
    ("max_zones", "المناطق", "Limit", "map_outlined"),
    ("max_beneficiaries", "المستفيدون", "Limit", "people_outline"),
    ("max_branches", "الفروع", "Limit", "store_outlined"),
    ("max_employees", "الموظفون", "Limit", "work_outline"),
    ("max_ai_tokens", "رموز الذكاء الاصطناعي", "Limit", "memory"),
    ("allow_photo_capture", "التقاط الصور", "Feature", "photo_camera_outlined"),
    ("allow_reports_export", "تصدير التقارير", "Feature", "download_outlined"),
    ("allow_branches", "الفروع", "Feature", "store_outlined"),
    ("allow_website", "الموقع الإلكتروني", "Feature", "language"),
    ("allow_hr", "الموارد البشرية", "Feature", "groups_outlined"),
    ("allow_incidents", "البلاغات", "Feature", "report_problem_outlined"),
    ("allow_tracking", "التتبّع", "Feature", "my_location"),
    ("allow_messaging", "الرسائل", "Feature", "sms_outlined"),
    ("allow_ai_analytics", "تحليلات الذكاء الاصطناعي", "Feature", "insights"),
    ("allow_meter_ocr", "قراءة العدّاد آلياً", "Feature", "document_scanner_outlined"),
    ("collectors", "محصّلون", "Usage", "badge_outlined"),
    ("zones", "مناطق", "Usage", "map_outlined"),
    ("beneficiaries", "مستفيدون", "Usage", "people_outline"),
    ("beneficiaries_active", "مستفيدون نشطون", "Usage", "how_to_reg"),
    ("branches", "فروع", "Usage", "store_outlined"),
    ("employees", "موظفون", "Usage", "work_outline"),
    ("incidents_open", "بلاغات مفتوحة", "Usage", "report_problem_outlined"),
    ("violations", "مخالفات", "Usage", "gavel"),
    ("messages_30d", "رسائل ٣٠ يوماً", "Usage", "sms_outlined"),
    ("ai_tokens", "رموز مستهلكة", "Usage", "memory"),
    ("ai_calls", "طلبات ذكاء اصطناعي", "Usage", "bolt"),
    ("ocr_reads", "قراءات آلية", "Usage", "document_scanner_outlined"),
]

EDUPULSE_METRICS = [
    ("max_students", "الطلاب", "Limit", "school_outlined"),
    ("max_teachers", "المعلّمون", "Limit", "person_outline"),
    ("max_courses", "المقررات", "Limit", "menu_book_outlined"),
    ("max_storage_gb", "التخزين (غيغابايت)", "Limit", "cloud_outlined"),
    ("allow_offline_library", "المكتبة دون اتصال", "Feature", "download_for_offline_outlined"),
    ("allow_remedial_engine", "المحرّك العلاجي", "Feature", "healing"),
    ("allow_parent_portal", "بوابة وليّ الأمر", "Feature", "family_restroom"),
    ("allow_executive_dashboard", "اللوحة التنفيذية", "Feature", "insights"),
    ("allow_video_upload", "رفع الفيديو", "Feature", "video_call_outlined"),
    ("students", "طلاب", "Usage", "school_outlined"),
    ("teachers", "معلّمون", "Usage", "person_outline"),
    ("courses", "مقررات", "Usage", "menu_book_outlined"),
    ("lessons", "دروس", "Usage", "play_lesson_outlined"),
    ("quiz_submissions_30d", "اختبارات ٣٠ يوماً", "Usage", "quiz_outlined"),
]

LEGACY_LIMITS = (
    "max_collectors", "max_zones", "max_beneficiaries",
    "max_branches", "max_employees", "max_ai_tokens",
)
LEGACY_FEATURES = (
    "allow_photo_capture", "allow_reports_export", "allow_branches",
    "allow_website", "allow_hr", "allow_incidents", "allow_tracking",
    "allow_messaging", "allow_ai_analytics", "allow_meter_ocr",
)


def execute():
    settings = frappe.get_single("SaaS Settings")

    _rased(settings)
    _edupulse(settings)

    _assign_product("SaaS Subscription Plan", RASED)
    _assign_product("Managed Company", RASED)

    _backfill_plan_rows()

    frappe.db.commit()
    frappe.clear_cache()


def _rased(settings):
    """Built from the globals it is replacing, so behaviour is unchanged."""
    if frappe.db.exists("SaaS Product", RASED):
        return

    doc = frappe.new_doc("SaaS Product")
    doc.product_code = RASED
    doc.product_name = "راصد"
    doc.is_active = 1
    doc.description = "إدارة شركات المياه والكهرباء"
    doc.bench_path = settings.bench_path
    doc.bench_executable = settings.bench_executable or "bench"
    doc.apps_to_install = settings.apps_to_install or "utility_billing"
    doc.default_site_domain = settings.default_site_domain
    # The key provision.py used to write literally.
    doc.secret_config_key = "rased_control_plane_secret"
    doc.manager_role = "Rased Manager"
    doc.apply_path = "/api/method/utility_billing.rased.api.subscription.apply_subscription"
    doc.usage_path = "/api/method/utility_billing.rased.api.subscription.get_usage"
    doc.enable_public_trial = settings.enable_public_trial
    doc.trial_plan = settings.trial_plan
    doc.trial_days = settings.trial_days or 14
    doc.trial_site_url_template = settings.trial_site_url_template
    doc.mobile_app_url = settings.mobile_app_url

    _add_metrics(doc, RASED_METRICS)
    doc.insert(ignore_permissions=True)


def _edupulse(settings):
    """Seeded here so the console has a second vocabulary to render against.

    Created inactive, with a blank bench path: EduPulse runs on Frappe v16, and
    both the bench and the receiving endpoints arrive in the next step. Seeding
    it now gives the console a second vocabulary to render against, without
    letting anyone provision onto a product that cannot yet answer a push.
    """
    if frappe.db.exists("SaaS Product", EDUPULSE):
        return

    doc = frappe.new_doc("SaaS Product")
    doc.product_code = EDUPULSE
    doc.product_name = "نبض التعلم"
    # Inactive on purpose: no bench path and no receiving endpoints yet. It is
    # also what keeps `_default_product()` unambiguous, so a console build that
    # predates the product picker keeps creating Rased companies exactly as before.
    doc.is_active = 0
    doc.description = "منصّة تعلّم الإتقان للمدارس"
    doc.bench_path = ""
    doc.bench_executable = "bench"
    doc.apps_to_install = "lms\nedupulse_core"
    doc.secret_config_key = "edupulse_control_plane_secret"
    doc.manager_role = "EduPulse Admin"
    doc.apply_path = "/api/method/edupulse_core.api.subscription.apply_subscription"
    doc.usage_path = "/api/method/edupulse_core.api.subscription.get_usage"
    doc.trial_days = 14

    _add_metrics(doc, EDUPULSE_METRICS)
    # `bench_path` is mandatory on the DocType — an operator must supply it
    # before anything is provisioned, but the row itself has to exist first.
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)


def _add_metrics(doc, metrics):
    for key, label, kind, icon in metrics:
        doc.append(
            "metrics",
            {"metric_key": key, "label_ar": label, "metric_kind": kind, "icon": icon},
        )


def _assign_product(doctype, product):
    """Everything that exists today is Rased — nothing else was possible."""
    frappe.db.sql(
        f"""UPDATE `tab{doctype}`
            SET product = %(product)s
            WHERE COALESCE(product, '') = ''""",
        {"product": product},
    )


def _backfill_plan_rows():
    """Copy the typed columns into the generic rows, key for key.

    Same names in, same names out — `definition()` then emits from the rows and
    the legacy fallback contributes nothing, so the payload on the wire does not
    move by a single byte.
    """
    for name in frappe.get_all("SaaS Subscription Plan", pluck="name"):
        plan = frappe.get_doc("SaaS Subscription Plan", name)

        if plan.limits or plan.features:
            continue

        for key in LEGACY_LIMITS:
            plan.append("limits", {"metric_key": key, "value": plan.get(key) or 0})

        for key in LEGACY_FEATURES:
            plan.append(
                "features", {"metric_key": key, "enabled": 1 if plan.get(key) else 0}
            )

        plan.save(ignore_permissions=True)
