import assert from "node:assert/strict";
import test from "node:test";
import { createGuidanceScheduler } from "../../backend/web/guidance.js";

function policy(overrides = {}) {
  return {
    enabled: true,
    maximum_responses_per_sweep: 6,
    minimum_response_interval_ms: 10000,
    maximum_event_age_ms: 3000,
    ...overrides,
  };
}

function event(overrides = {}) {
  return {
    id: "ev_1",
    frame_id: "frame_a",
    observed_at_ms: 1000,
    expires_at_ms: 4000,
    row_id: null,
    issue: "preview_not_advancing",
    severity: "warning",
    ...overrides,
  };
}

const clear = {
  speechActive: false,
  responseActive: false,
  playbackActive: false,
  userTurnPending: false,
  voiceAvailable: true,
  captureActive: true,
};

test("fresh actionable event produces one selection then suppression", () => {
  const now = { v: 1500 };
  const s = createGuidanceScheduler({ clock: () => now.v, policy: policy() });
  s.update([event()]);
  const picked = s.next(clear);
  assert.equal(picked.id, "ev_1");
  assert.equal(s.next(clear), null);
});

test("duplicate polls do not re-emit the same episode", () => {
  const s = createGuidanceScheduler({ clock: () => 1500, policy: policy() });
  s.update([event()]);
  s.next(clear);
  s.update([event({ id: "ev_2" })]);
  assert.equal(s.next(clear), null);
  s.update([event({ id: "ev_3" })]);
  assert.equal(s.next(clear), null);
});

test("repeated issue coalesces into one episode", () => {
  const s = createGuidanceScheduler({ clock: () => 1500, policy: policy() });
  s.update([event({ id: "a" }), event({ id: "b", observed_at_ms: 1200 })]);
  const first = s.next(clear);
  assert.equal(first.id, "b");
  assert.equal(s.next(clear), null);
});

test("clear before availability permits a new warning subject to interval", () => {
  const now = { v: 1500 };
  const s = createGuidanceScheduler({ clock: () => now.v, policy: policy() });
  s.update([event()]);
  s.next(clear);
  s.update([]);
  now.v = 4000;
  s.update([event({ id: "ev_4", observed_at_ms: 3900, expires_at_ms: 9000 })]);
  assert.equal(s.next(clear), null);
  s.update([]);
  now.v = 11500;
  s.update([event({ id: "ev_5", observed_at_ms: 11400, expires_at_ms: 20000 })]);
  const again = s.next(clear);
  assert.equal(again.id, "ev_5");
});

test("expired event is removed and never spoken", () => {
  const now = { v: 1500 };
  const s = createGuidanceScheduler({ clock: () => now.v, policy: policy() });
  s.update([event()]);
  now.v = 5000;
  assert.equal(s.next(clear), null);
});

test("an event past maximum age is not dispatched", () => {
  const now = { v: 5000 };
  const s = createGuidanceScheduler({ clock: () => now.v, policy: policy() });
  s.update([event()]);
  assert.equal(s.next(clear), null);
});

test("busy or unavailable contexts suppress dispatch", () => {
  const s = createGuidanceScheduler({ clock: () => 1500, policy: policy() });
  s.update([event()]);
  for (const key of ["speechActive", "responseActive", "playbackActive", "userTurnPending"]) {
    assert.equal(s.next({ ...clear, [key]: true }), null, key);
  }
  assert.equal(s.next({ ...clear, voiceAvailable: false }), null);
  assert.equal(s.next({ ...clear, captureActive: false }), null);
  assert.ok(s.next(clear));
});

test("cap of six includes cancelled dispatches and is not reset on polling", () => {
  const now = { v: 0 };
  const s = createGuidanceScheduler({ clock: () => now.v, policy: policy() });
  let picked = 0;
  for (let i = 0; i < 8; i++) {
    now.v = i * 10000;
    s.update([event({ id: "ev_" + i, observed_at_ms: now.v - 500, expires_at_ms: now.v + 3000 })]);
    if (s.next(clear)) picked += 1;
    s.update([]);
  }
  assert.equal(picked, 6);
  now.v = 90000;
  s.update([event({ id: "ev_late", observed_at_ms: 89000, expires_at_ms: 99000 })]);
  assert.equal(s.next(clear), null);
});

test("a failed transport still consumes the episode and slot", () => {
  const now = { v: 1500 };
  const s = createGuidanceScheduler({ clock: () => now.v, policy: policy() });
  s.update([event()]);
  assert.ok(s.next(clear));
  s.update([]);
  now.v = 12000;
  s.update([event({ id: "ev_retry", observed_at_ms: 11900, expires_at_ms: 20000 })]);
  assert.equal(s.next(clear).id, "ev_retry");
});

test("stop suppresses all future dispatch", () => {
  const s = createGuidanceScheduler({ clock: () => 1500, policy: policy() });
  s.update([event()]);
  s.stop();
  assert.equal(s.next(clear), null);
});

test("disabled, missing or invalid policy fails closed", () => {
  for (const p of [
    null,
    policy({ enabled: false }),
    policy({ maximum_responses_per_sweep: 0 }),
    policy({ minimum_response_interval_ms: Number.NaN }),
    policy({ maximum_event_age_ms: -1 }),
  ]) {
    const s = createGuidanceScheduler({ clock: () => 1500, policy: p });
    s.update([event()]);
    assert.equal(s.next(clear), null);
  }
});

test("issues on different rows are separate episodes", () => {
  const now = { v: 1500 };
  const s = createGuidanceScheduler({ clock: () => now.v, policy: policy({ minimum_response_interval_ms: 0 }) });
  s.update([event({ id: "r1", row_id: "row_1" }), event({ id: "r2", row_id: "row_2" })]);
  assert.ok(s.next(clear));
  assert.ok(s.next(clear));
  assert.equal(s.next(clear), null);
});

test("critical severity dispatches before warning", () => {
  const s = createGuidanceScheduler({ clock: () => 1500, policy: policy() });
  s.update([
    event({ id: "warn", issue: "blur" }),
    event({ id: "crit", issue: "occlusion", severity: "critical", observed_at_ms: 1400 }),
  ]);
  assert.equal(s.next(clear).id, "crit");
});

test("expired source retains the episode; only absence clears suppression", () => {
  const now = { v: 1500 };
  const s = createGuidanceScheduler({ clock: () => now.v, policy: policy() });
  s.update([event()]);
  assert.ok(s.next(clear));
  now.v = 5000;
  s.update([event({ id: "ev_x", expires_at_ms: 4000, observed_at_ms: 1000 })]);
  assert.equal(s.next(clear), null);
  now.v = 15000;
  s.update([event({ id: "ev_fresh", observed_at_ms: 14900, expires_at_ms: 20000 })]);
  assert.equal(s.next(clear), null);
  s.update([]);
  s.update([event({ id: "ev_after_clear", observed_at_ms: 15000, expires_at_ms: 20000 })]);
  assert.equal(s.next(clear).id, "ev_after_clear");
});

test("invalid events are rejected", () => {
  const s = createGuidanceScheduler({ clock: () => 1500, policy: policy() });
  s.update([event({ observed_at_ms: 2000 })]);
  s.update([event({ expires_at_ms: 500 })]);
  s.update([event({ frame_id: null })]);
  s.update([event({ frame_id: undefined })]);
  assert.equal(s.next(clear), null);
});

test("non-integer or nonpositive cap fails closed", () => {
  for (const cap of [6.5, 0, -1, Number.NaN]) {
    const s = createGuidanceScheduler({
      clock: () => 1500,
      policy: policy({ maximum_responses_per_sweep: cap }),
    });
    s.update([event()]);
    assert.equal(s.next(clear), null, String(cap));
  }
});
