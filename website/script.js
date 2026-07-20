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

const platform = (navigator.userAgentData?.platform || navigator.platform || "").toLowerCase();
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
      const oldLabel = button.textContent;
      button.textContent = "Kopiert";
      setTimeout(() => {
        button.textContent = oldLabel;
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
    const status = document.getElementById("manifest-status");
    status.textContent = `Siste versjon: ${manifest.latest_version} · publisert ${new Date(
      manifest.published_at
    ).toLocaleDateString("nn-NO")}`;
  })
  .catch(() => {
    document.getElementById("manifest-status").textContent =
      "Versjonsmanifestet kunne ikkje lastast.";
  });
