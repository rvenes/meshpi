"use strict";

// Runs before the stylesheet so a saved dark preference does not flash light content.
const meshpiTheme = (() => {
  const key = "meshpi-web-theme";
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const choices = ["system", "light", "dark"];
  let preference = "system";
  let toggleButton;
  try {
    const saved = window.localStorage.getItem(key);
    if (choices.includes(saved)) preference = saved;
  } catch (_) {
    // Private browsing or storage restrictions must not prevent theme changes.
  }

  function apply() {
    document.documentElement.dataset.theme = preference === "system"
      ? (media.matches ? "dark" : "light") : preference;
    if (toggleButton) {
      const dark = document.documentElement.dataset.theme === "dark";
      toggleButton.setAttribute("aria-pressed", String(dark));
      toggleButton.title = dark ? "Byt til lys modus" : "Byt til mørk modus";
    }
  }
  apply();
  if (typeof media.addEventListener === "function") media.addEventListener("change", apply);
  else if (typeof media.addListener === "function") media.addListener(apply);

  function setPreference(value) {
    if (!choices.includes(value)) return;
    preference = value;
    try {
      if (value === "system") window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (_) {
      // Keep the selected theme in memory when persistence is unavailable.
    }
    apply();
  }

  return {
    getPreference: () => preference,
    setPreference,
    bindToggle(button) {
      toggleButton = button;
      button.addEventListener("click", () => setPreference(
        document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
      apply();
    },
  };
})();
