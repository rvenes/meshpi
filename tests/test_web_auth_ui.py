"""Authentication UI against a mock browser; no real credentials or radio use."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_auth(checks):
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for innloggingskontrollen")
    environment = r"""
if (typeof atob === "undefined") {
  globalThis.atob = s => String.fromCharCode(...imports.gi.GLib.base64_decode(s));
  globalThis.btoa = s => imports.gi.GLib.base64_encode(Uint8Array.from(s,c=>c.charCodeAt(0)));
}
if (typeof URL === "undefined") globalThis.URL = class {
  constructor(value) {
    const parts=/^(https?:)\/\/([^/]+)(.*)$/.exec(value);
    if(!parts) throw new Error("bad URL");
    this.protocol=parts[1];this.host=parts[2];this.hostname=parts[2].split(':')[0];this.href=value;
  }
};
class Control {
  constructor(id) {this.id=id;this.listeners={};this.children=[];this.value="";this.open=false;}
  addEventListener(event, fn) {this.listeners[event]=fn;}
  setAttribute(key,value) {this[key]=value;}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=children;}
  showModal() {this.open=true;}
  close() {this.open=false;}
}
const controls=new Map();
const document={body:{dataset:{}},getElementById(id){
  if(!controls.has(id))controls.set(id,new Control(id));return controls.get(id);
},createElement(tag){return new Control(tag);},
querySelectorAll(){return [...controls.values()].filter(el=>el.open);}};
const el=id=>document.getElementById(id);
const PublicKeyCredential=class {};
const navigator={credentials:{get(){},create(){}}};
const confirm=()=>true;
const check=(condition,message)=>{if(!condition)throw new Error(message);};
const events=[];let response;let keyList=[];
const config={
  session(data){events.push("session");meshpiAuth.update(data);},
  lock(){events.push("lock");meshpiAuth.lock();},
  async renew(){events.push("renew");},
  async resume(){events.push("resume");},
  async request(path,data){events.push({path,data});
    return typeof response==="function"?response(path,data):response;},
  async keys(){return {keys:keyList};},
};
"""
    source = (ROOT / "meshpi/web_assets/auth.js").read_text()
    script = environment + source + "\nasync function scenario(){\n" + checks + r"""
}
scenario().then(()=>{if(typeof print==='function')print('auth-ok');else console.log('auth-ok');})
.catch(error=>{if(typeof printerr==='function'){printerr(error.stack);imports.system.exit(1);}
else {console.error(error);process.exitCode=1;}});
"""
    result = subprocess.run(
        [engine, "-e" if Path(engine).name == "node" else "-c", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "auth-ok" in result.stdout


def test_webauthn_binary_conversion_round_trip_without_new_browser_helpers():
    run_auth(r"""
const creation=meshpiAuth.options({challenge:"AP_-",user:{id:"AQI",name:"owner"},
  excludeCredentials:[{id:"_w",type:"public-key"}]},true);
check([...new Uint8Array(creation.challenge)].join(',')==='0,255,254','Challenge conversion');
check([...new Uint8Array(creation.user.id)].join(',')==='1,2','User ID conversion');
check(new Uint8Array(creation.excludeCredentials[0].id)[0]===255,'Excluded ID conversion');
const assertion=meshpiAuth.options({challenge:'AA',
  allowCredentials:[{id:'AQ',type:'public-key'}]},false);
check(new Uint8Array(assertion.allowCredentials[0].id)[0]===1,'Allowed ID conversion');
const json=meshpiAuth.credentialJSON({id:'AP_-',rawId:creation.challenge,type:'public-key',
 response:{clientDataJSON:creation.user.id,signature:creation.challenge,userHandle:null,
 getTransports:()=>['internal','hybrid']},getClientExtensionResults:()=>({})});
check(json.rawId==='AP_-'&&json.response.signature==='AP_-','Binary serialisation');
check(json.response.clientDataJSON==='AQI','Client data serialisation');
check(!('userHandle' in json.response),'Null user handle must stay optional');
check(json.response.transports.includes('hybrid'),'Authenticator transports discarded');
""")


def test_login_gate_existing_session_and_expiry_close_private_dialogs():
    run_auth(r"""
meshpiAuth.init(config);
check(document.body.dataset.auth==='locked'&&el('auth-dialog').open,'Initial shell unlocked');
let cancelled=false;el('auth-dialog').listeners.cancel({preventDefault(){cancelled=true;}});
check(cancelled,'Anonymous login modal dismissed');
meshpiAuth.update({authenticated:true,passkeys_available:true});
check(document.body.dataset.auth==='unlocked'&&!el('auth-dialog').open,
  'Existing login not restored');
el('node-dialog').showModal();el('auth-keys').append(new Control('secret'));
meshpiAuth.lock();
check(document.body.dataset.auth==='locked'&&!el('node-dialog').open,
  'Private dialog remains visible');
check(el('auth-dialog').open&&el('auth-keys').children.length===0,'Keys visible after expiry');
""")


def test_code_login_clears_code_and_resumes_only_after_authentication():
    run_auth(r"""
meshpiAuth.init(config);el('auth-code').value='single-use-test';
response={authenticated:true,csrf:'new',passkeys_available:false};
await el('auth-code-form').listeners.submit({preventDefault(){}});
check(events[0]==='renew','Code entry did not renew a revoked preauth session');
check(events[1].path==='auth/code'&&events[1].data.code==='single-use-test','Code not submitted');
check(events[2]==='session'&&events[3]==='resume','Data loaded before authentication');
check(el('auth-code').value===''&&!el('auth-dialog').open,'Code retained after login');
""")


def test_cancelled_passkey_does_not_submit_finish_or_resume():
    run_auth(r"""
meshpiAuth.init(config);meshpiAuth.update({authenticated:false,passkeys_available:true});
response={challenge_id:'challenge',options:{challenge:'AA'}};
navigator.credentials.get=async()=>{const error=new Error('raw browser message');
  error.name='NotAllowedError';throw error;};
await el('auth-passkey').listeners.click();
check(events.length===2&&events[1].path==='auth/login/begin','Cancelled assertion submitted');
check(el('auth-error').textContent.includes('avbroten'),'Cancellation not explained');
check(document.body.dataset.auth==='locked','Cancelled authentication unlocked UI');
""")


def test_canonical_link_never_switches_automatically_or_accepts_unsafe_url():
    run_auth(r"""
meshpiAuth.init(config);
meshpiAuth.update({authenticated:false,passkeys_available:false,canonical_url:'https://mesh.example.ts.net/meshpi/'});
check(el('auth-passkey').disabled&&!el('auth-canonical').hidden,
  'Missing canonical-address guidance');
check(el('auth-canonical').href==='https://mesh.example.ts.net/meshpi/',
  'Canonical address changed');
check(events.length===0,'Opening login performed implicit network actions');
meshpiAuth.update({authenticated:false,canonical_url:'javascript:alert(1)'});
check(el('auth-canonical').hidden,'Unsafe canonical URL displayed');
meshpiAuth.update({authenticated:false,canonical_url:'http://localhost:8780/meshpi/'});
check(!el('auth-canonical').hidden,'Local canonical address unavailable');
meshpiAuth.update({authenticated:false,canonical_url:'http://remote.example/meshpi/'});
check(el('auth-canonical').hidden,'Unencrypted remote canonical address displayed');
""")


def test_key_removal_is_explicit_and_locks_all_local_views():
    run_auth(r"""
meshpiAuth.init(config);meshpiAuth.update({authenticated:true,passkeys_available:true});
keyList=[{id:'id-a',name:'Laptop <script>'}];
await el('auth-button').listeners.click();
const row=el('auth-keys').children[0];
check(row.children[0].textContent==='Laptop <script>','Key name not treated as text');
await row.children[1].listeners.click();
check(events.some(e=>e.path==='auth/remove'&&e.data.id==='id-a'),'Wrong key removed');
check(document.body.dataset.auth==='locked'&&el('auth-dialog').open,
  'Removal retained logged in view');
""")
