"""Gateway and cooldown regressions use browser mocks, never a radio."""

import pytest
from test_web_node_removal import run_browser

from meshpi.web import validate_payload


@pytest.mark.parametrize("gateway", [None, "", "short-id", "!abcdefgh"])
def test_web_node_actions_require_the_gateway_seen_by_the_user(gateway):
    payload = {"command": "node_action", "action": "position_exchange",
               "node_id": "!11112222"}
    if gateway is not None:
        payload["expected_local_node_id"] = gateway
    with pytest.raises(ValueError):
        validate_payload(payload)


def test_node_action_keeps_the_dialog_gateway_when_another_client_switches():
    run_browser(r"""
document.querySelectorAll=()=>[];
renderNodeOverview=()=>{};
response=command=>command==="node_action_availability"?{available:true}:{};
await openNode(state.node);
state.status={state:"tilkopla",local_node_id:"!bbbbbbbb"};
requests=[];
await nodeAction("position_exchange");
const writes=requests.filter(request=>request.command==="node_action");
check(writes.length===1 && writes[0].expected_local_node_id===local,
  "Node action silently used the new gateway");
state.nodeGatewayId=null;requests=[];
await nodeAction("traceroute").then(()=>{throw new Error("Missing gateway accepted");},()=>{});
check(requests.length===0,"An unbound action reached the daemon");
""")


@pytest.mark.parametrize("action, button", [
    ("traceroute", "traceroute"), ("position_exchange", "position-exchange"),
    ("node_info_exchange", "node-info-exchange"),
])
def test_completed_node_action_recovers_after_cooldown_without_reopening(action, button):
    run_browser(r"""
document.querySelectorAll=()=>[];
renderNodeOverview=()=>{};
state.nodeGatewayId=local;
let cooldownEnd=0;
response=(command,fields)=>{
  if(command==="node_action"){cooldownEnd=now+30000;return {action_id:"mock-action"};}
  if(command==="node_action_status")return {status:"completed",result:{}};
  if(command==="node_action_availability"){
    const seconds=fields.action===ACTION?Math.max(0,Math.ceil((cooldownEnd-now)/1000)):0;
    return {available:seconds===0,cooldown_seconds:seconds,reason:seconds?"Vent":""};
  }
  return {};
};
await nodeAction(ACTION);
check($(BUTTON).disabled,"Action ignored its cooldown");
advance(3000);
for(let i=0;i<20;i++)await Promise.resolve();
check($(BUTTON).disabled,"Completed action bypassed the remaining cooldown");
advance(27000);
for(let i=0;i<20;i++)await Promise.resolve();
check(!$(BUTTON).disabled,"Action stayed disabled after cooldown expired");
check(requests.filter(request=>request.command==="node_action").length===1,
  "Cooldown refresh repeated a radio action");
$("node-dialog").close();$("node-dialog").listeners.close();
const count=requests.length;
advance(60000);
for(let i=0;i<20;i++)await Promise.resolve();
check(requests.length===count,"Closed node dialog kept polling");
""".replace("ACTION", f'"{action}"').replace("BUTTON", f'"{button}"'))


def test_late_availability_reply_cannot_change_a_new_node_dialog():
    run_browser(r"""
state.nodeGatewayId=local;
let release;
api=async()=>new Promise(resolve=>{release=resolve;});
const old=refreshNodeActionButtons();
stopNodeAvailability();
state.node={node_id:"!33334444"};state.nodeGatewayId="!bbbbbbbb";
$("traceroute").disabled=false;
release({available:false,cooldown_seconds:30});await old;
check(!$("traceroute").disabled && nodeAvailabilityTimer===null,
  "A stale response changed or scheduled the new dialog");
""")


def test_cooldown_poll_recovers_after_a_temporary_network_failure():
    run_browser(r"""
state.nodeGatewayId=local;
let offline=false;
api=async(command,fields)=>{
  requests.push({command,...fields});
  if(offline)throw Object.assign(new Error("offline"),{transient:true});
  const seconds=now<1000?1:0;
  return {available:seconds===0,cooldown_seconds:seconds};
};
await refreshNodeActionButtons();
check($("traceroute").disabled,"Initial cooldown disappeared");
offline=true;advance(1000);
for(let i=0;i<20;i++)await Promise.resolve();
check(nodeAvailabilityTimer!==null,"Network failure stopped cooldown recovery");
offline=false;advance(3000);
for(let i=0;i<20;i++)await Promise.resolve();
check(!$("traceroute").disabled && !$("position-exchange").disabled,
  "Buttons did not recover after the connection returned");
check(requests.every(request=>request.command==="node_action_availability"),
  "Read-only recovery repeated a radio action");
""")
