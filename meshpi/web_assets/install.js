"use strict";

// Installation is optional; failures here must never interrupt the mesh client.
(() => {
  const button = document.getElementById("install-button");
  const dialog = document.getElementById("install-dialog");
  const nativeButton = document.getElementById("install-native");
  const status = document.getElementById("install-status");
  const mode = window.matchMedia("(display-mode: standalone)");
  const basePath = document.documentElement.dataset.basePath;
  let pendingPrompt = null;
  let prompting = false;

  function update() {
    button.hidden = mode.matches || window.navigator.standalone === true;
    nativeButton.hidden = !pendingPrompt;
  }

  update();
  if (typeof mode.addEventListener === "function") mode.addEventListener("change", update);
  button.addEventListener("click", () => dialog.showModal());
  document.getElementById("close-install").addEventListener("click", () => dialog.close());

  window.addEventListener("beforeinstallprompt", event => {
    event.preventDefault();
    pendingPrompt = event;
    status.textContent = "Nettlesaren er klar til å installere MeshPi.";
    update();
  });
  window.addEventListener("appinstalled", () => {
    pendingPrompt = null;
    update();
    button.hidden = true;
    dialog.close();
  });
  nativeButton.addEventListener("click", async () => {
    if (!pendingPrompt || prompting) return;
    const prompt = pendingPrompt;
    pendingPrompt = null;
    prompting = true;
    nativeButton.disabled = true;
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      if (choice.outcome === "accepted") dialog.close();
      else status.textContent = "Installasjonen blei avbroten. Du kan prøve igjen frå nettlesarmenyen.";
    } catch (_) {
      status.textContent = "Installasjonen kunne ikkje startast. Bruk rettleiinga for nettlesaren din.";
    } finally {
      prompting = false;
      nativeButton.disabled = false;
      update();
    }
  });

  if (!window.isSecureContext) {
    status.textContent = "Opne den private HTTPS-adressa for å installere appen på denne eininga.";
  } else if ("serviceWorker" in window.navigator) {
    window.navigator.serviceWorker.register(`${basePath}/sw.js`, {
      scope: `${basePath}/`, updateViaCache: "none",
    }).catch(() => {
      status.textContent = "Appstøtta kunne ikkje aktiverast. Last sida på nytt, eller bruk nettlesarmenyen.";
    });
  }
})();
