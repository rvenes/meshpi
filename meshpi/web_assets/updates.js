"use strict";

const meshpiUpdates = (() => {
  let nextCheck = 0;
  let checking = false;
  let notifiedVersion = "";

  function reset() {
    nextCheck = 0;
    $("update-status").textContent = webText("Logg inn for å sjå oppdateringar.");
    $("update-instructions").hidden = true;
    $("update-command").textContent = "";
    $("settings-button").textContent = webText("Innstillingar");
  }

  async function check() {
    if (checking || state.authenticated !== true || Date.now() < nextCheck) return;
    checking = true;
    const epoch = authEpoch;
    try {
      const data = await api("update_check");
      if (epoch !== authEpoch || state.authenticated !== true) return;
      nextCheck = Date.now() + (data.state === "unavailable" ? 300000 : 900000);
      const available = data.state === "available";
      $("update-instructions").hidden = !available;
      $("update-command").textContent = available ? data.command : "";
      $("settings-button").textContent = available ? webText("Innstillingar · Ny versjon") : webText("Innstillingar");
      if (available) {
        $("update-status").textContent = webText`MeshPi ${data.latest_version} er tilgjengeleg. Du køyrer ${data.current_version}.`;
        // Leave an existing action/error notice alone; the settings reminder stays visible.
        if (notifiedVersion !== data.latest_version && $("notice").hidden) {
          notice(webText`Ny versjon av MeshPi: ${data.latest_version}. Opne terminalen på ${state.hostname || webText("MeshPi-maskina")} og køyr ${data.command}. Sjå Innstillingar → Oppdateringar.`);
          notifiedVersion = data.latest_version;
        }
      } else if (data.state === "current") {
        $("update-status").textContent = webText`MeshPi ${data.current_version} – ingen nyare stabil versjon er tilgjengeleg.`;
      } else if (data.state === "disabled") {
        $("update-status").textContent = webText("Oppdateringskontrollen er slått av på denne maskina.");
      } else {
        $("update-status").textContent = webText("Kunne ikkje sjå etter ny versjon. Prøver igjen seinare.");
      }
    } catch (_) {
      // Update service failures must never interrupt messages or replace action notices.
      if (epoch === authEpoch && state.authenticated === true) {
        nextCheck = Date.now() + 300000;
        $("update-status").textContent = webText("Kunne ikkje sjå etter ny versjon. Prøver igjen seinare.");
      }
    } finally {
      checking = false;
    }
  }

  if (typeof meshpiI18n !== "undefined") meshpiI18n.onChange(() => {nextCheck = 0; void check();});
  return {check, reset};
})();
void meshpiUpdates.check();
