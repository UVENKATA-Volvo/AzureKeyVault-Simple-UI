# Key Vault Desk

A local workspace for your team's **Azure Key Vault secrets**.
Searchable vault selection, fast secret name/status filtering, on-demand reveal, editing,
and guarded saves, using each person's existing `az login` identity.

**Python standard library only. No pip packages, Node.js, database, build step,
client secret, service principal, or cloud deployment.**

## What it does

- Any number of configured vaults, with optional nicknames and prod/test/dev/other
  environments. The first non-production vault opens by default (or the first
  vault if all are production).
- A vault dropdown displaying actual vault names, nicknames and environments,
  with name/nickname search and an environment filter.
- Case-insensitive name search and active/inactive filters, entirely in-browser.
- Paginated Azure inventory with per-vault, in-memory metadata caching.
- Reveal individual values; automatically hide after 60 seconds.
- Create secrets or update them by creating a new version.
- Preserve existing tags, content type, enabled state, expiry and not-before
  properties when updating a readable secret.
- Normalize names directly in the input field, and paste `NAME=value`.
- Support multiline values without stripping whitespace.
- Confirm every save. Production requires typing the production vault name.
- Responsive dark/light UI, keyboard navigation, reduced-motion support,
  screen-reader labels, and no externally hosted assets or fonts.

Deliberately **no deletion, export, bulk operations, role management, certificate
or key management, secret-version browser, or rollback**. Use Azure Portal/CLI for
those operations. Updating a secret makes a new latest version; applications
using an unversioned secret URL may immediately consume that version.

## Prerequisites

1. **Python 3.11+** on PATH (validated with Python 3.13 on Windows).
   Install from [python.org](https://www.python.org/downloads/), including the PATH option.
2. **Azure CLI 2.54+**, preferably the current supported release:
   [installation instructions](https://learn.microsoft.com/cli/azure/install-azure-cli).
   The app needs the CLI's numeric `expires_on` token field.
3. A modern browser supporting native dialogs and CSS text masking:
   current Edge or Chrome recommended.
4. Access to configured vaults from your machine: VPN/private-endpoint DNS and Key Vault
   firewall rules still apply. The app does not bypass network restrictions.
5. Your signed-in identity must have data-plane secret permissions on each vault.
   Your team's **Key Vault Administrator** assignment is sufficient on RBAC-based
   vaults when inherited group membership is effective. The app does not need
   Administrator specifically: Key Vault Secrets Officer is generally sufficient
   for these operations; use your organization's least-privilege policy.
   A Reader/Secrets User identity cannot necessarily save.

This release targets **Azure public cloud** (`*.vault.azure.net`) and one Azure
CLI tenant context for all vaults. Sovereign clouds and vaults in different
tenants are not supported by this configuration. No subscription enumeration or
management-plane listing permission is needed; configure approved vault names.

## Run on Windows

Clone/download the repository, open PowerShell in its directory, then:

```powershell
python --version
az version
az login --tenant YOUR_TENANT_ID
az account show --query "{tenant:tenantId, subscription:name, user:user.name}"
Copy-Item .\config.example.json .\config.json
notepad .\config.json
```

Replace the example names with your **actual vault names**, not URLs:

```json
[
  {"name": "your-dev-vault", "nickname": "Development", "env": "dev"},
  {"name": "your-test-vault", "nickname": "Integration testing", "env": "test"},
  {"name": "your-prod-vault", "nickname": "Production", "env": "prod"},
  {"name": "your-shared-vault", "env": "other"}
]
```

Use a non-empty JSON array. Each entry requires a unique `name` (case-insensitive)
and an `env`: exactly `prod`, `test`, `dev` or `other`. `nickname` is optional
text up to 80 characters. Multiple vaults may share an environment or nickname;
actual vault names always appear so destinations remain unambiguous.

**Mark every live vault `prod`.** Production warnings and server-side save
confirmation are enforced from this configuration, not inferred from a name or
nickname. Configuration is trusted local policy, not an Azure classification.

Config is validated at startup and excluded from Git. Never put secret values,
passwords, or tokens in this file. Restart the server and reload all app tabs
after changing config.

The original `{"nonprod": "vault-a", "prod": "vault-b"}` format is still accepted,
mapping to `test` and `prod` respectively. Migrate to the array to add vaults or
nicknames. Existing files are not rewritten automatically.

```powershell
python .\app.py
```

Open **http://127.0.0.1:8765** in your browser. Use that exact address, not
`localhost`. Keep the terminal open; press **Ctrl+C** to stop.

If `python` is unavailable but the Windows Python launcher is installed, use
`py -3.13 .\app.py` (or your installed 3.11+ version).

Optional explicit config location / alternate port:

```powershell
python .\app.py --config "C:\TeamTools\KeyVaultDesk\config.json" --port 8877
```

Open the address printed by the server. Config changes require a restart.
There is no automatic login prompt: complete `az login` in your own terminal.
If browser login is unavailable, your policy may allow `az login --use-device-code`.
For an account without subscriptions, your administrator may advise
`az login --tenant YOUR_TENANT_ID --allow-no-subscriptions`.

### Local debug logging

Stop the server, then restart with:

```powershell
python .\app.py --debug
```

Combine `--debug` with `--config` or `--port` as needed. Diagnostics appear in
the **server terminal**, not the browser console. Normal runs now also report
failed requests with their HTTP status, error code and local correlation ID.
Debug mode adds:

- Local request/response IDs, environment, operation, status and elapsed time.
- Azure CLI token acquisition/cache events, exit codes and token lifetime.
- Azure request operation, configured host, attempt, HTTP status and timing.
- Inventory page/count progress and whether another page exists.
- Continuation rejection reasons: `host_mismatch`, `https_required`,
  `port_not_https`, `userinfo_not_allowed`, `fragment_not_allowed`,
  `unexpected_path` or `malformed_url`.
- Unexpected exception types and code locations, without exception text,
  source lines, local variables or raw tracebacks.

For `invalid_next_link`, reproduce the inventory refresh with `--debug` and
inspect the `Azure URL rejected` entry. It identifies the environment, expected
host, whether this is a continuation, and the exact validation rule that failed.
Equivalent HTTPS URLs with hostname case differences/default port 443, and
same-vault relative continuations, are supported. Other hosts, ports, schemes,
credentials and non-inventory continuation paths remain blocked; never disable
this validation to troubleshoot.

Even in debug mode, logs omit secret names/values, tokens, authorization headers,
request/response bodies, returned URLs, query strings/continuation tokens and
raw CLI output. Logs include configured vault hosts and operational metadata:
review them before sharing. The app creates no log files automatically.

## Everyday workflow

### Find and inspect

1. Use **Filter vaults** to search actual names or nicknames, optionally limit by
   **Environment**, then choose the target in **Key vault**.
   The full vault name/environment appear above inventory and beside the save
   button. Filtering the dropdown never changes the selected destination or
   clears a draft. If the selection is outside the filter, it remains explicitly
   labeled; no-match filters disable the dropdown until filters are cleared.
2. Filter secret names; press `/` to focus that search box.
3. Optionally select Active/Inactive. Inactive includes disabled, expired, or
   not-yet-valid secrets.
4. Reveal a value only when needed. Hide it individually or use **Hide all values**.
5. Refresh to fetch current metadata. Vault switches can reuse metadata;
   the connection indicator explicitly says **cached** in that case.

Azure still enforces whether disabled, expired, or otherwise restricted secrets
can be read. An inventory entry does not imply its value is readable.

### Create or edit

1. Enter a name and value, or click a secret's **Edit** button.
2. Normalization lowercases, replaces non-ASCII-letter/digit/hyphen characters
   with hyphens, collapses hyphens, trims boundary hyphens, and limits to 127
   characters, **directly in the name input**. A trailing separator remains
   while typing so you can continue a multi-word name; it is trimmed when you
   leave the field or save. Disable normalization to keep an exact valid name.
3. Pasting `NAME=value=more` into the name field splits at the **first** equals
   sign; the rest is preserved as the value.
4. Editing an existing secret preserves its name and disables normalization.
   Clear the form to compose a different name.
5. Save, review the name and destination, then confirm. For production, type
   the exact production vault name.

Values are masked by default with a Show/Hide control; this is a visual privacy
aid, **not encryption or protection against browser inspection**. Values must be
1-25,600 UTF-8 bytes, not characters. Leading/trailing whitespace is preserved. Unchanged values fetched
for editing (and `NAME=value` paste payloads) preserve their original line
endings. Browsers normalize manually edited multiline text to LF newlines.
The app does not edit binary/file-backed secrets.

Every successful save creates a new Azure version, even if the value is unchanged.
The form resets and inventory refreshes. If refresh fails after a successful save,
the save confirmation remains valid; refresh the inventory again.

**No automatic retries of failed writes** (apart from one rejected-401 token
refresh). A lost connection can occur after Azure committed a save. Check the
secret's versions in Azure Portal before retrying to avoid duplicate versions.
Failed saves keep the form available for correction, unless the session expired.

Concurrent writes from this app instance are serialized by rejecting overlapping
saves. Other app instances and Portal/CLI writers are not coordinated; **last
write wins**. Arrange production changes with your team. Existing properties are
read before each update, but concurrent metadata changes can still race. The app
does not bypass an unreadable secret to overwrite it.

### Clear or end a session

- Vault switches clear the form and revealed values, even between two vaults
  with the same environment.
- `Escape` outside a dialog clears secret values.
- Switching away from the tab clears revealed/editor values, except while a
  save is in progress. This intentionally trades draft retention for privacy.
- Refresh clears revealed values but retains the editor.
- **Lock session** invalidates this server session for all tabs, drops cached
  backend tokens, and clears this tab. Other tabs clear when their next API call
  receives the locked-session response. **Lock does not remotely erase a value
  already rendered in another tab**; close those tabs too.
- Restart the app to unlock, then reload the browser page.
- Locking does **not** run `az logout` or sign out Microsoft browser sessions.
  Those are shared with other Azure CLI work. Stop the app and run `az logout`
  separately if you intend to sign out Azure CLI completely.
- Restart the app after changing Azure CLI account/tenant. Cached access tokens
  can otherwise continue using the previous identity until they expire.

An accepted Azure write cannot be undone by closing a tab, locking another tab,
or stopping the local app.

## Sharing with the team

Share this source repository or a reviewed archive containing:

```text
app.py
config.example.json
README.md
UI.md
static\
tests\
```

Each teammate installs Python and Azure CLI, creates their own `config.json`,
and signs in independently. Share approved vault names through your
normal internal channel, or provide a separately reviewed config containing
**only vault names**. Never share `.azure`, tokens, session URLs via a tunnel,
browser profiles, secret exports, or credentials.

No administrator rights are needed to run the application itself. Updates are
source-based: review/pull a new version, stop the old server, and restart.

## Security and operational boundaries

- **Local desktop utility, not a hosted web application.** It binds only to
  `127.0.0.1`. Never reverse-proxy, expose, tunnel, port-forward, or modify it to
  bind to a shared network address. No multi-user server authentication exists.
- Host allowlisting, same-origin checks, request tokens on every API request,
  no CORS, restrictive CSP, no framing, and `no-store` responses protect against
  cross-site browser requests. These do not protect against local malware,
  administrator access, compromised browser extensions, or other local programs
  that can read the page/token. Use a trusted, patched workstation/profile.
- Browser-to-backend traffic is loopback HTTP; backend-to-Azure traffic is HTTPS
  with normal certificate verification. Never disable TLS verification.
- CLI tokens exist in backend memory. Values are fetched only on demand and are
  not cached by the backend, placed in process arguments, or written by this app
  to files/logs/browser storage. Metadata and displayed values exist in memory.
  Only the theme goes to `localStorage`.
- Azure CLI manages its **own** persistent credential cache. OS paging, crash
  dumps, endpoint tools, browser autofill/extensions, and developer tools are
  outside the app's control. There is no secure-memory erasure guarantee.
- Server errors are sanitized; no raw Azure/CLI error output is sent to the
  browser. Failed requests are logged; `--debug` enables additional sanitized
  diagnostics. Request URLs, names, values, and tokens are not access-logged.
- Enable Azure Key Vault diagnostic logging through your organization for
  centralized auditing; there is no local audit database. Production use still
  requires your normal access review, change process and workstation controls.
- Secret inventory is fetched 25 entries per Azure page with a 25,000-entry
  safety limit. Exceeding it returns an error, never a silently partial list.
  Filtering is local and can be slower with very large vaults.

## Troubleshooting

| Symptom | Action |
|---|---|
| Missing config / invalid vault entry | Copy the example; use unique names and prod/test/dev/other environments. Restart and reload after editing. |
| Azure CLI missing | Reopen the terminal after installation; check `az version`. |
| Login required / expired session | Run `az login --tenant YOUR_TENANT_ID`; retry or restart the app. No app passwords are needed. |
| Access denied (403) | Check signed-in tenant, effective group membership, RBAC propagation, vault permission model, VPN and firewall/private endpoint policy. Re-login after group changes. |
| Vault/secret not found (404) | Verify the configured vault name and refresh. Check for deletion or soft deletion in Portal. |
| Conflicting name (409) | Check soft-deleted secrets in Portal; this app never recovers or purges them. |
| Throttled (429) | Wait before refreshing/saving. Avoid repeated clicks or many concurrent tabs. |
| Network / TLS failure | Check corporate VPN, DNS, approved proxy and certificate trust. Python/CLI may have different trust settings; involve IT rather than disabling TLS. |
| Port already in use | Use `--port 8877`. Do not terminate an unrelated process. |
| Invalid host / cross-origin request | Open the exact `http://127.0.0.1:PORT` address printed at startup. |
| Session locked / invalid local session | Restart server and reload all app tabs. |
| Save unconfirmed | Check Azure Portal versions before retrying; a network failure isn't proof the write failed. |
| Unexpected server error | Check terminal, restart, and capture only a sanitized reproduction for maintainers. Never attach values/tokens. |

## Validation and maintenance

Run the dependency-free automated suite:

```powershell
python -m unittest discover -s tests -v
```

It uses synthetic identities and vaults; **no Azure credentials or live writes**.
It covers multi-vault configuration, legacy migration, configured-vault routing,
production checks from metadata, token cache/lock/refresh, pagination, redirect/continuation
protection, property preservation, permissions, production confirmation, byte
limits, request protection, response headers, concurrency and HTTP endpoints.

For browser testing against synthetic data only:

```powershell
python -m tests.browser_fixture
```

Open http://127.0.0.1:8766. This separate test server has fake vaults, including
hostile text fixtures to check safe rendering; it never contacts Azure.
Stop it with Ctrl+C. Do not mistake its successful saves for Azure saves.
The application has also been exercised in-browser at desktop and mobile sizes.

Before team rollout, verify with an approved **non-production test secret**:
listing, read, create/update, property preservation, group authorization, network
policy and Azure diagnostic logs. Then obtain your normal production approval.
Automated/synthetic checks cannot certify your tenant's live configuration.

## Implementation

`app.py` serves the UI and a small same-origin JSON API. Azure CLI supplies a
short-lived token for `https://vault.azure.net`; Python calls the configured
vault's Key Vault REST API (version 7.4). There are no third-party runtime
dependencies. Static files are served from an explicit allowlist, not a directory
browser. Azure remains the source of truth for access and secret versions.

The [UI vision](UI.md) documents the intended workspace and implementation notes.