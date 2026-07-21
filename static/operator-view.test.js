// Run: node static/operator-view.test.js
const assert = require("assert");
const { operatorView, lastReported, formatHistory, connectivity } = require("./operator-view.js");

let total = 0, pass = 0;
function test(name, fn) { total++; try { fn(); pass++; } catch (e) { console.error("FAIL", name, e.message); } }

const NOW = 1_784_500_000; // realistic epoch seconds (2026) so ms×1000 > 1e12
const FRESH = NOW - 600;    // 10min ago → live tier
const IDLE = NOW - 7200;    // 2h ago    → idle tier
const OLD = NOW - 100_000;  // ~27h ago  → offline tier
const okState = { model: "I-8", gate_state: "Closed", motor: "Stop", limit: "Close", ac_voltage: 27.3, battery_voltage: 28.0 };
const DEVICE = { fw_version: "1.5", mac: "AABBCCDDEEFF", device_id: "0000-VM-00000", arch: "esp32" };
const base = (extra) => Object.assign({ reachable: true, state: okState, error: { cleared: true } }, extra);

test("null / reachable:false => unreachable", () => {
  assert.strictEqual(operatorView(null, NOW).status, "unreachable");
  assert.strictEqual(operatorView({ reachable: false }, NOW).status, "unreachable");
});
test("reachable but no state => connecting", () => {
  assert.strictEqual(operatorView({ reachable: true, state: null }, NOW).status, "connecting");
});

test("connectivity tiers from presence + freshness", () => {
  assert.strictEqual(connectivity({ online: true, reported_at: OLD }, NOW), "live");   // presence wins
  assert.strictEqual(connectivity({ online: false, reported_at: FRESH }, NOW), "offline"); // presence wins
  assert.strictEqual(connectivity({ online: null, reported_at: FRESH }, NOW), "live");
  assert.strictEqual(connectivity({ online: null, reported_at: IDLE }, NOW), "idle");
  assert.strictEqual(connectivity({ online: null, reported_at: OLD }, NOW), "offline");
  assert.strictEqual(connectivity({ online: null, reported_at: null }, NOW), "offline");
});

test("fresh/unknown-presence => live, line says live", () => {
  const v = operatorView(base({ online: null, reported_at: FRESH }), NOW);
  assert.strictEqual(v.status, "live");
  assert.ok(v.line.includes("live"));
});
test("idle => amber tier, line says idle + last reported", () => {
  const v = operatorView(base({ online: null, reported_at: IDLE }), NOW);
  assert.strictEqual(v.status, "idle");
  assert.ok(v.line.includes("idle") && v.line.includes("last reported"));
});
test("very old => offline", () => {
  assert.strictEqual(operatorView(base({ online: null, reported_at: OLD }), NOW).status, "offline");
});
test("explicit presence overrides freshness", () => {
  assert.strictEqual(operatorView(base({ online: false, reported_at: FRESH }), NOW).status, "offline");
  assert.strictEqual(operatorView(base({ online: true, reported_at: OLD }), NOW).status, "live");
});

test("Online field tracks tier when presence unknown", () => {
  const label = (o) => operatorView(o, NOW).fields.find((f) => f.label === "Online").value;
  assert.strictEqual(label(base({ online: null, reported_at: FRESH })), "Yes");
  assert.strictEqual(label(base({ online: null, reported_at: IDLE })), "Idle");
  assert.strictEqual(label(base({ online: null, reported_at: OLD })), "No");
  assert.strictEqual(label(base({ online: true, reported_at: OLD })), "Yes");
  assert.strictEqual(label(base({ online: false, reported_at: FRESH })), "No");
});

test("stale backend => warning status, overrides tier", () => {
  const v = operatorView(base({ online: true, reported_at: FRESH, stale: true }), NOW);
  assert.strictEqual(v.status, "backendstale");
  assert.ok(v.line.includes("not updating"));
  assert.ok(v.fields.length > 0); // still shows last-known data
});

test("uncleared error => fault, wins over any tier", () => {
  const v = operatorView(base({ online: true, reported_at: FRESH, error: { code: "15", cleared: false, description: "ERR FUSE 15 AMP" } }), NOW);
  assert.strictEqual(v.status, "fault");
  assert.ok(v.errorText.includes("15") && v.errorText.includes("ERR FUSE 15 AMP"));
});

test("firmware + MAC (colon) + device id surface", () => {
  const v = operatorView(base({ online: null, reported_at: FRESH, device: DEVICE }), NOW);
  const byLabel = Object.fromEntries(v.fields.map((f) => [f.label, f.value]));
  assert.strictEqual(byLabel["Firmware"], "v1.5 (esp32)");
  assert.strictEqual(byLabel["MAC"], "AA:BB:CC:DD:EE:FF");
  assert.strictEqual(byLabel["Device ID"], "0000-VM-00000");
});
test("full field set present", () => {
  const v = operatorView(base({ online: null, reported_at: FRESH, device: DEVICE }), NOW);
  const keys = v.fields.map((f) => f.label);
  ["Model", "State", "Motor", "Limit", "AC", "Battery", "Online", "Firmware", "MAC", "Device ID", "Last reported"]
    .forEach((k) => assert.ok(keys.includes(k), "missing " + k));
});

test("formatHistory turns rows into human lines, ms timestamps, cleared skipped", () => {
  const rows = [
    { timestamp: (NOW - 60) * 1000, status: "Open" },
    { timestamp: (NOW - 120) * 1000, online: false },
    { timestamp: (NOW - 180) * 1000, batteryVoltage: 27.8 },
    { timestamp: (NOW - 240) * 1000, error: "15", errorDescription: "ERR FUSE 15 AMP" },
    { timestamp: (NOW - 300) * 1000, error: "0" },
  ];
  const h = formatHistory(rows, NOW);
  assert.strictEqual(h[0].text, "Status: Open"); assert.strictEqual(h[0].kind, "gate");
  assert.strictEqual(h[0].when, "1m ago");
  assert.strictEqual(h[1].text, "Went offline"); assert.strictEqual(h[1].kind, "online");
  assert.strictEqual(h[2].text, "Battery 27.8 V"); assert.strictEqual(h[2].kind, "voltage");
  assert.strictEqual(h[3].text, "Error 15 — ERR FUSE 15 AMP"); assert.strictEqual(h[3].kind, "fault");
  assert.strictEqual(h.length, 4);
});
test("formatHistory does NOT cap (UI paginates); [] when absent", () => {
  const rows = Array.from({ length: 30 }, () => ({ timestamp: NOW * 1000, status: "S" }));
  assert.strictEqual(formatHistory(rows, NOW).length, 30);
  assert.deepStrictEqual(operatorView(base({ reported_at: FRESH }), NOW).history, []);
});

test("lastReported: pretty two-unit relative time", () => {
  assert.strictEqual(lastReported(null, NOW), "unknown");
  assert.strictEqual(lastReported(NOW - 10, NOW), "just now");
  assert.strictEqual(lastReported(NOW - 120, NOW), "2m ago");
  assert.strictEqual(lastReported(NOW - 7200, NOW), "2h ago");
  assert.strictEqual(lastReported(NOW - 4800, NOW), "1h 20m ago");
  assert.strictEqual(lastReported(NOW - 172800, NOW), "2d ago");
  assert.strictEqual(lastReported(NOW - 147600, NOW), "1d 17h ago");
});

console.log(`${pass}/${total} passed`);
process.exit(pass === total ? 0 : 1);
