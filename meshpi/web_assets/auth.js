"use strict";

// WebAuthn's JSON helpers are not available in every supported browser yet.
const meshpiAuth = (() => {
  const byId = id => document.getElementById(id);
  let session = {authenticated: false};
  let hooks;
  let busy = false;
  function decode(value) {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(padded + "=".repeat((4 - padded.length % 4) % 4)), c => c.charCodeAt(0)).buffer;
  }
  function encode(value) {
    return btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function options(json, creation) {
    const result = {...json, challenge: decode(json.challenge)};
    if (creation) result.user = {...json.user, id: decode(json.user.id)};
    const key = creation ? "excludeCredentials" : "allowCredentials";
    if (json[key]) result[key] = json[key].map(item => ({...item, id: decode(item.id)}));
    return result;
  }
  function credentialJSON(credential) {
    const response = {};
    for (const key of ["clientDataJSON", "attestationObject", "authenticatorData", "signature", "userHandle"]) {
      if (credential.response[key] != null) response[key] = encode(credential.response[key]);
    }
    if (credential.response.getTransports) response.transports = credential.response.getTransports();
    return {id: credential.id, rawId: encode(credential.rawId), type: credential.type, response,
      clientExtensionResults: credential.getClientExtensionResults?.() || {}};
  }
  function supported() {
    return session.passkeys_available && typeof PublicKeyCredential !== "undefined" &&
      typeof navigator.credentials?.get === "function" && typeof navigator.credentials?.create === "function";
  }
  function render() {
    if (!hooks) return;
    document.body.dataset.auth = session.authenticated ? "unlocked" : "locked";
    byId("auth-login").hidden = !!session.authenticated;
    byId("auth-manage").hidden = !session.authenticated;
    byId("auth-close").hidden = !session.authenticated;
    byId("auth-title").textContent = session.authenticated ? "Innlogging og nøklar" : "Logg inn i MeshPi";
    byId("auth-passkey").disabled = busy || !supported();
    byId("auth-register").disabled = busy || !supported();
    byId("auth-code-submit").disabled = busy;
    byId("auth-logout").disabled = busy;
    const link = byId("auth-canonical");
    link.hidden = true;
    if (!session.passkeys_available && session.canonical_url) {
      try {
        const url = new URL(session.canonical_url);
        if (url.protocol === "https:" || (url.protocol === "http:" && url.hostname === "localhost")) {
          link.href = url.href;
          link.textContent = `Opne innloggingsadressa: ${url.host}`;
          link.hidden = false;
        }
      } catch (_) { /* Ignore a malformed server URL. */ }
    }
    byId("auth-passkey-note").textContent = supported()
      ? "Passkeys er knytte til denne adressa. Du kan bruke Bitwarden, Apple Passord, Windows Hello eller ein tryggleiksnøkkel. Legg gjerne til ein ekstra nøkkel."
      : session.passkeys_available
        ? "Denne nettlesaren støttar ikkje passkeys. Bruk ein oppdatert nettlesar eller ein eingongskode."
        : "Passkeys krev den faste HTTPS-adressa. På localhost brukar du ein eingongskode frå terminalen på MeshPi-maskina.";
    if (!session.authenticated) {
      for (const dialog of document.querySelectorAll("dialog[open]")) {
        if (dialog.id !== "auth-dialog") dialog.close();
      }
      if (!byId("auth-dialog").open) byId("auth-dialog").showModal();
    }
  }
  function update(data) {
    const newlyAuthenticated = !session.authenticated && data.authenticated;
    session = {...data};
    render();
    if (hooks && newlyAuthenticated && byId("auth-dialog").open) byId("auth-dialog").close();
  }
  function lock() {
    update({...session, authenticated: false});
    byId("auth-keys").replaceChildren();
    byId("auth-error").textContent = "Logg inn for å halde fram. Utkastet ditt er bevart i denne fana.";
  }
  async function action(fn) {
    if (busy) return;
    busy = true;
    byId("auth-error").textContent = "";
    render();
    try { await fn(); }
    catch (error) {
      byId("auth-error").textContent = ["NotAllowedError", "AbortError"].includes(error.name)
        ? "Innlogginga blei avbroten eller tok for lang tid. Du kan prøve igjen."
        : error.message || "Kunne ikkje fullføre innlogginga.";
    } finally { busy = false; render(); }
  }
  async function loggedIn(data) {
    hooks.session(data);
    update(data);
    if (!data.authenticated) throw new Error("Innlogginga blei ikkje godkjend.");
    byId("auth-code").value = "";
    byId("auth-dialog").close();
    await hooks.resume();
  }
  async function keys() {
    const data = await hooks.keys();
    const root = byId("auth-keys");
    root.replaceChildren();
    for (const key of data.keys || []) {
      const row = document.createElement("div");
      row.className = "auth-key";
      const label = document.createElement("span");
      label.textContent = key.name;
      const remove = document.createElement("button");
      remove.className = "quiet";
      remove.type = "button";
      remove.textContent = "Fjern";
      remove.setAttribute("aria-label", `Fjern nøkkelen ${key.name}`);
      remove.addEventListener("click", () => action(async () => {
        if (!confirm(`Fjerne nøkkelen «${key.name}»? Alle nettlesarar blir logga ut. Du kan få ein ny eingongskode frå terminalen.`)) return;
        await hooks.request("auth/remove", {id: key.id});
        hooks.lock();
        await hooks.renew();
      }));
      row.append(label, remove);
      root.append(row);
    }
    if (!root.children.length) root.textContent = "Ingen passkeys er registrerte enno.";
  }
  function init(callbacks) {
    hooks = callbacks;
    byId("auth-dialog").addEventListener("cancel", event => {if (!session.authenticated) event.preventDefault();});
    byId("auth-close").addEventListener("click", () => byId("auth-dialog").close());
    byId("auth-button").addEventListener("click", () => action(async () => {
      await hooks.renew();
      if (!byId("auth-dialog").open) byId("auth-dialog").showModal();
      if (session.authenticated) await keys();
    }));
    byId("auth-code-form").addEventListener("submit", event => {
      event.preventDefault();
      const code = byId("auth-code").value.trim();
      if (!code) return;
      return action(async () => {
        try {
          await hooks.renew();
          await loggedIn(await hooks.request("auth/code", {code}));
        }
        finally { byId("auth-code").value = ""; }
      });
    });
    byId("auth-passkey").addEventListener("click", () => action(async () => {
      await hooks.renew();
      const begin = await hooks.request("auth/login/begin", {});
      const credential = await navigator.credentials.get({publicKey: options(begin.options, false)});
      if (!credential) throw new Error("Ingen nøkkel blei vald.");
      await loggedIn(await hooks.request("auth/login/finish", {challenge_id: begin.challenge_id, credential: credentialJSON(credential)}));
    }));
    byId("auth-register-form").addEventListener("submit", event => {
      event.preventDefault();
      const name = byId("auth-name").value.trim();
      if (!name) return;
      return action(async () => {
        const begin = await hooks.request("auth/register/begin", {});
        const credential = await navigator.credentials.create({publicKey: options(begin.options, true)});
        if (!credential) throw new Error("Ingen nøkkel blei oppretta.");
        await hooks.request("auth/register/finish", {challenge_id: begin.challenge_id, name, credential: credentialJSON(credential)});
        byId("auth-name").value = "";
        await keys();
        byId("auth-error").textContent = "Nøkkelen er lagra.";
      });
    });
    byId("auth-logout").addEventListener("click", () => action(async () => {
      // Keep the UI locked even if the logout reply is lost.
      try { await hooks.request("auth/logout", {}); }
      finally { hooks.lock(); }
    }));
    render();
  }
  return {init, update, lock, options, credentialJSON};
})();
