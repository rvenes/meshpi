"""Three browser languages with synthetic DOM/data; never connect to IPC or radio."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from web_helpers import javascript_asset

ASSETS = Path(__file__).resolve().parents[1] / "meshpi" / "web_assets"


def run_js(tmp_path, source):
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for språkkontrollen")
    script = tmp_path / "browser-languages.js"
    script.write_text(source, encoding="utf-8")
    result = subprocess.run([engine, str(script)], capture_output=True, text=True,
                            encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    assert "i18n-ok" in result.stdout, result.stderr


@pytest.mark.parametrize("language", ["nb", "nn", "en"])
@pytest.mark.parametrize("blocked", [False, True])
def test_browser_languages_preserve_drafts_and_arbitrary_user_text(tmp_path, language, blocked):
    setup = "const settings=" + json.dumps({"language": language, "blocked": blocked}) + ";\n"
    ids = re.findall(r'id="([^"]+)"', (ASSETS / "index.html").read_text())
    setup += "const ids=" + json.dumps(ids) + ";\n"
    environment = r'''
function check(value,message) {if(!value) throw new Error(message);}
class Element {
  constructor(tag) {
    this.tagName=tag;this.children=[];this.listeners={};this.attributes={};
    this.value="";this.textContent="";this.open=false;this.isConnected=true;
    this.dataset={};this.classList={toggle(){}};
  }
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=children;}
  addEventListener(event, callback) {this.listeners[event]=callback;}
  setAttribute(key,value) {this.attributes[key]=value;}
  getAttribute(key) {return this.attributes[key] ?? null;}
  showModal() {this.open=true;}
  close() {this.open=false;}
}
const elements=new Map(ids.map(id=>[id,new Element("div")]));
const staticText={nodeValue:" Innstillingar ",isConnected:true,parentElement:{tagName:"LABEL"}};
const replacedText={nodeValue:"Namn",isConnected:true,parentElement:{tagName:"SPAN"}};
const codeText={nodeValue:"Namn",isConnected:true,parentElement:{tagName:"CODE"}};
const staticInput=new Element("input");
staticInput.setAttribute("placeholder","Skriv ei melding …");
const pickers=[elements.get("auth-language"),elements.get("app-language")];
const manifest={};
const document={body:{dataset:{}},documentElement:{dataset:{basePath:"/meshpi"}},
  getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),
  createTreeWalker() {
    let index=0;return {nextNode:()=>[staticText,replacedText,codeText][index++]};
  },
  querySelector:selector=>selector.includes("manifest")?manifest:null,
  querySelectorAll:selector=>selector.includes("data-language-picker")?pickers:
    selector.includes("[aria-label]")?[staticInput]:[],
  addEventListener(){},
};
let saved=settings.blocked?null:settings.language;
const window={navigator:{},localStorage:{
  getItem(key) {
    if(settings.blocked)throw Error("blocked");return key==="meshpi-web-language"?saved:null;
  },
  setItem(key,value) {
    if(settings.blocked)throw Error("blocked");if(key==="meshpi-web-language")saved=value;
  },
}};
const meshpiTheme={bindToggle(){}};
let requests=[];
const fetch=(url,options)=>{requests.push({url,options});return new Promise(()=>{});};
const AbortController=undefined;
function setTimeout(){return 1;}
function clearTimeout(){}
const subtreeText=root=>[root.textContent||"",...root.children.map(subtreeText)].join(" ");
'''
    checks = r'''
async function scenario() {
if(settings.blocked)check(meshpiI18n.getLanguage()==="nn","Restricted storage changed default");
meshpiI18n.setLanguage(settings.language);
check(document.documentElement.lang===settings.language,"Document language not updated");
check(pickers.every(p=>p.value===settings.language),"Language selectors disagree");
check(manifest.href.endsWith("?language="+settings.language),"Manifest language missing");
check(codeText.nodeValue==="Namn","A code example was translated");
check(staticText.nodeValue===" "+webText("Innstillingar")+" ","Static text or whitespace changed");
check(staticInput.getAttribute("placeholder")===webText("Skriv ei melding …"),
  "Placeholder unchanged");
check(!meshpiI18n.setLanguage("de"),"Unsupported language accepted");
const userText="Namn <img src=x> {1}";
check(webText`DM til ${userText} · ${"Mottakar"}`.includes(userText),
  "Interpolated user text changed");
state.status={state:"tilkopla",local_node_id:"!00000001"};
state.selected={conversation:"dm:test",kind:"dm",peer_node:"!00000002",sendable:true};
state.selectionReady=true;state.authenticated=true;
state.nodes=[{node_id:"!00000002",long_name:"Namn",short_name:"SAME"}];
$("message-text").value="Innstillingar <img src=x>";
state.drafts.set("dm:test",$("message-text").value);
$("node-transport-filter").value="all";
renderNodes();updateComposer();
check($("nodes").children[0].children[0].textContent==="Namn","A node name was translated");
check(!$("message-text").disabled,"Translation changed the connected machine state");
const telemetry=new Element("div");
renderTelemetry(telemetry,[{kind:"device",metrics:{batteryLevel:101,voltage:4.2},transport:"RF"}]);
check(subtreeText(telemetry).includes(webText("Ekstern straum")),"Telemetry label not translated");
check(metricLabel("battery_level")===webText("Batteri"),"Metric label uses old language");
const messageData=[{id:1,direction:"in",from_node:"!00000002",text:"Innstillingar",
  timestamp:"2026-10-29T12:00:00Z",status:"motteken"}];
state.messageKey=JSON.stringify(messageData);
state.messageLanguage=settings.language;
$("messages").scrollTop=23;$("messages").scrollHeight=1000;$("messages").clientHeight=200;
api=async command=>{check(command==="messages","Language change requested an unexpected command");
  return messageData;};
historySettings.data={local_node_id:"!00000001",retention_days:30,counts:{messages:5}};
historySettings.preview={older_than_days:7,counts:{messages:2},cutoff:"2026-10-22T12:00:00Z",
  cleanup_token:"preserved-preview"};
$("retention-days").value="custom";$("retention-custom").value="42";
meshpiAuth.init({renew:async()=>{},keys:async()=>({keys:[{id:"key1",name:"Namn"}]}),
  request:()=>{throw Error("Language change performed an authentication action");}});
meshpiAuth.update({authenticated:true,passkeys_available:false});
await $("auth-button").listeners.click();
check($("auth-keys").children[0].children[0].textContent==="Namn","Key name translated");
const changed=settings.language==="en"?"nb":"en";
replacedText.nodeValue="User supplied content";
meshpiI18n.setLanguage(changed);
await Promise.resolve();await Promise.resolve();await Promise.resolve();
check($("messages").scrollTop===23,"Language change jumped to the end of the history");
check(subtreeText($("messages")).includes("Innstillingar"),"A message was translated");
check($("retention-days").value==="custom" && $("retention-custom").value==="42",
  "Language change reset unsaved retention preferences");
check($("auth-keys").children[0].children[1].textContent===webText("Fjern"),
  "Existing key buttons kept the old language");
check($("auth-keys").children[0].children[0].textContent==="Namn","Key name changed");
check(historySettings.preview.cleanup_token==="preserved-preview","Cleanup preview changed");
check($("retention-limit").textContent.includes(changed==="en"?"message limit":"Meldingsgrensen"),
  "History metadata kept the old language");
check($("cleanup-summary").textContent.includes(changed==="en"?"preview":"Forhåndsvisningen"),
  "Cleanup preview kept the old language");
check($("message-text").value==="Innstillingar <img src=x>","Language change lost the draft");
check(state.drafts.get("dm:test")===$("message-text").value,"Stored draft changed");
check(state.selected.conversation==="dm:test","Language change selected a new conversation");
check(replacedText.nodeValue==="User supplied content","Replaced static content was translated");
check(state.nodes[0].long_name==="Namn","Node data mutated");
check(metricLabel("battery_level")===webText("Batteri"),"Metric labels did not switch immediately");
check(requests.every(r=>r.options.headers["X-MeshPi-Language"]),"Request language missing");
check(requests.every(r=>!r.options.body || !writeCommands.has(JSON.parse(r.options.body).command)),
  "Language change triggered a write");
if(!settings.blocked)check(saved===changed,"Language not persisted");
if(typeof print==="function")print("i18n-ok");else console.log("i18n-ok");
}
scenario().catch(error=>{throw error;});
'''
    run_js(tmp_path, setup + environment + javascript_asset("i18n.js")
           + javascript_asset("auth.js") + javascript_asset("app.js") + checks)


def test_client_catalog_placeholders_are_complete_in_three_languages():
    source = javascript_asset("i18n.js")
    catalog = json.loads(source.split("const catalogs = ", 1)[1].split(";\n", 1)[0])
    assert len(catalog) >= 350
    for original, translated in catalog.items():
        assert len(translated) == 2
        fields = sorted(re.findall(r"\{\d+\}", original))
        for text in translated:
            assert text.strip()
            assert sorted(re.findall(r"\{\d+\}", text)) == fields, original


@pytest.mark.parametrize("language,title", [
    ("nb", "MeshPi er frakoblet"), ("nn", "MeshPi er fråkopla"), ("en", "MeshPi is offline"),
])
def test_offline_page_retains_selected_language_without_cache(tmp_path, language, title):
    setup = "const language=" + json.dumps(language) + ";\n"
    setup += "const expected=" + json.dumps(title) + ";\n"
    environment = r'''
const listeners={};
const self={registration:{scope:"https://example.test/meshpi/"},
  clients:{claim:()=>Promise.resolve()},addEventListener:(name,fn)=>listeners[name]=fn};
class URL {
  constructor(value) {
    const match=/^(https?:\/\/[^/]+)(\/[^?#]*)/.exec(value);
    this.origin=match[1];this.pathname=match[2];
    this.searchParams={get:key=>key==="language"?language:null};
  }
}
class Response {constructor(body,options) {this.body=body;Object.assign(this,options);}}
const fetch=()=>Promise.reject(Error("offline"));
const caches=new Proxy({}, {get(){throw Error("Offline page accessed private cache");}});
'''
    checks = r'''
let response;
listeners.fetch({request:{method:"GET",mode:"navigate",
  url:"https://example.test/meshpi/?language="+language},respondWith:r=>response=r});
response.then(result=>{
  if(result.status!==503 || !result.body.includes(expected)
      || !result.body.includes('lang="'+language+'"')
      || !result.body.includes('href="/meshpi/?language='+language+'"')
      || result.headers["Cache-Control"]!=="no-store") throw Error("Wrong offline language");
  if(typeof print==="function")print("i18n-ok");else console.log("i18n-ok");
}).catch(error=>{throw error;});
'''
    run_js(tmp_path, setup + environment + javascript_asset("sw.js") + checks)


@pytest.mark.parametrize("saved,requested,blocked,expected", [
    (None, "en", True, "en"),
    (None, "nb", True, "nb"),
    ("nn", "en", False, "nn"),
    ("invalid", "nb", False, "nb"),
    (None, "unsupported", True, "nn"),
])
def test_language_url_fallback_survives_offline_retry(
    tmp_path, saved, requested, blocked, expected,
):
    settings = {"saved": saved, "requested": requested, "blocked": blocked, "expected": expected}
    environment = "const settings=" + json.dumps(settings) + ";\n" + r'''
class URL {constructor() {this.searchParams={get:()=>settings.requested};}}
const window={location:{href:"https://example.test/meshpi/"},
  localStorage:{getItem(){if(settings.blocked)throw Error("blocked");return settings.saved;}}};
const document={body:{},documentElement:{dataset:{basePath:"/meshpi"}},
  createTreeWalker:()=>({nextNode:()=>null}),querySelectorAll:()=>[],querySelector:()=>null};
'''
    checks = r'''
if(meshpiI18n.getLanguage()!==settings.expected)throw Error("Wrong URL or saved language priority");
if(document.documentElement.lang!==settings.expected)throw Error("Page language not applied");
if(typeof print==="function")print("i18n-ok");else console.log("i18n-ok");
'''
    run_js(tmp_path, environment + javascript_asset("i18n.js") + checks)
