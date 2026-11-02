# Key Vault Desk UI

## Functional workspace

The page retains the branded header with the K: mark, Key Vault Desk wordmark
and local-workspace tagline, followed by vault selection controls, secret inventory
and an add/update form. Large hero headings, sidebar marketing copy,
numbered section headings and footer slogans are intentionally omitted.

The header provides connection status, a dark/light theme toggle and local
session locking. Theme preference is the only value saved in browser storage.
All assets and fonts are local/system resources.

## Vault selection

Configuration is a non-empty array of objects:

```json
[
  {"name": "team-dev-vault", "nickname": "Development", "env": "dev"},
  {"name": "team-live-vault", "env": "prod"}
]
```

`name` and `env` are required; `nickname` is optional. Environments are `prod`,
`test`, `dev` and `other`. Names are unique, case-insensitively. Any number of
vaults can share an environment. The original two-vault config is accepted for
compatibility; README.md describes migration.

- **Filter vaults** searches names and nicknames case-insensitively.
- **Environment** limits the dropdown to an environment or all environments.
- **Key vault** displays actual names, optional nicknames and environment.
- Filtering does not select another vault or clear a draft. A selected vault
  outside the filter remains explicitly labeled. With no matches, the dropdown
  is disabled and the user is prompted to clear filters.
- The first non-production vault in configuration order is initially selected.
  If all vaults are production, the first is selected with production warnings.
- Choosing a different vault clears form and revealed values, resets secret
  filters, and loads that vault's inventory. Metadata is cached in page memory
  separately by vault, never by environment.

The selected actual vault name and environment appear above inventory and at the
save destination. Each production vault shows a warning. Production classification
comes from trusted local configuration and must correctly mark live vaults.

## Secret inventory

The inventory shows a total count, refresh action, local case-insensitive name
filter, active/inactive filter and rows with name, status and update date.
Inactive includes disabled, expired and not-yet-valid secrets.

- Reveal fetches a value on demand. Hide removes it; revealed values also
  disappear after 60 seconds. Errors appear inline.
- Edit fetches a value into the form, preserves the existing name exactly,
  disables normalization, and focuses the value field.
- Refresh bypasses the metadata cache and hides revealed values while retaining
  the editor. Cached metadata is clearly labeled.
- Loading, empty vault, no matching names and request failures have separate
  states. Request failures offer retry.
- Responses started for a previous vault cannot populate the selected vault.
- Long names truncate visually with full-name tooltips; values wrap and scroll.

## Add/update form

Normalization is enabled by default and changes the **name input itself**:
lowercase, replace characters outside `a-z`, `0-9` and hyphen with hyphens,
collapse repeated hyphens, remove leading hyphens, limit to 127 characters.
A trailing hyphen remains while typing so separators can be entered normally;
blur/save removes it. No separate "Saves as" element exists.

Turning normalization off retains exact input; Azure-compatible name validation
still applies. Editing an existing name does not normalize or rename it.
Pasting `NAME=value=more` splits on the first equals sign and keeps the remaining
value, including original line endings.

The multiline value field is masked by default with a Show/Hide control. Values
must be 1-25,600 UTF-8 bytes. Unchanged fetched values preserve original line
endings; manually edited textarea content uses the browser's LF normalization.

Every save displays exact secret name, actual vault name and environment in a
confirmation dialog. **Each prod vault** requires typing its actual vault name;
nicknames do not qualify. The backend independently enforces production
confirmation from configuration and rejects unknown vault IDs.

During save, the form and vault-selection/filter controls are disabled. Failed
saves remain editable (unless authentication expired); uncertain network results
warn users to inspect Azure versions before retrying. Successful saves clear
the form and refresh inventory. Every save creates a new Azure secret version.

## Privacy and sessions

The backend uses the current Azure CLI login and a local session token.
Azure authorization and network policies govern every operation.

- No deletion, export, bulk editing, role management or application login.
- No secret values in browser storage or app logs, including debug logs.
- Escape outside dialogs clears values.
- Leaving the tab clears displayed/editor values except during an active save.
- Lock invalidates the server session; restart/reload to reconnect. It does not
  sign out the shared Azure CLI identity or erase already-rendered values in
  other tabs; close those tabs separately.
- Read failures due to expired authentication clear in-memory secret data.

## Layout and accessibility

Desktop uses inventory/editor columns; narrow viewports stack controls and panels.
The workspace supports widths down to 320px with keyboard-accessible native
dropdowns, labeled inputs, dialogs, focus indicators, live status messages and
safe text rendering.

Scrollbars use theme-matched tracks, rounded thumbs and hover states, with
standard scrollbar-color/width and WebKit fallbacks. Inventory, revealed values
and editor have bounded scrolling. Native dropdown popups remain browser/OS
controlled. Forced-colors mode retains system scrollbar colors.
