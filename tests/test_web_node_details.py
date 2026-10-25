"""Node summaries and telemetry use mocked data, without IPC or radio access."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_node_summary_and_telemetry_preserve_measurements_and_tab_requests():
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for nodeinformasjon")
    ids = re.findall(r'id="([^"]+)"', (ROOT / "meshpi/web_assets/index.html").read_text())
    environment = "const ids=" + json.dumps(ids) + ";\n" + r"""
class Element {
  constructor(tag) {
    this.tagName=tag;this.children=[];this.listeners={};this.attributes={};
    this.classList={toggle(){}};
  }
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=children;}
  addEventListener(event, callback) {this.listeners[event]=callback;}
  setAttribute(key, value) {this.attributes[key]=value;}
}
const elements=new Map(ids.map(id=>[id,new Element("div")]));
const document={
  documentElement:{dataset:{basePath:"/meshpi"}},
  getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),
  querySelectorAll:()=>[],addEventListener(){},
};
const meshpiTheme={bindToggle(){}};
const fetch=()=>new Promise(()=>{});
function check(value,message){if(!value)throw new Error(message);}
const nodes=(root,tag)=>root.children.flatMap(child=>
  [...(child.tagName===tag?[child]:[]),...nodes(child,tag)]);
const text=root=>[root.textContent||"",...root.children.map(text)].join(" ");
"""
    checks = r"""
const samples=[
  {id:1,kind:"device",sample_time:"2026-10-20T12:00:00Z",transport:"RF",
    metrics:{batteryLevel:101,voltage:4.2,channelUtilization:null}},
  {id:3,kind:"environment",sample_time:"2026-10-21T12:00:00Z",transport:"MQTT",
    metrics:{temperature:-2.25,relativeHumidity:0,extraSensor:"<img src=x>"}},
  {id:2,kind:"device",sample_time:"2026-10-21T12:00:00Z",transport:"RF",
    metrics:{battery_level:0,channel_utilization:0,uptime_seconds:90061}},
];
const original=JSON.stringify(samples);
const root=new Element("div");renderTelemetry(root,samples);
check(JSON.stringify(samples)===original,"Rendering mutated observations");
const tables=nodes(root,"table").filter(t=>t.className==="telemetry-table");
check(tables.length===2,"Metric kinds must have separate tables");
const device=tables.find(t=>text(t).includes("Batteri"));
const headings=nodes(nodes(device,"thead")[0],"th");
check(headings.every(h=>h.scope==="col"),"Column headings lost semantics");
const rows=nodes(device,"tbody")[0].children;
check(rows.length===2,"History rows disappeared");
check(rows[0].children[2].textContent==="0 %","Zero battery hidden or reordered");
check(rows[0].children[3].textContent==="—","Missing voltage must stay missing");
check(rows[0].children[4].textContent==="0 %","Zero channel use hidden");
check(rows[0].children[5].textContent==="1 d 1 t 1 min","Uptime is unreadable");
check(rows[1].children[2].textContent==="Ekstern straum","External power shown as 101%");
check(text(root).replaceAll("−","-").includes("-2,25 °C"),
  "Negative temperature/decimal format lost");
check(text(root).includes("<img src=x>"),"Unknown sensor value lost");
check(nodes(root,"img").length===0,"Values must be rendered as text");
check(nodes(root,"details").length===3,"Technical details must remain available");
check(nodes(root,"div").some(d=>d.attributes.role==="region"&&d.tabIndex===0),
  "Wide table must support keyboard scrolling");
const many=new Element("div");renderTelemetry(many,Array(100).fill(samples[0]));
check(many.children[0].textContent.includes("siste 100"),"Limit hidden");
const empty=new Element("div");renderTelemetry(empty,[]);
check(text(empty).includes("Ingen lagra telemetri"),"Empty history is unclear");
state.nodes=[{node_id:"!00000001",long_name:"Gateway"}];
const overview={node:{node_id:"!00000002",local_node_id:"!00000001",long_name:"Test",
  last_heard:1792584000,snr:0,rssi:-90,hops_away:0,battery_level:0},
  latest_telemetry:{device:samples[2]},counts:{telemetry:3,positions:0,traceroutes:0},
  latest_position:{latitude:0,longitude:0,altitude_msl:0,sample_time:"2026-10-21T12:00:00Z"},
  extra:{future:"bevart"}};
const summary=new Element("div");renderNodeOverview(summary,overview);
const facts=nodes(summary,"dl").map(text).join(" ").replaceAll("−","-");
check(facts.includes("0 dB")&&facts.includes("-90 dBm")&&facts.includes("Gateway"),
  "Link metrics or gateway name lost");
check(facts.includes("0 %")&&facts.includes("0.0000000"),"Zero observations lost");
check(text(summary).includes("bevart"),"Future backend data must remain in details");
check(nodes(summary,"h3").some(h=>h.textContent==="Identitet"),"Overview grouping missing");
const sparse=new Element("div");
renderNodeOverview(sparse,{node:{node_id:"!00000003",long_name:"Rv Sensecap",short_name:"2414",
  hw_model:"TRACKER_T1000_E",role:null,battery_level:101,voltage:4.228},
  latest_telemetry:{environment:samples[1]}});
const sparseFacts=nodes(sparse,"dl").map(text).join(" ");
check(sparseFacts.includes("Langt namn") && sparseFacts.includes("TRACKER_T1000_E")
  && sparseFacts.includes("Ekstern straum") && sparseFacts.includes("4,228 V"),
  "Node identity and power must remain visible with environmental telemetry only");
check(sparseFacts.includes("Rolle —") && sparseFacts.includes("SNR —"),"Unknown fields fabricated");

async function testTabs() {
  const requests=[];let finishOverview;
  api=async(command,fields)=>{
    requests.push({command,...fields});
    if(command==="node_overview")return new Promise(resolve=>{finishOverview=resolve;});
    if(command==="node_telemetry")return samples;
    if(command==="node_action_availability")return {available:false};
    throw new Error(`Unexpected request: ${command}`);
  };
  state.node=overview.node;state.tab="overview";
  const pending=renderNode();
  state.tab="telemetry";await renderNode();
  const content=text($("node-data"));
  finishOverview(overview);await pending;
  check(text($("node-data"))===content,"Late response overwrote current tab");
  check(requests[1].command==="node_telemetry"&&requests[1].limit===100
    &&requests[1].node_id===overview.node.node_id,"Wrong history request");
  check(requests.length===4,"Rendering caused an unexpected request/write");
  check($("traceroute").disabled&&$("position-exchange").disabled,"Availability ignored");
  api=async()=>{throw new Error("offline");};
  await renderNode().catch(()=>{});
  check(text($("node-data")).includes("Kunne ikkje hente"),"Loading error stuck");
  if(typeof print==="function")print("node details checked");
  else console.log("node details checked");
}
testTabs().catch(error=>{throw error;});
"""
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([
        engine, flag, environment + (ROOT / "meshpi/web_assets/app.js").read_text() + checks,
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "node details checked" in result.stdout, result.stderr
