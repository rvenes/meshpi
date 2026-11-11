"""Signed release checks and browser reminders; no installations or radio calls."""

import io
import json
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from web_helpers import javascript_asset

from meshpi import __version__
from meshpi.config import Settings
from meshpi.update import UpdateCheckError, UpdateNotice
from meshpi.web import WebApplication

ROOT = Path(__file__).resolve().parents[1]


def test_update_checks_are_shared_cached_and_retry_after_failure(monkeypatch):
    clock = [100.0]
    calls = []
    available = UpdateNotice(__version__, "1.0.0", "sudo meshpi update")

    def checker(settings):
        calls.append(settings)
        if len(calls) == 2:
            raise UpdateCheckError("mock network error")
        return available if len(calls) < 3 else None

    monkeypatch.setattr("meshpi.web.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("meshpi.web.check_for_update", checker)
    app = WebApplication(Settings())
    result = app.update_status()
    assert result == {"state": "available", "current_version": __version__,
                      "latest_version": "1.0.0", "command": "sudo meshpi update"}
    result["command"] = "changed by caller"
    clock[0] += 3599
    assert app.update_status()["command"] == "sudo meshpi update"
    assert len(calls) == 1
    clock[0] += 1
    assert app.update_status()["state"] == "available"  # Last verified release survives failure.
    clock[0] += 299
    assert app.update_status()["state"] == "available"
    assert len(calls) == 2
    clock[0] += 1
    assert app.update_status() == {"current_version": __version__, "state": "current"}
    assert len(calls) == 3


def test_concurrent_browsers_fetch_one_manifest(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def checker(_):
        calls.append(True)
        entered.set()
        assert release.wait(3)
        return None

    monkeypatch.setattr("meshpi.web.check_for_update", checker)
    app = WebApplication(Settings())
    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(app.update_status)
        assert entered.wait(2)
        others = [pool.submit(app.update_status) for _ in range(3)]
        release.set()
        assert all(f.result()["state"] == "current" for f in [first, *others])
    assert len(calls) == 1


def test_disabled_update_check_does_not_fetch(monkeypatch):
    def forbidden(_):
        pytest.fail("Disabled update check accessed the network")

    monkeypatch.setattr("meshpi.web.check_for_update", forbidden)
    assert WebApplication(Settings(update_url="")).update_status() == {
        "current_version": __version__, "state": "disabled",
    }


def test_bad_manifest_signature_cannot_trigger_web_notice(monkeypatch):
    manifest = json.loads((ROOT / "website/version.json").read_text())
    manifest["latest_version"] = "99.0.0"

    class Response(io.BytesIO):
        def geturl(self):
            return "https://updates.example/version.json"

    monkeypatch.setattr("meshpi.update.urlopen", lambda *a, **kw: Response(
        json.dumps(manifest).encode(),
    ))
    result = WebApplication(Settings()).update_status()
    assert result == {"current_version": __version__, "state": "unavailable"}
    assert "command" not in result


def run_browser(checks):
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor")
    environment = r'''
const controls = new Map();
const document = {getElementById(id) {
  if (!controls.has(id)) controls.set(id, {hidden:true,textContent:"",addEventListener(){}});
  return controls.get(id);
}};
const window = {localStorage:{getItem(){return null;}}};
const meshpiTheme = {bindToggle(){}};
let now=0;Date.now=()=>now;
const timers=[];
const setTimeout=(fn,delay)=>{const timer={fn,delay};timers.push(timer);return timer;};
const clearTimeout=timer=>{if(timer)timer.cancelled=true;};
let authEpoch=0;
let calls=0;
let reply={state:"available",current_version:"0.9.8",latest_version:"1.0.0",
  command:"sudo meshpi update"};
let api=async command=>{
  check(command==="update_check","Unexpected write or IPC command");calls++;return reply;
};
function check(ok,message){if(!ok)throw new Error(message);}
'''
    prefix = javascript_asset("app.js").split("function name(node)")[0]
    updates = javascript_asset("updates.js")
    # The automatic initial check runs while logged out, just like the real startup.
    script = environment + prefix + updates + "\nasync function scenario(){\n" + checks + r'''
}
scenario().then(()=>{
  if(typeof print==="function")print("updates-ok");else console.log("updates-ok");
}).catch(error=>{
  if(typeof printerr==="function"){printerr(error.stack);imports.system.exit(1);}
  else{console.error(error);process.exitCode=1;}
});
'''
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([engine, flag, script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "updates-ok" in result.stdout


def test_update_reminder_autocloses_once_and_keeps_settings_instructions():
    run_browser(r'''
check(calls===0,"Update information leaked before login");
state.authenticated=true;state.hostname="test-host";
await meshpiUpdates.check();
check(!$("notice").hidden && $("notice-text").textContent.includes("test-host"),
  "Missing host reminder");
check($("notice-text").textContent.includes("sudo meshpi update"),"Missing terminal command");
check(timers.length===1 && timers[0].delay===10000,"Reminder bypassed notice timer");
timers[0].fn();
check($("notice").hidden,"Reminder did not close");
check(!$("update-instructions").hidden && $("update-command").textContent==="sudo meshpi update",
  "Closing reminder removed instructions");
check($("settings-button").textContent.includes("Ny versjon"),"Missing persistent settings hint");
await meshpiUpdates.check();check(calls===1,"Browser polling was not throttled");
now+=900000;await meshpiUpdates.check();
check(calls===2 && $("notice").hidden,"Same version repeatedly interrupted the user");
reply={...reply,latest_version:"1.0.1",command:"meshpi update"};
now+=900000;autoCloseNotices=false;await meshpiUpdates.check();
check(!$("notice").hidden && timers.length===1,"Manual notice preference ignored");
notice();reply={state:"current",current_version:"1.0.1"};
now+=900000;await meshpiUpdates.check();
check($("update-instructions").hidden && $("settings-button").textContent==="Innstillingar",
  "Updated installation retained stale update instructions");
''')


def test_update_failure_and_auth_races_do_not_disrupt_existing_notice():
    run_browser(r'''
state.authenticated=true;notice("Handling starta");
await meshpiUpdates.check();
check($("notice-text").textContent==="Handling starta","Update replaced an action notice");
notice();now+=900000;await meshpiUpdates.check();
check(!$("notice").hidden,"Deferred reminder never appeared");
notice();now+=900000;api=async()=>{throw new Error("offline");};
await meshpiUpdates.check();
check($("notice").hidden && $("update-status").textContent.includes("Prøver igjen"),
  "Network failure disrupted chat");
let finish;now+=300000;api=()=>new Promise(resolve=>{finish=resolve;});
const pending=meshpiUpdates.check();state.authenticated=false;authEpoch++;meshpiUpdates.reset();
finish(reply);await pending;
check($("update-instructions").hidden && $("update-command").textContent==="",
  "Late response restored update information after logout");
check($("notice").hidden,"Late response showed a logged-out reminder");
''')
