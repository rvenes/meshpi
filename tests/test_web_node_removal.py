"""Web node removal is mocked: these tests never contact a daemon or a radio."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from web_helpers import javascript_asset

from meshpi.config import Settings
from meshpi.web import WebApplication, validate_payload

ROOT = Path(__file__).resolve().parents[1]
DELETE = {
    "command": "remove_nodes", "node_ids": ["!11112222"],
    "expected_local_node_id": "!aaaaaaaa", "confirmed": True,
}


@pytest.mark.parametrize("change", [
    {"confirmed": False}, {"confirmed": 1}, {"node_ids": []},
    {"node_ids": ["!11112222", "!33334444"]}, {"node_ids": "!11112222"},
    {"node_ids": ["!aaaaaaaa"]}, {"node_ids": ["!ffffffff"]},
    {"node_ids": ["!00000001"]}, {"expected_local_node_id": "short-id"},
])
def test_removal_validation_requires_one_remote_node_and_confirmation(change):
    with pytest.raises(ValueError):
        validate_payload(DELETE | change)


@pytest.mark.parametrize("missing", ["confirmed", "node_ids", "expected_local_node_id"])
def test_removal_validation_requires_all_fields(missing):
    with pytest.raises(ValueError):
        validate_payload({k: v for k, v in DELETE.items() if k != missing})


def test_removal_uses_existing_ipc_and_deduplicates_writes(monkeypatch, tmp_path):
    calls = []

    def request(settings, payload, **kwargs):
        calls.append(payload)
        return {"ok": True, "data": {"action_id": "mock-removal", "status": "started"}}

    monkeypatch.setattr("meshpi.web.request", request)
    app = WebApplication(Settings(database_path=tmp_path / "mock.db"))
    first = app.dispatch(DELETE, "node-removal-operation")
    assert first == app.dispatch(DELETE, "node-removal-operation")
    assert first[0] == 200
    assert calls == [DELETE]
    assert app.operation({"operation_id": "node-removal-operation"})["data"]["state"] == "completed"
    with pytest.raises(ValueError):
        app.dispatch(DELETE, "")
    assert len(calls) == 1


def run_browser(checks, *, saved=None, blocked=False):
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor")
    html = (ROOT / "meshpi/web_assets/index.html").read_text()
    ids = re.findall(r'id="([^"]+)"', html)
    setup = "const fixture = " + json.dumps({"ids": ids, "saved": saved, "blocked": blocked})
    environment = r""";
class Element {
  constructor() {this.children=[];this.listeners={};this.hidden=true;this.open=false;
    this.textContent="";this.value="";this.disabled=false;}
  addEventListener(event,callback) {this.listeners[event]=callback;}
  setAttribute() {}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=children;}
  showModal() {this.open=true;}
  close() {this.open=false;}
}
const elements=new Map(fixture.ids.map(id=>[id,new Element()]));
const document={documentElement:{dataset:{basePath:"/meshpi"}},
  getElementById:id=>elements.get(id),createElement:()=>new Element(),addEventListener(){}};
let stored=fixture.saved;
const window={localStorage:{
  getItem(){if(fixture.blocked)throw new Error("blocked");return stored;},
  setItem(key,value){if(fixture.blocked)throw new Error("blocked");stored=value;},
}};
const meshpiTheme={bindToggle(){}};
const fetch=()=>new Promise(()=>{});
const AbortController=undefined;
const crypto={getRandomValues(bytes){bytes.fill(1);return bytes;}};
const timers=new Map();let nextTimer=0,now=0;
function setTimeout(callback,delay){
  const id=++nextTimer;timers.set(id,{callback,end:now+delay});return id;
}
function clearTimeout(id){timers.delete(id);}
function advance(ms){now+=ms;for(const [id,timer] of [...timers]){
  if(timer.end<=now){timers.delete(id);timer.callback();}
}}
function check(value,message){if(!value)throw new Error(message);}
const local="!aaaaaaaa",remote="!11112222";
let confirmations=[],approved=true,requests=[],lookups=[],refreshes=0;
const confirm=text=>{confirmations.push(text);return approved;};
const action={action:"remove_nodes",action_id:"mock-action",local_node_id:local,
  node_id:remote,status:"started"};
let response=()=>({...action});
let lookup=()=>({state:"unknown"});
"""
    script = setup + environment + javascript_asset("app.js") + r"""
api=async(command,fields={},operationId="")=>{
  requests.push({command,...fields,operationId});return response(command,fields);};
webRequest=async(path,payload)=>{lookups.push({path,payload});return lookup();};
refresh=async()=>{refreshes++;};
state.status={state:"tilkopla",local_node_id:local};
state.nodes=[{node_id:local,long_name:"Gateway"},{node_id:remote,long_name:"Vald testnode"}];
state.node=state.nodes[1];$("node-dialog").showModal();updateNodeRemovalButton();
async function scenario(){
""" + checks + r"""
}
scenario().then(()=>{
  if(typeof print==="function")print("browser-ok");else console.log("browser-ok");
})
.catch(error=>{if(typeof printerr==="function"){printerr(error.stack);imports.system.exit(1);}
else{console.error(error);process.exitCode=1;}});
"""
    result = subprocess.run([engine, "-e" if Path(engine).name == "node" else "-c", script],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "browser-ok" in result.stdout, result.stderr


def test_cancelled_or_local_node_removal_never_reaches_ipc():
    run_browser(r"""
approved=false;await removeSelectedNode();
check(requests.length===0&&$("node-dialog").open,"Cancelled removal changed data");
check(confirmations[0].includes("Vald testnode")&&confirmations[0].includes(remote)
  &&confirmations[0].includes(local)&&confirmations[0].includes("bevarte"),"Unclear confirmation");
state.node=state.nodes[0];updateNodeRemovalButton();approved=true;
await removeSelectedNode();
check($("node-delete").disabled&&requests.length===0&&confirmations.length===1,
  "Local gateway was allowed to be deleted");
""")


def test_confirmed_removal_waits_for_verification_and_prevents_double_clicks():
    run_browser(r"""
await removeSelectedNode();
check(requests.length===1&&requests[0].command==="remove_nodes"&&requests[0].confirmed===true,
  "Confirmation did not start exactly one removal");
check(requests[0].node_ids.join()===remote&&requests[0].expected_local_node_id===local
  &&requests[0].operationId,"Removal lost its target, gateway or operation ID");
check($("node-delete").disabled&&$("node-dialog").open&&refreshes===0,
  "Started operation was treated as completed");
await removeSelectedNode();check(requests.length===1,"Double click repeated removal");
response=()=>({...action,status:"completed",result:{contacts:[{node_id:remote,status:"removed"}]}});
await checkNodeRemoval();
check(requests[1].command==="node_action_status"&&refreshes===1&&!$("node-dialog").open,
  "Confirmed result did not close and refresh the list");
check(nodeRemoval===null&&$("notice-text").textContent.includes("Historikken er bevart"),
  "Verified completion was not shown");
""")


def test_uncertain_removal_is_reconciled_without_resending():
    run_browser(r"""
response=()=>{throw Object.assign(new Error("Sambandsbrot"),{uncertain:true});};
await removeSelectedNode();
check(nodeRemoval.phase==="uncertain"&&!$("node-delete").disabled
  &&$("node-delete").textContent==="Kontroller sletting","No safe recovery control");
lookup=()=>({state:"completed",result:{ok:true,data:{...action,status:"completed",
  result:{contacts:[{node_id:remote,status:"removed"}]}}}});
await removeSelectedNode();
check(requests.length===1&&lookups.length===1&&lookups[0].path==="operation",
  "Reconciliation repeated the destructive request");
check(nodeRemoval===null&&refreshes===1,"Recovered completion was not applied");
""")


def test_failed_or_wrong_removal_never_removes_a_new_selection():
    run_browser(r"""
await removeSelectedNode();
state.node={node_id:"!33334444"};
response=()=>({...action,status:"failed",error:"Noden finst framleis"});
await checkNodeRemoval();
check($("node-dialog").open&&state.node.node_id==="!33334444"&&nodeRemoval===null,
  "Failure closed a newer node selection");
state.node=state.nodes[1];
response=()=>({...action,node_id:"!33334444",status:"completed"});
await removeSelectedNode();
check(nodeRemoval.phase==="uncertain"&&$("node-dialog").open,
  "Wrong-node response was accepted as success");
""")


def test_lost_removal_status_keeps_checks_read_only_after_gateway_change():
    run_browser(r"""
await removeSelectedNode();
state.status.local_node_id="!bbbbbbbb";
response=()=>{throw new Error("Statusen er mellombels utilgjengeleg");};
await checkNodeRemoval();
check(nodeRemoval.phase==="uncertain"&&$("node-dialog").open,"Lost status hid pending removal");
response=()=>({...action,status:"completed",result:{contacts:[{node_id:remote,status:"removed"}]}});
await checkNodeRemoval();
check($("node-dialog").open&&state.node.node_id===remote,
  "Old gateway completion closed the node in the new gateway");
check(requests.filter(r=>r.command==="remove_nodes").length===1,"Status recovery repeated removal");
""")


@pytest.mark.parametrize("saved,blocked", [(None, False), ("manual", False), (None, True)])
def test_notice_auto_close_setting_is_persisted_and_applies_to_all_notices(saved, blocked):
    run_browser(r"""
check($("notice-auto-close").checked===(fixture.saved!=="manual"),"Saved preference ignored");
for(const message of ["Informasjon","Noden svarte ikkje","Ei uavklart sending"]){
  notice(message);advance(9999);check(!$("notice").hidden,"Notice expired too soon");
  advance(1);check($("notice").hidden===(fixture.saved!=="manual"),"Wrong notice lifetime");
}
notice("Sambandsvarsel","connection");connectionRestored();
check($("notice").hidden===(fixture.saved!=="manual"),"Recovery ignored manual closing preference");
$("notice-auto-close").checked=false;$("notice-auto-close").listeners.change();
notice("Ståande varsel");advance(20000);check(!$("notice").hidden,"Manual choice did not persist");
if(!fixture.blocked)check(stored==="manual","Manual preference not saved");
$("notice-auto-close").checked=true;$("notice-auto-close").listeners.change();
advance(10000);check($("notice").hidden,"Enabling auto close did not schedule current notice");
if(!fixture.blocked)check(stored==="auto","Automatic preference not saved");
""", saved=saved, blocked=blocked)
