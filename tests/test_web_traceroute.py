"""Check frontend route semantics with an available JavaScript engine, without radio."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_traceroute_preserves_direction_names_zero_snr_and_missing_values():
    engine = shutil.which("node") or shutil.which("gjs")
    if engine is None:
        pytest.skip("Ingen lokal JavaScript-motor for modelltesten")
    source = (ROOT / "meshpi/web_assets/traceroute.js").read_text()
    checks = r"""
function check(condition, message) {
  if (!condition) throw new Error(message);
}
const nodes = [
  {node_id: "!aaaaaaaa", long_name: "Gateway"},
  {node_id: "!bbbbbbbb", short_name: "Fjell"},
];
const action = {
  status: "completed", local_node_id: "!aaaaaaaa", node_id: "!bbbbbbbb",
  started_at: "2026-10-12T17:19:43Z", finished_at: "2026-10-12T17:19:46Z",
  result: {
    forward: [
      {node_id: "!AAAAAAAA", snr: null},
      {node_id: "!cccccccc", snr: 0},
      {node_id: "!bbbbbbbb", snr: -2.25},
    ],
    return: [
      {node_id: "!bbbbbbbb", snr: null},
      {node_id: "!aaaaaaaa", snr: null},
    ],
  },
};
const view = tracerouteModel(action, nodes);
check(view.duration === 3, "Wrong elapsed time");
check(view.status === "Fullført", "Status must be localized");
check(view.source.name === "Gateway" && view.target.name === "Fjell", "Name fallback failed");
check(view.forward.hops[0].name === "Gateway", "Case-insensitive node lookup failed");
check(view.forward.hops[1].id === "!cccccccc", "Unknown node ID was lost");
check(view.forward.hops[1].name === "Ukjend node", "Missing name must be explicit");
check(view.forward.hops[1].snr === 0, "Zero SNR is a measured value");
check(view.forward.lowestSnr === -2.25, "Negative SNR must be preserved");
check(view.forward.links === 2 && view.back.links === 1, "Directions must remain distinct");
check(view.back.hops[0].name === "Fjell", "Return direction was reversed");
check(view.back.lowestSnr === null && view.back.measured === 0, "Missing SNR was invented");
const empty = tracerouteModel({status: "started", result: {forward: [], return: null}}, nodes);
check(empty.forward === null && empty.back === null, "Missing route was invented");
check(empty.duration === null && empty.status === "Ventar på svar", "Pending state is wrong");
const failed = tracerouteModel({...action, status: "failed", finished_at: "invalid"}, nodes);
check(failed.status === "Feila" && failed.duration === null, "Failed state is wrong");
const nonNumeric = tracerouteModel({...action, result: {
  forward: [{node_id: "!aaaaaaaa"}, {node_id: "!bbbbbbbb", snr: "0"}],
}}, nodes);
check(nonNumeric.forward.measured === 0, "Malformed SNR must not become a measurement");
"""
    flag = "-e" if Path(engine).name == "node" else "-c"
    result = subprocess.run([engine, flag, source + checks], capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 0, result.stderr
