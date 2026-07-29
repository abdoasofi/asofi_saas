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
}

# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------
fixtures = [
    {"dt": "SaaS Subscription Plan"},
]

# ------------------------------------------------------------------------------
# Testing
# ------------------------------------------------------------------------------
# before_tests = "asofi_saas.install.before_tests"
