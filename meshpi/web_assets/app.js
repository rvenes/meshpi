"use strict";
const $ = id => document.getElementById(id);
meshpiTheme.bindToggle($("theme-toggle"));
const endpoint = path => `${document.documentElement.dataset.basePath}/api/${path}`;
const state = {csrf:"",status:{},channels:[],conversations:[],nodes:[],selected:null,node:null,tab:"overview",busy:false,refreshing:false,again:false,drafts:new Map(),messageKey:"",selectionReady:false,selectionEpoch:0,messageRequest:0,gatewayId:null};
const writeCommands = new Set(["send_public","send_dm","archive_conversation","unarchive_conversation","connect","discover_connections","discover_ble_connections","node_action","history_settings_set","history_cleanup"]);
const pendingSends = new Map();
function element(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=String(text??"—");if(cls)el.className=cls;return el;}
function notice(text=""){ $("notice-text").textContent=text;$("notice").hidden=!text; }
$("dismiss-notice").addEventListener("click",()=>notice());
function name(node){return node?.long_name||node?.short_name||node?.node_id||"Ukjend node";}
function nodeName(id){return name(state.nodes.find(n=>n.node_id===id)||{node_id:id});}
function renderTitle(){
  const label = document.documentElement.dataset.serverName || state.hostname || "Privat meshklient";
  document.title = `MeshPi · ${label}`;
}
function renderIdentity(){
  renderTitle();
  const host = state.hostname ? `MeshPi ${state.hostname}` : "MeshPi";
  const nodeId = state.status.state === "tilkopla" ? state.status.local_node_id : null;
  const node = nodeId ? `Node: ${nodeName(nodeId)}` : "Privat meshklient";
  $("host-name").textContent = host;
  $("host-name").title = host;
  $("gateway-name").textContent = node;
  $("gateway-name").title = nodeId ? `${node} · ${nodeId}` : node;
}
function time(value){if(!value)return "";const date=new Date(typeof value==="number"?value*1000:value);return Number.isNaN(date.getTime())?String(value):date.toLocaleString("nb-NO",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false});}
function newOperationId(){
  // randomUUID needs HTTPS; getRandomValues also works over trusted LAN HTTP.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,"0")).join("");
}
let sessionPromise = null;
let eventStream = null;
let refreshInterval = null;
let authEpoch = 0;
function loginRequired() {
  const error = new Error("Logg inn for å halde fram. Handlinga blei ikkje utført.");
  error.unauthenticated = true;
  error.uncertain = false;
  return error;
}
function lockSession() {
  if (state.authenticated !== false) authEpoch++;
  state.authenticated = false;
  invalidateHistorySettings();
  state.selectionReady = false;
  if (typeof updateComposer === "function") updateComposer();
  eventStream?.close();
  eventStream = null;
  state.hostname = "";
  renderTitle();
  $("host-name").textContent = "MeshPi";
  $("status").textContent = "Innlogging er nødvendig";
  if (typeof meshpiAuth !== "undefined") meshpiAuth.lock();
}
function applySession(data) {
  state.csrf = data.csrf;
  state.hostname = typeof data.hostname === "string" ? data.hostname : "";
  // The production authentication module requires explicit authenticated=true.
  const authenticated = typeof meshpiAuth === "undefined" ? data.authenticated !== false : data.authenticated === true;
  if (!authenticated) lockSession();
  else state.authenticated = true;
  if (typeof meshpiAuth !== "undefined") meshpiAuth.update(data);
  renderIdentity();
}
async function fetchJson(url, options = {}) {
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), 45000) : null;
  timer?.unref?.();
  try {
    const response = await fetch(url, {...options, ...(controller ? {signal: controller.signal} : {})});
    return {response, result: await response.json()};
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}
async function renewSession(rejectedToken = null) {
  if (rejectedToken !== null && state.csrf && state.csrf !== rejectedToken) return;
  if (!sessionPromise) {
    const epoch = authEpoch;
    sessionPromise = (async () => {
      const {response, result: session} = await fetchJson(endpoint("session"));
      if (!response.ok || !session?.ok || typeof session.data?.csrf !== "string" || !session.data.csrf) {
        throw new Error("Kunne ikkje fornye sambandet. Prøver igjen automatisk.");
      }
      if (epoch === authEpoch) applySession(session.data);
    })().finally(() => { sessionPromise = null; });
  }
  await sessionPromise;
}

async function webRequest(path, payload, {write = false, operationId = ""} = {}) {
  if (!state.csrf) await renewSession();
  const authentication = path.startsWith("auth/");
  if (!authentication && state.authenticated === false) throw loginRequired();
  const epoch = authEpoch;
  for (let attempt = 0; attempt < 2; attempt++) {
    const token = state.csrf;
    let response;
    let result;
    try {
      ({response, result} = await fetchJson(endpoint(path), {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-MeshPi-CSRF": token,
          "X-MeshPi-Operation": operationId},
        body: JSON.stringify(payload),
      }));
      if (!result || typeof result.ok !== "boolean") throw new Error("Ugyldig svar");
    } catch (_) {
      const error = new Error(write
        ? "Ukjend sendestatus. Kontroller sendestatus og historikken før du sender på nytt."
        : "Sambandet blei brote. Prøver igjen ved neste oppfrisking.");
      error.uncertain = write;
      throw error;
    }
    if (response.status === 401) {
      lockSession();
      throw loginRequired();
    }
    if (!authentication && epoch !== authEpoch) {
      const error = loginRequired();
      // An accepted write may have completed while another request expired.
      error.uncertain = write && ![400, 403, 404, 409, 413, 415].includes(response.status);
      throw error;
    }
    if (response.status === 403 && attempt === 0) {
      await renewSession(token);
      if (!authentication && state.authenticated === false) throw loginRequired();
      // A write rejected by the access check never reached IPC. Do not replay it.
      if (!write && state.csrf !== token) continue;
      if (write && state.csrf !== token) {
        result.error = "Sambandet er fornya. Handlinga blei avvist før utføring. Prøv igjen når du er klar.";
      }
    }
    if (!response.ok || !result.ok) {
      const error = new Error(result.error || "Førespurnaden feila.");
      error.uncertain = write && ![400, 403, 404, 409, 413, 415].includes(response.status);
      throw error;
    }
    return result.data;
  }
}

async function api(command, fields = {}, operationId = "") {
  const write = writeCommands.has(command);
  return webRequest("request", {command, ...fields}, {
    write, operationId: operationId || (write ? newOperationId() : ""),
  });
}
async function safe(fn){try{await fn();}catch(error){notice(error.message||"Sambandet blei brote. Kontroller historikken før du sender på nytt.");}}
function conversationName(item){return item.kind==="public"?(item.channel_name||item.name||"Kanal"):(item.long_name||item.short_name||nodeName(item.peer_node));}
function renderConversations(){
  const root=$("conversations");root.replaceChildren();
  for(const kind of ["public","dm"]){const items=state.conversations.filter(c=>c.kind===kind);if(!items.length)continue;root.append(element("p",kind==="public"?"Kanalar":"Direkte meldingar","list-heading"));
    for(const item of items){const button=element("button",undefined,"list-button"+(state.selected?.conversation===item.conversation?" active":""));button.type="button";button.setAttribute("aria-pressed",String(state.selected?.conversation===item.conversation));
      const title=element("strong",conversationName(item));if(item.unread){const badge=element("span",item.unread,"badge");title.prepend(badge);}button.append(title,element("small",item.last_text||item.channel_name||"Ingen meldingar enno"));button.addEventListener("click",()=>safe(()=>selectConversation(item)));root.append(button);
    }
  }
  if(!root.children.length)root.append(element("p","Ingen samtalar. Vel ein gateway i Tilkoplingar.","empty"));
}
function renderNodes(){
  const root=$("nodes");root.replaceChildren();
  const query=$("node-search").value.trim().toLocaleLowerCase("nn-NO");
  const mode=$("node-transport-filter").value;
  const nodes=state.nodes.filter(node=>[node.long_name,node.short_name,node.node_id].join(" ").toLocaleLowerCase("nn-NO").includes(query)&&(mode==="all"||nodeTransports(node).join("+")==={rf:"RF",mqtt:"MQTT",both:"RF+MQTT"}[mode]));
  nodes.sort(compareNodes);
  $("node-count").textContent=nodes.length===state.nodes.length?String(nodes.length):`${nodes.length} / ${state.nodes.length}`;
  for(const node of nodes){
    const button=element("button",undefined,"list-button");button.type="button";
    button.append(element("strong",name(node)),element("small",[node.node_id,nodeTransports(node).join(" + "),node.battery_level==null?"":metricValue("battery_level",node.battery_level)].filter(Boolean).join(" · ")),element("small",`sist ${nodeAge(node.last_heard)}`));
    button.addEventListener("click",()=>{$("nodes-dialog").close();safe(()=>openNode(node));});root.append(button);
  }
  if(!nodes.length)root.append(element("p","Ingen nodar passar søket eller filteret.","empty"));
  $("known-nodes").replaceChildren(...state.nodes.map(n=>{const o=element("option",name(n));o.value=n.node_id;return o;}));
}
function nodeTransports(node){return ["RF","MQTT"].filter(value=>node.transport===value||node[value==="RF"?"seen_rf":"seen_mqtt"]);}
function nodeDate(value){
  if(value==null||value==="")return null;
  const date=new Date(typeof value==="number"?value*1000:value);
  return Number.isFinite(date.getTime())?date:null;
}
function nodeAge(value,now=Date.now()){
  const date=nodeDate(value);if(!date)return "–";
  const age=(now-date.getTime())/1000;
  if(age>=0&&age<60)return "nettopp";
  if(age>=60&&age<3600)return `${Math.floor(age/60)} min sidan`;
  if(age>=3600&&age<21600){const hours=Math.floor(age/3600);return hours===1?"1 time sidan":`${hours} timar sidan`;}
  const pad=value=>String(value).padStart(2,"0");
  const clock=`${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return age>=86400?`${pad(date.getDate())}.${pad(date.getMonth()+1)}.${pad(date.getFullYear()%100)} ${clock}`:`kl. ${clock}`;
}
function compareNodes(a,b){
  const mode=$("node-sort").value;
  if(mode==="seen"){
    const local=Number(!!b.is_local)-Number(!!a.is_local);if(local)return local;
    const recent=(nodeDate(b.last_heard)?.getTime()??-Infinity)-(nodeDate(a.last_heard)?.getTime()??-Infinity);if(recent)return recent;
  }
  const byId=String(a.node_id||"").localeCompare(String(b.node_id||""));
  return mode==="id"?byId:name(a).localeCompare(name(b),"nn-NO",{sensitivity:"base"})||byId;
}
function messageBytes(text){return Array.from(text).reduce((sum,char)=>{const cp=char.codePointAt(0);return sum+(cp<=0x7f?1:cp<=0x7ff?2:cp<=0xffff?3:4);},0);}
function updateComposer(){
  const item=state.selected;const connected=state.status.state==="tilkopla";
  const pending=pendingSends.get(item?.conversation);
  const sendable=!!item?.sendable&&connected&&state.selectionReady&&!state.busy&&!pending;
  const bytes=messageBytes($("message-text").value);const tooLong=bytes>237;
  $("message-text").disabled=!sendable;$("send-button").disabled=!sendable||tooLong||!$("message-text").value.trim();
  $("message-bytes").textContent=`${bytes} / 237 byte`;$("message-bytes").classList.toggle("over-limit",tooLong);
  $("message-text").setAttribute("aria-invalid",String(tooLong));
  $("send-hint").textContent=!item?"Vel ei samtale før du sender.":!connected?"Daemonen er ikkje tilkopla.":!state.selectionReady?"Ventar på meldingshistorikken. Sending er sperra.":!item.sendable?"Denne historiske ruta er berre lesbar.":item.kind==="public"?"Send til alle på denne kanalen.":`DM til ${item.peer_node} · ${item.channel_name||"vald kanal"}`;
  $("archive").hidden=item?.kind!=="dm";$("archive").disabled=state.busy||!!pending;
  $("send-resolution").hidden=!pending?.uncertain;
  $("check-send").disabled=state.busy||!!pending?.checking;
  $("release-send").disabled=state.busy||!!pending?.checking||!pending?.checked||pending?.status==="pending";
  $("send-resolution-text").textContent=pending?.message||"Ukjend sendestatus. Kontroller status før du sender på nytt.";
}
function saveDraft(){if(state.selected)state.drafts.set(state.selected.conversation,$("message-text").value);}
function renderConversationHeader(){
  const item=state.selected;
  $("chat-title").textContent=item?conversationName(item):"Vel ei samtale";
  $("chat-subtitle").textContent=!item?"Kanalar og direkte meldingar":item.kind==="public"?`Kanal · ${item.channel_name||""}`:`${item.peer_node} · ${item.channel_name||"kanalrute"}`;
}
function clearConversation(){
  saveDraft();state.selected=null;state.selectionReady=false;state.selectionEpoch++;state.messageKey="";
  $("message-text").value="";$("messages").replaceChildren(element("p","Vel ein kanal eller start ein DM.","empty"));
  $("new-messages").hidden=true;renderConversationHeader();renderConversations();updateComposer();
}
async function selectConversation(item){
  saveDraft();state.selected=item;state.selectionReady=false;state.selectionEpoch++;state.messageKey="";
  $("message-text").value=state.drafts.get(item.conversation)||"";$("new-messages").hidden=true;
  $("messages").replaceChildren(element("p","Hentar meldingshistorikk …","empty"));
  renderConversationHeader();renderConversations();updateComposer();await loadMessages(true,true);
}
async function loadMessages(markRead=false,scrollToEnd=false){
  const item=state.selected;if(!item)return;const route=item.conversation;
  const epoch=state.selectionEpoch;const request=++state.messageRequest;
  const current=()=>state.selectionEpoch===epoch&&state.selected?.conversation===route&&request===state.messageRequest;
  let data;
  try{data=await api("messages",{conversation:route,...(item.merged_routes?.length?{conversations:[route,...item.merged_routes].filter(r=>r.startsWith("dm:"))}:{}),limit:300,mark_read:markRead&&document.visibilityState==="visible"});}
  catch(error){if(!current())return;state.selectionReady=false;updateComposer();throw error;}
  if(!current())return;
  const firstLoad=state.messageKey==="";state.selectionReady=true;renderConversationHeader();updateComposer();
  const key=JSON.stringify(data);if(key===state.messageKey)return;const previous=state.messageKey?JSON.parse(state.messageKey):[];state.messageKey=key;
  const root=$("messages");const top=root.scrollTop;const stick=root.scrollHeight-top-root.clientHeight<100;root.replaceChildren();
  for(const msg of data){const article=element("article",undefined,"message"+(msg.direction==="ut"?" outgoing":""));const meta=element("div",undefined,"message-meta");meta.append(element("strong",msg.direction==="ut"?"Du":nodeName(msg.from_node)),element("span",time(msg.timestamp)));article.append(meta,element("div",msg.text,"bubble"));const info=[msg.status,msg.transport,msg.rssi==null?"":`RSSI ${msg.rssi}`,msg.snr==null?"":`SNR ${msg.snr}`,msg.raw_metadata?.failure_reason||msg.failure_reason||msg.nak_reason||""].filter(Boolean).join(" · ");article.append(element("div",info,"message-status"+(msg.status==="feila"?" failed":"")));root.append(article);}
  if(!data.length)root.append(element("p","Ingen meldingar i denne samtalen enno.","empty"));
  if(stick||scrollToEnd||firstLoad){root.scrollTop=root.scrollHeight;$("new-messages").hidden=true;}
  else{root.scrollTop=top;if(data.length>previous.length||data.at(-1)?.id!==previous.at(-1)?.id)$("new-messages").hidden=false;}
}
$("new-messages").addEventListener("click",()=>{$("messages").scrollTop=$("messages").scrollHeight;$("new-messages").hidden=true;});
$("messages").addEventListener("scroll",()=>{const root=$("messages");if(root.scrollHeight-root.scrollTop-root.clientHeight<100)$("new-messages").hidden=true;});
async function refresh(){
  if(state.authenticated===false)return;
  const epoch=authEpoch;
  if(state.refreshing){state.again=true;return;}state.refreshing=true;
  try{const [status,channels,conversations,nodes]=await Promise.all([api("status"),api("channels"),api("conversations",{preferred_conversation:state.selected?.conversation||""}),api("nodes")]);
    if(epoch!==authEpoch||state.authenticated===false)return;
    const switched=state.gatewayId&&status.history_local_node_id&&state.gatewayId!==status.history_local_node_id;if(status.history_local_node_id)state.gatewayId=status.history_local_node_id;state.status=status;state.channels=channels;state.conversations=conversations;state.nodes=nodes;
    $("status").textContent=[status.state,status.connection_name,status.transport].filter(Boolean).join(" · ");
    renderIdentity();
    if(switched){invalidateHistorySettings();clearConversation();state.node=null;$("node-dialog").close();notice("Gatewayen er bytt. Vel ei samtale før du sender.");}
    if(state.selected){const fresh=conversations.find(c=>c.conversation===state.selected.conversation);if(fresh)state.selected=fresh;else state.selected.sendable=state.channels.some(c=>c.local_node_id===state.selected.local_node_id&&c.channel_key===state.selected.channel_key);}
    renderConversationHeader();renderConversations();renderNodes();updateComposer();await loadMessages(document.visibilityState==="visible");
  }catch(error){if(state.authenticated===false)return;$("status").textContent="Sambandet til daemonen er brote";state.status={};renderIdentity();updateComposer();notice(error.message);}
  finally{state.refreshing=false;if(state.again){state.again=false;setTimeout(()=>safe(refresh),300);}}
}
function openDm(nodeId=""){$("dm-node").value=nodeId;$("dm-channel").replaceChildren(...state.channels.map(c=>{const o=element("option",c.display_name||c.name||`Kanal ${c.channel_index}`);o.value=c.channel_key;return o;}));$("dm-dialog").showModal();}
$("new-dm").addEventListener("click",()=>openDm());$("close-dm").addEventListener("click",()=>$("dm-dialog").close());
$("dm-form").addEventListener("submit",event=>{event.preventDefault();const peer=$("dm-node").value.trim().toLowerCase();const channel=state.channels.find(c=>c.channel_key===$("dm-channel").value);if(!channel||!/^![0-9a-f]{8}$/.test(peer)){notice("Vel ein mottakar og ein aktiv kanal.");return;}if(peer===state.status.local_node_id){notice("Vel ein annan mottakar enn lokal node.");return;}
  const item={conversation:`dm:${channel.local_node_id}:${peer}:${channel.channel_key}`,kind:"dm",peer_node:peer,channel_key:channel.channel_key,channel_name:channel.display_name||channel.name,local_node_id:channel.local_node_id,sendable:true};$("dm-dialog").close();safe(()=>selectConversation(item));
});
function finishSend(operation) {
  pendingSends.delete(operation.item.conversation);
  if (state.drafts.get(operation.item.conversation) === operation.text) {
    state.drafts.delete(operation.item.conversation);
  }
  if (state.selected?.conversation === operation.item.conversation &&
      $("message-text").value === operation.text) $("message-text").value = "";
}

$("send-form").addEventListener("submit", event => {
  event.preventDefault();
  return safe(async () => {
    if (state.busy || !state.selectionReady || state.status.state !== "tilkopla" ||
        !state.selected?.sendable || pendingSends.has(state.selected.conversation)) return;
    const item = {...state.selected};
    const text = $("message-text").value;
    if (!text.trim()) return;
    if (messageBytes(text) > 237) {
      notice("Meldinga er for lang. Maksimal lengd er 237 UTF-8-byte.");
      return;
    }
    const operation = {id: newOperationId(), item, text, uncertain: false, checking: false};
    saveDraft();
    pendingSends.set(item.conversation, operation);
    state.busy = true;
    updateComposer();
    try {
      await api(item.kind === "public" ? "send_public" : "send_dm", {
        conversation: item.conversation, text,
        ...(item.kind === "dm" ? {node_id: item.peer_node} : {}),
      }, operation.id);
      finishSend(operation);
      notice();
      await refresh();
    } catch (error) {
      if (error.uncertain) {
        operation.uncertain = true;
        operation.message = "Ukjend sendestatus. Meldinga kan vere send. Kontroller status eller historikken før eit nytt forsøk.";
      } else pendingSends.delete(item.conversation);
      throw error;
    } finally {
      state.busy = false;
      updateComposer();
    }
  });
});

async function reconcileSend(operation) {
  operation.checking = true;
  updateComposer();
  try {
    const outcome = await webRequest("operation", {operation_id: operation.id});
    if (!["pending", "completed", "unknown"].includes(outcome?.state)) {
      throw new Error("Sendestatus kunne ikkje lesast. Prøv å kontrollere status igjen.");
    }
    operation.status = outcome.state;
    operation.checked = true;
    if (outcome.state === "completed" && outcome.status === 200 && outcome.result?.ok) {
      finishSend(operation);
      notice("Sendinga er registrert. Sjå leveringsstatus i historikken.");
      await refresh();
    } else {
      operation.message = outcome.state === "pending"
        ? "Sendinga blir framleis handsama. Vent litt og kontroller status igjen."
        : "Sendestatus kunne ikkje stadfestast. Kontroller historikken før du frigjev skrivefeltet. Inga melding er send på nytt.";
    }
  } finally {
    operation.checking = false;
    updateComposer();
  }
}

$("check-send").addEventListener("click", () => safe(async () => {
  const operation = pendingSends.get(state.selected?.conversation);
  if (!operation || operation.checking || state.busy) return;
  await reconcileSend(operation);
}));

$("release-send").addEventListener("click", () => safe(async () => {
  const operation = pendingSends.get(state.selected?.conversation);
  if (!operation || !operation.checked || operation.checking || state.busy ||
      operation.status === "pending") return;
  const epoch = state.selectionEpoch;
  await reconcileSend(operation);
  if (pendingSends.get(operation.item.conversation) !== operation ||
      state.selectionEpoch !== epoch || operation.status === "pending") return;
  if (!confirm("Har du kontrollert historikken? Meldinga kan allereie vere send. Frigje skrivefeltet utan å sende noko?")) return;
  pendingSends.delete(operation.item.conversation);
  notice("Skrivefeltet er frigjeve. Ingen ny melding er send.");
  updateComposer();
}));
$("message-text").addEventListener("input",()=>{saveDraft();updateComposer();});
$("message-text").addEventListener("keydown",event=>{if(event.key==="Enter"&&(event.ctrlKey||event.metaKey)){event.preventDefault();$("send-form").requestSubmit();}});
$("archive").addEventListener("click",()=>safe(async()=>{const item=state.selected;if(!item||item.kind!=="dm"||state.busy||pendingSends.has(item.conversation))return;const epoch=state.selectionEpoch;saveDraft();state.busy=true;updateComposer();try{await api("archive_conversation",{node_id:item.peer_node,conversation:item.conversation});if(state.selectionEpoch===epoch)clearConversation();await refresh();}finally{state.busy=false;updateComposer();}}));
$("node-search").addEventListener("input",renderNodes);
$("node-transport-filter").addEventListener("change",renderNodes);
$("node-sort").addEventListener("change",renderNodes);
async function openNode(node){state.node=node;state.tab="overview";$("node-title").textContent=`${name(node)} · ${node.node_id}`;$("node-dialog").showModal();await renderNode();}
const labels={node_id:"Node-ID",long_name:"Namn",short_name:"Kortnamn",hw_model:"Maskinvare",role:"Rolle",battery_level:"Batteri (%)",voltage:"Spenning",last_heard:"Sist høyrd",timestamp:"Tid",received_at:"Motteken",latitude:"Breiddegrad",longitude:"Lengdegrad",altitude:"Høgd",transport:"Transport",snr:"SNR",rssi:"RSSI",hops_away:"Hopp",status:"Status",action:"Handling",kind:"Type",local_node_id:"Lokal node",channel_name:"Kanal",updated_at:"Oppdatert"};
function display(value){if(value==null)return "—";if(typeof value==="object")return JSON.stringify(value,null,2);return String(value);}
function tableRecord(record){const table=element("table");for(const [key,value] of Object.entries(record||{})){if(["raw_metadata","raw_payload","gateway_profile_id"].includes(key))continue;const row=element("tr");row.append(element("th",labels[key]||key.replaceAll("_"," ")),element("td",display(value)));table.append(row);}return table;}
const telemetryKinds = {device:"Eining",environment:"Miljø",air_quality:"Luftkvalitet",
  power:"Straum",local_stats:"Lokal statistikk",health:"Helse",host:"Vert",
  traffic_management:"Trafikkstyring"};
const metricInfo = {
  battery_level:["Batteri","%"],voltage:["Spenning","V"],
  channel_utilization:["Kanalbruk","%"],air_util_tx:["Sendetid","%"],
  uptime_seconds:["Oppetid","s"],temperature:["Temperatur","°C"],
  relative_humidity:["Luftfukt","%"],barometric_pressure:["Lufttrykk","hPa"],
  gas_resistance:["Gassmotstand","MΩ"],current:["Straum","A"],iaq:["Luftkvalitetsindeks",""],
  distance:["Avstand","mm"],lux:["Lysstyrke","lx"],white_lux:["Kvitt lys","lx"],
  ir_lux:["Infraraudt lys","lx"],uv_lux:["UV-lys","lx"],wind_direction:["Vindretning","°"],
  wind_speed:["Vindfart","m/s"],wind_gust:["Vindkast","m/s"],wind_lull:["Minste vindfart","m/s"],
  weight:["Vekt","kg"],rainfall_1h:["Nedbør siste time","mm"],rainfall_24h:["Nedbør siste døgn","mm"],
  soil_moisture:["Jordfukt","%"],soil_temperature:["Jordtemperatur","°C"],
  one_wire_temperature:["Ekstern temperatur","°C"],co2:["CO₂","ppm"],
};
const nodeNumber = new Intl.NumberFormat(["nn-NO","nb-NO"], {maximumFractionDigits:3});
function fieldName(key) {return key.replace(/([a-z0-9])([A-Z])/g,"$1_$2").toLowerCase();}
function readableField(key) {return key.replaceAll("_"," ").replace(/^./,c=>c.toUpperCase());}
function metricLabel(key) {return metricInfo[key]?.[0] || readableField(key);}
function metricValue(key, value) {
  if (value == null || value === "") return "—";
  if (key === "battery_level" && typeof value === "number" && value > 100) return "Ekstern straum";
  if (typeof value !== "number" || !Number.isFinite(value)) return display(value);
  if (key === "uptime_seconds" && value >= 0) {
    const days=Math.floor(value/86400),hours=Math.floor(value%86400/3600),minutes=Math.floor(value%3600/60);
    return value < 60 ? `${nodeNumber.format(value)} s`
      : `${days ? `${days} d ` : ""}${hours || days ? `${hours} t ` : ""}${minutes} min`;
  }
  return `${nodeNumber.format(value)}${metricInfo[key]?.[1] ? ` ${metricInfo[key][1]}` : ""}`;
}
function normalizedMetrics(sample) {
  return Object.fromEntries(Object.entries(sample?.metrics || {}).map(([key,value])=>[fieldName(key),value]));
}
function nodeFacts(items) {
  const list=element("dl",undefined,"node-facts");
  for (const [label,value] of items) {
    const fact=element("div");
    fact.append(element("dt",label),element("dd",value == null || value === "" ? "—" : value));
    list.append(fact);
  }
  return list;
}
function nodeSection(title, ...children) {
  const section=element("section",undefined,"node-section");
  section.append(element("h3",title),...children);
  return section;
}
function nodeDetails(record) {
  const details=element("details",undefined,"node-details");
  details.append(element("summary","Vis tekniske detaljar"),tableRecord(record));
  return details;
}
function renderNodeOverview(root, result) {
  const node=result.node || result;
  const grid=element("div",undefined,"node-overview-grid");
  grid.append(nodeSection("Identitet",nodeFacts([
    ["Langt namn",node.long_name],["Kortnamn",node.short_name],["Node-ID",node.node_id],
    ["Maskinvare",node.hw_model],["Rolle",node.role],
  ])),nodeSection("Samband",nodeFacts([
    ["Sist høyrd",positionTimestamp(node.last_heard)],["Transport",nodeTransports(node).join(" + ") || node.transport || "—"],
    ["Hopp",node.hops_away],["SNR",node.snr == null ? "—" : `${nodeNumber.format(node.snr)} dB`],
    ["RSSI",node.rssi == null ? "—" : `${nodeNumber.format(node.rssi)} dBm`],
    ["Lokal gateway",node.local_node_id ? nodeName(node.local_node_id) : "—"],
  ])));
  root.append(grid);
  const deviceMetrics=normalizedMetrics(result.latest_telemetry?.device);
  grid.append(nodeSection("Straum",nodeFacts([
    ["Batteri",metricValue("battery_level",deviceMetrics.battery_level ?? node.battery_level)],
    ["Spenning",metricValue("voltage",deviceMetrics.voltage ?? node.voltage)],
  ])));
  const latest=Object.entries(result.latest_telemetry || {});
  const readings=element("div",undefined,"node-reading-grid");
  for (const [kind,sample] of latest) {
    const panel=nodeSection(telemetryKinds[kind] || readableField(kind),
      element("p",`${positionTimestamp(sample.sample_time || sample.received_at)} · ${sample.transport || "—"}`,"node-help"),
      nodeFacts(Object.entries(normalizedMetrics(sample)).map(([key,value])=>[metricLabel(key),metricValue(key,value)])));
    readings.append(panel);
  }
  if (!latest.length) readings.append(nodeSection("Eining",nodeFacts([
    ["Batteri",metricValue("battery_level",node.battery_level)],
    ["Spenning",metricValue("voltage",node.voltage)],
  ])));
  root.append(nodeSection("Siste telemetri",readings));
  const counts=result.counts || {};
  root.append(nodeSection("Lagra historikk",nodeFacts([
    ["Telemetrirapportar",counts.telemetry ?? 0],["Posisjonsrapportar",counts.positions ?? 0],
    ["Traceroute",counts.traceroutes ?? 0],
  ])));
  if (result.latest_position) {
    const position=result.latest_position;
    root.append(nodeSection("Siste posisjon",nodeFacts([
      ["Tidspunkt",positionTimestamp(position.sample_time || position.received_at)],
      ["Breiddegrad",positionCoordinate(position.latitude,90)],
      ["Lengdegrad",positionCoordinate(position.longitude,180)],
      ["Høgd (moh.)",position.altitude_msl],
    ])));
  }
  const details=nodeDetails(node);
  // Keep additional and future backend fields available without crowding the summary.
  for (const [key,value] of Object.entries(result)) {
    if (key === "node" || value == null || !result.node) continue;
    details.append(element("h4",({latest_telemetry:"Siste telemetri",latest_position:"Siste posisjon",counts:"Historikktal"})[key] || readableField(key)),
      typeof value === "object" ? tableRecord(value) : element("p",display(value)));
  }
  root.append(details);
}
function renderTelemetry(root, samples) {
  if (!samples.length) {
    root.append(element("p","Ingen lagra telemetrirapportar enno.","empty"));
    return;
  }
  root.append(element("p",`${samples.length} telemetrirapportar · grupperte etter måletype · nyaste måling først${samples.length >= 100 ? " · viser dei siste 100 mottekne rapportane" : ""}.`,"node-help"));
  const grouped=new Map();
  for (const sample of samples.slice().sort((a,b)=>
    (nodeDate(b.sample_time)?.getTime() ?? nodeDate(b.received_at)?.getTime() ?? 0)
    - (nodeDate(a.sample_time)?.getTime() ?? nodeDate(a.received_at)?.getTime() ?? 0)
    || (b.id ?? 0)-(a.id ?? 0))) {
    const kind=sample.kind || "telemetry";
    if (!grouped.has(kind)) grouped.set(kind,[]);
    grouped.get(kind).push(sample);
  }
  for (const [kind,rows] of grouped) {
    const metrics=rows.map(normalizedMetrics);
    const keys=[...new Set(metrics.flatMap(row=>Object.keys(row)))];
    const ordered=[...Object.keys(metricInfo).filter(key=>keys.includes(key)),...keys.filter(key=>!metricInfo[key]).sort()];
    const title=telemetryKinds[kind] || readableField(kind);
    const scroll=element("div",undefined,"telemetry-scroll");
    scroll.tabIndex=0;scroll.setAttribute("role","region");
    scroll.setAttribute("aria-label",`${title}, rull for fleire kolonnar`);
    const table=element("table",undefined,"telemetry-table");
    table.append(element("caption",title,"sr-only"));
    const head=element("thead"),heading=element("tr"),body=element("tbody");
    for (const label of ["Tidspunkt","Transport",...ordered.map(metricLabel),"Detaljar"]) {
      const th=element("th",label);th.scope="col";heading.append(th);
    }
    head.append(heading);
    rows.forEach((sample,index)=>{
      const row=element("tr");
      row.append(element("td",positionTimestamp(sample.sample_time || sample.received_at)),
        element("td",sample.transport || "—"));
      for (const key of ordered) row.append(element("td",metricValue(key,metrics[index][key])));
      const cell=element("td");cell.append(nodeDetails(sample));row.append(cell);body.append(row);
    });
    table.append(head,body);scroll.append(table);
    root.append(nodeSection(`${title} · ${rows.length}`,scroll));
  }
}
const traceNumber = new Intl.NumberFormat("nb-NO", {maximumFractionDigits: 2});

function traceTimestamp(value) {
  const date = new Date(value);
  return value && !Number.isNaN(date.getTime()) ? date.toLocaleString("nb-NO", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }) : "Ikkje oppgitt";
}

function traceMetric(label, value) {
  const metric = element("div", undefined, "trace-metric");
  metric.append(element("small", label), element("strong", value));
  return metric;
}

function traceDirection(title, route) {
  const panel = element("section", undefined, "trace-direction");
  panel.setAttribute("aria-label", title);
  const header = element("div", undefined, "trace-direction-head");
  header.append(element("h4", title), element("span", route ? `${route.links} hopp` : "Ikkje oppgitt"));
  panel.append(header);
  if (!route) {
    panel.append(element("p", "Denne ruta blei ikkje rapportert.", "empty"));
    return panel;
  }
  const list = element("ol", undefined, "trace-route");
  route.hops.forEach((hop, index) => {
    const item = element("li");
    if (index) {
      const link = element("div", undefined, "trace-link");
      const snr = hop.snr === null ? "SNR ikkje oppgitt" : `SNR ${traceNumber.format(hop.snr)} dB`;
      link.append(element("span", "↓", "trace-arrow"), element("span", snr, "trace-snr"));
      item.append(link);
    }
    const node = element("div", undefined, "trace-node");
    const identity = element("div", undefined, "trace-identity");
    identity.append(element("strong", hop.name), element("small", hop.id));
    const role = index === 0 ? "Start" : index === route.hops.length - 1 ? "Mål" : "Mellomnode";
    node.append(element("span", index, "trace-step"), identity, element("span", role, "trace-role"));
    item.append(node);
    list.append(item);
  });
  panel.append(list);
  const minimum = route.lowestSnr === null ? "Ikkje oppgitt" : `${traceNumber.format(route.lowestSnr)} dB`;
  const summary = element("div", undefined, "trace-route-summary");
  summary.append(traceMetric("Lågaste oppgitte SNR", minimum),
    traceMetric("Hopp med SNR", `${route.measured} av ${route.links}`));
  panel.append(summary);
  return panel;
}

function renderTraceroutes(root, actions) {
  if (!actions.length) {
    root.append(element("p", "Ingen lagra traceroute-forsøk enno.", "empty"));
    return;
  }
  const sorted = actions.slice().sort((a, b) =>
    (Date.parse(b.started_at) || 0) - (Date.parse(a.started_at) || 0));
  for (const action of sorted) {
    const view = tracerouteModel(action, state.nodes);
    const card = element("article", undefined, "trace-card");
    const header = element("div", undefined, "trace-card-head");
    const heading = element("div");
    heading.append(element("h3", `Traceroute til ${view.target.name}`),
      element("p", traceTimestamp(action.started_at)));
    const statusClass = action.status === "completed" ? "complete" : action.status === "failed" ? "failed" : "pending";
    header.append(heading, element("span", view.status, `trace-status ${statusClass}`));
    card.append(header);
    const metrics = element("div", undefined, "trace-metrics");
    metrics.append(traceMetric("Lokal node", view.source.name),
      traceMetric("Svartid", view.duration === null ? "Ikkje oppgitt" : `${traceNumber.format(view.duration)} s`),
      traceMetric("Hopp fram / retur", `${view.forward?.links ?? "—"} / ${view.back?.links ?? "—"}`));
    card.append(metrics);
    if (action.status === "failed") card.append(element("p", action.error || "Førespurnaden feila.", "trace-error"));
    if (action.status === "started") card.append(element("p", "Ventar på svar frå noden. Resultatet blir lagra i historikken.", "trace-pending"));
    const directions = element("div", undefined, "trace-directions");
    directions.append(traceDirection("Framrute", view.forward), traceDirection("Returrute", view.back));
    card.append(directions);
    const details = element("details", undefined, "trace-details");
    details.append(element("summary", "Vis tekniske detaljar"), tableRecord({
      "Lokal node": `${view.source.name} · ${view.source.id}`,
      "Mottakar": `${view.target.name} · ${view.target.id}`,
      "Starta": traceTimestamp(action.started_at),
      "Avslutta": traceTimestamp(action.finished_at),
      "Pakke-ID": action.packet_id,
      "Førespurnads-ID": action.action_id,
    }));
    card.append(details);
    root.append(card);
  }
  root.append(element("p", "SNR viser mottaket frå førre node på den aktuelle ruta. Manglande målingar blir ikkje estimerte. Nodenamn blir henta frå den kjende nodelista.", "trace-help"));
}
function positionTimestamp(value) {
  return nodeDate(value)?.toLocaleString(["nn-NO","nb-NO"], {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }) || "—";
}

function positionCoordinate(value, maximum) {
  return typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= maximum
    ? value.toFixed(7) : null;
}

function renderPositions(root, positions) {
  if (!positions.length) {
    root.append(element("p", "Ingen lagra posisjonsrapportar enno.", "empty"));
    return;
  }
  const sorted = positions.slice().sort((a, b) =>
    (nodeDate(b.sample_time)?.getTime() ?? nodeDate(b.received_at)?.getTime() ?? 0)
    - (nodeDate(a.sample_time)?.getTime() ?? nodeDate(a.received_at)?.getTime() ?? 0)
    || (b.id ?? 0) - (a.id ?? 0));
  const count = positions.length === 1 ? "1 posisjonsrapport" : `${positions.length} posisjonsrapportar`;
  root.append(element("p", `${count} · nyaste først${positions.length >= 100 ? " · viser dei siste 100 mottekne rapportane" : ""}.`, "position-help"));
  const scroll = element("div", undefined, "position-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "Posisjonshistorikk, rull for fleire rapportar og kolonnar");
  const table = element("table", undefined, "position-table");
  table.append(element("caption", "Posisjonshistorikk", "sr-only"));
  const head = element("thead");
  const headings = element("tr");
  for (const title of ["Tidspunkt", "Breiddegrad", "Lengdegrad", "Høgd (moh.)", "Satellittar", "Transport", "Kart", "Detaljar"]) {
    const th = element("th", title);
    th.scope = "col";
    headings.append(th);
  }
  head.append(headings);
  const body = element("tbody");
  sorted.forEach((position, index) => {
    const row = element("tr");
    const latitude = positionCoordinate(position.latitude, 90);
    const longitude = positionCoordinate(position.longitude, 180);
    for (const value of [positionTimestamp(nodeDate(position.sample_time) ? position.sample_time : position.received_at),
      latitude, longitude, position.altitude_msl, position.sats_in_view, position.transport]) {
      row.append(element("td", display(value)));
    }
    const mapCell = element("td");
    if (latitude !== null && longitude !== null) {
      const link = element("a", "Google Maps ↗");
      link.href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${latitude},${longitude}`)}`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", `Opne ${latitude}, ${longitude} i Google Maps (ny fane)`);
      mapCell.append(link);
    } else mapCell.textContent = "—";
    const detailRow = element("tr", undefined, "position-detail-row");
    detailRow.id = `position-details-${index}`;
    detailRow.hidden = true;
    const detailCell = element("td");
    detailCell.colSpan = 8;
    const positionLabels = {
      id: "Observasjons-ID", dedupe_key: "Duplikatnøkkel", packet_id: "Pakke-ID",
      sample_time: "Posisjonstid", altitude_msl: "Høgd over havet (m)",
      altitude_hae: "Høgd over ellipsoiden (m)", geoidal_separation: "Geoideseparasjon (m)",
      pdop: "PDOP", hdop: "HDOP", vdop: "VDOP", gps_accuracy_mm: "GPS-avvik (mm)",
      ground_speed: "Fart over bakken (m/s)", ground_track: "Kurs (°)",
      fix_quality: "GPS-fikskvalitet", fix_type: "GPS-fikstype", sats_in_view: "Satellittar",
      location_source: "Posisjonskjelde", altitude_source: "Høgdekjelde",
      precision_bits: "Presisjonsbitar", metadata: "Posisjonsdata",
      hop_limit: "Hoppgrense", hop_start: "Opphavleg hoppgrense",
      gateway_node_id: "Gateway-ID", gateway_transport: "Gatewaytransport",
    };
    const details = Object.fromEntries(Object.entries(position)
      .filter(([key]) => !["raw_metadata", "raw_payload", "gateway_profile_id"].includes(key))
      .map(([key, value]) => [positionLabels[key] || labels[key] || key.replaceAll("_", " "),
        ["sample_time", "received_at"].includes(key) ? positionTimestamp(value) : value]));
    detailCell.append(tableRecord(details));
    detailRow.append(detailCell);
    const toggleCell = element("td");
    const toggle = element("button", "Vis", "quiet");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", detailRow.id);
    toggle.setAttribute("aria-label", `Vis detaljar for rapport ${index + 1}`);
    toggle.addEventListener("click", () => {
      detailRow.hidden = !detailRow.hidden;
      toggle.textContent = detailRow.hidden ? "Vis" : "Skjul";
      toggle.setAttribute("aria-expanded", String(!detailRow.hidden));
      toggle.setAttribute("aria-label", `${detailRow.hidden ? "Vis" : "Skjul"} detaljar for rapport ${index + 1}`);
    });
    toggleCell.append(toggle);
    row.append(mapCell, toggleCell);
    body.append(row, detailRow);
  });
  table.append(head, body);
  scroll.append(table);
  root.append(scroll);
}

async function renderNode() {
  const node=state.node,tab=state.tab;
  if (!node) return;
  const root=$("node-data");
  root.replaceChildren(element("p","Hentar nodeinformasjon …","empty"));
  root.scrollTop=0;
  document.querySelectorAll("[data-tab]").forEach(button=>{
    button.classList.toggle("active",button.dataset.tab===tab);
    button.setAttribute("aria-pressed",String(button.dataset.tab===tab));
  });
  const commands={overview:"node_overview",telemetry:"node_telemetry",positions:"node_positions",traceroutes:"node_actions"};
  let result;
  try {
    result=await api(commands[tab],{node_id:node.node_id,
      ...(tab==="overview"?{}:{limit:100}),...(tab==="traceroutes"?{action:"traceroute"}:{})});
  } catch (error) {
    if (state.node===node && state.tab===tab) {
      root.replaceChildren(element("p","Kunne ikkje hente nodeinformasjonen. Vel fana igjen for å prøve på nytt.","empty"));
    }
    throw error;
  }
  if (state.node!==node || state.tab!==tab) return;
  root.replaceChildren();
  if (tab==="overview") renderNodeOverview(root,result);
  else if (tab==="telemetry") renderTelemetry(root,result);
  else if (tab==="traceroutes") renderTraceroutes(root,result);
  else if (tab==="positions") renderPositions(root,result);
  for (const [button,action] of [["traceroute","traceroute"],["position-exchange","position_exchange"]]) {
    const availability=await api("node_action_availability",{node_id:node.node_id,action});
    if (state.node!==node || state.tab!==tab) return;
    $(button).disabled=!availability.available;
    $(button).title=availability.reason||"";
  }
}

$("node-tabs").addEventListener("click",event=>{const button=event.target.closest("[data-tab]");if(button){state.tab=button.dataset.tab;safe(renderNode);}});$("close-node").addEventListener("click",()=>$("node-dialog").close());$("node-dm").addEventListener("click",()=>{const id=state.node?.node_id;$("node-dialog").close();openDm(id);});
$("node-dialog").addEventListener("click", event => {
  const dialog = $("node-dialog");
  if (event.target !== dialog) return;
  const bounds = dialog.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right ||
      event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
});
async function nodeAction(action){const node=state.node;if(!node)return;if(action==="position_exchange"&&!confirm(`Utveksle posisjonsdata med ${node.node_id}? Dette kan dele lokal posisjon når kanalen tillèt det.`))return;const data=await api("node_action",{node_id:node.node_id,action});notice(`Handling starta mot ${node.node_id}. Resultatet blir lagra i historikken.`);if(data.action_id){const check=async()=>{const result=await api("node_action_status",{action_id:data.action_id});if(result.status==="started")setTimeout(()=>safe(check),3000);else if(state.node===node&&$("node-dialog").open)await renderNode();};setTimeout(()=>safe(check),3000);}await renderNode();}
$("traceroute").addEventListener("click",()=>safe(()=>nodeAction("traceroute")));$("position-exchange").addEventListener("click",()=>safe(()=>nodeAction("position_exchange")));
async function showConnections(){const data=await api("connections");const root=$("profiles");root.replaceChildren();for(const profile of data.profiles||[]){const button=element("button",undefined,"list-button");button.append(element("strong",`${profile.profile_id===data.active_profile_id?"● ":""}${profile.name||profile.profile_id}`),element("small",`${profile.transport||""} · ${profile.target||profile.endpoint||profile.device||profile.host||profile.ble_identifier||""}`));button.addEventListener("click",()=>safe(async()=>{await api("connect",{profile_id:profile.profile_id});$("connections-dialog").close();await refresh();}));root.append(button);}if(!root.children.length)root.append(element("p","Ingen lagra profilar.","empty"));}
$("connections-button").addEventListener("click",()=>safe(async()=>{$("connections-dialog").showModal();await showConnections();}));$("close-connections").addEventListener("click",()=>$("connections-dialog").close());
async function discover(ble){const button=$(ble?"discover-ble":"discover");button.disabled=true;try{const data=await api(ble?"discover_ble_connections":"discover_connections",ble?{}:{include_ble:false});const root=$("discovery");root.replaceChildren();for(const kind of ["serial","tcp","ble"]){for(const item of data[kind]||[]){const target=item.target||item.device||item.address||item.identifier;const button=element("button",`${item.name||item.description||target} · ${target}`,"list-button");button.addEventListener("click",()=>{$("connect-target").value=target;});root.append(button);}}for(const key of ["tcp_error","serial_error","ble_error"])if(data[key])root.append(element("p",data[key]));if(!root.children.length)root.append(element("p","Ingen nye einingar funne.","empty"));}finally{button.disabled=false;}}
$("discover").addEventListener("click",()=>safe(()=>discover(false)));$("discover-ble").addEventListener("click",()=>safe(()=>discover(true)));
$("connect-form").addEventListener("submit",event=>{event.preventDefault();safe(async()=>{await api("connect",{target:$("connect-target").value.trim()});$("connections-dialog").close();await refresh();});});
document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible")safe(refresh);});
async function start() {
  try { await renewSession(); }
  catch (_) {
    $("status").textContent = "Sambandet er brote. Koplar til på nytt …";
    setTimeout(() => safe(start), 3000);
    return;
  }
  if (state.authenticated === false) return;
  await refresh();
  if (state.authenticated === false || eventStream) return;
  const stream = new EventSource(endpoint("events"));
  eventStream = stream;
  let timer;
  stream.onmessage = () => {
    clearTimeout(timer);
    timer = setTimeout(() => safe(refresh), 250);
  };
  stream.onerror = () => {
    $("status").textContent = "Sanntid koplar til på nytt …";
    safe(() => renewSession());
  };
  if (!refreshInterval) refreshInterval = setInterval(() => {
    if (document.visibilityState === "visible" && state.authenticated !== false) safe(refresh);
  }, 15000);
}
const historySettings = {data:null,preview:null,epoch:0,busy:false};
function invalidateCleanup() {
  historySettings.epoch++;
  historySettings.preview=null;
  $("cleanup-preview").hidden=true;
  $("confirm-cleanup").disabled=true;
}
function invalidateHistorySettings() {
  invalidateCleanup();
  historySettings.data=null;
  $("save-retention").disabled=true;
  $("preview-cleanup").disabled=true;
  $("settings-status").textContent="Gateway eller innlogging er endra. Lukk og opne innstillingane på nytt.";
}
function settingsControls() {
  const disabled=historySettings.busy || !historySettings.data?.local_node_id;
  for (const id of ["save-retention","preview-cleanup","retention-days","retention-custom","cleanup-scope","cleanup-days","cleanup-custom"]) $(id).disabled=disabled;
  $("confirm-cleanup").disabled=disabled || !historySettings.preview;
}
function historyCounts(counts) {
  return [["messages","meldingar"],["telemetry_samples","telemetrirapportar"],["positions","posisjonar"],["node_actions","nodehandlingar"]]
    .map(([key,label])=>`${Number(counts?.[key] || 0).toLocaleString("nb-NO")} ${label}`).join(", ");
}
function settingsDays(prefix) {
  const selected=$(`${prefix}-days`).value;
  const raw=selected==="custom" ? $(`${prefix}-custom`).value : selected;
  const days=Number(raw);
  if (!raw || !Number.isInteger(days) || days<0 || days>36500 || (selected==="custom" && days===0)) throw new Error("Vel eit heilt tal frå 1 til 36500 dagar, eller det eigne valet utan aldersgrense.");
  return days;
}
function applyHistorySettings(data) {
  historySettings.data=data;
  $("settings-gateway").textContent=data.local_node_id ? `Gateway: ${nodeName(data.local_node_id)} · ${data.local_node_id}` : "Ingen aktiv gateway. Vel ei tilkopling først.";
  const days=data.retention_days ?? 0;
  $("retention-days").value=[0,7,30].includes(days) ? String(days) : "custom";
  $("retention-custom").value=String(days || 30);
  $("retention-custom-wrap").hidden=$("retention-days").value!=="custom";
  $("retention-review").hidden=!data.needs_review;
  $("retention-limit").textContent=`Aldersgrensa gjeld meldingar, telemetri, posisjonar og nodehandlingar. Meldingsgrensa er ${Number(data.max_messages || 50000).toLocaleString("nb-NO")}. Utan aldersgrense opphevar ikkje denne grensa. Lagra no: ${historyCounts(data.counts)}.`;
  settingsControls();
}
async function openHistorySettings() {
  invalidateCleanup();historySettings.data=null;settingsControls();
  const epoch=historySettings.epoch;
  $("settings-status").textContent="Hentar innstillingar …";
  $("settings-dialog").showModal();
  try {
    const data=await api("history_settings");
    if (epoch!==historySettings.epoch) return;
    applyHistorySettings(data);$("settings-status").textContent="";
  } catch(error) {if(epoch===historySettings.epoch)$("settings-status").textContent=error.message;}
}
async function historyOperation(fn) {
  if(historySettings.busy || !historySettings.data?.local_node_id)return;
  historySettings.busy=true;settingsControls();
  try {await fn();}
  catch(error){
    invalidateCleanup();
    $("settings-status").textContent=error.uncertain ? "Ukjent resultat. Handlinga blir ikkje send på nytt automatisk. Lukk og opne innstillingane for å kontrollere lagringsval og tal før du prøver igjen." : error.message;
  } finally {historySettings.busy=false;settingsControls();}
}
async function saveRetention() {
  return historyOperation(async()=>{
    const data=historySettings.data,days=settingsDays("retention");
    const description=days===0 ? "utan aldersgrense" : `i ${days} dagar`;
    if(!confirm(`Lagre historikk ${description} for ${nodeName(data.local_node_id)} (${data.local_node_id})? ${days ? "Eksisterande og framtidig historikk eldre enn denne grensa blir sletta automatisk. Dette kan ikkje angrast." : "Meldingsgrensa gjeld framleis."}`))return;
    invalidateCleanup();const epoch=historySettings.epoch;
    const result=await api("history_settings_set",{expected_local_node_id:data.local_node_id,retention_days:days,confirmed:true});
    if(epoch!==historySettings.epoch)return;
    applyHistorySettings(result);$("settings-status").textContent="Lagringsvalet er lagra.";
  });
}
async function previewCleanup() {
  return historyOperation(async()=>{
    invalidateCleanup();const epoch=historySettings.epoch;
    const data=historySettings.data,scope=$("cleanup-scope").value,days=settingsDays("cleanup");
    const preview=await api("history_cleanup_preview",{expected_local_node_id:data.local_node_id,scope,older_than_days:days});
    if(epoch!==historySettings.epoch || preview.local_node_id!==data.local_node_id)return;
    historySettings.preview={...preview,expiresAt:Date.now()+preview.expires_in*1000};
    $("cleanup-summary").textContent=`${nodeName(data.local_node_id)} (${data.local_node_id}): ${historyCounts(preview.counts)}. ${days ? `Eldre enn ${days} dagar (før ${time(preview.cutoff)}).` : "Alt i det valde omfanget, uansett alder."} Førehandsvisinga er gyldig i fem minutt.`;
    $("cleanup-preview").hidden=false;
    $("settings-status").textContent="Kontroller omfanget før du stadfestar sletting.";
  });
}
async function confirmCleanup() {
  return historyOperation(async()=>{
    const preview=historySettings.preview,data=historySettings.data;
    if(!preview || preview.local_node_id!==data.local_node_id || Date.now()>=preview.expiresAt){invalidateCleanup();throw new Error("Førehandsvisinga er utgått. Vis kva som blir sletta på nytt.");}
    if(!confirm(`Slett ${historyCounts(preview.counts)} frå ${nodeName(data.local_node_id)} (${data.local_node_id})? Dette kan ikkje angrast.`))return;
    invalidateCleanup();const epoch=historySettings.epoch;
    const result=await api("history_cleanup",{cleanup_token:preview.cleanup_token,expected_local_node_id:data.local_node_id,confirmed:true});
    if(epoch!==historySettings.epoch)return;
    $("settings-status").textContent=`Sletta: ${historyCounts(result.deleted)}.`;
    const current=await api("history_settings");
    if(epoch!==historySettings.epoch)return;
    applyHistorySettings(current);
    await refresh();
  });
}
$("settings-button").addEventListener("click",()=>safe(openHistorySettings));
$("close-settings").addEventListener("click",()=>$("settings-dialog").close());
$("settings-dialog").addEventListener("close",invalidateHistorySettings);
$("save-retention").addEventListener("click",saveRetention);
$("preview-cleanup").addEventListener("click",previewCleanup);
$("confirm-cleanup").addEventListener("click",confirmCleanup);
for(const prefix of ["retention","cleanup"]){
  $(`${prefix}-days`).addEventListener("change",()=>{
    $(`${prefix}-custom-wrap`).hidden=$(`${prefix}-days`).value!=="custom";
    invalidateCleanup();
  });
  $(`${prefix}-custom`).addEventListener("input",invalidateCleanup);
}
$("cleanup-scope").addEventListener("change",invalidateCleanup);

if (typeof meshpiAuth !== "undefined") meshpiAuth.init({
  session: applySession, lock: lockSession, renew: renewSession, resume: start,
  request: (path, fields) => webRequest(path, fields),
  keys: async () => {
    const {response, result} = await fetchJson(endpoint("auth/keys"), {headers: {"X-MeshPi-CSRF": state.csrf}});
    if (response.status === 401) {lockSession(); throw loginRequired();}
    if (!response.ok || !result?.ok) throw new Error(result?.error || "Kunne ikkje hente nøklane.");
    return result.data;
  },
});
safe(start);

$("nodes-button").addEventListener("click",()=>{$("mobile-nodes").append($("node-browser"));renderNodes();$("nodes-dialog").showModal();});
$("nodes-dialog").addEventListener("close",()=>{document.querySelector(".nodes-panel").append($("node-browser"));});
$("close-nodes").addEventListener("click",()=>$("nodes-dialog").close());
