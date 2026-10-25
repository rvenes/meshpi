"""Exercise the node list with a mocked DOM and no IPC or radio access."""

import json
import shutil
import subprocess
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import pytest

from meshpi.i18n import using_language
from meshpi.tui import _age_time, _node_matches_transport

ROOT = Path(__file__).resolve().parents[1]


def test_node_filters_sorting_recency_and_mobile_share_the_same_list():
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for nodekontrollen")

    class Controls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                self.ids.append(attrs["id"])

    controls = Controls()
    controls.feed((ROOT / "meshpi/web_assets/index.html").read_text())
    assert len(controls.ids) == len(set(controls.ids))
    now = datetime(2026, 10, 21, 12, 30).astimezone()
    ages = [-60, 0, 59, 60, 3599, 3600, 7199, 7200, 21599, 21600, 86399, 86400]
    values = [int(now.timestamp()) - age for age in ages]
    values += [now.isoformat(), None]
    with using_language("nn"):
        times = [[value, _age_time(value, now=now)] for value in values]
    nodes = [
        {"node_id": "!00000004", "long_name": "Zulu", "is_local": True},
        {"node_id": "!00000003", "long_name": "Beta", "short_name": "B",
         "transport": "RF", "last_heard": values[7], "battery_level": 0},
        {"node_id": "!00000002", "long_name": "Alfa", "transport": "MQTT",
         "last_heard": now.isoformat()},
        {"node_id": "!00000001", "long_name": "Beta", "transport": "MQTT",
         "seen_rf": True, "last_heard": "invalid"},
        {"node_id": "!00000005", "short_name": "Ukjend", "seen_mqtt": True},
        {"node_id": "!00000006", "short_name": "Utan transport"},
    ]
    filters = {mode: [node["node_id"] for node in nodes if _node_matches_transport(node, mode)]
               for mode in ("all", "rf", "mqtt", "both")}
    setup = "const fixtures = " + json.dumps({
        "ids": controls.ids, "nodes": nodes, "times": times, "filters": filters,
        "now": now.timestamp() * 1000,
    }) + ";\n"
    environment = r"""
class Element {
  constructor() {this.children=[];this.listeners={};this.value="";this.textContent="";}
  append(...children) {
    for (const child of children) {
      if(child.parent) child.parent.children=child.parent.children.filter(c=>c!==child);
      child.parent=this;this.children.push(child);
    }
  }
  replaceChildren(...children) {this.children=[];this.append(...children);}
  addEventListener(event, callback) {this.listeners[event]=callback;}
  showModal() {this.open=true;}
  close() {this.open=false;this.listeners.close?.();}
}
const elements=new Map(fixtures.ids.map(id=>[id,new Element()]));
const panel=new Element();
const document={
  documentElement:{dataset:{basePath:"/meshpi"}},
  getElementById:id=>elements.get(id),createElement:()=>new Element(),
  querySelector:()=>panel,addEventListener(){},
};
const meshpiTheme={bindToggle(){}};
const requests=[];
const fetch=async url=>{requests.push(url);return new Promise(()=>{});};
function check(value,message){if(!value)throw new Error(message);}
"""
    checks = r"""
for(const [value,expected] of fixtures.times)
  check(nodeAge(value,fixtures.now)===expected,`Terminal time differs: ${value}`);
check(nodeAge("invalid")==="–","Invalid date must be explicit");
state.nodes=fixtures.nodes;
$("node-sort").value="seen";
const names=()=>$("nodes").children.map(button=>button.children[0]?.textContent);
for(const [mode,expected] of Object.entries(fixtures.filters)) {
  $("node-transport-filter").value=mode;renderNodes();
  check($("nodes").children.length===expected.length,`Wrong ${mode} count`);
  check($("known-nodes").children.length===fixtures.nodes.length,"DM choices were filtered");
  const ids=$("nodes").children.map(button=>button.children[1].textContent.split(" · ")[0]);
  check(JSON.stringify(ids.slice().sort())===JSON.stringify(expected.slice().sort()),
    `Terminal filter differs: ${mode}`);
}
$("node-transport-filter").value="all";renderNodes();
check(names()[0]==="Zulu"&&names()[1]==="Alfa",
  "Latest heard must pin gateway and put unknown times last");
check($("nodes").children[2].children[1].textContent.endsWith("0 %"),"Zero battery was hidden");
state.nodes[1].battery_level=101;renderNodes();
check($("nodes").children[2].children[1].textContent.endsWith("Ekstern straum"),
  "External power must not appear as 101%");
$("node-sort").value="name";$("node-sort").listeners.change();
check(names()[0]==="Alfa"&&names()[1]==="Beta","Alphabetical sort failed");
check($("nodes").children[1].children[1].textContent.startsWith("!00000001"),
  "Name ties need stable IDs");
$("node-sort").value="id";$("node-sort").listeners.change();
check($("nodes").children[0].children[1].textContent.includes("RF + MQTT"),
  "Both transports must be visible");
check($("nodes").children[0].children[2].textContent==="sist –","Missing recency must be visible");
$("node-search").value="  ALFA ";$("node-search").listeners.input();
check(names().join()==="Alfa"&&$("node-count").textContent==="1 / 6","Search/count failed");
$("node-transport-filter").value="rf";$("node-transport-filter").listeners.change();
check($("node-count").textContent==="0 / 6"
  &&$("nodes").children[0].textContent.includes("Ingen nodar"),
  "Combined filter empty state failed");
$("node-search").value="";
$("nodes-button").listeners.click();
check($("node-browser").parent===$("mobile-nodes"),"Mobile must have the same controls");
check($("node-transport-filter").value==="rf"&&names().join()==="Beta","Mobile filter changed");
state.nodes=[...state.nodes,{node_id:"!00000007",long_name:"Ny node",seen_rf:true}];
renderNodes();check($("nodes").children.length===2,"Mobile list did not refresh");
$("close-nodes").listeners.click();
check($("node-browser").parent===panel,"Closing mobile did not restore sidebar");
check($("node-sort").value==="id"&&$("node-transport-filter").value==="rf",
  "Choices did not survive refresh/close");
let opened;
openNode=async node=>{opened=node.node_id;};
$("nodes").children[0].listeners.click();
check(opened==="!00000003","Node click opened wrong details");
check(requests.length===1&&requests[0].endsWith("/session"),"List controls called the backend");
"""
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([
        engine, flag, setup + environment
        + (ROOT / "meshpi/web_assets/app.js").read_text() + checks,
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
