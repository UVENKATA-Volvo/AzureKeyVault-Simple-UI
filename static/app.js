"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const token = document.querySelector('meta[name="desk-token"]').content;
  document.querySelector('meta[name="desk-token"]').remove();
  const cache = new Map();
  const reveals = new Map();
  let environment = "";
  let vaults = {};
  let epoch = 0;
  let loading = false;
  let saving = false;
  let locked = false;
  let pending = null;
  let toastTimer;
  let inventoryController;
  let readGeneration = 0;
  let editGeneration = 0;
  let inventoryError = "";
  let showingValue = false;
  let originalValue = null;

  function notify(message, error = false) {
    clearTimeout(toastTimer);
    $("toast").textContent = message;
    $("toast").classList.toggle("error", error);
    $("toast").hidden = false;
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, error ? 10000 : 6000);
  }

  function connection(text, state = "ready") {
    $("connection").lastElementChild.textContent = text;
    $("connection").dataset.state = state;
  }

  function clearReveals() {
    readGeneration += 1;
    for (const entry of reveals.values()) clearTimeout(entry.timer);
    reveals.clear();
  }

  function clearValues() {
    clearReveals();
    editGeneration += 1;
    $("secret-value").value = "";
    originalValue = null;
    $("confirm-dialog").close();
    $("prod-confirm").value = "";
    pending = null;
    resetValueVisibility();
    renderInventory();
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      cache: "no-store",
      credentials: "omit",
      headers: { "Content-Type": "application/json", "X-Desk-Token": token },
    });
    const body = await response.json();
    if (!response.ok) {
      if (response.status === 401 || body.error?.code === "invalid_session") {
        clearValues();
        cache.clear();
        inventoryError = body.error.message;
        if (body.error.code === "session_locked") lockPage();
        renderInventory();
      }
      const error = new Error(body.error?.message || `Request failed (${response.status}).`);
      error.code = body.error?.code;
      throw error;
    }
    return body;
  }

  function normalizeName(finalize = false) {
    const input = $("secret-name");
    if (!input.readOnly && $("normalize").checked) {
      const clean = (text) => text.toLowerCase().replace(/[^a-z0-9-]/g, "-")
        .replace(/-+/g, "-").replace(/^-/, "").slice(0, 127);
      const caret = clean(input.value.slice(0, input.selectionStart)).length;
      input.value = clean(input.value);
      // Keep a trailing separator while typing so multi-word names remain possible.
      if (finalize) input.value = input.value.replace(/-$/, "");
      input.setSelectionRange(Math.min(caret, input.value.length), Math.min(caret, input.value.length));
    }
  }

  function resetValueVisibility() {
    showingValue = false;
    $("secret-value").classList.add("masked");
    $("toggle-value").textContent = "Show value";
    $("toggle-value").setAttribute("aria-pressed", "false");
  }

  function resetForm() {
    $("secret-form").reset();
    originalValue = null;
    $("secret-name").readOnly = false;
    $("form-error").hidden = true;
    resetValueVisibility();
  }

  function isActive(secret) {
    const now = Date.now() / 1000;
    return secret.enabled && (secret.expires == null || secret.expires > now)
      && (secret.notBefore == null || secret.notBefore <= now);
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function emptyState(title, message, retry = false) {
    const box = node("div", "empty-state");
    box.append(node("span", "empty-glyph", inventoryError ? "!" : "◇"));
    box.append(node("strong", "", title), node("p", "", message));
    if (retry) {
      const button = node("button", "quiet", "Try again");
      button.addEventListener("click", () => loadInventory(true));
      box.append(button);
    }
    return box;
  }

  function renderInventory() {
    const list = $("inventory-list");
    list.replaceChildren();
    list.setAttribute("aria-busy", String(loading));
    const all = cache.get(environment)?.secrets || [];
    $("total").textContent = String(all.length);
    $("refresh").disabled = loading || saving || locked;
    if (locked) {
      list.append(emptyState("Session locked", "Restart the app in your terminal to reconnect."));
      $("results").textContent = "Session ended";
      return;
    }
    if (loading) {
      list.append(emptyState("Opening your vault", "Fetching secret names and status. Values remain hidden."));
      $("results").textContent = "Loading inventory...";
      return;
    }
    if (inventoryError) {
      list.append(emptyState("Couldn't load this vault", inventoryError, true));
      $("results").textContent = "Inventory unavailable";
      return;
    }
    const query = $("filter").value.trim().toLowerCase();
    const status = $("status-filter").value;
    const filtered = all.filter((secret) => secret.name.toLowerCase().includes(query)
      && (status === "all" || isActive(secret) === (status === "active")));
    $("results").textContent = `${filtered.length} of ${all.length} secrets`;
    if (!all.length) {
      list.append(emptyState("A clean slate", "No secrets yet. Add the first one using the form."));
    } else if (!filtered.length) {
      list.append(emptyState("No matching secrets", "Try another name or change the status filter."));
    }
    const fragment = document.createDocumentFragment();
    for (const secret of filtered) {
      const row = node("article", "secret-row");
      const main = node("div", "secret-main");
      const active = isActive(secret);
      const statusMark = node("span", `secret-status${active ? "" : " inactive"}`);
      statusMark.title = active ? "Active" : "Inactive (disabled, expired or not yet valid)";
      statusMark.setAttribute("aria-label", statusMark.title);
      const info = node("div", "secret-info");
      const name = node("span", "secret-name", secret.name);
      name.title = secret.name;
      const updated = secret.updated ? new Date(secret.updated * 1000).toLocaleDateString() : "Unknown";
      info.append(name, node("div", "secret-meta", `${active ? "Active" : "Inactive"} · Updated ${updated}`));
      const actions = node("div", "row-actions");
      const revealed = reveals.get(secret.name);
      const reveal = node("button", "row-button", revealed ? "Hide" : "Reveal");
      reveal.setAttribute("aria-label", `${revealed ? "Hide" : "Reveal"} ${secret.name}`);
      reveal.setAttribute("aria-expanded", String(Boolean(revealed)));
      reveal.addEventListener("click", () => toggleReveal(secret.name));
      const edit = node("button", "row-button", "Edit");
      edit.setAttribute("aria-label", `Edit ${secret.name}`);
      edit.disabled = saving;
      edit.addEventListener("click", () => editSecret(secret.name, edit));
      actions.append(reveal, edit);
      main.append(statusMark, info, actions);
      row.append(main);
      if (revealed) {
        const content = node("div", `reveal${revealed.error ? " error" : ""}`,
          revealed.loading ? "Fetching value..." : (revealed.error || revealed.value));
        content.setAttribute("role", "status");
        row.append(content);
      }
      fragment.append(row);
    }
    list.append(fragment);
  }

  async function loadInventory(force = false) {
    if (!vaults[environment]) {
      await start();
      return;
    }
    inventoryController?.abort();
    const controller = new AbortController();
    inventoryController = controller;
    const currentEpoch = ++epoch;
    const target = environment;
    inventoryError = "";
    clearReveals();
    const existing = cache.get(target);
    if (existing && !force) {
      loading = false;
      connection(`${vaults[target].name} · cached`);
      $("freshness").textContent = `Cached metadata · ${new Date(existing.at).toLocaleTimeString()}`;
      renderInventory();
      return;
    }
    loading = true;
    connection("Connecting to Azure", "loading");
    renderInventory();
    try {
      const data = await api(`/api/${target}/secrets`, { signal: controller.signal });
      if (currentEpoch !== epoch || locked) return;
      cache.set(target, { secrets: data.secrets, at: Date.now() });
      connection(`${vaults[target].name} connected`);
      $("freshness").textContent = `Synced ${new Date().toLocaleTimeString()} · values on demand`;
    } catch (error) {
      if (error.name === "AbortError" || currentEpoch !== epoch || locked) return;
      inventoryError = error.message;
      $("freshness").textContent = "No current inventory. Refresh to retry.";
      connection("Connection needs attention", "error");
      notify(error.message, true);
    } finally {
      if (currentEpoch === epoch) {
        loading = false;
        renderInventory();
      }
    }
  }

  async function toggleReveal(name) {
    if (reveals.has(name)) {
      clearTimeout(reveals.get(name).timer);
      reveals.delete(name);
      renderInventory();
      return;
    }
    const target = environment;
    const generation = readGeneration;
    const entry = { loading: true };
    reveals.set(name, entry);
    renderInventory();
    try {
      const data = await api(`/api/${target}/secrets/${encodeURIComponent(name)}`);
      if (target !== environment || generation !== readGeneration || reveals.get(name) !== entry || locked) return;
      entry.value = data.value;
      entry.loading = false;
      entry.timer = setTimeout(() => {
        if (reveals.get(name) === entry) {
          reveals.delete(name);
          renderInventory();
        }
      }, 60000);
    } catch (error) {
      if (target !== environment || generation !== readGeneration || reveals.get(name) !== entry || locked) return;
      entry.error = error.message;
      entry.loading = false;
    }
    renderInventory();
  }

  async function editSecret(name, button) {
    const target = environment;
    const generation = readGeneration;
    const editVersion = ++editGeneration;
    button.disabled = true;
    button.textContent = "...";
    try {
      const data = await api(`/api/${target}/secrets/${encodeURIComponent(name)}`);
      if (target !== environment || generation !== readGeneration || editVersion !== editGeneration || locked || saving) return;
      $("secret-name").value = name;
      $("secret-name").readOnly = true;
      // Editing must never silently normalize an existing mixed-case secret name.
      $("normalize").checked = false;
      $("secret-value").value = data.value;
      originalValue = { raw: data.value, displayed: $("secret-value").value };
      $("form-error").hidden = true;
      resetValueVisibility();
      $("editor").scrollIntoView({ behavior: "smooth", block: "nearest" });
      $("secret-value").focus({ preventScroll: true });
    } catch (error) {
      if (target === environment && generation === readGeneration && editVersion === editGeneration && !locked) notify(error.message, true);
    } finally {
      button.disabled = saving;
      button.textContent = "Edit";
    }
  }

  function selectVault(target) {
    if (saving || locked || !vaults[target] || environment === target) return;
    environment = target;
    resetForm();
    $("filter").value = "";
    $("status-filter").value = "all";
    clearValues();
    updateVault();
    filterVaults();
    loadInventory();
  }

  function updateVault() {
    const vault = vaults[environment];
    const prod = vault?.env === "prod";
    document.body.classList.toggle("production", prod);
    $("production-notice").hidden = !prod;
    $("vault-name").textContent = vault ? `${vault.name}.vault.azure.net · ${vault.env}` : "Vault not configured";
    $("save-target").textContent = vault ? `${vault.name} (${vault.env})` : "Select a vault";
    $("save").textContent = prod ? "Save to production" : "Save secret";
  }

  function filterVaults() {
    const query = $("vault-filter").value.trim().toLowerCase();
    const env = $("vault-env-filter").value;
    const matches = Object.entries(vaults).filter(([, vault]) =>
      `${vault.name} ${vault.nickname}`.toLowerCase().includes(query)
      && (env === "all" || vault.env === env));
    const select = $("vault-select");
    select.replaceChildren();
    for (const [id, vault] of matches) {
      select.append(new Option(`${vault.name}${vault.nickname ? ` — ${vault.nickname}` : ""} (${vault.env})`, id));
    }
    // Filtering never changes the destination or discards the current draft.
    if (!matches.some(([id]) => id === environment)) {
      const selected = vaults[environment];
      const option = new Option(selected ? `Selected: ${selected.name} (${selected.env}) — outside filter`
        : "Select a vault", environment);
      option.disabled = true;
      select.prepend(option);
    }
    select.value = environment;
    select.disabled = saving || locked || !matches.length;
    $("vault-results").textContent = locked ? "Session ended"
      : !Object.keys(vaults).length ? loading ? "Loading configuration..." : "Vault configuration unavailable. Retry below."
      : `${matches.length} of ${Object.keys(vaults).length} vaults`
        + (!matches.length ? " · No matching vaults. Clear filters to select." : "");
  }

  function setSaving(value) {
    saving = value;
    for (const element of $("secret-form").elements) element.disabled = value || locked || !vaults[environment];
    $("vault-filter").disabled = value || locked || !vaults[environment];
    $("vault-env-filter").disabled = value || locked || !vaults[environment];
    filterVaults();
    $("refresh").disabled = value || loading || locked;
    $("save").textContent = value ? "Saving new version..." : vaults[environment]?.env === "prod" ? "Save to production" : "Save secret";
    renderInventory();
  }

  $("secret-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (saving || locked) return;
    normalizeName(true);
    const name = $("secret-name").value;
    const displayedValue = $("secret-value").value;
    const value = originalValue?.displayed === displayedValue ? originalValue.raw : displayedValue;
    const size = new TextEncoder().encode(value).length;
    if (!/^[a-zA-Z0-9-]{1,127}$/.test(name) || !size || size > 25600) {
      $("form-error").textContent = !/^[a-zA-Z0-9-]{1,127}$/.test(name)
        ? "Use a name with 1-127 letters, numbers or hyphens."
        : "Enter a value between 1 and 25,600 UTF-8 bytes.";
      $("form-error").hidden = false;
      return;
    }
    $("form-error").hidden = true;
    pending = { name, value, environment };
    const exists = cache.get(environment)?.secrets.some((secret) => secret.name.toLowerCase() === name.toLowerCase());
    $("confirm-title").textContent = exists ? "Update this secret?" : "Save this secret?";
    const vault = vaults[environment];
    $("confirm-description").textContent = `Save "${name}" to ${vault.name} (${vault.env})? This creates a new version if the name exists.`;
    $("prod-confirm-wrap").hidden = vault.env !== "prod";
    $("prod-confirm").value = "";
    $("confirm-save").disabled = vault.env === "prod";
    $("confirm-dialog").showModal();
  });

  $("prod-confirm").addEventListener("input", () => {
    $("confirm-save").disabled = $("prod-confirm").value !== vaults[environment]?.name;
  });
  $("cancel-save").addEventListener("click", () => $("confirm-dialog").close());
  $("confirm-dialog").addEventListener("close", () => { pending = null; $("prod-confirm").value = ""; });
  $("confirm-save").addEventListener("click", async () => {
    if (!pending || saving || locked) return;
    const snapshot = pending;
    const vault = vaults[snapshot.environment];
    const confirmed = vault.env === "prod" && $("prod-confirm").value === vault.name;
    if (vault.env === "prod" && !confirmed) return;
    $("confirm-dialog").close();
    setSaving(true);
    try {
      await api(`/api/${snapshot.environment}/secrets/${encodeURIComponent(snapshot.name)}`, {
        method: "PUT", body: JSON.stringify({ value: snapshot.value, confirmProduction: confirmed }),
      });
      if (locked) return;
      resetForm();
      cache.delete(snapshot.environment);
      notify(`Saved "${snapshot.name}" to ${vault.name} (${vault.env}).`);
      await loadInventory(true);
    } catch (error) {
      if (locked) return;
      // A transport failure may happen after Azure committed the new version.
      $("form-error").textContent = `${error.message} If the connection failed, check Azure's versions before retrying; the save may have completed.`;
      $("form-error").hidden = false;
      notify("Save not confirmed. Check the message below the form.", true);
    } finally {
      snapshot.value = "";
      setSaving(false);
    }
  });

  $("secret-name").addEventListener("input", () => normalizeName());
  $("secret-name").addEventListener("blur", () => normalizeName(true));
  $("normalize").addEventListener("change", () => normalizeName(true));
  $("secret-name").addEventListener("paste", (event) => {
    const text = event.clipboardData?.getData("text");
    const split = text?.indexOf("=") ?? -1;
    if (split < 0 || $("secret-name").readOnly) return;
    event.preventDefault();
    $("secret-name").value = text.slice(0, split).trim();
    $("secret-value").value = text.slice(split + 1);
    originalValue = { raw: text.slice(split + 1), displayed: $("secret-value").value };
    normalizeName(true);
  });
  $("toggle-value").addEventListener("click", () => {
    showingValue = !showingValue;
    $("secret-value").classList.toggle("masked", !showingValue);
    $("toggle-value").textContent = showingValue ? "Hide value" : "Show value";
    $("toggle-value").setAttribute("aria-pressed", String(showingValue));
  });
  $("reset-form").addEventListener("click", () => {
    editGeneration += 1;
    resetForm();
  });
  $("filter").addEventListener("input", renderInventory);
  $("status-filter").addEventListener("change", renderInventory);
  $("refresh").addEventListener("click", () => loadInventory(true));
  $("hide-all").addEventListener("click", () => {
    clearReveals();
    renderInventory();
  });
  $("vault-select").addEventListener("change", () => selectVault($("vault-select").value));
  $("vault-filter").addEventListener("input", filterVaults);
  $("vault-env-filter").addEventListener("change", filterVaults);
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)
      && !document.querySelector("dialog[open]") && !locked) {
      event.preventDefault();
      $("filter").focus();
    }
    if (event.key === "Escape" && !document.querySelector("dialog[open]")) clearValues();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && !saving) clearValues();
  });
  window.addEventListener("pagehide", clearValues);

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $("theme").setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} theme`);
  }
  try { applyTheme(localStorage.getItem("desk-theme") === "light" ? "light" : "dark"); }
  catch {
    applyTheme("dark");
    notify("Theme preference is unavailable. Using dark theme.", true);
  }
  $("theme").addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(theme);
    try { localStorage.setItem("desk-theme", theme); }
    catch { notify("Theme changed. Browser storage is unavailable; preference won't persist.", true); }
  });

  function lockPage() {
    locked = true;
    loading = false;
    epoch += 1;
    inventoryController?.abort();
    cache.clear();
    clearValues();
    resetForm();
    filterVaults();
    for (const element of document.querySelectorAll("button,input,textarea,select")) {
      if (element.id !== "theme") element.disabled = true;
    }
    connection("Session locked", "error");
    if (!vaults[environment]) $("vault-name").textContent = "Session ended";
    $("freshness").textContent = "Restart the app to reconnect.";
    renderInventory();
  }
  $("lock").addEventListener("click", () => {
    if (saving) {
      notify("Wait for the save to finish before locking.", true);
      return;
    }
    $("lock-dialog").showModal();
  });
  $("cancel-lock").addEventListener("click", () => $("lock-dialog").close());
  $("confirm-lock").addEventListener("click", async () => {
    $("lock-dialog").close();
    try {
      await api("/api/lock", { method: "POST" });
      lockPage();
      notify("Session locked. Restart the app to reconnect.");
    } catch (error) {
      clearValues();
      notify(`Could not confirm server lock: ${error.message} Stop the app in your terminal.`, true);
    }
  });

  async function start() {
    loading = true;
    setSaving(false);
    try {
      const session = await api("/api/session");
      vaults = session.vaults;
      environment = Object.keys(vaults).find((id) => vaults[id].env !== "prod") || Object.keys(vaults)[0];
      filterVaults();
      updateVault();
      await loadInventory();
    } catch (error) {
      if (locked) return;
      connection("Unable to connect", "error");
      inventoryError = error.message;
      loading = false;
      renderInventory();
      notify(error.message, true);
    } finally {
      setSaving(false);
    }
  }
  start();
})();
