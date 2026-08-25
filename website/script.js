const LANGUAGE_STORAGE_KEY = "meshpi-language";
const SUPPORTED_LANGUAGES = new Set(["nn", "en"]);
const PAGE_METADATA = {
  nn: {
    title: "MeshPi – Meshtastic i terminalen",
    description:
      "MeshPi er ein terminalklient for Meshtastic på Linux, Raspberry Pi, macOS og Windows.",
    copied: "Kopiert",
    latest: "Siste versjon",
    published: "publisert",
    manifestError: "Versjonsmanifestet kunne ikkje lastast.",
    dateLocale: "nn-NO",
  },
  en: {
    title: "MeshPi – Meshtastic in your terminal",
    description:
      "MeshPi is a terminal client for Meshtastic on Linux, Raspberry Pi, macOS, and Windows.",
    copied: "Copied",
    latest: "Latest version",
    published: "published",
    manifestError: "The version manifest could not be loaded.",
    dateLocale: "en-GB",
  },
};

let activeLanguage = "en";
let manifestState = { status: "loading" };

function browserLanguage() {
  const requested = navigator.languages?.length
    ? navigator.languages
    : [navigator.language || ""];
  return requested.some((language) => /^(nn|nb|no)(-|$)/i.test(language))
    ? "nn"
    : "en";
}

function savedLanguage() {
  try {
    const language = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return SUPPORTED_LANGUAGES.has(language) ? language : null;
  } catch {
    return null;
  }
}

function saveLanguage(language) {
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    // Language switching still works when storage is unavailable.
  }
}

function rememberNynorskContent() {
  document.querySelectorAll("[data-en]").forEach((element) => {
    element.dataset.nn = element.textContent.trim();
  });
  document.querySelectorAll("[data-en-html]").forEach((element) => {
    element.dataset.nnHtml = element.innerHTML.trim();
  });
  document.querySelectorAll("[data-aria-en]").forEach((element) => {
    element.dataset.ariaNn = element.getAttribute("aria-label") || "";
  });
}

function renderManifestStatus() {
  const status = document.getElementById("manifest-status");
  const metadata = PAGE_METADATA[activeLanguage];
  if (manifestState.status === "loaded") {
    const published = new Date(manifestState.publishedAt).toLocaleDateString(
      metadata.dateLocale
    );
    status.textContent = `${metadata.latest}: ${manifestState.version} · ${metadata.published} ${published}`;
  } else if (manifestState.status === "error") {
    status.textContent = metadata.manifestError;
  }
}

function applyLanguage(language, { persist = false } = {}) {
  activeLanguage = SUPPORTED_LANGUAGES.has(language) ? language : "en";
  const metadata = PAGE_METADATA[activeLanguage];

  document.documentElement.lang = activeLanguage;
  const languageSuffix = activeLanguage === "nn" ? "Nn" : "En";
  const title = document.body.dataset[`title${languageSuffix}`];
  const description = document.body.dataset[`description${languageSuffix}`];
  document.title = title || metadata.title;
  document.querySelector('meta[name="description"]').content =
    description || metadata.description;

  document.querySelectorAll("[data-en]").forEach((element) => {
    element.textContent = element.dataset[activeLanguage];
  });
  document.querySelectorAll("[data-en-html]").forEach((element) => {
    element.innerHTML = element.dataset[`${activeLanguage}Html`];
  });
  document.querySelectorAll("[data-aria-en]").forEach((element) => {
    const suffix = activeLanguage === "nn" ? "Nn" : "En";
    element.setAttribute("aria-label", element.dataset[`aria${suffix}`]);
  });
  document.querySelectorAll("[data-language]").forEach((button) => {
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.language === activeLanguage)
    );
  });
  document.querySelectorAll("[data-copying='true']").forEach((button) => {
    button.textContent = metadata.copied;
  });

  renderManifestStatus();
  if (persist) saveLanguage(activeLanguage);
}

rememberNynorskContent();
applyLanguage(savedLanguage() || browserLanguage());

document.querySelectorAll("[data-language]").forEach((button) => {
  button.addEventListener("click", () => {
    applyLanguage(button.dataset.language, { persist: true });
  });
});

const tabs = [...document.querySelectorAll("[data-platform-tab]")];
const panels = [...document.querySelectorAll("[data-platform-panel]")];

function selectPlatform(platform) {
  tabs.forEach((tab) => {
    const selected = tab.dataset.platformTab === platform;
    tab.setAttribute("aria-selected", String(selected));
  });
  panels.forEach((panel) => {
    const selected = panel.dataset.platformPanel === platform;
    panel.hidden = !selected;
    panel.classList.toggle("active", selected);
  });
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => selectPlatform(tab.dataset.platformTab));
});

const platform = (
  navigator.userAgentData?.platform ||
  navigator.platform ||
  ""
).toLowerCase();
if (platform.includes("mac")) {
  selectPlatform("macos");
} else if (platform.includes("win")) {
  selectPlatform("windows");
}

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copyTarget);
    if (!target) return;
    try {
      await navigator.clipboard.writeText(target.textContent.trim());
      button.dataset.copying = "true";
      button.textContent = PAGE_METADATA[activeLanguage].copied;
      setTimeout(() => {
        delete button.dataset.copying;
        button.textContent = button.dataset[activeLanguage];
      }, 1600);
    } catch {
      window.getSelection()?.selectAllChildren(target);
    }
  });
});

fetch("version.json", { cache: "no-store" })
  .then((response) => {
    if (!response.ok) throw new Error("manifest");
    return response.json();
  })
  .then((manifest) => {
    manifestState = {
      status: "loaded",
      version: manifest.latest_version,
      publishedAt: manifest.published_at,
    };
    renderManifestStatus();
  })
  .catch(() => {
    manifestState = { status: "error" };
    renderManifestStatus();
  });
