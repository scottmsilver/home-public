// Pure presenter for the Viking gate-operator affordance on the Gate tab.
// Maps the /operator snapshot to a display shape. No React/DOM — unit-tested
// in operator-view.test.js. index.html loads this and uses window.OperatorView.
//
// Freshness is honest. AWS returns the cached Device Shadow even when the module
// is offline, and it only tells us "connected" via presence EVENTS (which we miss
// if we subscribe after the device is already up). We can't query live
// connectivity (Viking's account doesn't index it), so we infer a 3-tier state
// from presence-when-known + the device's real report time (reported_at):
//   live   (green) : online presence, or reported within LIVE_WITHIN
//   idle   (amber) : reported a while ago — connected-but-quiet, not alarming
//   offline(grey)  : offline presence, reported > OFFLINE_AFTER, or unknown
(function (root) {
  var LIVE_WITHIN = 1800;    // <=30min since last report → actively live
  var OFFLINE_AFTER = 21600; // >6h since last report → treat as offline

  function volts(v) { return (v == null) ? null : `${v} V`; }

  function lastReported(reportedAt, now) {
    if (reportedAt == null) return "unknown";
    var d = now - reportedAt;
    if (d < 0) d = 0;
    if (d < 45) return "just now";
    if (d < 3600) return `${Math.round(d / 60)}m ago`;
    if (d < 86400) {
      var h = Math.floor(d / 3600), m = Math.floor((d % 3600) / 60);
      return m ? `${h}h ${m}m ago` : `${h}h ago`;
    }
    var days = Math.floor(d / 86400), rh = Math.floor((d % 86400) / 3600);
    return rh ? `${days}d ${rh}h ago` : `${days}d ago`;
  }

  // "live" | "idle" | "offline" — presence wins when known, else use freshness.
  function connectivity(operator, now) {
    if (operator.online === true) return "live";
    if (operator.online === false) return "offline";
    var ra = operator.reported_at;
    if (ra == null) return "offline";
    var age = now - ra;
    if (age <= LIVE_WITHIN) return "live";
    if (age <= OFFLINE_AFTER) return "idle";
    return "offline";
  }

  function onlineLabel(operator, tier) {
    if (operator.online === true) return "Yes";
    if (operator.online === false) return "No";
    return tier === "live" ? "Yes" : tier === "idle" ? "Idle" : "No";
  }

  function macColons(m) {
    if (!m) return null;
    return /^[0-9A-Fa-f]{12}$/.test(m) ? m.match(/.{2}/g).join(":") : m;
  }

  function fieldsOf(operator, state, lr, tier) {
    var dev = operator.device || {};
    var fw = dev.fw_version ? `v${dev.fw_version}${dev.arch ? ` (${dev.arch})` : ""}` : null;
    var rows = [
      ["Model", state.model], ["State", state.gate_state], ["Motor", state.motor],
      ["Limit", state.limit], ["AC", volts(state.ac_voltage)], ["Battery", volts(state.battery_voltage)],
      ["Online", onlineLabel(operator, tier)],
      ["Firmware", fw], ["MAC", macColons(dev.mac)], ["Device ID", dev.device_id || null],
      ["Last reported", lr],
    ];
    return rows.filter(([, v]) => v != null && v !== "").map(([label, value]) => ({ label, value }));
  }

  // History rows are one-attribute-per-event (DynamoDB). Turn each into a human
  // line + relative time + a category kind (for the UI's filter chips). No cap —
  // the UI filters by kind and paginates. Timestamps are epoch ms → seconds.
  function formatHistory(history, now) {
    if (!Array.isArray(history)) return [];
    var out = [];
    for (var i = 0; i < history.length; i++) {
      var row = history[i];
      var ts = row.timestamp;
      if (ts != null && ts > 1e12) ts = Math.floor(ts / 1000);
      var text = null, kind = null;
      if (row.status != null) { text = `Status: ${row.status}`; kind = "gate"; }
      else if (row.online === true) { text = "Came online"; kind = "online"; }
      else if (row.online === false) { text = "Went offline"; kind = "online"; }
      else if (row.error != null && String(row.error) !== "0") {
        text = `Error ${row.error}${row.errorDescription ? ` — ${row.errorDescription}` : ""}`; kind = "fault";
      } else if (row.acVoltage != null) { text = `AC ${row.acVoltage} V`; kind = "voltage"; }
      else if (row.batteryVoltage != null) { text = `Battery ${row.batteryVoltage} V`; kind = "voltage"; }
      if (text) out.push({ text: text, when: lastReported(ts, now), kind: kind, ts: ts });
    }
    return out;
  }

  // status ∈ {"unreachable","connecting","fault","live","idle","offline"}
  function operatorView(operator, now) {
    now = now || Math.floor(Date.now() / 1000);
    if (!operator || operator.reachable === false) {
      return { status: "unreachable", line: "Gate operator unreachable",
               errorText: null, fields: [], history: [], reportedAt: null, lastReported: "unknown" };
    }
    var state = operator.state;
    var reportedAt = operator.reported_at != null ? operator.reported_at : null;
    var lr = lastReported(reportedAt, now);
    if (!state) {
      return { status: "connecting", line: "Gate operator connecting…",
               errorText: null, fields: [], history: [], reportedAt: reportedAt, lastReported: lr };
    }
    var err = operator.error || { cleared: true };
    var tier = connectivity(operator, now);
    var fields = fieldsOf(operator, state, lr, tier);
    var history = formatHistory(operator.history, now);
    // Backend stopped refreshing (expired creds / network) — the data below is
    // frozen last-known, so warn rather than present it as current.
    if (operator.stale === true) {
      return { status: "backendstale", line: `⚠ Data not updating · last reported ${lr}`,
               errorText: null, fields: fields, history: history, reportedAt: reportedAt, lastReported: lr };
    }
    if (err.cleared === false) {
      var desc = err.description ? ` — ${err.description}` : "";
      return { status: "fault", line: `Gate fault · last reported ${lr}`,
               errorText: `⚠ ERR ${err.code}${desc}`, fields: fields, history: history,
               reportedAt: reportedAt, lastReported: lr };
    }
    var gs = state.gate_state || "—";
    var line = tier === "live" ? `Gate ${gs} · live`
             : tier === "idle" ? `Gate ${gs} · idle · last reported ${lr}`
             : `Gate ${gs} · last reported ${lr}`;
    return { status: tier, line: line, errorText: null, fields: fields, history: history,
             reportedAt: reportedAt, lastReported: lr };
  }

  var api = { operatorView: operatorView, lastReported: lastReported, formatHistory: formatHistory,
              connectivity: connectivity, LIVE_WITHIN: LIVE_WITHIN, OFFLINE_AFTER: OFFLINE_AFTER };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.OperatorView = api;
})(typeof window !== "undefined" ? window : this);
