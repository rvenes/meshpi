import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("saved,dark,blocked,legacy", [
    (None, True, False, False),
    ("light", True, False, False),
    ("dark", False, False, False),
    ("invalid", True, False, False),
    (None, False, True, False),
    (None, False, False, True),
])
def test_theme_preference_system_changes_and_restricted_storage(saved, dark, blocked, legacy):
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for temakontrollen")
    setup = "const settings = " + json.dumps({
        "saved": saved, "dark": dark, "blocked": blocked, "legacy": legacy,
    }) + ";\n"
    environment = r"""
let stored = settings.saved;
let changed;
const media = {matches: settings.dark};
if (settings.legacy) media.addListener = callback => {changed = callback;};
else media.addEventListener = (event, callback) => {changed = callback;};
const window = {
  matchMedia: () => media,
  localStorage: {
    getItem() {if (settings.blocked) throw new Error("Restricted storage"); return stored;},
    setItem(key, value) {
      if (settings.blocked) throw new Error("Restricted storage");
      stored = value;
    },
    removeItem() {if (settings.blocked) throw new Error("Restricted storage"); stored = null;},
  },
};
const document = {documentElement: {dataset: {}}};
function check(condition, message) {if (!condition) throw new Error(message);}
"""
    checks = r"""
const initial = !settings.blocked && ["light", "dark"].includes(settings.saved)
  ? settings.saved : (settings.dark ? "dark" : "light");
check(document.documentElement.dataset.theme === initial, "Wrong initial theme");
let click;
const button = {
  setAttribute(key, value) {this[key] = value;},
  addEventListener(event, callback) {if (event === "click") click = callback;},
};
meshpiTheme.bindToggle(button);
check(button["aria-pressed"] === String(initial === "dark"), "Wrong initial button state");
const opposite = initial === "dark" ? "light" : "dark";
click();
check(document.documentElement.dataset.theme === opposite, "Button did not toggle theme");
check(meshpiTheme.getPreference() === opposite, "Button did not set an explicit choice");
if (!settings.blocked) check(stored === opposite, "Button choice was not persisted");
click();
check(document.documentElement.dataset.theme === initial, "Second click did not restore theme");
meshpiTheme.setPreference("dark");
check(document.documentElement.dataset.theme === "dark", "Dark theme not applied");
check(button["aria-pressed"] === "true", "Dark button state not applied");
check(button.title === "Byt til lys modus", "Dark button action not updated");
check(meshpiTheme.getPreference() === "dark", "Preference was not remembered in memory");
if (!settings.blocked) check(stored === "dark", "Preference was not persisted");
media.matches = false;
changed();
check(document.documentElement.dataset.theme === "dark", "System overrode an explicit choice");
meshpiTheme.setPreference("light");
check(document.documentElement.dataset.theme === "light", "Light theme not applied");
meshpiTheme.setPreference("system");
check(document.documentElement.dataset.theme === "light", "System theme not restored");
check(button["aria-pressed"] === "false", "Button did not follow system theme");
if (!settings.blocked) check(stored === null, "Explicit preference was not cleared");
media.matches = true;
changed();
check(document.documentElement.dataset.theme === "dark", "System theme change not followed");
check(button["aria-pressed"] === "true", "Button did not follow system theme change");
meshpiTheme.setPreference("invalid");
check(meshpiTheme.getPreference() === "system", "Invalid preference was accepted");
"""
    source = (ROOT / "meshpi/web_assets/theme.js").read_text()
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([engine, flag, setup + environment + source + checks],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
