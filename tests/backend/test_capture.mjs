import assert from "node:assert/strict";
import test from "node:test";
import { createSampler } from "../../backend/web/capture.js";

function makeTimers() {
  const timers = new Map();
  let nextId = 1;
  return {
    setInterval: (fn) => {
      const id = nextId++;
      timers.set(id, fn);
      return id;
    },
    clearInterval: (id) => timers.delete(id),
    tick: () => {
      for (const fn of [...timers.values()]) fn();
    },
    pending: () => timers.size,
  };
}

function makeVideo() {
  return { currentTime: 0, readyState: 4, videoWidth: 640, videoHeight: 480 };
}

function makeCanvas(jpegBytes = new Uint8Array([0xff, 0xd8, 0xff])) {
  return {
    width: 0,
    height: 0,
    getContext: () => ({ drawImage: () => {} }),
    toBlob: (cb) => cb(jpegBytes),
  };
}

function makeSampler(overrides = {}) {
  const events = [];
  const uploads = [];
  const timers = makeTimers();
  const sampler = createSampler({
    video: overrides.video || makeVideo(),
    canvas: overrides.canvas || makeCanvas(),
    upload: overrides.upload || ((jpeg, ts) => { uploads.push({ jpeg, ts }); return Promise.resolve(); }),
    onEvent: (e) => events.push(e),
    clock: () => 0,
    setInterval: timers.setInterval,
    clearInterval: timers.clearInterval,
    intervalMs: 1000,
    ...overrides.samplerOptions,
  });
  return { sampler, events, uploads, timers };
}

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

test("non-advancing preview time is skipped with freshness warning", async () => {
  const video = makeVideo();
  video.currentTime = 1;
  const { sampler, events, uploads, timers } = makeSampler({ video });
  sampler.start();
  timers.tick();
  await flush();
  assert.equal(uploads.length, 1);
  timers.tick();
  await flush();
  assert.equal(uploads.length, 1);
  const warnings = events.filter((e) => e.event === "freshness_warning");
  assert.equal(warnings.length, 1);
  assert.equal(warnings[0].video_time_s, 1);
  sampler.stop();
});

test("a second tick while upload in flight is skipped", async () => {
  const video = makeVideo();
  let release;
  const upload = () => new Promise((resolve) => { release = resolve; });
  const { sampler, events, uploads, timers } = makeSampler({ video, upload });
  sampler.start();
  video.currentTime = 1;
  timers.tick();
  video.currentTime = 2;
  timers.tick();
  await flush();
  assert.equal(events.filter((e) => e.event === "upload_started").length, 1);
  const skipped = events.filter(
    (e) => e.event === "sampling_tick" && e.skip_reason === "upload_in_flight"
  );
  assert.equal(skipped.length, 1);
  release();
  await flush();
  video.currentTime = 3;
  timers.tick();
  await flush();
  assert.equal(events.filter((e) => e.event === "upload_started").length, 2);
  sampler.stop();
});

test("stop aborts the in-flight upload and no late success fires", async () => {
  const video = makeVideo();
  const signals = [];
  const upload = (jpeg, ts, signal) => {
    signals.push(signal);
    return new Promise((resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    });
  };
  const { sampler, events, timers } = makeSampler({ video, upload });
  sampler.start();
  video.currentTime = 1;
  timers.tick();
  await flush();
  assert.equal(signals.length, 1);
  sampler.stop();
  await flush();
  assert.ok(signals[0].aborted);
  assert.equal(events.filter((e) => e.event === "upload_success").length, 0);
});

test("identical bytes with advancing time are not a freshness failure", async () => {
  const video = makeVideo();
  const canvas = makeCanvas(new Uint8Array([1, 2, 3]));
  const { sampler, events, uploads, timers } = makeSampler({ video, canvas });
  sampler.start();
  for (const t of [1, 2, 3]) {
    video.currentTime = t;
    timers.tick();
    await flush();
  }
  assert.equal(uploads.length, 3);
  assert.equal(events.filter((e) => e.event === "freshness_warning").length, 0);
  assert.equal(events.filter((e) => e.event === "upload_success").length, 3);
  sampler.stop();
});

test("a restarted sampler owns a new callback generation", async () => {
  const video = makeVideo();
  const resolvers = [];
  const upload = (jpeg, ts, signal) => new Promise((resolve) => resolvers.push(resolve));
  const { sampler, events, timers } = makeSampler({ video, upload });
  sampler.start();
  video.currentTime = 1;
  timers.tick();
  await flush();
  sampler.stop();
  sampler.start();
  video.currentTime = 2;
  timers.tick();
  await flush();
  assert.equal(resolvers.length, 2);
  for (const resolve of resolvers) resolve();
  await flush();
  assert.equal(events.filter((e) => e.event === "upload_success").length, 1);
  const success = events.find((e) => e.event === "upload_success");
  assert.equal(success.video_time_s, 2);
  sampler.stop();
});

test("an encode finishing after stop does not upload or emit", async () => {
  const video = makeVideo();
  video.currentTime = 1;
  let heldCallback;
  const canvas = {
    width: 0,
    height: 0,
    getContext: () => ({ drawImage: () => {} }),
    toBlob: (cb) => { heldCallback = cb; },
  };
  const { sampler, events, uploads, timers } = makeSampler({ video, canvas });
  sampler.start();
  timers.tick();
  await flush();
  sampler.stop();
  heldCallback(new Uint8Array([1, 2, 3]));
  await flush();
  assert.equal(uploads.length, 0);
  const outcomes = events.filter(
    (e) => e.event === "upload_started" || e.event === "upload_success" || e.event === "upload_failed"
  );
  assert.equal(outcomes.length, 0);
});

test("stop before the queued microtask prevents draw and upload", async () => {
  const video = makeVideo();
  video.currentTime = 1;
  let drew = false;
  const canvas = {
    width: 0,
    height: 0,
    getContext: () => ({ drawImage: () => { drew = true; } }),
    toBlob: (cb) => cb(new Uint8Array([1])),
  };
  const { sampler, uploads, timers } = makeSampler({ video, canvas });
  sampler.start();
  timers.tick();
  sampler.stop();
  await flush();
  assert.equal(drew, false);
  assert.equal(uploads.length, 0);
});

test("a backwards timestamp is a freshness skip", async () => {
  const video = makeVideo();
  const { sampler, events, uploads, timers } = makeSampler({ video });
  sampler.start();
  video.currentTime = 5;
  timers.tick();
  await flush();
  assert.equal(uploads.length, 1);
  video.currentTime = 3;
  timers.tick();
  await flush();
  assert.equal(uploads.length, 1);
  assert.equal(events.filter((e) => e.event === "freshness_warning").length, 1);
  sampler.stop();
});

test("an old aborted upload emits nothing into a new generation", async () => {
  const video = makeVideo();
  const signals = [];
  const upload = (jpeg, ts, signal) => {
    signals.push(signal);
    return new Promise((resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    });
  };
  const { sampler, events, timers } = makeSampler({ video, upload });
  sampler.start();
  video.currentTime = 1;
  timers.tick();
  await flush();
  sampler.stop();
  sampler.start();
  video.currentTime = 2;
  timers.tick();
  await flush();
  const stale = events.filter(
    (e) => e.video_time_s === 1 && (e.event === "upload_failed" || e.event === "upload_success" || e.event === "upload_aborted")
  );
  assert.equal(stale.length, 0);
  sampler.stop();
});

test("a thrown encode produces a controlled upload_failed", async () => {
  const video = makeVideo();
  video.currentTime = 1;
  const canvas = {
    width: 0,
    height: 0,
    getContext: () => ({ drawImage: () => {} }),
    toBlob: () => { throw new Error("tainted canvas"); },
  };
  const { sampler, events, uploads, timers } = makeSampler({ video, canvas });
  sampler.start();
  timers.tick();
  await flush();
  assert.equal(uploads.length, 0);
  const failures = events.filter((e) => e.event === "upload_failed");
  assert.equal(failures.length, 1);
  assert.match(String(failures[0].error), /tainted|encode/);
  sampler.stop();
});

test("upload success reports frame identity, bytes, dims and durations", async () => {
  const video = makeVideo();
  video.currentTime = 1;
  let now = 100;
  const { sampler, events, timers } = makeSampler({
    video,
    upload: async (jpeg, ts) => ({ saved: true, frame_id: "frame_x9", frame_ref: "frames/frame_x9.jpg" }),
    samplerOptions: { clock: () => now },
  });
  sampler.start();
  timers.tick();
  await flush();
  const started = events.find((e) => e.event === "upload_started");
  assert.equal(started.width, 640);
  assert.equal(started.height, 480);
  assert.equal(started.encoded_bytes, 3);
  assert.equal(started.encode_ms, 0);
  const done = events.find((e) => e.event === "upload_success");
  assert.equal(done.frame_id, "frame_x9");
  assert.equal(done.frame_ref, "frames/frame_x9.jpg");
  assert.equal(done.encoded_bytes, 3);
  assert.equal(done.upload_ms, 0);
  sampler.stop();
});

test("sanitized capture settings expose only the whitelist", async () => {
  const video = makeVideo();
  video.srcObject = {
    getVideoTracks: () => [{
      getSettings: () => ({
        width: 640,
        height: 480,
        frameRate: 30,
        aspectRatio: 1.333,
        facingMode: "environment",
        deviceId: "secret-device",
        groupId: "secret-group",
        label: "Front Camera",
      }),
    }],
  };
  const { sampler, events } = makeSampler({ video });
  sampler.start();
  const settings = events.find((e) => e.event === "capture_settings");
  assert.ok(settings);
  assert.equal(settings.width, 640);
  assert.equal(settings.frameRate, 30);
  assert.equal(settings.facingMode, "environment");
  assert.ok(!("deviceId" in settings));
  assert.ok(!("groupId" in settings));
  assert.ok(!("label" in settings));
  sampler.stop();
});

test("missing or partial settings are safe", async () => {
  const video = makeVideo();
  const a = makeSampler({ video });
  a.sampler.start();
  assert.ok(!a.events.some((e) => e.event === "capture_settings"));
  a.sampler.stop();
  video.srcObject = { getVideoTracks: () => [{ getSettings: () => ({ width: 1280 }) }] };
  const b = makeSampler({ video });
  b.sampler.start();
  const s = b.events.find((e) => e.event === "capture_settings");
  assert.deepEqual({ width: s.width }, { width: 1280 });
  b.sampler.stop();
});

test("skipped attempts stay separate from upload attempts", async () => {
  const video = makeVideo();
  const { sampler, events, uploads, timers } = makeSampler({ video });
  sampler.start();
  timers.tick();
  timers.tick();
  await flush();
  const skips = events.filter((e) => e.event === "sampling_tick" && e.skip_reason);
  assert.ok(skips.length >= 1);
  assert.equal(uploads.length, 1);
  assert.equal(events.filter((e) => e.event === "upload_started").length, 1);
  sampler.stop();
});
