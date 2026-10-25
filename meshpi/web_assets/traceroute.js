"use strict";

function tracerouteModel(action, nodes = []) {
  const byId = new Map(nodes.map(node => [String(node.node_id || "").toLowerCase(), node]));

  function nodeInfo(value) {
    const id = typeof value === "string" ? value.toLowerCase() : "";
    const known = byId.get(id);
    return {
      id: id || "Ikkje oppgitt",
      name: known?.long_name || known?.short_name || "Ukjend node",
    };
  }

  function route(value) {
    if (!Array.isArray(value) || !value.length) return null;
    const hops = value.map((hop, index) => ({
      ...nodeInfo(hop?.node_id),
      snr: index > 0 && typeof hop?.snr === "number" && Number.isFinite(hop.snr)
        ? hop.snr : null,
    }));
    const measured = hops.slice(1).filter(hop => hop.snr !== null).map(hop => hop.snr);
    return {
      hops,
      links: Math.max(0, hops.length - 1),
      measured: measured.length,
      lowestSnr: measured.length ? Math.min(...measured) : null,
    };
  }

  const started = Date.parse(action.started_at);
  const finished = Date.parse(action.finished_at);
  const result = action.result && typeof action.result === "object" ? action.result : {};
  return {
    status: {started: "Ventar på svar", completed: "Fullført", failed: "Feila"}[action.status]
      || "Ukjend status",
    duration: Number.isFinite(started) && Number.isFinite(finished) && finished >= started
      ? (finished - started) / 1000 : null,
    source: nodeInfo(action.local_node_id),
    target: nodeInfo(action.node_id),
    forward: route(result.forward),
    back: route(result.return),
  };
}
