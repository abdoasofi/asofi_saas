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

### Domains & SSL automation

When `SaaS Settings → Enable Domain & SSL Automation` is on, provisioning (and the
per-company **Setup Domain & SSL** action) runs, after the site is Active:

1. `bench setup nginx --yes` — regenerate the nginx config to include the new site.
2. `bench setup reload-nginx` — reload nginx.
3. `sudo -n -H bench setup lets-encrypt <site> [--custom-domain <domain>] -n` —
   issue a Let's Encrypt certificate.

This step is **optional and non-fatal**: if it fails (no nginx, no sudo, DNS not
pointed yet) the site stays Active and a warning is recorded — so it is safely
skipped on a dev laptop (`bench start`), and left off by default.

**Production prerequisites** (one-time, on the server):

- The bench must be a production bench with nginx (`sudo bench setup production <user>`).
- Passwordless sudo for the bench user so the worker can reload nginx / run certbot:
  `sudo bench setup sudoers <user>`.
- `certbot` installed, and its ACME account registered once (email + TOS) — the
  automated calls use `-n` (non-interactive), so the very first certificate on a
  fresh host may need one interactive `bench setup lets-encrypt <site>` run.
- **DNS**: the site name (or `custom_domain`) must already resolve to the server's
  public IP, and ports 80/443 must be reachable, before the certificate can issue.

On a single dev bench you don't need any of this — set each Managed Company's
`site_url` to `http://127.0.0.1:8000`; the `X-Frappe-Site-Name` header routes the
push to the right site. See the memory note on multi-tenant dev serving
(`default_site` must be empty for `bench serve` to honor the header).

### License

MIT
# asofi_saas
