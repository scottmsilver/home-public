// Unit tests for the slider state machine. Run: node static/slider-model.test.js
// Covers the failure modes that bit us on real hardware: per-tick command floods,
// snap-back from stale readings, out-of-order arrival, and lost/missed commands.
const assert = require("assert");
const M = require("./slider-model.js");

// Thread a list of events through the reducer; collect emitted commands.
function run(events, start = 0) {
  let s = M.initial(start);
  const commands = [];
  for (const e of events) {
    const r = M.reduce(s, e);
    s = r.state;
    if (r.command != null) commands.push(r.command);
  }
  return { state: s, commands };
}

let total = 0, pass = 0;
function test(name, fn) {
  total++;
  try { fn(); pass++; console.log("ok   - " + name); }
  catch (err) { console.log("FAIL - " + name + "\n         " + err.message); }
}

// --- commit semantics --------------------------------------------------------

test("drag ticks then release => exactly one command, at the release value", () => {
  const { state, commands } = run([
    { type: "drag", v: 10 }, { type: "drag", v: 20 }, { type: "drag", v: 15 },
    { type: "release", v: 15 },
  ], 0);
  assert.deepStrictEqual(commands, [15]);
  assert.strictEqual(state.value, 15);
  assert.strictEqual(state.pending, 15);
});

test("back-and-forth drag, one release => single command (no flood)", () => {
  const { commands } = run([
    { type: "drag", v: 15 }, { type: "drag", v: 21 }, { type: "drag", v: 6 },
    { type: "drag", v: 18 }, { type: "release", v: 12 },
  ], 8);
  assert.deepStrictEqual(commands, [12]); // NOT [15,21,6,18,12]
});

test("dragging never emits a command on its own", () => {
  const { commands } = run([
    { type: "drag", v: 30 }, { type: "drag", v: 31 }, { type: "drag", v: 32 },
  ], 8);
  assert.deepStrictEqual(commands, []);
});

test("livecommit emits a command but stays dragging (live-follow)", () => {
  const r = M.reduce(M.initial(8), { type: "livecommit", v: 20 });
  assert.strictEqual(r.command, 20);
  assert.strictEqual(r.state.value, 20);
  assert.strictEqual(r.state.dragging, true);   // still dragging
  assert.strictEqual(r.state.pending, 20);
});

test("server is ignored during a live drag (livecommit keeps dragging)", () => {
  let s = M.initial(8);
  s = M.reduce(s, { type: "livecommit", v: 20 }).state;
  s = M.reduce(s, { type: "server", v: 8 }).state;  // stale reading mid-drag
  assert.strictEqual(s.value, 20);                  // thumb not yanked
});

test("live drag then release: each livecommit + the release emit; ends at release", () => {
  const { state, commands } = run([
    { type: "livecommit", v: 12 }, { type: "drag", v: 15 },
    { type: "livecommit", v: 18 }, { type: "drag", v: 22 },
    { type: "release", v: 25 },
  ], 8);
  assert.deepStrictEqual(commands, [12, 18, 25]);   // throttled live updates + final
  assert.strictEqual(state.dragging, false);
  assert.strictEqual(state.pending, 25);
});

// --- reconciliation: snap-back protection ------------------------------------

test("stale reading after release is ignored (no snap-back)", () => {
  let { state } = run([{ type: "release", v: 11 }], 8);
  const r = M.reduce(state, { type: "server", v: 8 }); // old value still in flight
  assert.strictEqual(r.state.value, 11);   // held at what the user chose
  assert.strictEqual(r.state.pending, 11);
});

test("correct echo after release clears the guard and resyncs", () => {
  let { state } = run([{ type: "release", v: 11 }], 8);
  const r = M.reduce(state, { type: "server", v: 11 });
  assert.strictEqual(r.state.value, 11);
  assert.strictEqual(r.state.pending, null);
});

test("rounding: echo within ±1 counts as confirmed", () => {
  let s = M.initial(8);
  s = M.reduce(s, { type: "release", v: 20 }).state;
  s = M.reduce(s, { type: "server", v: 21 }).state; // device rounded to 21
  assert.strictEqual(s.pending, null);
  assert.strictEqual(s.value, 21);
});

test("server update mid-drag is ignored (thumb not yanked)", () => {
  let s = M.initial(8);
  s = M.reduce(s, { type: "drag", v: 50 }).state;
  s = M.reduce(s, { type: "server", v: 8 }).state;
  assert.strictEqual(s.value, 50);
});

// --- out-of-order arrival ----------------------------------------------------

test("out-of-order echoes: hold until the committed value actually arrives", () => {
  let s = M.initial(8);
  s = M.reduce(s, { type: "release", v: 30 }).state;
  s = M.reduce(s, { type: "server", v: 8 }).state;   // stale
  assert.strictEqual(s.value, 30);
  s = M.reduce(s, { type: "server", v: 18 }).state;  // an intermediate
  assert.strictEqual(s.value, 30);
  s = M.reduce(s, { type: "server", v: 30 }).state;  // finally ours
  assert.strictEqual(s.value, 30);
  assert.strictEqual(s.pending, null);
});

test("two quick releases: last-write-wins, earlier echo ignored", () => {
  let s = M.initial(8);
  s = M.reduce(s, { type: "release", v: 12 }).state;
  s = M.reduce(s, { type: "release", v: 30 }).state; // re-drag & release before echo
  assert.strictEqual(s.pending, 30);
  s = M.reduce(s, { type: "server", v: 12 }).state;  // echo of the FIRST command
  assert.strictEqual(s.value, 30);                   // ignored
  s = M.reduce(s, { type: "server", v: 30 }).state;
  assert.strictEqual(s.value, 30);
  assert.strictEqual(s.pending, null);
});

// --- lost / missed / failed command ------------------------------------------

test("lost command: timeout reconciles to the carried server reading (no fresh event needed)", () => {
  // The device never moved (command lost), so NO new server event arrives — the
  // value prop stays 8 and useEffect([serverValue]) never re-fires. The hook
  // therefore carries the latest reading on the timeout event itself.
  let s = M.initial(8);
  s = M.reduce(s, { type: "release", v: 20 }).state; // asked for 20
  s = M.reduce(s, { type: "server", v: 8 }).state;   // stale echo -> ignored
  assert.strictEqual(s.value, 20);                   // still optimistic
  s = M.reduce(s, { type: "timeout", server: 8 }).state; // safety fires, carries truth
  assert.strictEqual(s.pending, null);
  assert.strictEqual(s.value, 8);                    // snapped to reality WITHOUT a new server event
});

test("timeout reconciles to a changed device value the guard had been ignoring", () => {
  let s = M.initial(8);
  s = M.reduce(s, { type: "release", v: 20 }).state;
  s = M.reduce(s, { type: "server", v: 15 }).state;  // device went to 15, not 20 -> ignored
  assert.strictEqual(s.value, 20);
  s = M.reduce(s, { type: "timeout", server: 15 }).state;
  assert.strictEqual(s.value, 15);                   // ends on the device's real value
  assert.strictEqual(s.pending, null);
});

test("timeout never yanks an active drag", () => {
  let s = M.initial(8);
  s = M.reduce(s, { type: "release", v: 20 }).state; // first interaction
  s = M.reduce(s, { type: "drag", v: 55 }).state;    // user starts a NEW drag before the 4s elapses
  s = M.reduce(s, { type: "timeout", server: 8 }).state; // old safety fires mid-drag
  assert.strictEqual(s.value, 55);                   // thumb stays under the finger
  assert.strictEqual(s.pending, null);               // old guard still dropped
});

test("timeout with no pending / no server is a no-op", () => {
  let s = M.initial(40);
  const r = M.reduce(s, { type: "timeout" });
  assert.strictEqual(r.state.value, 40);
  assert.strictEqual(r.state.pending, null);
  assert.strictEqual(r.command, null);
});

// --- external changes --------------------------------------------------------

test("idle external change (no pending) follows the server", () => {
  let s = M.initial(8);
  s = M.reduce(s, { type: "server", v: 40 }).state; // changed from another client / app
  assert.strictEqual(s.value, 40);
  assert.strictEqual(s.pending, null);
});

test("full lifecycle: drag, release, stale, confirm, then external change", () => {
  let s = M.initial(8);
  s = M.reduce(s, { type: "drag", v: 25 }).state;
  s = M.reduce(s, { type: "release", v: 25 }).state;
  s = M.reduce(s, { type: "server", v: 8 }).state;   // stale -> ignored
  assert.strictEqual(s.value, 25);
  s = M.reduce(s, { type: "server", v: 25 }).state;  // confirmed
  assert.strictEqual(s.pending, null);
  s = M.reduce(s, { type: "server", v: 50 }).state;  // later external change
  assert.strictEqual(s.value, 50);
});

console.log(`\n${pass}/${total} passed`);
process.exit(pass === total ? 0 : 1);
