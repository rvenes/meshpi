"""Browser settings exercise mocked requests only; no daemon or radio access."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_settings(checks):
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor")
    markup = (ROOT / "meshpi/web_assets/index.html").read_text()
    ids = re.findall(r'id="([^"]+)"', markup)
    environment = "const ids=" + json.dumps(ids) + ";\n" + r"""
const elements=new Map(ids.map(id=>[id,{
  value:"",hidden:false,disabled:false,textContent:"",listeners:{},open:false,
  addEventListener(event,fn){this.listeners[event]=fn;},
  showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},
}]));
const document={documentElement:{dataset:{basePath:"/meshpi"}},
  getElementById:id=>elements.get(id),addEventListener(){}};
const meshpiTheme={bindToggle(){}};
const fetch=()=>new Promise(()=>{});
let approved=true;let confirmations=[];
const confirm=message=>{confirmations.push(message);return approved;};
function check(ok,message){if(!ok)throw new Error(message);}
const original={local_node_id:"!00000001",retention_days:0,needs_review:true,
  max_messages:50000,counts:{messages:20,telemetry_samples:3,positions:2,node_actions:1}};
let calls=[];let responder;
"""
    source = (ROOT / "meshpi/web_assets/app.js").read_text()
    scenario = r"""
api=async(command,fields={})=>{calls.push({command,...fields});return responder(command,fields);};
refresh=async()=>{};updateComposer=()=>{};
responder=async()=>original;
async function scenario(){
""" + checks + r"""
}
scenario().then(()=>{
  if(typeof print==="function")print("settings-ok");else console.log("settings-ok");
}).catch(error=>{
  if(typeof printerr==="function"){printerr(error.stack);imports.system.exit(1);}
  else {console.error(error);process.exitCode=1;}
});
"""
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([engine, flag, environment + source + scenario],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "settings-ok" in result.stdout, result.stderr


def test_removed_controls_and_accessible_settings():
    markup = (ROOT / "meshpi/web_assets/index.html").read_text()
    assert 'id="export"' not in markup
    assert "Eksporter historikk" not in markup
    assert "prototype" not in markup
    assert 'aria-labelledby="settings-title"' in markup
    assert 'for="retention-days"' in markup
    assert 'for="cleanup-scope"' in markup
    assert "eigen database" in markup


def test_existing_policy_is_unchanged_until_explicit_confirmation():
    run_settings(r"""
await openHistorySettings();
check(calls.length===1 && calls[0].command==="history_settings","Opening settings wrote data");
check($("retention-days").value==="0" && !$("retention-review").hidden,"Legacy protection missing");
check($("retention-limit").textContent.includes("50"),"Message cap hidden");
$("retention-days").value="custom";$("retention-custom").value="14";
approved=false;await saveRetention();check(calls.length===1,"Cancelled policy was sent");
approved=true;responder=async(command,fields)=>({...original,retention_days:fields.retention_days,needs_review:false});
await saveRetention();
check(calls[1].command==="history_settings_set" &&
  calls[1].expected_local_node_id===original.local_node_id
  && calls[1].retention_days===14 && calls[1].confirmed===true,
  "Policy write lost gateway or consent");
check(confirmations[1].includes("Eksisterande og framtidig"),"Destructive policy impact hidden");
check($("retention-days").value==="custom" && $("retention-review").hidden,"Saved policy missing");
$("retention-custom").value="1.5";await saveRetention();
check(calls.length===2 &&
  $("settings-status").textContent.includes("heilt tal"),
  "Invalid days accepted");
""")


def test_cleanup_preview_confirmation_and_no_automatic_retry():
    run_settings(r"""
await openHistorySettings();
$("cleanup-scope").value="history";$("cleanup-days").value="30";
responder=async(command,fields)=>command==="history_cleanup_preview" ? {
  ...fields,local_node_id:original.local_node_id,cleanup_token:"private-preview",counts:original.counts,
  expires_in:300,cutoff:"2026-09-01T00:00:00Z",
} : original;
await previewCleanup();
check(!$("cleanup-preview").hidden && !$("confirm-cleanup").disabled,"Preview unavailable");
check(calls[1].scope==="history" && calls[1].older_than_days===30,"Wrong cleanup range");
approved=false;await confirmCleanup();check(calls.length===2,"Cancelled cleanup sent");
approved=true;responder=async()=>{const error=new Error("offline");
  error.uncertain=true;
  throw error;};
await confirmCleanup();
check(calls.length===3 && calls[2].command==="history_cleanup" && calls[2].confirmed===true,
  "Cleanup must execute only once");
check(calls[2].cleanup_token==="private-preview" &&
  !("token" in calls[2]),
  "Preview token lost or collided with IPC auth");
check(!historySettings.preview &&
  $("confirm-cleanup").disabled,
  "Uncertain operation can be replayed");
check($("settings-status").textContent.includes("ikkje send på nytt automatisk"),
  "Uncertainty hidden");
await confirmCleanup();check(calls.length===3,"Repeated click replayed cleanup");
""")


def test_preview_invalidated_by_selection_gateway_expiry_and_late_response():
    run_settings(r"""
await openHistorySettings();$("cleanup-scope").value="messages";$("cleanup-days").value="7";
const preview={local_node_id:original.local_node_id,
  cleanup_token:"old",expires_in:300,counts:{messages:4}};
responder=async()=>preview;await previewCleanup();
$("cleanup-days").value="0";$("cleanup-days").listeners.change();
check(!historySettings.preview && $("confirm-cleanup").disabled,"Selection retained stale preview");
await previewCleanup();historySettings.preview.expiresAt=0;
const count=calls.length;await confirmCleanup();
check(calls.length===count &&
  $("settings-status").textContent.includes("utgått"),
  "Expired preview sent");
let release;responder=async()=>new Promise(resolve=>{release=resolve;});
const pending=previewCleanup();invalidateHistorySettings();release(preview);await pending;
check(!historySettings.preview &&
  !historySettings.data &&
  $("preview-cleanup").disabled,
  "Late preview survived gateway change");
responder=async()=>original;await openHistorySettings();
responder=async()=>preview;await previewCleanup();lockSession();
check(!historySettings.preview && !historySettings.data,"Logout retained destructive state");
""")


def test_successful_cleanup_refreshes_counts_without_changing_retention():
    run_settings(r"""
await openHistorySettings();$("cleanup-scope").value="messages";$("cleanup-days").value="0";
responder=async(command)=>{
  if(command==="history_cleanup_preview")return {local_node_id:original.local_node_id,
    cleanup_token:"one",expires_in:300,counts:{messages:20}};
  if(command==="history_cleanup")return {local_node_id:original.local_node_id,
    deleted:{messages:20}};
  if(command==="history_settings")return {...original,counts:{messages:0}};
  throw new Error("Unexpected write");
};
await previewCleanup();await confirmCleanup();
check(calls.map(call=>call.command).join(",")===
  "history_settings,history_cleanup_preview,history_cleanup,history_settings",
  "Unexpected request sequence");
check($("settings-status").textContent.startsWith("Sletta: 20"),"Result not visible");
check(historySettings.data.counts.messages===0 &&
  historySettings.data.needs_review,
  "Cleanup altered policy");
""")
