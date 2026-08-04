app_name = "asofi_saas"
app_title = "Asofi SaaS"
app_publisher = "Asofi"
app_description = "Control plane for provisioning and managing Rased company sites and their subscriptions"
app_email = "info@gain.sa"
app_license = "mit"
required_apps = ["frappe"]

# ------------------------------------------------------------------------------
# Includes in <head>
# ------------------------------------------------------------------------------
# app_include_css = "/assets/asofi_saas/css/asofi_saas.css"
# app_include_js = "/assets/asofi_saas/js/asofi_saas.js"

# ------------------------------------------------------------------------------
# Document Events
# ------------------------------------------------------------------------------
# Managed Company pushes its subscription snapshot to the company site whenever
# the plan / status / dates change. The controller handles this in on_update, so
# no doc_events wiring is needed here.

# ------------------------------------------------------------------------------
# Scheduled Tasks
# ------------------------------------------------------------------------------
scheduler_events = {
    "daily": [
        "asofi_saas.asofi_saas.subscription.lifecycle.run_daily_subscription_check",
    ],
    "hourly": [
        "asofi_saas.asofi_saas.sync.usage.sync_all_usage",
    ],
}

# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------
# Module 6 — plan definitions are NOT fixtures.
#
# Two separate mechanisms, and only one of them is this hook:
#   * this `fixtures` list drives `bench export-fixtures` (writing them OUT);
#   * `bench migrate` calls `sync_fixtures`, which scans the app's `fixtures/`
#     DIRECTORY and re-imports every .json it finds there, ignoring this list
#     entirely (frappe/utils/fixtures.py:import_fixtures).
#
# So emptying this list is not enough — the seed file also had to move out of
# `fixtures/` (it now lives in `seed/`). Otherwise every migrate silently
# restored plan limits and feature gates to whatever was frozen into the app,
# undoing whatever the operator had configured.
fixtures = []

# ------------------------------------------------------------------------------
# Testing
# ------------------------------------------------------------------------------
# before_tests = "asofi_saas.install.before_tests"
