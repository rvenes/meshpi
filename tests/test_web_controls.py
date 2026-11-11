"""Exercise browser controls without IPC or radio access."""

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from web_helpers import javascript_asset

ROOT = Path(__file__).resolve().parents[1]


def test_lan_http_writes_work_without_secure_context_random_uuid():
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for LAN-kontrollen")
    environment = r"""
const document = {
  documentElement: {dataset: {basePath: "/meshpi"}},
  getElementById: () => ({addEventListener() {}}),
};
const meshpiTheme = {bindToggle() {}};
let randomCalls = 0;
// HTTP on LAN exposes getRandomValues, but not randomUUID.
const crypto = {getRandomValues(bytes) {bytes.fill(++randomCalls); return bytes;}};
const calls = [];
const fetch = async (url, options) => {
  calls.push({url, ...options});
  return {ok: true, json: async () => ({ok: true, data: {}})};
};
function check(condition, message) {if (!condition) throw new Error(message);}
"""
    checks = r"""
async function scenario() {
  state.csrf = "test-csrf";
  await api("status");
  check(randomCalls === 0, "Reads should not allocate operation IDs");
  await api("send_dm", {text: "mock"});
  await api("send_dm", {text: "another mock"});
  const first = calls[1].headers["X-MeshPi-Operation"];
  const second = calls[2].headers["X-MeshPi-Operation"];
  check(/^[0-9a-f]{32}$/.test(first), "LAN operation ID should be accepted by the server");
  check(first !== second && randomCalls === 2, "Each write needs a fresh random ID");
  await api("send_dm", {text: "mock"}, first);
  check(calls[3].headers["X-MeshPi-Operation"] === first, "Explicit ID must be preserved");
  check(randomCalls === 2, "Reusing an ID should not allocate a new one");
  check(calls.every(call => call.headers["X-MeshPi-CSRF"] === "test-csrf"), "Missing CSRF");
  check(calls.length === 4, "Unexpected retry");
}
scenario().then(() => {
  if (typeof print === "function") print("lan-http-ok");
  else console.log("lan-http-ok");
});
"""
    source = javascript_asset("app.js").split("async function safe", 1)[0]
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([engine, flag, environment + source + checks],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "lan-http-ok" in result.stdout, result.stderr


def test_error_can_be_dismissed_and_shown_again():
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for varselkontrollen")

    class Controls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.controls = {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                self.controls[attrs["id"]] = {"tag": tag, **attrs}

    controls = Controls()
    controls.feed((ROOT / "meshpi/web_assets/index.html").read_text())
    assert controls.controls["dismiss-notice"]["tag"] == "button"
    assert controls.controls["dismiss-notice"]["aria-label"] == "Lukk varsel"
    assert controls.controls["notice-text"]["role"] == "alert"
    setup = "const controls = " + json.dumps(controls.controls) + ";\n"
    environment = r"""
const elements = new Map(Object.entries(controls).map(([id, attrs]) => [id, {
  listeners: {}, hidden: "hidden" in attrs, disabled: false, textContent: "",
  addEventListener(event, callback) {this.listeners[event] = callback;},
}]));
const document = {
  documentElement: {dataset: {basePath: "/meshpi"}},
  getElementById: id => elements.get(id),
  addEventListener() {},
};
const meshpiTheme = {bindToggle() {}};
let failures = 0;
const message = "Sambandet blei brote.";
const fetch = () => new Promise(() => {});
function check(condition, message) {if (!condition) throw new Error(message);}
"""
    checks = r"""
async function scenario() {
  const banner = elements.get("notice");
  const text = elements.get("notice-text");
  const close = elements.get("dismiss-notice");
  check(!elements.has("export"), "Removed export button returned");
  check(banner.hidden, "Notice should start hidden");
  for (let i = 0; i < 2; i++) {
    await safe(async()=>{failures++;throw new Error(message);});
    check(!banner.hidden && text.textContent === message, "Error was not shown");
    close.listeners.click();
    check(banner.hidden && text.textContent === "", "Close did not dismiss error");
  }
  check(failures === 2, "Dismissing the error retried the request");
}
scenario().then(() => {
  if (typeof print === "function") print("controls-ok");
  else console.log("controls-ok");
});
"""
    source = javascript_asset("app.js")
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([engine, flag, setup + environment + source + checks],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "controls-ok" in result.stdout, result.stderr


@pytest.mark.parametrize("action", ["traceroute", "position_exchange", "node_info_exchange"])
def test_node_action_notice_expires_without_hiding_newer_notices(action):
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for varselkontrollen")
    environment = r"""
const elements = new Map();
const document = {
  documentElement: {dataset: {basePath: "/meshpi"}},
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, {
      hidden: true, textContent: "", listeners: {},
      addEventListener(event, callback) {this.listeners[event] = callback;},
    });
    return elements.get(id);
  },
  addEventListener() {},
};
const meshpiTheme = {bindToggle() {}};
const fetch = () => new Promise(() => {});
const confirm = () => true;
let now = 0, timerId = 0;
const timers = new Map();
function setTimeout(callback, delay) {
  const id = ++timerId;
  timers.set(id, {callback, deadline: now + delay});
  return id;
}
function clearTimeout(id) {timers.delete(id);}
function advance(ms) {
  now += ms;
  for (const [id, timer] of [...timers]) {
    if (timer.deadline <= now) {timers.delete(id); timer.callback();}
  }
}
function check(condition, message) {if (!condition) throw new Error(message);}
"""
    checks = "const action = " + json.dumps(action) + ";\n" + r"""
async function scenario() {
  const requests = [];
  api = async (command, fields) => {requests.push({command, ...fields}); return {};};
  renderNode = async () => {};
  state.node = {node_id: "!00000002"};
  state.nodeGatewayId = "!aaaaaaaa";
  const banner = $("notice"), text = $("notice-text");
  await nodeAction(action);
  check(requests[0].command === "node_action" && requests[0].action === action,
    "Wrong node action");
  check(!banner.hidden && text.textContent.includes("Handling starta"), "Notice missing");
  advance(9999);
  check(!banner.hidden, "Notice disappeared too soon");
  advance(1);
  check(banner.hidden && text.textContent === "", "Notice did not expire after ten seconds");

  await nodeAction(action);
  advance(5000);
  await nodeAction(action);
  advance(5000);
  check(!banner.hidden, "Previous timer hid the next action notice");
  advance(5000);
  check(banner.hidden, "Repeated action notice did not expire");

  await nodeAction(action);
  advance(500);
  await safe(async () => {throw new Error("Sambandet blei brote.");});
  advance(9500);
  check(!banner.hidden && text.textContent === "Sambandet blei brote.",
    "Action timer hid a newer error");
  advance(500);check(banner.hidden,"Error notice did not expire after ten seconds");

  await nodeAction(action);
  $("dismiss-notice").listeners.click();
  check(banner.hidden && noticeTimer === null, "Manual dismissal did not cancel expiry");
  notice("Eit nytt varsel");
  advance(9999);
  check(!banner.hidden && text.textContent === "Eit nytt varsel", "Dismissed timer survived");
  advance(1);check(banner.hidden,"New notice did not expire");
  check(requests.length === 5, "Expiry retried a node action");
}
scenario().then(() => {
  if (typeof print === "function") print("notice-expiry-ok");
  else console.log("notice-expiry-ok");
}).catch(error => {
  if (typeof printerr === "function") printerr(error.stack || error.message);
  else console.error(error);
});
"""
    source = javascript_asset("app.js")
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([engine, flag, environment + source + checks],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "notice-expiry-ok" in result.stdout, result.stderr
