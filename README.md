## Asofi SaaS

Central **control plane** for the Rased platform. It provisions and manages the
per-company Frappe sites and their subscriptions from one place, so a Super Admin
never has to touch each company's bench by hand.

Each company runs its **own** Frappe site with the `utility_billing` app. Every such
site exposes a guest endpoint
`utility_billing.rased.api.subscription.apply_subscription`, secured by a shared
secret stored in that site's `site_config.json` (`rased_control_plane_secret`).
This app is the authority that pushes each company's plan / status / dates to that
endpoint, and (optionally) provisions brand-new company sites via `bench new-site`.

### What it does

- **Managed Company registry** — one record per company site (URL, shared secret,
  plan, status, dates).
- **Subscription push** — writes a company's subscription snapshot to its site over
  HTTP; every push is recorded in a `Subscription Push Log` (no silent failures).
- **Lifecycle automation** — a daily scheduled job expires overdue subscriptions
  (and pushes the change) and sends a reminder before expiry.
- **Automated provisioning** — creates a new company site end-to-end: `bench new-site`,
  install `utility_billing`, create the manager user, set the control-plane secret,
  register the company, and push its initial subscription.
- **REST API** — whitelisted endpoints consumed by the `asofi_saas_app` Flutter
  console for the Super Admin.

### Design notes

- Subscription management uses **HTTP** (`requests`), not `subprocess` — only
  provisioning shells out to `bench`.
- **No mock metrics.** Any number shown to the Super Admin is derived from real data
  or omitted.
- Errors are logged and surfaced, never swallowed by a bare `except: pass`.
- Audit trails live in proper doctypes, not temp files under `sites/`.

### License

MIT
# asofi_saas
