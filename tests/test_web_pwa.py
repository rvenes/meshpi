"""Exercise install prompts and network-only navigation without a daemon or radio."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[1] / "meshpi" / "web_assets"


def run_javascript(source):
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for webappkontrollen")
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([engine, flag, source], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "pwa-ok" in result.stdout, result.stderr


@pytest.mark.parametrize("base_path,secure,worker,standalone,ios,rejected", [
    ("/meshpi", True, True, False, False, False),
    ("", True, True, False, False, False),
    ("/apps/meshpi", True, True, False, False, False),
    ("/meshpi", True, True, True, False, False),
    ("/meshpi", True, True, False, True, False),
    ("/meshpi", False, True, False, False, False),
    ("/meshpi", True, False, False, False, False),
    ("/meshpi", True, True, False, False, True),
])
def test_install_prompt_manual_fallback_and_optional_worker(
    base_path, secure, worker, standalone, ios, rejected,
):
    settings = json.dumps({
        "base": base_path, "secure": secure, "worker": worker, "standalone": standalone,
        "ios": ios, "rejected": rejected,
    })
    environment = "const settings = " + settings + ";\n" + r"""
function check(value, message) {if (!value) throw new Error(message);}
function success() {if (typeof print === "function") print("pwa-ok"); else console.log("pwa-ok");}
const elements = new Map();
for (const id of ["install-button", "install-dialog", "install-native",
    "install-status", "close-install"]) {
  elements.set(id, {hidden: true, disabled: false, listeners: {}, textContent: "", open: false,
    addEventListener(event, callback) {this.listeners[event] = callback;},
    showModal() {this.open = true;}, close() {this.open = false;},
  });
}
const document = {getElementById: id => elements.get(id),
  documentElement: {dataset: {basePath: settings.base}}};
const listeners = {};
const media = {matches: settings.standalone,
  addEventListener(event, callback) {this.changed = callback;}};
let registration = null;
const window = {isSecureContext: settings.secure, navigator: {standalone: settings.ios},
  matchMedia: () => media, addEventListener(event, callback) {listeners[event] = callback;}};
if (settings.worker) window.navigator.serviceWorker = {register(url, options) {
  registration = {url, ...options};
  return settings.rejected ? Promise.reject(new Error("Blocked")) : Promise.resolve({});
}};
"""
    checks = r"""
async function scenario() {
  await Promise.resolve();
  const button = elements.get("install-button");
  const native = elements.get("install-native");
  const dialog = elements.get("install-dialog");
  const status = elements.get("install-status");
  check(button.hidden === (settings.standalone || settings.ios), "Wrong standalone visibility");
  check(native.hidden, "Native prompt offered before browser event");
  check(!!registration === (settings.secure && settings.worker),
    "Worker registered in wrong context");
  if (registration) {
    check(registration.url === settings.base + "/sw.js", "Worker escaped mount");
    check(registration.scope === settings.base + "/", "Worker has wrong scope");
    check(registration.updateViaCache === "none", "Worker update can use stale cache");
  }
  if (!settings.secure || settings.rejected) {
    check(status.textContent.length > 0, "Missing manual fallback");
  }
  button.listeners.click();
  check(dialog.open, "Help did not open");
  elements.get("close-install").listeners.click();
  check(!dialog.open, "Help did not close");
  let calls = 0;
  let prevented = false;
  let resolveChoice;
  const choice = new Promise(resolve => {resolveChoice = resolve;});
  listeners.beforeinstallprompt({preventDefault() {prevented = true;},
    async prompt() {calls++;}, userChoice: choice});
  check(prevented && !native.hidden, "Native install not offered");
  button.listeners.click();
  const pending = native.listeners.click();
  await native.listeners.click();
  check(calls === 1 && native.disabled, "Repeated click prompted twice");
  resolveChoice({outcome: "dismissed"});
  await pending;
  check(native.hidden && !native.disabled && dialog.open, "Dismissal lost manual help");
  check(status.textContent.includes("avbroten"), "Dismissal not explained");
  listeners.beforeinstallprompt({preventDefault() {},
    async prompt() {throw new Error("Blocked");}});
  await native.listeners.click();
  check(native.hidden && !native.disabled, "Failed prompt left control stuck");
  check(status.textContent.includes("rettleiinga"), "Missing prompt failure help");
  listeners.beforeinstallprompt({preventDefault() {}, async prompt() {},
    userChoice: Promise.resolve({outcome: "accepted"})});
  await native.listeners.click();
  check(!dialog.open, "Accepted prompt left help open");
  media.matches = true;
  media.changed();
  check(button.hidden, "Standalone change ignored");
  listeners.appinstalled();
  check(button.hidden && native.hidden && !dialog.open, "Installed state not reflected");
}
scenario().then(success).catch(error => {throw error;});
"""
    run_javascript(environment + (ASSETS / "install.js").read_text() + checks)


def test_worker_only_handles_app_navigation_and_never_caches_or_replays_requests():
    environment = r"""
function check(value, message) {if (!value) throw new Error(message);}
const listeners = {};
let claimed = false;
const self = {registration: {scope: "https://app.example/apps/meshpi/"},
  clients: {claim() {claimed = true; return Promise.resolve();}},
  addEventListener(event, callback) {listeners[event] = callback;}};
// GJS lacks browser URL/Response APIs. Supply their small data-only surface for this test.
class URL {
  constructor(value) {
    const match = /^(https?:\/\/[^/]+)(\/[^?#]*)/.exec(value);
    this.origin = match[1]; this.pathname = match[2];
  }
}
class Response {constructor(body, options) {this.body = body; Object.assign(this, options);}}
const caches = new Proxy({}, {get() {throw new Error("Private cache access");}});
let network = true;
let requests = [];
const onlineResponse = {status: 200, body: "Fresh page"};
function fetch(request) {
  requests.push(request);
  return network ? Promise.resolve(onlineResponse) : Promise.reject(new Error("Offline"));
}
function dispatch(path, method = "GET", mode = "navigate", origin = "https://app.example") {
  const event = {request: {url: origin + path, method, mode},
    respondWith(response) {this.response = response;}};
  listeners.fetch(event);
  return event.response;
}
"""
    checks = r"""
async function scenario() {
  await new Promise(resolve => listeners.activate({waitUntil: resolve}));
  check(claimed, "Existing app pages not controlled");
  const online = await dispatch("/apps/meshpi/");
  check(online === onlineResponse && requests.length === 1, "Navigation did not use network");
  network = false;
  const offline = await dispatch("/apps/meshpi/?view=chat");
  check(offline.status === 503 && offline.body.includes("MeshPi er fråkopla"), "No offline page");
  check(offline.body.includes('href="/apps/meshpi/"'), "Retry escaped app scope");
  check(offline.headers["Cache-Control"] === "no-store", "Offline page cached");
  check(offline.headers["Content-Security-Policy"].includes("default-src 'none'"),
    "Unsafe fallback");
  const count = requests.length;
  for (const path of ["/", "/other/", "/apps/meshpi-other/", "/apps/meshpi/api/session",
      "/apps/meshpi/api/events", "/apps/meshpi/api/request", "/apps/meshpi/api/export",
      "/apps/meshpi/app.js", "/apps/meshpi/manifest.webmanifest"]) {
    check(dispatch(path) === undefined, "Worker intercepted private data or a sibling app");
  }
  check(dispatch("/apps/meshpi/", "POST") === undefined, "Worker intercepted a write");
  check(dispatch("/apps/meshpi/", "GET", "cors") === undefined, "Worker intercepted a fetch");
  check(dispatch("/apps/meshpi/", "GET", "navigate", "https://other.example") === undefined,
    "Worker intercepted another origin");
  check(requests.length === count, "Worker replayed or fetched an excluded request");
  check(Object.keys(listeners).sort().join() === "activate,fetch", "Unexpected background work");
}
scenario().then(() => {
  if (typeof print === "function") print("pwa-ok"); else console.log("pwa-ok");
}).catch(error => {throw error;});
"""
    run_javascript(environment + (ASSETS / "sw.js").read_text() + checks)
