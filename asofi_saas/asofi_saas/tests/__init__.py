"""Tests for the guest-facing half of the console.

They live here, not beside the code they cover in `public/`, for one blunt
reason: `frappe.test_runner.run_all_tests` prunes any directory named `public`
while walking an app — that name is Frappe's convention for static assets. So
`bench run-tests --app asofi_saas` silently skipped every test in that package,
reported OK, and the trial funnel's suite had not run in a long time.
"""
