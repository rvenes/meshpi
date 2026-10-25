"""Exercise session recovery and uncertain writes without a server or a radio."""

import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "meshpi/web_assets/app.js"


def run_javascript(checks, *, startup=False):
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for nettverkskontrollen")
    source = APP.read_text()
    prefix = source.split("async function safe", 1)[0]
    environment = r"""
const controls = new Map();
const document = {documentElement: {dataset: {basePath: "/meshpi"}},
  visibilityState: "visible", getElementById(id) {
    if (!controls.has(id)) controls.set(id, {textContent: "", addEventListener() {}});
    return controls.get(id);
  }};
const meshpiTheme = {bindToggle() {}};
let counter = 0;
const crypto = {getRandomValues(bytes) {bytes.fill(++counter); return bytes;}};
let fetch;
let AbortController;
function reply(status, data) {return {status, ok: status === 200, json: async () => data};}
function check(value, message) {if (!value) throw new Error(message);}
async function failure(fn) {try {await fn();} catch (error) {return error;}
  throw new Error("Expected an error");}
"""
    if not startup:
        prefix += "\nfunction invalidateHistorySettings() {}\n"
    if startup:
        prefix += "\nasync function start()" + source.split(
            "async function start()", 1,
        )[1].split("safe(start);", 1)[0]
        environment += r"""
const timers = []; let refreshes = 0; let streams = 0; let intervals = 0;
const setTimeout = (callback, delay) => timers.push({callback, delay});
const clearTimeout = () => {};
const setInterval = () => {intervals++;};
const refresh = async () => {refreshes++;};
const safe = async fn => fn();
class EventSource {constructor() {streams++;}}
"""
    script = environment + prefix + "\nasync function scenario() {\n" + checks + r"""
}
scenario().then(() => {
  if (typeof print === "function") print("network-ok"); else console.log("network-ok");
}).catch(error => {
  if (typeof printerr === "function") {printerr(error.stack); imports.system.exit(1);}
  else {console.error(error); process.exitCode = 1;}
});
"""
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([engine, flag, script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "network-ok" in result.stdout, result.stderr


def test_concurrent_reads_renew_one_session_and_retry_after_restart():
    run_javascript(r"""
state.csrf = "old-session";
let sessions = 0; let reads = 0;
fetch = async (url, options) => {
  if (url.endsWith("/session")) {
    sessions++; return reply(200, {ok: true, data: {csrf: "new-session"}});
  }
  reads++;
  return options.headers["X-MeshPi-CSRF"] === "old-session"
    ? reply(403, {ok: false, error: "Last sida på nytt"})
    : reply(200, {ok: true, data: {restored: true}});
};
const results = await Promise.all([
  api("status"), api("nodes"), api("channels"), api("conversations"),
]);
check(results.every(result => result.restored), "Not all reads recovered");
check(sessions === 1 && reads === 8, "Session refresh storm or repeated requests");
check(counter === 0, "Recovery allocated write IDs");
""")


def test_header_identifies_host_and_current_gateway_without_stale_node_names():
    run_javascript(r"""
fetch = async () => reply(200, {ok: true, data: {csrf: "session", hostname: "test-host"}});
await renewSession();
check($("host-name").textContent === "MeshPi test-host", "Missing server hostname");
check($("gateway-name").textContent === "Privat meshklient", "Invented a gateway");
state.nodes = [
  {node_id: "!aaaaaaaa", long_name: "Gateway <A>", short_name: "A"},
  {node_id: "!bbbbbbbb", short_name: "B"},
];
state.status = {state: "tilkopla", local_node_id: "!aaaaaaaa"};
renderIdentity();
check($("gateway-name").textContent === "Node: Gateway <A>", "Missing literal node name");
check($("gateway-name").title.includes("!aaaaaaaa"), "Missing full node ID");
state.status.local_node_id = "!bbbbbbbb";
renderIdentity();
check($("gateway-name").textContent === "Node: B", "Gateway switch retained the old name");
state.status.local_node_id = "!cccccccc";
renderIdentity();
check($("gateway-name").textContent === "Node: !cccccccc", "Missing node ID fallback");
state.status.state = "frakopla";
renderIdentity();
check($("gateway-name").textContent === "Privat meshklient", "Disconnected node still shown");
state.status = {};
renderIdentity();
check($("host-name").textContent === "MeshPi test-host", "Lost host on daemon failure");
check($("gateway-name").textContent === "Privat meshklient", "Stale node on daemon failure");
fetch = async () => reply(200, {ok: true, data: {csrf: "session-2", hostname: "new-host"}});
await renewSession();
check($("host-name").textContent === "MeshPi new-host", "Host not refreshed with session");
""")


def test_rejected_write_renews_session_without_replaying_send():
    run_javascript(r"""
state.csrf = "old-session";
let writes = 0;
fetch = async (url, options) => {
  if (url.endsWith("/session")) return reply(200, {ok: true, data: {csrf: "new-session"}});
  writes++;
  return reply(403, {ok: false, error: "Last sida på nytt"});
};
const error = await failure(() => api("send_dm", {text: "mock"}));
check(!error.uncertain && writes === 1, "Rejected send was replayed or marked in flight");
check(state.csrf === "new-session", "Session was not renewed");
check(!error.message.includes("Last sida på nytt"), "Recovery asked user to discard their draft");
""")


@pytest.mark.parametrize("failure_mode", [
    "network", "invalid_json", "invalid_body", "timeout", "ipc",
])
def test_lost_or_uncertain_write_reply_never_retries(failure_mode):
    run_javascript("const mode = " + repr(failure_mode) + ";\n" + r"""
state.csrf = "session";
let writes = 0;
fetch = async () => {
  writes++;
  if (mode === "network") throw new TypeError("Failed to fetch");
  if (mode === "invalid_json") return {status: 200, ok: true, json() {throw new SyntaxError();}};
  if (mode === "invalid_body") return reply(200, null);
  return reply(mode === "timeout" ? 408 : 503, {ok: false, error: "Ukjend sendestatus"});
};
const error = await failure(() => api("send_dm", {text: "mock"}, "retained-operation-id"));
check(error.uncertain && writes === 1, "An uncertain send was retried or treated as rejected");
check(!error.message.includes("Failed to fetch"), "Raw English network error escaped");
""")


def test_operation_lookup_recovers_session_but_never_sends():
    run_javascript(r"""
state.csrf = "old-session";
const paths = [];
fetch = async (url, options) => {
  paths.push(url);
  if (url.endsWith("/session")) return reply(200, {ok: true, data: {csrf: "new-session"}});
  check(url.endsWith("/operation"), "Status lookup attempted an IPC command");
  check(JSON.parse(options.body).operation_id === "retained-operation-id",
    "Lost operation identity");
  return options.headers["X-MeshPi-CSRF"] === "old-session"
    ? reply(403, {ok: false}) : reply(200, {ok: true, data: {state: "unknown"}});
};
const result = await webRequest("operation", {operation_id: "retained-operation-id"});
check(result.state === "unknown" && paths.length === 3, "Unexpected reconciliation");
check(counter === 0, "Lookup created a new operation");
""")


def test_startup_network_failure_schedules_reconnection():
    run_javascript(r"""
fetch = async () => {throw new TypeError("Failed to fetch");};
await start();
check(timers.length === 1 && timers[0].delay === 3000, "Failed startup cannot recover");
fetch = async () => reply(200, {ok: true, data: {csrf: "recovered"}});
await timers[0].callback();
check(refreshes === 1 && streams === 1 && intervals === 1,
  "Recovery duplicated polling or streams");
""", startup=True)


def test_history_writes_are_tracked_and_never_retried():
    run_javascript(r"""
state.csrf = "session";
for(const command of ["history_settings_set","history_cleanup"]) {
  let calls=0;
  fetch=async(url,options)=>{
    calls++;
    check(Boolean(options.headers["X-MeshPi-Operation"]),"History write lacks operation ID");
    throw new Error("connection lost");
  };
  const error=await failure(()=>api(command,{confirmed:true}));
  check(error.uncertain && calls===1,"Destructive request retried or uncertainty lost");
}
""")


def test_stalled_response_body_times_out_as_uncertain_without_retry():
    run_javascript(r"""
state.csrf = "session";
let expire; let clearCalls = 0; let sends = 0; let rejectBody;
AbortController = class {
  constructor() {this.signal = {};}
  abort() {rejectBody(new Error("AbortError"));}
};
globalThis.setTimeout = (callback, delay) => {
  check(delay === 45000, "Unexpected timeout"); expire = callback; return 1;
};
globalThis.clearTimeout = () => {clearCalls++;};
fetch = async () => {
  sends++;
  return {ok: true, status: 200,
    json: () => new Promise((resolve, reject) => {rejectBody = reject;}),
  };
};
const pending = failure(() => api("send_dm", {text: "mock"}, "retained-operation-id"));
await Promise.resolve(); await Promise.resolve(); expire();
const error = await pending;
check(error.uncertain && sends === 1 && clearCalls === 1,
  "Timeout retried a send, lost uncertainty or leaked its timer");
""")


def test_expired_login_rejects_send_without_retry_and_preserves_draft():
    run_javascript(r"""
state.csrf = "session"; state.authenticated = true;
state.drafts.set("dm:test", "Privat utkast");
$("message-text").value = "Privat utkast";
let closed = 0; eventStream = {close() {closed++;}};
let sends = 0;
fetch = async () => {sends++; return reply(401, {ok:false, error:"Logg inn"});};
const error = await failure(() => api("send_dm", {text:"Privat utkast"}));
check(!error.uncertain && error.unauthenticated && sends === 1, "Rejected send replayed");
check(state.authenticated === false && closed === 1 && eventStream === null,
  "Expired session kept stream");
check($("message-text").value === "Privat utkast" &&
  state.drafts.get("dm:test") === "Privat utkast",
  "Draft lost at expiry");
await failure(() => api("nodes"));
check(sends === 1, "Locked client continued data access");
""")


def test_anonymous_startup_does_not_fetch_data_or_open_event_stream():
    run_javascript(r"""
fetch = async () => reply(200, {ok:true, data:{csrf:"preauth", authenticated:false}});
await start();
check(refreshes === 0 && streams === 0 && intervals === 0,
  "Anonymous startup fetched private data");
check(state.authenticated === false, "Anonymous session treated as authenticated");
""", startup=True)


def test_private_reply_after_logout_is_discarded():
    run_javascript(r"""
state.csrf = "session"; state.authenticated = true;
let resolve;
fetch = () => new Promise(done => {resolve=done;});
const pending = failure(() => api("nodes"));
lockSession();
resolve(reply(200, {ok:true,data:[{node_id:"!12345678"}]}));
const error = await pending;
check(error.unauthenticated, "Late private data was exposed after logout");
""")


def test_tab_title_uses_server_name_and_survives_authentication_changes():
    run_javascript(r"""
document.documentElement.dataset.serverName = "pi-west";
applySession({authenticated: false});
check(document.title === "MeshPi · pi-west", "HTTPS title missing before login");
applySession({authenticated: true, hostname: "generic-pi"});
check(document.title === "MeshPi · pi-west", "HTTPS identity changed after login");
lockSession();
check(document.title === "MeshPi · pi-west", "HTTPS identity lost on logout");
document.documentElement.dataset.serverName = "";
applySession({authenticated: true, hostname: "local <pi>"});
check(document.title === "MeshPi · local <pi>", "Local title did not use literal hostname");
lockSession();
check(document.title === "MeshPi · Privat meshklient", "Private local hostname retained on logout");
""")
