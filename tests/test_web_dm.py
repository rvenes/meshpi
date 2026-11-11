"""Exercise recipient selection with a mock DOM, without IPC or radio access."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from web_helpers import javascript_asset

ROOT = Path(__file__).resolve().parents[1]


def test_dm_picker_shows_full_names_and_opens_the_selected_recipient():
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for mottakarvalet")
    html = (ROOT / "meshpi/web_assets/index.html").read_text()
    assert '<datalist' not in html
    assert 'aria-controls="dm-recipients"' in html
    ids = re.findall(r'id="([^"]+)"', html)
    setup = "const ids = " + json.dumps(ids) + ";\n"
    environment = r"""
class Element {
  constructor(tag) {
    this.tagName=tag; this.children=[]; this.listeners={}; this.attributes={};
    this.value=""; this.textContent=""; this.open=false;
  }
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=children;}
  addEventListener(event, callback) {this.listeners[event]=callback;}
  setAttribute(key, value) {this.attributes[key]=value;}
  showModal() {this.open=true;}
  close() {this.open=false;}
}
const elements=new Map(ids.map(id=>[id,new Element("div")]));
const document={
  documentElement:{dataset:{basePath:"/meshpi"}},
  getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),
  addEventListener(){},
};
const meshpiTheme={bindToggle(){}};
const fetch=()=>new Promise(()=>{});
const AbortController=undefined;
let expireNotice;
function setTimeout(callback, delay) {
  check(delay===10000,"Unexpected notice duration");expireNotice=callback;return 1;
}
function clearTimeout() {expireNotice=null;}
function check(value,message){if(!value)throw new Error(message);}
"""
    checks = r"""
async function scenario() {
  const selected=[];
  selectConversation=async item=>selected.push(item);
  state.status={local_node_id:"!00000001"};
  state.channels=[{channel_key:"primary",local_node_id:"!00000001",display_name:"Testkanal"}];
  state.nodes=[
    {node_id:"!00000001",long_name:"Lokal gateway"},
    {node_id:"!00000002",long_name:"Alfa fullt og svært langt nodenamn",short_name:"SAME"},
    {node_id:"!00000003",long_name:"Beta fullt nodenamn",short_name:"SAME"},
    {node_id:"!00000004",short_name:"Kortnamn"},
    {node_id:"!00000005"},
    {node_id:"!00000006",long_name:"<img src=x>"},
  ];
  const choices=()=>$("dm-recipients").children.filter(el=>el.tagName==="button");
  const search=value=>{$("dm-node").value=value;$("dm-node").listeners.input();};
  $("new-dm").listeners.click();
  check($("dm-dialog").open && choices().length===5,"Missing recipients or local gateway included");
  check($("dm-open").disabled,"No recipient allowed opening a conversation");
  search("  same  ");
  check(choices().length===2,"Short-name search lost a matching recipient");
  check(choices()[0].children[0].textContent==="Alfa fullt og svært langt nodenamn",
    "Full name was replaced by short name");
  check(choices()[1].children[1].textContent==="SAME · !00000003","Recipient ID missing");
  choices()[1].listeners.click();
  check($("dm-node").value==="!00000003" && !$("dm-open").disabled,"Wrong selected recipient");
  check(choices()[0].children[0].textContent==="Beta fullt nodenamn"
    && choices()[0].attributes["aria-pressed"]==="true","Selected full name disappeared");
  $("dm-channel").value="primary";
  $("dm-form").listeners.submit({preventDefault(){}});
  check(!$("dm-dialog").open && selected[0].peer_node==="!00000003"
    && selected[0].channel_key==="primary","Conversation opened for wrong recipient/channel");

  openDm();search("FULLT OG");
  check(choices().length===1 && choices()[0].children[0].textContent.startsWith("Alfa"),
    "Full-name search failed");
  search("Kortnamn");check(choices()[0].children[0].textContent==="Kortnamn","Fallback name lost");
  search("00000005");check(choices()[0].children[0].textContent==="!00000005","Fallback ID lost");
  search("<img");check(choices()[0].children[0].tagName==="strong"
    && choices()[0].children[0].textContent==="<img src=x>","Name was interpreted as markup");
  search("ikkje funnen");
  check(!choices().length && $("dm-open").disabled
    && $("dm-recipients").children[0].textContent.includes("Ingen kjende"),"Missing empty state");
  search("!ABCDEF12");
  check(!$("dm-open").disabled,"Unknown full ID must remain usable");
  $("dm-channel").value="primary";
  $("dm-form").listeners.submit({preventDefault(){}});
  check(selected[1].peer_node==="!abcdef12","Manual full ID was not normalized");
  openDm("!00000002");
  check(choices()[0].children[0].textContent.startsWith("Alfa"),"Node detail shortcut lost name");
  search("!00000001");
  check($("dm-open").disabled,"Local gateway must not be selectable");
  $("dm-form").listeners.submit({preventDefault(){}});
  check(selected.length===2,"Self conversation was opened");
  check(!$("notice").hidden&&typeof expireNotice==="function","Self warning has no expiry");
  expireNotice();check($("notice").hidden,"Self warning did not disappear");
  state.channels=[];search("!00000002");
  check($("dm-open").disabled,"Opening without a channel must be blocked");
  state.nodes=[];search("");
  check($("dm-recipients").children[0].textContent.includes("Ingen kjende mottakarar"),
    "Missing initial empty state");

  $("dm-dialog").close();
  state.channels=[
    {channel_key:"private",channel_index:1,role:"SECONDARY",local_node_id:"!00000001"},
    {channel_key:"primary",channel_index:0,role:"PRIMARY",local_node_id:"!00000001"},
  ];
  state.node={node_id:"!00000007",long_name:"Direkte mottakar"};
  $("node-dialog").showModal();
  await $("node-dm").listeners.click();
  check(!$("node-dialog").open&&!$("dm-dialog").open,"Node DM reopened the recipient picker");
  check(selected[2].peer_node==="!00000007"&&selected[2].channel_key==="primary"
    &&selected[2].long_name==="Direkte mottakar","Node shortcut picked wrong peer or channel");
  state.node={node_id:"!00000001"};
  await $("node-dm").listeners.click();
  check(selected.length===3&&!$("notice").hidden,"Local node shortcut did not warn");
  expireNotice();check($("notice").hidden,"Local node shortcut warning did not disappear");
  state.node={node_id:"!00000007"};
  state.channels=[];
  await $("node-dm").listeners.click();
  check(selected.length===3&&!$("dm-dialog").open,"Missing channel reopened recipient picker");
}
scenario().then(() => {
  if(typeof print==="function")print("dm-picker-ok");else console.log("dm-picker-ok");
}).catch(error=>{
  if(typeof printerr==="function")printerr(error.stack||error.message);else console.error(error);
});
"""
    flag = "-e" if Path(engine).name == "node" else "-c"
    source = javascript_asset("app.js")
    result = subprocess.run([engine, flag, setup + environment + source + checks],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "dm-picker-ok" in result.stdout, result.stderr
