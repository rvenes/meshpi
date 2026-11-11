"use strict";

function webLocale() {return typeof meshpiI18n === "undefined" ? "nn-NO" : meshpiI18n.locale();}
const $ = id => document.getElementById(id);
meshpiTheme.bindToggle($("theme-toggle"));
const endpoint = path => `${document.documentElement.dataset.basePath}/api/${path}`;
const state = {csrf:"",status:{},channels:[],conversations:[],nodes:[],selected:null,node:null,nodeGatewayId:null,tab:"overview",busy:false,refreshing:false,again:false,drafts:new Map(),messageKey:"",selectionReady:false,selectionEpoch:0,messageRequest:0,gatewayId:null};
const writeCommands = new Set(["send_public","send_dm","archive_conversation","unarchive_conversation","connect","discover_connections","discover_ble_connections","node_action","remove_nodes","history_settings_set","history_cleanup"]);
const pendingSends = new Map();
let nodeRemoval = null;
let nodeAvailabilityTimer = null;
let nodeAvailabilityRequest = 0;
function element(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=String(text??"—");if(cls)el.className=cls;return el;}
let autoCloseNotices = true;
try {autoCloseNotices = window.localStorage.getItem("meshpi-web-notices") !== "manual";}
catch (_) { /* Keep the default when local storage is unavailable. */ }
$("notice-auto-close").checked = autoCloseNotices;
let noticeTimer = null;
let noticeKind = "";
function notice(text="", kind="") {
  clearTimeout(noticeTimer);
  noticeTimer = null;
  noticeKind = text ? kind : "";
  $("notice-text").textContent = text;
  $("notice").hidden = !text;
  if (text && autoCloseNotices) {
    noticeTimer = setTimeout(() => notice(), 10000);
    noticeTimer?.unref?.();
  }
}
$("dismiss-notice").addEventListener("click",()=>notice());
function name(node){return node?.long_name||node?.short_name||node?.node_id||webText("Ukjend node");}
function nodeName(id){return name(state.nodes.find(n=>n.node_id===id)||{node_id:id});}
function renderTitle(){
  const label = document.documentElement.dataset.serverName || state.hostname || webText("Privat meshklient");
  document.title = `MeshPi · ${label}`;
}
function renderIdentity(){
  renderTitle();
  const host = state.hostname ? `MeshPi ${state.hostname}` : "MeshPi";
  const nodeId = state.status.state === "tilkopla" ? state.status.local_node_id : null;
  const node = nodeId ? `Node: ${nodeName(nodeId)}` : webText("Privat meshklient");
  $("host-name").textContent = host;
  $("host-name").title = host;
  $("gateway-name").textContent = node;
  $("gateway-name").title = nodeId ? `${node} · ${nodeId}` : node;
}
function time(value){if(!value)return "";const date=new Date(typeof value==="number"?value*1000:value);return Number.isNaN(date.getTime())?String(value):date.toLocaleString(webLocale(),{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false});}
function newOperationId(){
  // randomUUID needs HTTPS; getRandomValues also works over trusted LAN HTTP.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,"0")).join("");
}
let sessionPromise = null;
let eventStream = null;
let refreshInterval = null;
let authEpoch = 0;
function loginRequired() {
  const error = new Error(webText("Logg inn for å halde fram. Handlinga blei ikkje utført."));
  error.unauthenticated = true;
  error.uncertain = false;
  return error;
}
function lockSession() {
  if (state.authenticated !== false) authEpoch++;
  state.authenticated = false;
  stopNodeAvailability();
  if (typeof meshpiUpdates !== "undefined") meshpiUpdates.reset();
  invalidateHistorySettings();
  state.selectionReady = false;
  if (typeof updateComposer === "function") updateComposer();
  eventStream?.close();
  eventStream = null;
  state.hostname = "";
  renderTitle();
  $("host-name").textContent = "MeshPi";
  $("status").textContent = webText("Innlogging er nødvendig");
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
    const response = await fetch(url, {...options, headers: {...options.headers, "X-MeshPi-Language": typeof meshpiI18n === "undefined" ? "nn" : meshpiI18n.getLanguage()}, ...(controller ? {signal: controller.signal} : {})});
    return {response, result: await response.json()};
  } catch (_) {
    throw connectionError();
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}
function connectionError() {
  const error = new Error(webText("Sambandet blei brote. Prøver igjen automatisk."));
  error.transient = true;
  return error;
}
async function renewSession(rejectedToken = null) {
  if (rejectedToken !== null && state.csrf && state.csrf !== rejectedToken) return;
  if (!sessionPromise) {
    const epoch = authEpoch;
    sessionPromise = (async () => {
      const {response, result: session} = await fetchJson(endpoint("session"));
      if (!response.ok || !session?.ok || typeof session.data?.csrf !== "string" || !session.data.csrf) {
        throw connectionError();
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
      if (!result || typeof result.ok !== "boolean") throw new Error(webText("Ugyldig svar"));
    } catch (_) {
      const error = new Error(write
        ? webText("Ukjend sendestatus. Kontroller sendestatus og historikken før du sender på nytt.")
        : webText("Sambandet blei brote. Prøver igjen ved neste oppfrisking."));
      error.uncertain = write;
      error.transient = !write;
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
        result.error = webText("Sambandet er fornya. Handlinga blei avvist før utføring. Prøv igjen når du er klar.");
      }
    }
    if (!response.ok || !result.ok) {
      const error = new Error(result.error || webText("Førespurnaden feila."));
      error.uncertain = write && ![400, 403, 404, 409, 413, 415].includes(response.status);
      error.transient = !write && (response.status >= 500 || response.status === 408);
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
function reportError(error) {
  notice(error.message || webText("Sambandet blei brote. Kontroller historikken før du sender på nytt."),
    error.transient && !error.uncertain ? "connection" : "");
}
function connectionRestored() {if (autoCloseNotices && noticeKind === "connection") notice();}
async function safe(fn){try{await fn();}catch(error){reportError(error);}}
function conversationName(item){return item.kind==="public"?(item.channel_name||item.name||webText("Kanal")):(item.long_name||item.short_name||nodeName(item.peer_node));}
function channelOrder(item) {
  const channel = state.channels.find(c => c.channel_key === item.channel_key &&
    (!item.local_node_id || c.local_node_id === item.local_node_id)) || item;
  return channel.role === "PRIMARY" ? -1 : (channel.channel_index ?? Number.MAX_SAFE_INTEGER);
}
function renderConversations(){
  const root=$("conversations");root.replaceChildren();
  for(const kind of ["public","dm"]){const items=state.conversations.filter(c=>c.kind===kind);if(!items.length)continue;root.append(element("p",kind==="public"?webText("Kanalar"):webText("Direkte meldingar"),"list-heading"));
    if (kind === "public") items.sort((a,b) => channelOrder(a)-channelOrder(b) ||
      conversationName(a).localeCompare(conversationName(b), webLocale()));
    for(const item of items){const button=element("button",undefined,"list-button"+(state.selected?.conversation===item.conversation?" active":""));button.type="button";button.setAttribute("aria-pressed",String(state.selected?.conversation===item.conversation));
      const title=element("strong",conversationName(item));if(item.unread){const badge=element("span",item.unread,"badge");title.prepend(badge);}button.append(title,element("small",item.last_text||item.channel_name||webText("Ingen meldingar enno")));button.addEventListener("click",()=>safe(()=>selectConversation(item)));root.append(button);
    }
  }
  if(!root.children.length)root.append(element("p",webText("Ingen samtalar. Vel ein gateway i Tilkoplingar."),"empty"));
}
function renderNodes(){
  const root=$("nodes");root.replaceChildren();
  const query=$("node-search").value.trim().toLocaleLowerCase(webLocale());
  const mode=$("node-transport-filter").value;
  const nodes=state.nodes.filter(node=>[node.long_name,node.short_name,node.node_id].join(" ").toLocaleLowerCase(webLocale()).includes(query)&&(mode==="all"||nodeTransports(node).join("+")==={rf:"RF",mqtt:"MQTT",both:"RF+MQTT"}[mode]));
  nodes.sort(compareNodes);
  $("node-count").textContent=nodes.length===state.nodes.length?String(nodes.length):`${nodes.length} / ${state.nodes.length}`;
  for(const node of nodes){
    const button=element("button",undefined,"list-button");button.type="button";
    button.append(element("strong",name(node)),element("small",[node.node_id,nodeTransports(node).join(" + "),node.battery_level==null?"":metricValue("battery_level",node.battery_level)].filter(Boolean).join(" · ")),element("small",webText`sist ${nodeAge(node.last_heard)}`));
    button.addEventListener("click",()=>{$("nodes-dialog").close();safe(()=>openNode(node));});root.append(button);
  }
  if(!nodes.length)root.append(element("p",webText("Ingen nodar passar søket eller filteret."),"empty"));
  if ($("dm-dialog").open) renderDmRecipients();
  updateNodeRemovalButton();
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
  if(age>=0&&age<60)return webText("nettopp");
  if(age>=60&&age<3600)return webText`${Math.floor(age/60)} min sidan`;
  if(age>=3600&&age<21600){const hours=Math.floor(age/3600);return hours===1?webText("1 time sidan"):webText`${hours} timar sidan`;}
  const pad=value=>String(value).padStart(2,"0");
  const clock=`${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return age>=86400?`${pad(date.getDate())}.${pad(date.getMonth()+1)}.${pad(date.getFullYear()%100)} ${clock}`:webText`kl. ${clock}`;
}
function compareNodes(a,b){
  const mode=$("node-sort").value;
  if(mode==="seen"){
    const local=Number(!!b.is_local)-Number(!!a.is_local);if(local)return local;
    const recent=(nodeDate(b.last_heard)?.getTime()??-Infinity)-(nodeDate(a.last_heard)?.getTime()??-Infinity);if(recent)return recent;
  }
  const byId=String(a.node_id||"").localeCompare(String(b.node_id||""));
  return mode==="id"?byId:name(a).localeCompare(name(b),webLocale(),{sensitivity:"base"})||byId;
}
function messageBytes(text){return Array.from(text).reduce((sum,char)=>{const cp=char.codePointAt(0);return sum+(cp<=0x7f?1:cp<=0x7ff?2:cp<=0xffff?3:4);},0);}
function updateComposer(){
  const item=state.selected;const connected=state.status.state==="tilkopla";
  const pending=pendingSends.get(item?.conversation);
  const sendable=!!item?.sendable&&connected&&state.selectionReady&&!state.busy&&!pending;
  const bytes=messageBytes($("message-text").value);const tooLong=bytes>237;
  $("message-text").disabled=!sendable;$("send-button").disabled=!sendable||tooLong||!$("message-text").value.trim();
  $("message-bytes").textContent=webText`${bytes} / 237 byte`;$("message-bytes").classList.toggle("over-limit",tooLong);
  $("message-text").setAttribute("aria-invalid",String(tooLong));
  $("send-hint").textContent=!item?webText("Vel ei samtale før du sender."):!connected?webText("Daemonen er ikkje tilkopla."):!state.selectionReady?webText("Ventar på meldingshistorikken. Sending er sperra."):!item.sendable?webText("Denne historiske ruta er berre lesbar."):item.kind==="public"?webText("Send til alle på denne kanalen."):webText`DM til ${item.peer_node} · ${item.channel_name||webText("vald kanal")}`;
  $("archive").hidden=item?.kind!=="dm";$("archive").disabled=state.busy||!!pending;
  $("send-resolution").hidden=!pending?.uncertain;
  $("check-send").disabled=state.busy||!!pending?.checking;
  $("release-send").disabled=state.busy||!!pending?.checking||!pending?.checked||pending?.status==="pending";
  $("send-resolution-text").textContent=pending?.message||webText("Ukjend sendestatus. Kontroller status før du sender på nytt.");
}
function saveDraft(){if(state.selected)state.drafts.set(state.selected.conversation,$("message-text").value);}
function renderConversationHeader(){
  const item=state.selected;
  $("chat-title").textContent=item?conversationName(item):webText("Vel ei samtale");
  $("chat-subtitle").textContent=!item?webText("Kanalar og direkte meldingar"):item.kind==="public"?webText`Kanal · ${item.channel_name||""}`:`${item.peer_node} · ${item.channel_name||webText("kanalrute")}`;
}
function clearConversation(){
  saveDraft();state.selected=null;state.selectionReady=false;state.selectionEpoch++;state.messageKey="";
  $("message-text").value="";$("messages").replaceChildren(element("p",webText("Vel ein kanal eller start ein DM."),"empty"));
  $("new-messages").hidden=true;renderConversationHeader();renderConversations();updateComposer();
}
async function selectConversation(item){
  saveDraft();state.selected=item;state.selectionReady=false;state.selectionEpoch++;state.messageKey="";
  $("message-text").value=state.drafts.get(item.conversation)||"";$("new-messages").hidden=true;
  $("messages").replaceChildren(element("p",webText("Hentar meldingshistorikk …"),"empty"));
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
  const key=JSON.stringify(data);const language=typeof meshpiI18n === "undefined" ? "nn" : meshpiI18n.getLanguage();if(key===state.messageKey && state.messageLanguage===language)return;state.messageLanguage=language;const previous=state.messageKey?JSON.parse(state.messageKey):[];state.messageKey=key;
  const root=$("messages");const top=root.scrollTop;const stick=root.scrollHeight-top-root.clientHeight<100;root.replaceChildren();
  for(const msg of data){const article=element("article",undefined,"message"+(msg.direction==="ut"?" outgoing":""));const meta=element("div",undefined,"message-meta");meta.append(element("strong",msg.direction==="ut"?webText("Du"):nodeName(msg.from_node)),element("span",time(msg.timestamp)));article.append(meta,element("div",msg.text,"bubble"));const info=[webText(msg.status || ""),msg.transport,msg.rssi==null?"":`RSSI ${msg.rssi}`,msg.snr==null?"":`SNR ${msg.snr}`,msg.raw_metadata?.failure_reason||msg.failure_reason||msg.nak_reason||""].filter(Boolean).join(" · ");article.append(element("div",info,"message-status"+(msg.status==="feila"?" failed":"")));root.append(article);}
  if(!data.length)root.append(element("p",webText("Ingen meldingar i denne samtalen enno."),"empty"));
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
    $("status").textContent=[webText(status.state),status.connection_name,status.transport].filter(Boolean).join(" · ");
    renderIdentity();
    if(switched){invalidateHistorySettings();clearConversation();stopNodeAvailability();state.node=null;state.nodeGatewayId=null;$("node-dialog").close();notice(webText("Gatewayen er bytt. Vel ei samtale før du sender."));}
    if(state.selected){const fresh=conversations.find(c=>c.conversation===state.selected.conversation);if(fresh)state.selected=fresh;else state.selected.sendable=state.channels.some(c=>c.local_node_id===state.selected.local_node_id&&c.channel_key===state.selected.channel_key);}
    renderConversationHeader();renderConversations();renderNodes();updateComposer();await loadMessages(document.visibilityState==="visible");connectionRestored();
  }catch(error){if(state.authenticated===false)return;$("status").textContent=webText("Sambandet til daemonen er brote");state.status={};renderIdentity();updateComposer();reportError(error);}
  finally{state.refreshing=false;if(state.again){state.again=false;setTimeout(()=>safe(refresh),300);}}
}
function renderDmRecipients() {
  const query = $("dm-node").value.trim().toLocaleLowerCase(webLocale());
  const root = $("dm-recipients");
  const nodes = state.nodes.filter(node => node.node_id !== state.status.local_node_id &&
    [node.long_name, node.short_name, node.node_id].join(" ").toLocaleLowerCase(webLocale()).includes(query));
  nodes.sort((a, b) => name(a).localeCompare(name(b), webLocale()) || a.node_id.localeCompare(b.node_id));
  root.replaceChildren();
  for (const node of nodes) {
    const selected = query === node.node_id;
    const button = element("button", undefined, "list-button" + (selected ? " active" : ""));
    button.type = "button";
    button.setAttribute("aria-pressed", String(selected));
    button.append(element("strong", name(node)),
      element("small", [node.short_name, node.node_id].filter(Boolean).join(" · ")));
    button.addEventListener("click", () => {
      $("dm-node").value = node.node_id;
      renderDmRecipients();
    });
    root.append(button);
  }
  if (!nodes.length) root.append(element("p", query
    ? webText("Ingen kjende nodar passar søket. Du kan òg skrive ein full node-ID.")
    : webText("Ingen kjende mottakarar enno. Skriv ein full node-ID."), "dm-help"));
  $("dm-open").disabled = !/^![0-9a-f]{8}$/.test(query) || query === state.status.local_node_id || !state.channels.length;
}
function openDm(nodeId=""){$("dm-node").value=nodeId;$("dm-channel").replaceChildren(...state.channels.map(c=>{const o=element("option",c.display_name||c.name||webText`Kanal ${c.channel_index}`);o.value=c.channel_key;return o;}));renderDmRecipients();$("dm-dialog").showModal();}
function dmConversation(peer, channel) {
  return {conversation:`dm:${channel.local_node_id}:${peer}:${channel.channel_key}`,
    kind:"dm",peer_node:peer,channel_key:channel.channel_key,
    channel_name:channel.display_name||channel.name,
    local_node_id:channel.local_node_id,sendable:true};
}
async function openNodeDm() {
  const node = state.node;
  if (!node) return;
  const channel = state.channels.find(c => c.role === "PRIMARY") ||
    state.channels.find(c => c.channel_index === 0);
  if (!channel) throw new Error(webText("Ingen primærkanal er tilgjengeleg. Vent på kanaloversikta og prøv igjen."));
  if (node.node_id === state.status.local_node_id) {
    notice(webText("Vel ein annan mottakar enn lokal node."));
    return;
  }
  const item = {...dmConversation(node.node_id, channel),
    long_name:node.long_name,short_name:node.short_name};
  $("node-dialog").close();
  await selectConversation(item);
}
$("dm-node").addEventListener("input", renderDmRecipients);
$("new-dm").addEventListener("click",()=>openDm());$("close-dm").addEventListener("click",()=>$("dm-dialog").close());
$("dm-form").addEventListener("submit",event=>{event.preventDefault();const peer=$("dm-node").value.trim().toLowerCase();const channel=state.channels.find(c=>c.channel_key===$("dm-channel").value);if(!channel||!/^![0-9a-f]{8}$/.test(peer)){notice(webText("Vel ein mottakar og ein aktiv kanal."));return;}if(peer===state.status.local_node_id){notice(webText("Vel ein annan mottakar enn lokal node."));return;}
  const item=dmConversation(peer,channel);$("dm-dialog").close();safe(()=>selectConversation(item));
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
      notice(webText("Meldinga er for lang. Maksimal lengd er 237 UTF-8-byte."));
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
        operation.message = webText("Ukjend sendestatus. Meldinga kan vere send. Kontroller status eller historikken før eit nytt forsøk.");
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
      throw new Error(webText("Sendestatus kunne ikkje lesast. Prøv å kontrollere status igjen."));
    }
    operation.status = outcome.state;
    operation.checked = true;
    if (outcome.state === "completed" && outcome.status === 200 && outcome.result?.ok) {
      finishSend(operation);
      notice(webText("Sendinga er registrert. Sjå leveringsstatus i historikken."));
      await refresh();
    } else {
      operation.message = outcome.state === "pending"
        ? webText("Sendinga blir framleis handsama. Vent litt og kontroller status igjen.")
        : webText("Sendestatus kunne ikkje stadfestast. Kontroller historikken før du frigjev skrivefeltet. Inga melding er send på nytt.");
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
  if (!confirm(webText("Har du kontrollert historikken? Meldinga kan allereie vere send. Frigje skrivefeltet utan å sende noko?"))) return;
  pendingSends.delete(operation.item.conversation);
  notice(webText("Skrivefeltet er frigjeve. Ingen ny melding er send."));
  updateComposer();
}));
$("message-text").addEventListener("input",()=>{saveDraft();updateComposer();});
$("message-text").addEventListener("keydown",event=>{if(event.key==="Enter"&&(event.ctrlKey||event.metaKey)){event.preventDefault();$("send-form").requestSubmit();}});
$("archive").addEventListener("click",()=>safe(async()=>{const item=state.selected;if(!item||item.kind!=="dm"||state.busy||pendingSends.has(item.conversation))return;const epoch=state.selectionEpoch;saveDraft();state.busy=true;updateComposer();try{await api("archive_conversation",{node_id:item.peer_node,conversation:item.conversation});if(state.selectionEpoch===epoch)clearConversation();await refresh();}finally{state.busy=false;updateComposer();}}));
$("node-search").addEventListener("input",renderNodes);
$("node-transport-filter").addEventListener("change",renderNodes);
$("node-sort").addEventListener("change",renderNodes);
async function openNode(node){stopNodeAvailability();state.node=node;state.nodeGatewayId=node.local_node_id||state.status.history_local_node_id||state.status.local_node_id||state.gatewayId;state.tab="overview";$("node-title").textContent=`${name(node)} · ${node.node_id}`;$("node-dialog").showModal();await renderNode();}
const labels = () => ({node_id:webText("Node-ID"),long_name:webText("Namn"),short_name:webText("Kortnamn"),hw_model:webText("Maskinvare"),role:webText("Rolle"),battery_level:webText("Batteri (%)"),voltage:webText("Spenning"),last_heard:webText("Sist høyrd"),timestamp:webText("Tid"),received_at:webText("Motteken"),latitude:webText("Breiddegrad"),longitude:webText("Lengdegrad"),altitude:webText("Høgd"),transport:webText("Transport"),snr:"SNR",rssi:"RSSI",hops_away:webText("Hopp"),status:webText("Status"),action:webText("Handling"),kind:webText("Type"),local_node_id:webText("Lokal node"),channel_name:webText("Kanal"),updated_at:webText("Oppdatert")});
function display(value){if(value==null)return "—";if(typeof value==="object")return JSON.stringify(value,null,2);return String(value);}
function tableRecord(record){const table=element("table");for(const [key,value] of Object.entries(record||{})){if(["raw_metadata","raw_payload","gateway_profile_id"].includes(key))continue;const row=element("tr");row.append(element("th",labels()[key]||key.replaceAll("_"," ")),element("td",display(value)));table.append(row);}return table;}
const telemetryKinds = () => ({device:webText("Eining"),environment:webText("Miljø"),air_quality:webText("Luftkvalitet"),
  power:webText("Straum"),local_stats:webText("Lokal statistikk"),health:webText("Helse"),host:webText("Vert"),
  traffic_management:webText("Trafikkstyring")});
const metricInfo = () => ({
  battery_level:[webText("Batteri"),"%"],voltage:[webText("Spenning"),"V"],
  channel_utilization:[webText("Kanalbruk"),"%"],air_util_tx:[webText("Sendetid"),"%"],
  uptime_seconds:[webText("Oppetid"),"s"],temperature:[webText("Temperatur"),"°C"],
  relative_humidity:[webText("Luftfukt"),"%"],barometric_pressure:[webText("Lufttrykk"),"hPa"],
  gas_resistance:[webText("Gassmotstand"),"MΩ"],current:[webText("Straum"),"A"],iaq:[webText("Luftkvalitetsindeks"),""],
  distance:[webText("Avstand"),"mm"],lux:[webText("Lysstyrke"),"lx"],white_lux:[webText("Kvitt lys"),"lx"],
  ir_lux:[webText("Infraraudt lys"),"lx"],uv_lux:[webText("UV-lys"),"lx"],wind_direction:[webText("Vindretning"),"°"],
  wind_speed:[webText("Vindfart"),"m/s"],wind_gust:[webText("Vindkast"),"m/s"],wind_lull:[webText("Minste vindfart"),"m/s"],
  weight:[webText("Vekt"),"kg"],rainfall_1h:[webText("Nedbør siste time"),"mm"],rainfall_24h:[webText("Nedbør siste døgn"),"mm"],
  soil_moisture:[webText("Jordfukt"),"%"],soil_temperature:[webText("Jordtemperatur"),"°C"],
  one_wire_temperature:[webText("Ekstern temperatur"),"°C"],co2:["CO₂","ppm"],
});
const nodeNumber = {format: value => new Intl.NumberFormat(webLocale(), {maximumFractionDigits:3}).format(value)};
function fieldName(key) {return key.replace(/([a-z0-9])([A-Z])/g,"$1_$2").toLowerCase();}
function readableField(key) {return key.replaceAll("_"," ").replace(/^./,c=>c.toUpperCase());}
function metricLabel(key) {return metricInfo()[key]?.[0] || readableField(key);}
function metricValue(key, value) {
  if (value == null || value === "") return "—";
  if (key === "battery_level" && typeof value === "number" && value > 100) return webText("Ekstern straum");
  if (typeof value !== "number" || !Number.isFinite(value)) return display(value);
  if (key === "uptime_seconds" && value >= 0) {
    const days=Math.floor(value/86400),hours=Math.floor(value%86400/3600),minutes=Math.floor(value%3600/60);
    return value < 60 ? `${nodeNumber.format(value)} s`
      : `${days ? `${days} d ` : ""}${hours || days ? webText`${hours} t ` : ""}${minutes} min`;
  }
  return `${nodeNumber.format(value)}${metricInfo()[key]?.[1] ? ` ${metricInfo()[key][1]}` : ""}`;
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
  details.append(element("summary",webText("Vis tekniske detaljar")),tableRecord(record));
  return details;
}
function nodeInfoExchangeText(action) {
  if (action.status === "started") return webText("Nodeinfo er sendt. Ventar på svar frå noden …");
  if (action.status === "failed") return action.error || webText("Utvekslinga fekk ikkje svar.");
  const result = action.result || {};
  return webText("Nodeinfo motteken. ") + (result.public_key_received
    ? webText("Noden sende ein offentleg nøkkel.")
    : webText("Noden sende ikkje ein offentleg nøkkel.")) + (result.public_key_shared
    ? webText(" Vår offentlege nøkkel blei send med førespurnaden.")
    : webText(" Gatewayen hadde ingen offentleg nøkkel å sende."));
}
function renderNodeOverview(root, result) {
  const node=result.node || result;
  const grid=element("div",undefined,"node-overview-grid");
  grid.append(nodeSection(webText("Identitet"),nodeFacts([
    [webText("Langt namn"),node.long_name],[webText("Kortnamn"),node.short_name],[webText("Node-ID"),node.node_id],
    [webText("Maskinvare"),node.hw_model],[webText("Rolle"),node.role],
  ])),nodeSection(webText("Samband"),nodeFacts([
    [webText("Sist høyrd"),positionTimestamp(node.last_heard)],[webText("Transport"),nodeTransports(node).join(" + ") || node.transport || "—"],
    [webText("Hopp"),node.hops_away],["SNR",node.snr == null ? "—" : `${nodeNumber.format(node.snr)} dB`],
    ["RSSI",node.rssi == null ? "—" : `${nodeNumber.format(node.rssi)} dBm`],
    [webText("Lokal gateway"),node.local_node_id ? nodeName(node.local_node_id) : "—"],
  ])));
  root.append(grid);
  const exchange = result.node_info_exchanges?.[0];
  root.append(nodeSection(webText("Nodeinfo og offentleg nøkkel"),
    element("p", webText("Bruk «Utveksle nodeinfo» for å sende vår nodeinformasjon og be om den andre noden sin informasjon og offentlege nøkkel."), "node-help"),
    ...(exchange ? [element("p", `${traceTimestamp(exchange.started_at)} · ${nodeInfoExchangeText(exchange)}`, "node-help")] : [])));
  const deviceMetrics=normalizedMetrics(result.latest_telemetry?.device);
  grid.append(nodeSection(webText("Straum"),nodeFacts([
    [webText("Batteri"),metricValue("battery_level",deviceMetrics.battery_level ?? node.battery_level)],
    [webText("Spenning"),metricValue("voltage",deviceMetrics.voltage ?? node.voltage)],
  ])));
  const latest=Object.entries(result.latest_telemetry || {});
  const readings=element("div",undefined,"node-reading-grid");
  for (const [kind,sample] of latest) {
    const panel=nodeSection(telemetryKinds()[kind] || readableField(kind),
      element("p",`${positionTimestamp(sample.sample_time || sample.received_at)} · ${sample.transport || "—"}`,"node-help"),
      nodeFacts(Object.entries(normalizedMetrics(sample)).map(([key,value])=>[metricLabel(key),metricValue(key,value)])));
    readings.append(panel);
  }
  if (!latest.length) readings.append(nodeSection(webText("Eining"),nodeFacts([
    [webText("Batteri"),metricValue("battery_level",node.battery_level)],
    [webText("Spenning"),metricValue("voltage",node.voltage)],
  ])));
  root.append(nodeSection(webText("Siste telemetri"),readings));
  const counts=result.counts || {};
  root.append(nodeSection(webText("Lagra historikk"),nodeFacts([
    [webText("Telemetrirapportar"),counts.telemetry ?? 0],[webText("Posisjonsrapportar"),counts.positions ?? 0],
    [webText("Traceroute"),counts.traceroutes ?? 0],
  ])));
  if (result.latest_position) {
    const position=result.latest_position;
    root.append(nodeSection(webText("Siste posisjon"),nodeFacts([
      [webText("Tidspunkt"),positionTimestamp(position.sample_time || position.received_at)],
      [webText("Breiddegrad"),positionCoordinate(position.latitude,90)],
      [webText("Lengdegrad"),positionCoordinate(position.longitude,180)],
      [webText("Høgd (moh.)"),position.altitude_msl],
    ])));
  }
  const details=nodeDetails(node);
  // Keep additional and future backend fields available without crowding the summary.
  for (const [key,value] of Object.entries(result)) {
    if (key === "node" || key === "node_info_exchanges" || value == null || !result.node) continue;
    details.append(element("h4",({latest_telemetry:webText("Siste telemetri"),latest_position:webText("Siste posisjon"),counts:webText("Historikktal")})[key] || readableField(key)),
      typeof value === "object" ? tableRecord(value) : element("p",display(value)));
  }
  root.append(details);
}
function renderTelemetry(root, samples) {
  if (!samples.length) {
    root.append(element("p",webText("Ingen lagra telemetrirapportar enno."),"empty"));
    return;
  }
  root.append(element("p",webText`${samples.length} telemetrirapportar · grupperte etter måletype · nyaste måling først${samples.length >= 100 ? webText(" · viser dei siste 100 mottekne rapportane") : ""}.`,"node-help"));
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
    const ordered=[...Object.keys(metricInfo()).filter(key=>keys.includes(key)),...keys.filter(key=>!metricInfo()[key]).sort()];
    const title=telemetryKinds()[kind] || readableField(kind);
    const scroll=element("div",undefined,"telemetry-scroll");
    scroll.tabIndex=0;scroll.setAttribute("role","region");
    scroll.setAttribute("aria-label",webText`${title}, rull for fleire kolonnar`);
    const table=element("table",undefined,"telemetry-table");
    table.append(element("caption",title,"sr-only"));
    const head=element("thead"),heading=element("tr"),body=element("tbody");
    for (const label of [webText("Tidspunkt"),webText("Transport"),...ordered.map(metricLabel),webText("Detaljar")]) {
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
const traceNumber = {format: value => new Intl.NumberFormat(webLocale(), {maximumFractionDigits:2}).format(value)};

function traceTimestamp(value) {
  const date = new Date(value);
  return value && !Number.isNaN(date.getTime()) ? date.toLocaleString(webLocale(), {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }) : webText("Ikkje oppgitt");
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
  header.append(element("h4", title), element("span", route ? webText`${route.links} hopp` : webText("Ikkje oppgitt")));
  panel.append(header);
  if (!route) {
    panel.append(element("p", webText("Denne ruta blei ikkje rapportert."), "empty"));
    return panel;
  }
  const list = element("ol", undefined, "trace-route");
  route.hops.forEach((hop, index) => {
    const item = element("li");
    if (index) {
      const link = element("div", undefined, "trace-link");
      const snr = hop.snr === null ? webText("SNR ikkje oppgitt") : `SNR ${traceNumber.format(hop.snr)} dB`;
      link.append(element("span", "↓", "trace-arrow"), element("span", snr, "trace-snr"));
      item.append(link);
    }
    const node = element("div", undefined, "trace-node");
    const identity = element("div", undefined, "trace-identity");
    identity.append(element("strong", hop.name), element("small", hop.id));
    const role = index === 0 ? webText("Start") : index === route.hops.length - 1 ? webText("Mål") : webText("Mellomnode");
    node.append(element("span", index, "trace-step"), identity, element("span", role, "trace-role"));
    item.append(node);
    list.append(item);
  });
  panel.append(list);
  const minimum = route.lowestSnr === null ? webText("Ikkje oppgitt") : `${traceNumber.format(route.lowestSnr)} dB`;
  const summary = element("div", undefined, "trace-route-summary");
  summary.append(traceMetric(webText("Lågaste oppgitte SNR"), minimum),
    traceMetric(webText("Hopp med SNR"), webText`${route.measured} av ${route.links}`));
  panel.append(summary);
  return panel;
}

function renderTraceroutes(root, actions) {
  if (!actions.length) {
    root.append(element("p", webText("Ingen lagra traceroute-forsøk enno."), "empty"));
    return;
  }
  const sorted = actions.slice().sort((a, b) =>
    (Date.parse(b.started_at) || 0) - (Date.parse(a.started_at) || 0));
  for (const action of sorted) {
    const view = tracerouteModel(action, state.nodes);
    const card = element("article", undefined, "trace-card");
    const header = element("div", undefined, "trace-card-head");
    const heading = element("div");
    heading.append(element("h3", webText`Traceroute til ${view.target.name}`),
      element("p", traceTimestamp(action.started_at)));
    const statusClass = action.status === "completed" ? "complete" : action.status === "failed" ? "failed" : "pending";
    header.append(heading, element("span", view.status, `trace-status ${statusClass}`));
    card.append(header);
    const metrics = element("div", undefined, "trace-metrics");
    metrics.append(traceMetric(webText("Lokal node"), view.source.name),
      traceMetric(webText("Svartid"), view.duration === null ? webText("Ikkje oppgitt") : `${traceNumber.format(view.duration)} s`),
      traceMetric(webText("Hopp fram / retur"), `${view.forward?.links ?? "—"} / ${view.back?.links ?? "—"}`));
    card.append(metrics);
    if (action.status === "failed") card.append(element("p", action.error || webText("Førespurnaden feila."), "trace-error"));
    if (action.status === "started") card.append(element("p", webText("Ventar på svar frå noden. Resultatet blir lagra i historikken."), "trace-pending"));
    const directions = element("div", undefined, "trace-directions");
    directions.append(traceDirection(webText("Framrute"), view.forward), traceDirection(webText("Returrute"), view.back));
    card.append(directions);
    const details = element("details", undefined, "trace-details");
    details.append(element("summary", webText("Vis tekniske detaljar")), tableRecord({
      [webText("Lokal node")]: `${view.source.name} · ${view.source.id}`,
      [webText("Mottakar")]: `${view.target.name} · ${view.target.id}`,
      [webText("Starta")]: traceTimestamp(action.started_at),
      [webText("Avslutta")]: traceTimestamp(action.finished_at),
      [webText("Pakke-ID")]: action.packet_id,
      [webText("Førespurnads-ID")]: action.action_id,
    }));
    card.append(details);
    root.append(card);
  }
  root.append(element("p", webText("SNR viser mottaket frå førre node på den aktuelle ruta. Manglande målingar blir ikkje estimerte. Nodenamn blir henta frå den kjende nodelista."), "trace-help"));
}
function positionTimestamp(value) {
  return nodeDate(value)?.toLocaleString(webLocale(), {
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
    root.append(element("p", webText("Ingen lagra posisjonsrapportar enno."), "empty"));
    return;
  }
  const sorted = positions.slice().sort((a, b) =>
    (nodeDate(b.sample_time)?.getTime() ?? nodeDate(b.received_at)?.getTime() ?? 0)
    - (nodeDate(a.sample_time)?.getTime() ?? nodeDate(a.received_at)?.getTime() ?? 0)
    || (b.id ?? 0) - (a.id ?? 0));
  const count = positions.length === 1 ? webText("1 posisjonsrapport") : webText`${positions.length} posisjonsrapportar`;
  root.append(element("p", webText`${count} · nyaste først${positions.length >= 100 ? webText(" · viser dei siste 100 mottekne rapportane") : ""}.`, "position-help"));
  const scroll = element("div", undefined, "position-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", webText("Posisjonshistorikk, rull for fleire rapportar og kolonnar"));
  const table = element("table", undefined, "position-table");
  table.append(element("caption", webText("Posisjonshistorikk"), "sr-only"));
  const head = element("thead");
  const headings = element("tr");
  for (const title of [webText("Tidspunkt"), webText("Breiddegrad"), webText("Lengdegrad"), webText("Høgd (moh.)"), webText("Satellittar"), webText("Transport"), webText("Kart"), webText("Detaljar")]) {
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
      const link = element("a", webText("Google Maps ↗"));
      link.href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${latitude},${longitude}`)}`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", webText`Opne ${latitude}, ${longitude} i Google Maps (ny fane)`);
      mapCell.append(link);
    } else mapCell.textContent = "—";
    const detailRow = element("tr", undefined, "position-detail-row");
    detailRow.id = `position-details-${index}`;
    detailRow.hidden = true;
    const detailCell = element("td");
    detailCell.colSpan = 8;
    const positionLabels = {
      id: webText("Observasjons-ID"), dedupe_key: webText("Duplikatnøkkel"), packet_id: webText("Pakke-ID"),
      sample_time: webText("Posisjonstid"), altitude_msl: webText("Høgd over havet (m)"),
      altitude_hae: webText("Høgd over ellipsoiden (m)"), geoidal_separation: webText("Geoideseparasjon (m)"),
      pdop: "PDOP", hdop: "HDOP", vdop: "VDOP", gps_accuracy_mm: webText("GPS-avvik (mm)"),
      ground_speed: webText("Fart over bakken (m/s)"), ground_track: webText("Kurs (°)"),
      fix_quality: webText("GPS-fikskvalitet"), fix_type: webText("GPS-fikstype"), sats_in_view: webText("Satellittar"),
      location_source: webText("Posisjonskjelde"), altitude_source: webText("Høgdekjelde"),
      precision_bits: webText("Presisjonsbitar"), metadata: webText("Posisjonsdata"),
      hop_limit: webText("Hoppgrense"), hop_start: webText("Opphavleg hoppgrense"),
      gateway_node_id: webText("Gateway-ID"), gateway_transport: webText("Gatewaytransport"),
    };
    const details = Object.fromEntries(Object.entries(position)
      .filter(([key]) => !["raw_metadata", "raw_payload", "gateway_profile_id"].includes(key))
      .map(([key, value]) => [positionLabels[key] || labels()[key] || key.replaceAll("_", " "),
        ["sample_time", "received_at"].includes(key) ? positionTimestamp(value) : value]));
    detailCell.append(tableRecord(details));
    detailRow.append(detailCell);
    const toggleCell = element("td");
    const toggle = element("button", webText("Vis"), "quiet");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", detailRow.id);
    toggle.setAttribute("aria-label", webText`Vis detaljar for rapport ${index + 1}`);
    toggle.addEventListener("click", () => {
      detailRow.hidden = !detailRow.hidden;
      toggle.textContent = detailRow.hidden ? webText("Vis") : webText("Skjul");
      toggle.setAttribute("aria-expanded", String(!detailRow.hidden));
      toggle.setAttribute("aria-label", webText`${detailRow.hidden ? webText("Vis") : webText("Skjul")} detaljar for rapport ${index + 1}`);
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
  updateNodeRemovalButton();
  const root=$("node-data");
  root.replaceChildren(element("p",webText("Hentar nodeinformasjon …"),"empty"));
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
      root.replaceChildren(element("p",webText("Kunne ikkje hente nodeinformasjonen. Vel fana igjen for å prøve på nytt."),"empty"));
    }
    throw error;
  }
  if (state.node!==node || state.tab!==tab) return;
  root.replaceChildren();
  if (tab==="overview") renderNodeOverview(root,result);
  else if (tab==="telemetry") renderTelemetry(root,result);
  else if (tab==="traceroutes") renderTraceroutes(root,result);
  else if (tab==="positions") renderPositions(root,result);
  await refreshNodeActionButtons(node);
}

function stopNodeAvailability() {
  clearTimeout(nodeAvailabilityTimer);
  nodeAvailabilityTimer = null;
  nodeAvailabilityRequest++;
}

async function refreshNodeActionButtons(node=state.node) {
  stopNodeAvailability();
  if (!node || state.authenticated === false) return;
  const request = nodeAvailabilityRequest, gateway = state.nodeGatewayId;
  let nextCheck = Infinity;
  try {
    for (const [button,action] of [["traceroute","traceroute"],["position-exchange","position_exchange"],["node-info-exchange","node_info_exchange"]]) {
      let availability;
      try { availability=await api("node_action_availability",{node_id:node.node_id,action}); }
      catch (error) {
        if (action !== "node_info_exchange" || error.transient) throw error;
        availability={available:false,reason:webText("Nodeinfo-utveksling krev ein oppdatert MeshPi-daemon. ") + error.message};
      }
      if (request!==nodeAvailabilityRequest || state.node!==node || state.nodeGatewayId!==gateway) return;
      $(button).disabled=!availability.available;
      $(button).title=availability.reason||"";
      if (availability.cooldown_seconds > 0) nextCheck = Math.min(nextCheck, availability.cooldown_seconds);
    }
  } catch (error) {
    nextCheck = 3;
    throw error;
  } finally {
    if (Number.isFinite(nextCheck) && request===nodeAvailabilityRequest && state.node===node &&
        state.nodeGatewayId===gateway && state.authenticated!==false && $("node-dialog").open) {
      nodeAvailabilityTimer = setTimeout(() => {
        nodeAvailabilityTimer = null;
        if (state.node===node && state.nodeGatewayId===gateway && $("node-dialog").open) {
          safe(() => refreshNodeActionButtons(node));
        }
      }, Math.max(1, nextCheck) * 1000);
      nodeAvailabilityTimer?.unref?.();
    }
  }
}

function updateNodeRemovalButton() {
  const button = $("node-delete");
  const checking = nodeRemoval?.phase === "uncertain" &&
    nodeRemoval.node_id === state.node?.node_id &&
    nodeRemoval.local_node_id === state.status.local_node_id;
  button.textContent = checking ? webText("Kontroller sletting") : nodeRemoval ? webText("Slettar …") : webText("Slett node");
  button.disabled = !checking && (!!nodeRemoval || state.status.state !== "tilkopla" ||
    !state.status.local_node_id || !state.node || state.node.node_id === state.status.local_node_id ||
    state.node.node_id === "!ffffffff" || parseInt(state.node.node_id.slice(1),16) < 4);
  button.title = state.node?.node_id === state.status.local_node_id
    ? webText("Den lokale gatewayen kan ikkje slettast.") : "";
}
async function finishNodeRemoval(data, pending) {
  if (nodeRemoval !== pending) return;
  if (data?.action !== "remove_nodes" || data.local_node_id !== pending.local_node_id ||
      data.node_id !== pending.node_id || !data.action_id) {
    throw new Error(webText("Svaret stadfestar ikkje den valde slettinga."));
  }
  pending.action_id = data.action_id;
  if (data.status === "started") {
    setTimeout(() => checkNodeRemoval(), 1500);
    return;
  }
  if (data.status !== "failed" && !(data.status === "completed" &&
      data.result?.contacts?.some(c => c.node_id === pending.node_id && c.status === "removed"))) {
    throw new Error(webText("Slettinga er ikkje stadfesta. Kontroller nodelista før du prøver igjen."));
  }
  nodeRemoval = null;
  if (data.status === "completed") {
    if (state.node?.node_id === pending.node_id &&
        state.status.local_node_id === pending.local_node_id) {
      $("node-dialog").close();state.node = null;
    }
    notice(webText`Noden ${pending.node_id} er sletta frå nodelista. Historikken er bevart.`);
  } else notice(data.error || webText("Noden kunne ikkje slettast."));
  updateNodeRemovalButton();
  await refresh();
}
function uncertainNodeRemoval(error, pending) {
  if (nodeRemoval !== pending) return;
  pending.phase = "uncertain";
  notice(webText("Slettinga er ikkje stadfesta. Bruk «Kontroller sletting»; ingen ny sletting blir send. ") +
    (error.message || ""));
  updateNodeRemovalButton();
}
async function checkNodeRemoval() {
  const pending = nodeRemoval;
  if (!pending || pending.phase === "checking") return;
  pending.phase = "checking";updateNodeRemovalButton();
  try {
    let data;
    if (pending.action_id) data = await api("node_action_status", {action_id:pending.action_id});
    else {
      const operation = await webRequest("operation", {operation_id:pending.operation_id});
      if (operation.state === "pending") {
        pending.phase = "waiting";
        setTimeout(() => checkNodeRemoval(), 1500);
        return;
      }
      if (operation.state !== "completed" || !operation.result?.ok) {
        throw new Error(operation.result?.error || webText("Statusen er ukjend etter sambandsbrotet."));
      }
      data = operation.result.data;
    }
    pending.phase = "waiting";
    await finishNodeRemoval(data, pending);
  } catch (error) {uncertainNodeRemoval(error, pending);}
}
async function removeSelectedNode() {
  if (nodeRemoval) {
    if (nodeRemoval.phase === "uncertain") await checkNodeRemoval();
    return;
  }
  const node = state.node, local = state.status.local_node_id;
  if (!node || !local || state.status.state !== "tilkopla") return;
  if (node.node_id === local || node.node_id === "!ffffffff" ||
      parseInt(node.node_id.slice(1),16) < 4) return;
  if (!confirm(webText`Slette ${name(node)} (${node.node_id}) frå nodelista til ${nodeName(local)} (${local})?\n\nKontakten blir fjerna frå den lokale radioen og MeshPi-lista. Meldingshistorikk og andre lagra rapportar blir bevarte. Noden kan dukke opp att ved ny trafikk.`)) return;
  const pending = {node_id:node.node_id,local_node_id:local,
    operation_id:newOperationId(),phase:"waiting"};
  nodeRemoval = pending;updateNodeRemovalButton();
  try {
    const data = await api("remove_nodes", {node_ids:[node.node_id],
      expected_local_node_id:local,confirmed:true}, pending.operation_id);
    await finishNodeRemoval(data, pending);
  } catch (error) {
    if (error.uncertain === false) {
      nodeRemoval = null;updateNodeRemovalButton();reportError(error);
    } else uncertainNodeRemoval(error, pending);
  }
}
$("node-delete").addEventListener("click", removeSelectedNode);
$("node-tabs").addEventListener("click",event=>{const button=event.target.closest("[data-tab]");if(button){state.tab=button.dataset.tab;safe(renderNode);}});$("close-node").addEventListener("click",()=>$("node-dialog").close());$("node-dm").addEventListener("click",()=>safe(openNodeDm));
$("node-dialog").addEventListener("click", event => {
  const dialog = $("node-dialog");
  if (event.target !== dialog) return;
  const bounds = dialog.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right ||
      event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
});
$("node-dialog").addEventListener("close", stopNodeAvailability);
async function nodeAction(action) {
  const node=state.node, gateway=state.nodeGatewayId;
  if (!node) return;
  if (!gateway) throw new Error(webText("Gatewayen er bytt. Vel ei samtale før du sender."));
  if (action==="position_exchange" && !confirm(webText`Utveksle posisjonsdata med ${node.node_id}? Dette kan dele lokal posisjon når kanalen tillèt det.`)) return;
  const data=await api("node_action", {node_id:node.node_id, action, expected_local_node_id:gateway});
  notice(webText`Handling starta mot ${node.node_id}. Resultatet blir lagra i historikken.`);
  if (data.action_id) {
    const check=async()=>{
      const result=await api("node_action_status", {action_id:data.action_id});
      if (result.status==="started") setTimeout(()=>safe(check),3000);
      else {
        if (state.node===node && state.nodeGatewayId===gateway && $("node-dialog").open) await renderNode();
        if (action==="node_info_exchange") notice(result.status==="failed" ? result.error : nodeInfoExchangeText(result));
      }
    };
    setTimeout(()=>safe(check),3000);
  }
  if (state.node===node && state.nodeGatewayId===gateway) await renderNode();
}
$("node-info-exchange").addEventListener("click",()=>safe(()=>nodeAction("node_info_exchange")));$("traceroute").addEventListener("click",()=>safe(()=>nodeAction("traceroute")));$("position-exchange").addEventListener("click",()=>safe(()=>nodeAction("position_exchange")));
async function showConnections(){const data=await api("connections");const root=$("profiles");root.replaceChildren();for(const profile of data.profiles||[]){const button=element("button",undefined,"list-button");button.append(element("strong",`${profile.profile_id===data.active_profile_id?"● ":""}${profile.name||profile.profile_id}`),element("small",`${profile.transport||""} · ${profile.target||profile.endpoint||profile.device||profile.host||profile.ble_identifier||""}`));button.addEventListener("click",()=>safe(async()=>{await api("connect",{profile_id:profile.profile_id});$("connections-dialog").close();await refresh();}));root.append(button);}if(!root.children.length)root.append(element("p",webText("Ingen lagra profilar."),"empty"));}
$("connections-button").addEventListener("click",()=>safe(async()=>{$("settings-dialog").close();$("connections-dialog").showModal();await showConnections();}));$("close-connections").addEventListener("click",()=>$("connections-dialog").close());
async function discover(ble){const button=$(ble?"discover-ble":"discover");button.disabled=true;try{const data=await api(ble?"discover_ble_connections":"discover_connections",ble?{}:{include_ble:false});const root=$("discovery");root.replaceChildren();for(const kind of ["serial","tcp","ble"]){for(const item of data[kind]||[]){const target=item.target||item.device||item.address||item.identifier;const button=element("button",`${item.name||item.description||target} · ${target}`,"list-button");button.addEventListener("click",()=>{$("connect-target").value=target;});root.append(button);}}for(const key of ["tcp_error","serial_error","ble_error"])if(data[key])root.append(element("p",data[key]));if(!root.children.length)root.append(element("p",webText("Ingen nye einingar funne."),"empty"));}finally{button.disabled=false;}}
$("discover").addEventListener("click",()=>safe(()=>discover(false)));$("discover-ble").addEventListener("click",()=>safe(()=>discover(true)));
$("connect-form").addEventListener("submit",event=>{event.preventDefault();safe(async()=>{await api("connect",{target:$("connect-target").value.trim()});$("connections-dialog").close();await refresh();});});
document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible")safe(refresh);});
async function start() {
  try { await renewSession(); }
  catch (_) {
    $("status").textContent = webText("Sambandet er brote. Koplar til på nytt …");
    setTimeout(() => safe(start), 3000);
    return;
  }
  if (state.authenticated === false) return;
  await refresh();
  if (typeof meshpiUpdates !== "undefined") void meshpiUpdates.check();
  if (state.authenticated === false || eventStream) return;
  const stream = new EventSource(endpoint("events"));
  eventStream = stream;
  let timer;
  stream.onmessage = () => {
    clearTimeout(timer);
    timer = setTimeout(() => safe(refresh), 250);
  };
  stream.onerror = () => {
    $("status").textContent = webText("Sanntid koplar til på nytt …");
    safe(() => renewSession());
  };
  if (!refreshInterval) refreshInterval = setInterval(() => {
    if (document.visibilityState === "visible" && state.authenticated !== false) {
      safe(refresh);
      if (typeof meshpiUpdates !== "undefined") void meshpiUpdates.check();
    }
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
  $("settings-status").textContent=webText("Gateway eller innlogging er endra. Lukk og opne innstillingane på nytt.");
}
function settingsControls() {
  const disabled=historySettings.busy || !historySettings.data?.local_node_id;
  for (const id of ["save-retention","preview-cleanup","retention-days","retention-custom","cleanup-scope","cleanup-days","cleanup-custom"]) $(id).disabled=disabled;
  $("confirm-cleanup").disabled=disabled || !historySettings.preview;
}
function historyCounts(counts) {
  return [["messages",webText("meldingar")],["telemetry_samples",webText("telemetrirapportar")],["positions",webText("posisjonar")],["node_actions",webText("nodehandlingar")]]
    .map(([key,label])=>`${Number(counts?.[key] || 0).toLocaleString(webLocale())} ${label}`).join(", ");
}
function settingsDays(prefix) {
  const selected=$(`${prefix}-days`).value;
  const raw=selected==="custom" ? $(`${prefix}-custom`).value : selected;
  const days=Number(raw);
  if (!raw || !Number.isInteger(days) || days<0 || days>36500 || (selected==="custom" && days===0)) throw new Error(webText("Vel eit heilt tal frå 1 til 36500 dagar, eller det eigne valet utan aldersgrense."));
  return days;
}
function renderHistoryLanguage() {
  const data=historySettings.data;
  if (!data) return;
  $("settings-gateway").textContent=data.local_node_id ? webText`Gateway: ${nodeName(data.local_node_id)} · ${data.local_node_id}` : webText("Ingen aktiv gateway. Vel ei tilkopling først.");
  $("retention-limit").textContent=webText`Aldersgrensa gjeld meldingar, telemetri, posisjonar og nodehandlingar. Meldingsgrensa er ${Number(data.max_messages || 50000).toLocaleString(webLocale())}. Utan aldersgrense opphevar ikkje denne grensa. Lagra no: ${historyCounts(data.counts)}.`;
  const preview=historySettings.preview;
  if (preview) {
    const days=preview.older_than_days;
    $("cleanup-summary").textContent=webText`${nodeName(data.local_node_id)} (${data.local_node_id}): ${historyCounts(preview.counts)}. ${days ? webText`Eldre enn ${days} dagar (før ${time(preview.cutoff)}).` : webText("Alt i det valde omfanget, uansett alder.")} Førehandsvisinga er gyldig i fem minutt.`;
  }
}
function applyHistorySettings(data) {
  historySettings.data=data;
  renderHistoryLanguage();
  const days=data.retention_days ?? 0;
  $("retention-days").value=[0,7,30].includes(days) ? String(days) : "custom";
  $("retention-custom").value=String(days || 30);
  $("retention-custom-wrap").hidden=$("retention-days").value!=="custom";
  $("retention-review").hidden=!data.needs_review;

  settingsControls();
}
async function openHistorySettings() {
  invalidateCleanup();historySettings.data=null;settingsControls();
  const epoch=historySettings.epoch;
  $("settings-status").textContent=webText("Hentar innstillingar …");
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
    $("settings-status").textContent=error.uncertain ? webText("Ukjent resultat. Handlinga blir ikkje send på nytt automatisk. Lukk og opne innstillingane for å kontrollere lagringsval og tal før du prøver igjen.") : error.message;
  } finally {historySettings.busy=false;settingsControls();}
}
async function saveRetention() {
  return historyOperation(async()=>{
    const data=historySettings.data,days=settingsDays("retention");
    const description=days===0 ? webText("utan aldersgrense") : webText`i ${days} dagar`;
    if(!confirm(webText`Lagre historikk ${description} for ${nodeName(data.local_node_id)} (${data.local_node_id})? ${days ? webText("Eksisterande og framtidig historikk eldre enn denne grensa blir sletta automatisk. Dette kan ikkje angrast.") : webText("Meldingsgrensa gjeld framleis.")}`))return;
    invalidateCleanup();const epoch=historySettings.epoch;
    const result=await api("history_settings_set",{expected_local_node_id:data.local_node_id,retention_days:days,confirmed:true});
    if(epoch!==historySettings.epoch)return;
    applyHistorySettings(result);$("settings-status").textContent=webText("Lagringsvalet er lagra.");
  });
}
async function previewCleanup() {
  return historyOperation(async()=>{
    invalidateCleanup();const epoch=historySettings.epoch;
    const data=historySettings.data,scope=$("cleanup-scope").value,days=settingsDays("cleanup");
    const preview=await api("history_cleanup_preview",{expected_local_node_id:data.local_node_id,scope,older_than_days:days});
    if(epoch!==historySettings.epoch || preview.local_node_id!==data.local_node_id)return;
    historySettings.preview={...preview,expiresAt:Date.now()+preview.expires_in*1000};
    $("cleanup-summary").textContent=webText`${nodeName(data.local_node_id)} (${data.local_node_id}): ${historyCounts(preview.counts)}. ${days ? webText`Eldre enn ${days} dagar (før ${time(preview.cutoff)}).` : webText("Alt i det valde omfanget, uansett alder.")} Førehandsvisinga er gyldig i fem minutt.`;
    $("cleanup-preview").hidden=false;
    $("settings-status").textContent=webText("Kontroller omfanget før du stadfestar sletting.");
  });
}
async function confirmCleanup() {
  return historyOperation(async()=>{
    const preview=historySettings.preview,data=historySettings.data;
    if(!preview || preview.local_node_id!==data.local_node_id || Date.now()>=preview.expiresAt){invalidateCleanup();throw new Error(webText("Førehandsvisinga er utgått. Vis kva som blir sletta på nytt."));}
    if(!confirm(webText`Slett ${historyCounts(preview.counts)} frå ${nodeName(data.local_node_id)} (${data.local_node_id})? Dette kan ikkje angrast.`))return;
    invalidateCleanup();const epoch=historySettings.epoch;
    const result=await api("history_cleanup",{cleanup_token:preview.cleanup_token,expected_local_node_id:data.local_node_id,confirmed:true});
    if(epoch!==historySettings.epoch)return;
    $("settings-status").textContent=webText`Sletta: ${historyCounts(result.deleted)}.`;
    const current=await api("history_settings");
    if(epoch!==historySettings.epoch)return;
    applyHistorySettings(current);
    await refresh();
  });
}
$("notice-auto-close").addEventListener("change", () => {
  autoCloseNotices = $("notice-auto-close").checked;
  try {window.localStorage.setItem("meshpi-web-notices", autoCloseNotices ? "auto" : "manual");}
  catch (_) { /* Retain the choice for this page even when persistence is blocked. */ }
  if (!$("notice").hidden) notice($("notice-text").textContent, noticeKind);
});
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
    if (!response.ok || !result?.ok) throw new Error(result?.error || webText("Kunne ikkje hente nøklane."));
    return result.data;
  },
});
safe(start);

$("nodes-button").addEventListener("click",()=>{$("mobile-nodes").append($("node-browser"));renderNodes();$("nodes-dialog").showModal();});
$("nodes-dialog").addEventListener("close",()=>{document.querySelector(".nodes-panel").append($("node-browser"));});
$("close-nodes").addEventListener("click",()=>$("nodes-dialog").close());

if (typeof meshpiI18n !== "undefined") meshpiI18n.onChange(() => {
  // Keep the current conversation, unsent draft, selection and pending writes intact.
  renderIdentity();renderConversations();renderNodes();renderConversationHeader();updateComposer();
  $("status").textContent=state.status.state ? [webText(state.status.state),state.status.connection_name,state.status.transport].filter(Boolean).join(" · ") : webText("Innlogging er nødvendig");
  renderHistoryLanguage();
  if (state.authenticated === true) {
    safe(() => loadMessages(false));
    if (state.node && $("node-dialog").open) safe(renderNode);
    if ($("connections-dialog").open) safe(showConnections);
  }
});
