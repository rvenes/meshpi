"""Exercise position history with a mocked DOM, without IPC or radio access."""

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_position_table_sorting_maps_details_and_missing_measurements():
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for posisjonskontrollen")

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
    environment = "const ids = " + json.dumps(controls.ids) + ";\n" + r"""
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
"""
    checks = r"""
const positions=[
  {id:2,sample_time:"2026-10-20T12:00:00Z",received_at:"2026-10-21T15:00:00Z",
    latitude:51.2345678,longitude:-0.1234567,altitude_msl:12,sats_in_view:8,
    transport:"RF",gps_accuracy_mm:2500,precision_bits:16},
  {id:1,sample_time:"2026-10-21T12:00:00Z",latitude:0,longitude:0,
    altitude_msl:0,sats_in_view:0,transport:"MQTT",snr:0},
  {id:3,sample_time:"invalid",received_at:"2026-10-19T12:00:00Z",
    latitude:null,longitude:181,transport:"<img src=x onerror=alert(1)>"},
];
const original=JSON.stringify(positions);
const root=new Element("div");renderPositions(root,positions);
check(JSON.stringify(positions)===original,"Rendering changed stored observations");
check(root.children[0].textContent.includes("3 posisjonsrapportar"),"Count missing");
const scroll=root.children[1];
check(scroll.tabIndex===0&&scroll.attributes.role==="region","Scroll area is not accessible");
const table=nodes(root,"table")[0];
check(table.className==="position-table","Missing table");
const head=nodes(table,"thead")[0];
check(nodes(head,"th").length===8&&nodes(head,"th").every(th=>th.scope==="col"),
  "Missing column headings");
const body=nodes(table,"tbody")[0];
check(body.children.length===6,"Reports should have one compact row and hidden details");
const [latest,details,older,olderDetails,invalid]=body.children;
check(latest.children.length===8,"Wrong number of report columns");
check(latest.children[0].textContent===positionTimestamp(positions[1].sample_time),
  "Must sort by position time, not reception time");
check(latest.children[1].textContent==="0.0000000"
  &&latest.children[3].textContent==="0"&&latest.children[4].textContent==="0",
  "Zero coordinates, altitude or satellites were hidden");
check(older.children[1].textContent==="51.2345678"
  &&older.children[2].textContent==="-0.1234567","Coordinate precision was lost");
const maps=nodes(body,"a");
check(maps.length===2,"Missing or invalid coordinates must not have a map");
check(maps[0].href==="https://www.google.com/maps/search/?api=1&query=0.0000000%2C0.0000000",
  "Zero position must link to the right map");
check(maps[1].href.endsWith("51.2345678%2C-0.1234567"),"Map coordinates were reversed");
check(maps.every(link=>link.target==="_blank"&&link.rel==="noopener noreferrer"),
  "External maps must open safely in a new tab");
check(invalid.children[1].textContent==="—"&&invalid.children[2].textContent==="—"
  &&invalid.children[3].textContent==="—","Missing values were invented");
check(invalid.children[5].textContent===positions[2].transport
  &&nodes(invalid,"img").length===0,"Transport must remain text");
check(details.hidden&&olderDetails.hidden,"Technical details should start collapsed");
const toggle=nodes(latest,"button")[0];
check(toggle.attributes["aria-controls"]===details.id,"Details are not associated with the report");
toggle.listeners.click();
check(!details.hidden&&toggle.attributes["aria-expanded"]==="true"
  &&toggle.textContent==="Skjul","Details did not expand");
toggle.listeners.click();check(details.hidden,"Details did not collapse");
check(nodes(olderDetails,"th").some(th=>th.textContent==="GPS-avvik (mm)")
  &&nodes(olderDetails,"td").some(td=>td.textContent==="2500"),
  "Extra measurements must remain available");
for(const value of [null,undefined,"",true,"51.2",NaN,Infinity,91,-91])
  check(positionCoordinate(value,90)===null,`Accepted invalid latitude: ${value}`);
check(positionCoordinate(-90,90)==="-90.0000000"
  &&positionCoordinate(180,180)==="180.0000000","Valid coordinate boundaries rejected");
check(positionTimestamp(null)==="—"&&positionTimestamp("invalid")==="—",
  "Invalid time must be explicit");
check(positionTimestamp(0)!=="—","Epoch timestamp must not disappear");
root.replaceChildren();renderPositions(root,[]);
check(!nodes(root,"table").length&&root.children[0].textContent.includes("Ingen lagra"),
  "Missing empty state");
root.replaceChildren();renderPositions(root,Array.from({length:100},(_,id)=>({...positions[0],id})));
check(root.children[0].textContent.includes("siste 100"),"History limit must be visible");
check(nodes(root,"tbody")[0].children.length===200,"Repeated positions were hidden");

// Exercise the actual tab route, with read-only API responses and no daemon.
const requests=[];
api=async(command,fields)=>{
  requests.push({command,...fields});
  if(command==="node_positions")return positions;
  if(command==="node_action_availability")return {available:false};
  throw new Error(`Unexpected command: ${command}`);
};
state.node={node_id:"!1234abcd"};state.tab="positions";
renderNode().then(()=>{
  check(nodes($("node-data"),"a").length===2,"Position tab did not render map links");
  check(requests[0].command==="node_positions"&&requests[0].limit===100
    &&requests[0].node_id==="!1234abcd","Wrong position history request");
  check(requests.length===3,"Rendering triggered a write or unexpected request");
  if(typeof print==="function")print("positions checked");
  else console.log("positions checked");
});
"""
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([
        engine, flag, environment + (ROOT / "meshpi/web_assets/app.js").read_text() + checks,
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "positions checked" in result.stdout, result.stderr
