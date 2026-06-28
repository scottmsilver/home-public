// Pure, framework-agnostic state machine for a slider that drives an async /
// remote device (volume, light brightness, ...). No React, no DOM, no timers —
// just (state, event) -> { state, command }. This is the piece that's easy to
// get subtly wrong (snap-back, flooding, out-of-order/lost commands), so it
// lives here on its own and is exhaustively unit-tested in slider-model.test.js.
// The React hook in index.html is a thin wrapper that feeds DOM events in and
// applies the emitted command + a safety timer.
//
// Design (matches the well-trodden "optimistic local value + reconcile-only-
// when-the-device-confirms" pattern — React Query cancel-in-flight / React 19
// useOptimistic):
//   * the thumb is LOCAL and instant while dragging;
//   * exactly ONE command is emitted, on release — so a back-and-forth drag
//     never fires a burst of racing commands;
//   * server readings are ignored until the device echoes (±1) the value we
//     committed, so a stale / out-of-order reading can't snap the thumb;
//   * a 'timeout' event drops that guard if the command was lost/missed, so the
//     UI reconciles to the device's real value instead of lying forever.
(function (root) {
  "use strict";

  var TOL = 1; // device may echo ±1 (rounding / unit conversion) — treat as confirmed.

  // state: { value:int, dragging:bool, pending:int|null }
  //   pending = the value we committed and are awaiting the device to echo.
  // event:
  //   {type:'drag', v}     user moved the thumb            (no command)
  //   {type:'release', v}  user let go                     (emit ONE command)
  //   {type:'server', v}   a fresh reading from the device
  //   {type:'timeout'}     the pending command went unconfirmed too long
  // returns { state, command }  — command is an int to send, or null.
  function reduce(state, event) {
    switch (event.type) {
      case "drag":
        return { state: mk(event.v, true, state.pending), command: null };

      case "release":
        return { state: mk(event.v, false, event.v), command: event.v };

      case "server":
        if (state.dragging) return { state: state, command: null }; // never fight a drag
        if (state.pending != null) {
          if (Math.abs(event.v - state.pending) <= TOL) {           // device reached us
            return { state: mk(event.v, false, null), command: null };
          }
          return { state: state, command: null };                   // stale / in-flight -> ignore
        }
        return { state: mk(event.v, false, null), command: null };  // no pending -> follow truth

      case "timeout":
        // Command unconfirmed (lost / missed / hopelessly out-of-order). Drop the
        // guard AND reconcile to the device's real value, carried in event.server.
        // This matters because if the device never moved off its old value, no
        // fresh 'server' event will arrive on its own (the value prop doesn't
        // change), so without snapping to event.server the thumb would stay stuck
        // on the optimistic value forever. Never yank an active drag, though.
        if (state.dragging) return { state: mk(state.value, true, null), command: null };
        return {
          state: mk(event.server != null ? event.server : state.value, false, null),
          command: null,
        };

      default:
        return { state: state, command: null };
    }
  }

  function mk(value, dragging, pending) {
    return { value: value, dragging: dragging, pending: pending };
  }

  function initial(value) { return mk(value, false, null); }

  var api = { reduce: reduce, initial: initial, TOL: TOL };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SliderModel = api;
})(typeof window !== "undefined" ? window : this);
