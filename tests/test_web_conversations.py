"""Conversation safety and scrolling with a mock DOM; no IPC or radio access."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", [
    r'''
    await selectConversation(dm);
    $("message-text").value="Privat utkast";
    const pending=deferred();responses.messages=()=>pending.promise;
    const changing=selectConversation(publicChat);
    check($("chat-title").textContent==="Public","Title must change immediately");
    check($("message-text").value==="","Private draft followed public selection");
    check(!state.selectionReady&&$("send-button").disabled,"Loading selection allowed sends");
    $("message-text").value="mock";
    await $("send-form").listeners.submit(event);
    check(!calls.some(c=>c.command.startsWith("send_")),"Loading selection sent text");
    pending.reject(new Error("Mock load error"));await changing.catch(()=>{});
    check(!state.selectionReady&&$("send-button").disabled,"Failed selection allowed sends");
    responses.messages=()=>[];await selectConversation(dm);
    check($("message-text").value==="Privat utkast","Private draft was lost");
    ''',
    r'''
    const first=deferred(),second=deferred(),third=deferred();
    const pending=[first,second,third];responses.messages=()=>pending.shift().promise;
    const a=selectConversation(dm),b=selectConversation(publicChat),c=selectConversation(dm);
    first.resolve([{id:1,text:"stale"}]);await a;
    check(!state.selectionReady,"Earlier visit to same route unlocked sending");
    second.reject(new Error("Stale public failure"));await b;
    check(!state.selectionReady,"Stale failure changed active load");
    third.resolve([{id:3,text:"latest"}]);await c;
    check(state.selectionReady&&state.messageKey.includes("latest"),"Latest load was not applied");
    ''',
    r'''
    await selectConversation(dm);$("message-text").value="Privat utkast";
    await $("archive").listeners.click();
    check(state.selected===null&&$("message-text").value==="","Archive retained recipient/text");
    check($("chat-title").textContent==="Vel ei samtale","Archive retained heading");
    check($("send-button").disabled,"Archive allowed sends");
    check(state.drafts.get(dm.conversation)==="Privat utkast","Archive lost draft");
    await refresh();check(state.selected===null,"Refresh automatically selected public");
    await selectConversation(publicChat);
    check($("message-text").value==="","Archived DM draft leaked to public");
    ''',
    r'''
    await selectConversation(dm);$("message-text").value="Privat utkast";
    const pending=deferred();responses.archive_conversation=()=>pending.promise;
    const archiving=$("archive").listeners.click();
    await selectConversation(publicChat);$("message-text").value="Public utkast";
    pending.resolve({});await archiving;
    check(state.selected.conversation===publicChat.conversation,"Archive cleared newer selection");
    check($("message-text").value==="Public utkast","Archive discarded newer draft");
    check(state.drafts.get(dm.conversation)==="Privat utkast","Archive lost original draft");
    ''',
    r'''
    await refresh();await selectConversation(dm);$("message-text").value="Gateway-utkast";
    responses.status=()=>{throw new Error("Offline");};await refresh();
    responses.status=()=>({state:"tilkopla",history_local_node_id:"other"});
    await refresh();
    check(state.selected===null&&$("message-text").value==="","Gateway switched with active draft");
    check(state.drafts.get(dm.conversation)==="Gateway-utkast","Gateway switch lost draft");
    check($("send-button").disabled,"Gateway switch allowed sends");
    ''',
    r'''
    responses.messages=()=>[{id:1,text:"first"}];await selectConversation(dm);
    const root=$("messages");root.scrollHeight=2000;root.clientHeight=400;root.scrollTop=200;
    responses.messages=()=>[{id:1,text:"first"},{id:2,text:"new"}];await loadMessages(true);
    check(root.scrollTop===200,"Refresh pulled user away from older history");
    check(!$("new-messages").hidden,"New-message jump was missing");
    $("new-messages").listeners.click();
    check(root.scrollTop===root.scrollHeight&&$("new-messages").hidden,"New-message jump failed");
    root.scrollTop=1580;
    responses.messages=()=>[{id:1,text:"first"},{id:2,text:"new",status:"levert"}];
    await loadMessages(true);
    check(root.scrollTop===root.scrollHeight,"Near-bottom refresh did not follow");
    root.scrollTop=200;
    responses.messages=()=>{throw new Error("Offline");};await loadMessages().catch(()=>{});
    responses.messages=()=>[{id:1,text:"first"},{id:2,text:"new",status:"sett"}];
    await loadMessages(true);check(root.scrollTop===200,"Recovered refresh jumped to bottom");
    ''',
    r'''
    await selectConversation(dm);
    $("message-text").value="a".repeat(235)+"ø";$("message-text").listeners.input();
    check(!$("send-button").disabled,"237-byte message was blocked");
    check($("message-bytes").textContent==="237 / 237 byte","UTF-8 count is wrong");
    $("message-text").value+="😀";$("message-text").listeners.input();
    check($("send-button").disabled,"Oversize UTF-8 message was enabled");
    check($("message-bytes").textContent==="241 / 237 byte","Emoji byte count is wrong");
    await $("send-form").listeners.submit(event);
    check(!calls.some(c=>c.command.startsWith("send_")),"Oversize submission reached daemon");
    state.status={};$("message-text").value="mock";await $("send-form").listeners.submit(event);
    check(!calls.some(c=>c.command.startsWith("send_")),"Disconnected submission reached daemon");
    ''',
    r'''
    await selectConversation(dm);$("message-text").value="Trygg mock-DM";
    responses.send_dm=()=>{throw Object.assign(new Error("Dropped response"),{uncertain:true});};
    await $("send-form").listeners.submit(event);
    const operation=pendingSends.get(dm.conversation);
    const sent=calls.filter(c=>c.command==="send_dm");
    check(operation?.uncertain&&sent.length===1,"Dropped response did not retain operation");
    check(operation.id===sent[0].operationId&&operation.text===sent[0].text,
      "Original operation ID or text was lost");
    check($("message-text").disabled&&$("send-button").disabled,"Unknown send was editable");
    check(!$("send-resolution").hidden,"Unknown send controls were hidden");
    await $("send-form").listeners.submit(event);
    await selectConversation(publicChat);await selectConversation(dm);
    check($("message-text").value===operation.text,"Route switch lost uncertain draft");
    await $("send-form").listeners.submit(event);
    check(calls.filter(c=>c.command==="send_dm").length===1,"Unknown send was retried");
    responses.operation=()=>({state:"completed",status:200,result:{ok:true,data:{}}});
    await $("check-send").listeners.click();
    check(lookups.length===1&&lookups[0].path==="operation"
      &&lookups[0].payload.operation_id===operation.id,"Check used wrong endpoint or ID");
    check(calls.filter(c=>c.command.startsWith("send_")).length===1,"Check replayed send");
    check(!pendingSends.has(dm.conversation)&&!state.drafts.has(dm.conversation),
      "Confirmed success retained pending operation or draft");
    check($("message-text").value===""&&!$("message-text").disabled,
      "Confirmed success did not restore clean composer");
    ''',
    r'''
    await selectConversation(dm);$("message-text").value="Uavklart mock-DM";
    responses.send_dm=()=>{throw Object.assign(new Error("Dropped"),{uncertain:true});};
    await $("send-form").listeners.submit(event);
    const operation=pendingSends.get(dm.conversation);
    check($("release-send").disabled,"Release was enabled before first status lookup");
    await $("release-send").listeners.click();
    check(pendingSends.get(dm.conversation)===operation&&confirmations.length===0
      &&lookups.length===0,"Release before first status lookup bypassed safety guard");
    responses.operation=()=>({state:"pending"});
    await $("check-send").listeners.click();
    check($("release-send").disabled,"Pending send allowed manual release");
    await $("release-send").listeners.click();
    check(pendingSends.get(dm.conversation)===operation&&confirmations.length===0,
      "Pending send was released or asked for confirmation");
    responses.operation=()=>({state:"unknown"});
    await $("check-send").listeners.click();
    check($("message-text").disabled&&$("send-button").disabled,
      "Unknown operation after restart allowed send");
    check(!$("release-send").disabled,"Unknown status did not allow explicit review");
    confirmation=false;await $("release-send").listeners.click();
    check(pendingSends.get(dm.conversation)===operation,"Declined release erased operation");
    confirmation=true;await $("release-send").listeners.click();
    check(!pendingSends.has(dm.conversation)&&!$("message-text").disabled,
      "Confirmed manual release did not unlock editor");
    check($("message-text").value===operation.text,"Manual release erased draft");
    check(calls.filter(c=>c.command.startsWith("send_")).length===1,
      "Manual release or status checks transmitted a message");
    check(lookups.length===4&&lookups.every(c=>c.path==="operation"
      &&c.payload.operation_id===operation.id),"Status checks changed original operation");
    ''',
    r'''
    await selectConversation(dm);$("message-text").value="Avvist mock-DM";
    responses.send_dm=()=>{throw Object.assign(new Error("Known validation error"),
      {uncertain:false});};
    await $("send-form").listeners.submit(event);
    check(!pendingSends.has(dm.conversation),"Known rejection left uncertain operation");
    check($("message-text").value==="Avvist mock-DM"&&!$("message-text").disabled,
      "Known rejection lost draft or kept it locked");
    check(!$("send-button").disabled&&$("send-resolution").hidden,
      "Known rejection did not restore editable composer");
    check(calls.filter(c=>c.command.startsWith("send_")).length===1&&lookups.length===0,
      "Known rejection retried or checked operation");
    ''',
    r'''
    await selectConversation(publicChat);$("message-text").value="Anna utkast";
    await selectConversation(dm);$("message-text").value="Sendt mock-DM";
    const pending=deferred();responses.send_dm=()=>pending.promise;
    const sending=$("send-form").listeners.submit(event);
    await selectConversation(publicChat);
    check($("message-text").value==="Anna utkast","Switch lost other route draft");
    pending.resolve({});await sending;
    check(state.selected.conversation===publicChat.conversation,
      "Send completion changed current recipient");
    check($("message-text").value==="Anna utkast"
      &&state.drafts.get(publicChat.conversation)==="Anna utkast",
      "Send completion erased another route draft");
    check(!state.drafts.has(dm.conversation)&&!pendingSends.has(dm.conversation),
      "Successful send retained completed draft");
    await selectConversation(dm);check($("message-text").value==="","Sent draft returned");
    ''',
    r'''
    await selectConversation(dm);$("message-text").value="Sendt mock-DM";
    const pending=deferred();responses.send_dm=()=>pending.promise;
    const sending=$("send-form").listeners.submit(event);
    await selectConversation(publicChat);
    state.drafts.set(dm.conversation,"Nyare utkast");
    pending.resolve({});await sending;
    check(state.drafts.get(dm.conversation)==="Nyare utkast",
      "Completion erased a newer draft on the original route");
    await selectConversation(dm);
    check($("message-text").value==="Nyare utkast","Newer draft was not restored");
    ''',
    r'''
    await selectConversation(dm);$("message-text").value="Uavklart mock-DM";
    responses.send_dm=()=>{throw Object.assign(new Error("Dropped"),{uncertain:true});};
    await $("send-form").listeners.submit(event);
    const operation=pendingSends.get(dm.conversation);
    responses.operation=()=>({state:"unknown"});await $("check-send").listeners.click();
    responses.operation=()=>({state:"pending"});await $("release-send").listeners.click();
    check(pendingSends.get(dm.conversation)===operation&&confirmations.length===0,
      "Fresh pending result allowed release or confirmation");
    check($("release-send").disabled&&$("message-text").disabled,
      "Fresh pending result left composer or release unlocked");
    responses.operation=()=>({state:"unknown"});await $("check-send").listeners.click();
    responses.operation=()=>{throw new Error("Status lookup failed");};
    await $("release-send").listeners.click();
    check(pendingSends.get(dm.conversation)===operation&&confirmations.length===0,
      "Failed fresh lookup allowed release or confirmation");
    check(calls.filter(c=>c.command.startsWith("send_")).length===1,
      "Rechecking release transmitted a message");
    check(lookups.length===4&&lookups.every(c=>c.path==="operation"
      &&c.payload.operation_id===operation.id),"Release did not recheck original operation");
    ''',
    r'''
    await selectConversation(dm);$("message-text").value="Uavklart mock-DM";
    responses.send_dm=()=>{throw Object.assign(new Error("Dropped"),{uncertain:true});};
    await $("send-form").listeners.submit(event);
    const operation=pendingSends.get(dm.conversation);
    responses.operation=()=>({state:"unknown"});await $("check-send").listeners.click();
    const lookup=deferred();responses.operation=()=>lookup.promise;
    const releasing=$("release-send").listeners.click();
    await selectConversation(publicChat);await selectConversation(dm);
    lookup.resolve({state:"unknown"});await releasing;
    check(pendingSends.get(dm.conversation)===operation&&confirmations.length===0,
      "Release result from previous selection unlocked composer or asked for confirmation");
    check($("message-text").disabled,"Stale release result unlocked composer");
    check(calls.filter(c=>c.command.startsWith("send_")).length===1,
      "Stale release transmitted a message");
    ''',
], ids=[
    "selection-failure", "stale-load", "archive", "archive-race", "gateway", "scroll", "byte-limit",
    "send-uncertain-success", "send-pending-release", "send-known-error",
    "send-other-draft", "send-newer-draft", "release-fresh-status", "release-selection-race",
])
def test_conversation_safety(scenario):
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for samtalekontrollen")
    html = (ROOT / "meshpi/web_assets/index.html").read_text()
    ids = re.findall(r'\bid="([^"]+)"', html)
    environment = "const ids=" + json.dumps(ids) + ";\n" + r'''
class Element {
  constructor(){
    this.children=[];this.listeners={};this.value="";this.textContent="";
    this.hidden=false;this.disabled=false;this.scrollTop=0;this.scrollHeight=1000;
    this.clientHeight=400;this.classList={toggle(){}};
  }
  append(...children){this.children.push(...children);}
  prepend(child){this.children.unshift(child);}
  replaceChildren(...children){this.children=children;}
  addEventListener(event,fn){this.listeners[event]=fn;}
  setAttribute(){} close(){} showModal(){}
}
const elements=new Map(ids.map(id=>[id,new Element()]));
const document={documentElement:{dataset:{basePath:"/meshpi"}},visibilityState:"visible",
  getElementById:id=>elements.get(id),createElement:()=>new Element(),addEventListener(){}};
const meshpiTheme={bindToggle(){}};
const fetch=()=>new Promise(()=>{});
let randomCalls=0;
const crypto={getRandomValues(bytes){bytes.fill(++randomCalls);return bytes;}};
function check(value,message){if(!value)throw new Error(message);}
function deferred(){
  let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});
  return {promise,resolve,reject};
}
const event={preventDefault(){}};
const dm={conversation:"dm:local:!00000001:channel",kind:"dm",peer_node:"!00000001",sendable:true};
const publicChat={conversation:"public:local:channel",kind:"public",
  channel_name:"Public",sendable:true};
const calls=[],lookups=[],confirmations=[];
let confirmation=true;
const confirm=message=>{confirmations.push(message);return confirmation;};
const responses={status:()=>({state:"tilkopla",history_local_node_id:"local"}),
  channels:()=>[],conversations:()=>[publicChat,dm],nodes:()=>[],messages:()=>[]};
'''
    checks = r'''
api=async(command,fields={},operationId="")=>{
  calls.push({command,...fields,operationId});return responses[command]?.()??{};
};
webRequest=async(path,payload,options)=>{
  lookups.push({path,payload,options});return responses[path]?.()??{};
};
state.status={state:"tilkopla",history_local_node_id:"local"};
$("node-transport-filter").value="all";$("node-sort").value="seen";
async function scenario(){
''' + scenario + r'''
}
scenario().then(()=>{
  if(typeof print==="function")print("conversation-ok");else console.log("conversation-ok");
});
'''
    source = (ROOT / "meshpi/web_assets/app.js").read_text()
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([engine, flag, environment + source + checks],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "conversation-ok" in result.stdout, result.stderr
