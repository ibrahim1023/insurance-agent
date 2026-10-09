import assert from "node:assert/strict";
import test from "node:test";
import { createCaptureController, httpError } from "../../backend/web/lifecycle.js";

function makeTimers() {
  const intervals = new Map();
  const timeouts = new Map();
  let nextId = 1;
  return {
    setInterval: (fn) => { const id = nextId++; intervals.set(id, fn); return id; },
    clearInterval: (id) => intervals.delete(id),
    setTimeout: (fn) => { const id = nextId++; timeouts.set(id, fn); return id; },
    clearTimeout: (id) => timeouts.delete(id),
    tick: () => { for (const fn of [...intervals.values()]) fn(); },
    fireTimeouts: () => { for (const fn of [...timeouts.values()]) fn(); timeouts.clear(); },
  };
}

function mkTrack(kind) {
  return { kind, stopped: false, stop() { this.stopped = true; }, readyState: "live" };
}

function mkStream() {
  const tracks = [mkTrack("video"), mkTrack("audio")];
  return {
    tracks,
    getTracks() { return tracks; },
    getVideoTracks() { return tracks.filter((t) => t.kind === "video"); },
    getAudioTracks() { return tracks.filter((t) => t.kind === "audio"); },
  };
}

function mkChannel() {
  return {
    readyState: "connecting",
    sent: [],
    onopen: null,
    onmessage: null,
    send(data) { this.sent.push(JSON.parse(data)); },
    open() { this.readyState = "open"; if (this.onopen) this.onopen(); },
    close() { this.readyState = "closed"; },
  };
}

function mkPeer() {
  const channel = mkChannel();
  const peer = {
    connectionState: "new",
    iceGatheringState: "complete",
    localDescription: null,
    remoteDescription: null,
    channel,
    closed: false,
    ontrack: null,
    onconnectionstatechange: null,
    addTrack() {},
    createDataChannel() { return channel; },
    async createOffer() { return { type: "offer", sdp: "v=0 offer" }; },
    async setLocalDescription(desc) { this.localDescription = desc; },
    async setRemoteDescription(desc) { this.remoteDescription = desc; },
    close() { this.closed = true; this.connectionState = "closed"; },
  };
  return peer;
}

function makeDeps(overrides = {}) {
  const ui = {
    statusText: null,
    statusCls: null,
    stage: null,
    frameCount: 0,
    sweepId: null,
    warnings: [],
    logLines: [],
    setStatus(text, cls) { this.statusText = text; this.statusCls = cls || ""; },
    setStage(text) { this.stage = text; },
    setFrameCount(n) { this.frameCount = n; },
    setSweepId(text) { this.sweepId = text; },
    warn(text) { this.warnings.push(text); },
    log(name, facts) { this.logLines.push(Object.assign({ event: name }, facts || {})); },
    observations: [],
    observation: undefined,
    setObservation(obs, ctx) { this.observations.push({ obs, ctx }); this.observation = obs; },
    latestFrameId: undefined,
    setLatestFrameId(id) { this.latestFrameId = id; },
    packet: undefined,
    setPacket(p) { this.packet = p; },
  };
  const fields = { enabled: true, setEnabled(v) { this.enabled = v; } };
  const calls = { endSweep: [], interruptSweep: [], addTurn: [], getSweep: [], createSweep: 0, correct: [], readSpines: [], priceBooks: [], packet: [], getPacket: [] };
  let counter = 0;
  const streams = [];
  const preview = new EventTarget();
  preview.readyState = 1;
  preview.currentTime = 0;
  preview.videoWidth = 640;
  preview.videoHeight = 480;
  preview.srcObject = null;
  preview.play = async () => {};
  const samplers = [];
  let now = 0;
  const deps = {
    ui,
    fields,
    api: {
      createSweep: overrides.createSweep || (async () => { calls.createSweep++; return { id: "s" + ++counter, status: "capturing", frames: [], revision: 0 }; }),
      getSweep: overrides.getSweep || (async (id) => { calls.getSweep.push(id); return { id, status: "capturing", frames: [], revision: 1 }; }),
      uploadFrame: async () => ({ saved: true }),
      addTurn: async (id, body) => { calls.addTurn.push(body); return {}; },
      endSweep: overrides.endSweep || (async (id, duration_s, opts) => { calls.endSweep.push({ id, duration_s, keepalive: !!(opts && opts.keepalive) }); return { id, status: "partial", frames: [], postwork_status: "completed", revision: 1 }; }),
      interruptSweep: async (id) => { calls.interruptSweep.push(id); return {}; },
      realtime: overrides.realtime || (async () => "v=0 answer"),
      analyzeFrame: overrides.analyzeFrame || (async () => { throw new Error("analyzeFrame not stubbed"); }),
      correct: overrides.correct || (async (id, intent) => { calls.correct.push(intent); return { applied: true, revision: 2, review_items: [] }; }),
      readSpines: overrides.readSpines || (async (id, refIds) => { calls.readSpines.push({ id, refIds }); return { readings: [], ref_ids: [], batches: 0, skipped: true }; }),
      priceBooks: overrides.priceBooks || (async (id, bookIds) => { calls.priceBooks.push({ id, bookIds }); return { valuations: {}, reviews_added: 0 }; }),
      packet: overrides.packet || (async (id) => { calls.packet.push(id); return { summary: "sum", revision: 1, stale: false, status: "partial" }; }),
      getPacket: overrides.getPacket || (async (id) => { calls.getPacket.push(id); return { summary: "sum", revision: 1, stale: false, status: "partial", packet_ref: "claim_packet.json", report_ref: "report.html" }; }),
    },
    media: {
      getUserMedia: overrides.getUserMedia || (async () => { const s = mkStream(); streams.push(s); return s; }),
    },
    preview,
    remoteAudio: { srcObject: null, play: async () => {} },
    samplerFactory: (opts) => {
      const s = { opts, started: false, stopped: false, start() { this.started = true; }, stop() { this.stopped = true; } };
      samplers.push(s);
      return s;
    },
    createPeer: () => { const peer = mkPeer(); deps.peers.push(peer); return peer; },
    waitForConnection: overrides.waitForConnection || (async (peer) => { peer.connectionState = "connected"; }),
    waitIceGathering: async () => {},
    storage: { values: {}, get(k) { return this.values[k]; }, set(k, v) { this.values[k] = v; } },
    timers: overrides.timers || makeTimers(),
    clock: () => now,
    setNow: (v) => { now = v; },
    device: () => "fake-device",
    locale: () => ({ country: "AE", currency: "USD" }),
    newTurnId: () => "abcdef0123456789abcdef0123456789",
    peers: [],
    lastPeer: () => deps.peers[deps.peers.length - 1],
  };
  return { deps, ui, fields, calls, streams, preview, samplers, storage: deps.storage };
}


let helperRespSeq = 0;
function lastCreateReq(channel) {
  for (let i = channel.sent.length - 1; i >= 0; i--) {
    const m = channel.sent[i];
    if (m.type === "response.create" && m.response && m.response.metadata) {
      return m.response.metadata.app_request;
    }
  }
  return null;
}
function beginResponse(channel, purpose) {
  const id = "r_auto_" + ++helperRespSeq;
  channel.onmessage({ data: JSON.stringify({
    type: "response.created",
    response: { id, metadata: { app_request: lastCreateReq(channel), purpose: purpose || "user" } },
  }) });
  return id;
}
function finishResponse(channel, id) {
  channel.onmessage({ data: JSON.stringify({ type: "response.done", response: { id } }) });
}
function completeResponse(channel, purpose) {
  const id = beginResponse(channel, purpose);
  finishResponse(channel, id);
  return id;
}

function flush(times = 4) {
  return new Promise((resolve) => setTimeout(resolve, 0)).then(() => times > 1 ? flush(times - 1) : undefined);
}

test("a stale restore response cannot overwrite a new run's UI", async () => {
  const { deps, ui, storage } = makeDeps();
  let releaseGet;
  deps.api.getSweep = (id) => new Promise((resolve) => { releaseGet = () => resolve({ id, status: "interrupted", frames: [1, 2, 3] }); });
  storage.set("sweepId", "old-sweep");
  const controller = createCaptureController(deps);
  const restore = controller.restoreSaved();
  deps.setNow(0);
  await controller.start();
  releaseGet();
  await restore;
  await flush();
  assert.equal(ui.sweepId, "s1");
  assert.equal(ui.frameCount, 0);
  controller.stop();
});

test("end persistence failure warns instead of claiming ended", async () => {
  const { deps, ui, calls } = makeDeps({
    endSweep: async () => { throw new Error("server gone"); },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  controller.stop();
  await flush();
  assert.match(ui.statusText, /END NOT SAVED/i);
  assert.equal(ui.warnings.length, 1);
  assert.match(ui.warnings[0], /end/i);
});

test("typed turn forwards the persisted turnId in the provider item", async () => {
  const { deps, calls } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  deps.lastPeer().channel.open();
  await controller.sendTurn("skip the top shelf");
  await flush();
  assert.equal(calls.addTurn.length, 1);
  const turnId = calls.addTurn[0].turn_id;
  assert.match(turnId, /^[0-9a-f]{32}$/);
  const peer0 = deps.lastPeer();
  const item = peer0.channel.sent.find((m) => m.type === "conversation.item.create");
  assert.equal(item.item.id, turnId);
  assert.equal(item.item.content[0].text, "skip the top shelf");
  controller.stop();
});

test("stop during pending getUserMedia stops the late stream", async () => {
  let release;
  const { deps, streams, calls } = makeDeps({
    getUserMedia: () => new Promise((resolve) => { release = resolve; }),
  });
  const controller = createCaptureController(deps);
  const started = controller.start();
  controller.stop();
  const late = mkStream();
  release(late);
  await started;
  await flush();
  assert.ok(late.tracks.every((t) => t.stopped));
  assert.equal(calls.createSweep, 0);
});

test("duration is measured from video-ready capture start", async () => {
  const { deps, calls, preview } = makeDeps();
  preview.readyState = 0;
  const controller = createCaptureController(deps);
  deps.setNow(0);
  const started = controller.start();
  await flush();
  deps.setNow(2000);
  preview.dispatchEvent(new Event("loadedmetadata"));
  await started;
  deps.setNow(5000);
  controller.stop();
  await flush();
  assert.equal(calls.endSweep[0].duration_s, 3);
});

test("pagehide releases owned tracks and persists a best-effort end", async () => {
  const { deps, streams, samplers, calls } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  controller.pagehide();
  const stream = streams[0];
  assert.ok(stream.tracks.every((t) => t.stopped));
  assert.ok(samplers[0].stopped);
  assert.equal(calls.endSweep.length, 1);
  assert.equal(calls.endSweep[0].keepalive, true);
  assert.deepEqual(calls.interruptSweep, []);
});

test("voice setup failure stops audio but preserves capture", async () => {
  const { deps, streams, samplers, ui } = makeDeps({
    realtime: async () => { throw new Error("http 503"); },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const stream = streams[0];
  assert.ok(stream.getAudioTracks()[0].stopped);
  assert.equal(stream.getVideoTracks()[0].stopped, false);
  assert.equal(samplers[0].stopped, false);
  assert.match(ui.statusText, /PARTIAL|camera only/i);
  controller.stop();
});

test("one greeting only after peer and channel are ready", async () => {
  const { deps } = makeDeps({
    waitForConnection: async (peer) => { peer.connectionState = "connected"; },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const peer = deps.lastPeer();
  assert.equal(peer.channel.sent.length, 0);
  peer.channel.open();
  assert.equal(peer.channel.sent.filter((m) => m.type === "response.create").length, 1);
  controller.stop();
});

test("locale fields disable during a run and re-enable after the call ends", async () => {
  const { deps, fields } = makeDeps();
  const controller = createCaptureController(deps);
  const started = controller.start();
  assert.equal(fields.enabled, false);
  await started;
  assert.equal(fields.enabled, false);
  controller.stop();
  assert.equal(fields.enabled, false);
  controller.endCall();
  assert.equal(fields.enabled, true);
});

test("sampling starts only after the preview is ready", async () => {
  const { deps, samplers, preview } = makeDeps();
  preview.readyState = 0;
  const controller = createCaptureController(deps);
  const started = controller.start();
  await flush();
  assert.equal(samplers.length, 0);
  preview.dispatchEvent(new Event("loadedmetadata"));
  await started;
  assert.equal(samplers.length, 1);
  assert.ok(samplers[0].started);
  controller.stop();
});

test("a createSweep response arriving after stop interrupts the orphaned sweep", async () => {
  let releaseCreate;
  const { deps, ui, calls } = makeDeps({
    createSweep: () => new Promise((resolve) => { releaseCreate = resolve; }),
  });
  const controller = createCaptureController(deps);
  const started = controller.start();
  await flush();
  controller.stop();
  releaseCreate({ id: "late-sweep", status: "capturing", frames: [], revision: 0 });
  await started;
  await flush();
  assert.deepEqual(calls.interruptSweep, ["late-sweep"]);
  assert.equal(ui.sweepId, null);
  assert.equal(calls.endSweep.length, 0);
});

test("stale peer callbacks cannot mutate UI after stop and restart", async () => {
  const { deps, ui } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  const oldPeer = deps.lastPeer();
  const staleOnTrack = oldPeer.ontrack;
  const staleOnMessage = oldPeer.channel.onmessage;
  const staleOnState = oldPeer.onconnectionstatechange;
  const staleOnOpen = oldPeer.channel.onopen;
  const staleChannel = oldPeer.channel;
  controller.stop();
  await flush();
  await controller.start();
  const logCount = ui.logLines.length;
  const warnCount = ui.warnings.length;
  staleOnTrack({ streams: [{ fake: true }] });
  staleOnMessage({ data: '{"type":"stale.event"}' });
  staleOnState();
  staleOnOpen();
  await flush();
  assert.equal(ui.logLines.length, logCount);
  assert.equal(ui.warnings.length, warnCount);
  assert.equal(deps.remoteAudio.srcObject, null);
  assert.equal(staleChannel.sent.length, 0);
});

test("a typed turn rejection from a dead run does not log into the new run", async () => {
  const { deps, ui } = makeDeps();
  let rejectTurn;
  deps.api.addTurn = () => new Promise((resolve, reject) => { rejectTurn = reject; });
  const controller = createCaptureController(deps);
  await controller.start();
  controller.sendTurn("stale note");
  await flush();
  controller.stop();
  await flush();
  await controller.start();
  const logCount = ui.logLines.length;
  rejectTurn(new Error("late failure"));
  await flush();
  assert.equal(ui.logLines.length, logCount);
  controller.stop();
});

test("preview failure after sweep creation persists an interrupt", async () => {
  const { deps, calls, preview } = makeDeps();
  preview.play = async () => { throw new Error("play denied"); };
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  assert.deepEqual(calls.interruptSweep, ["s1"]);
});

test("a late analyzeLatest result cannot paint into a new sweep", async () => {
  const { deps, ui } = makeDeps({
    getSweep: async (id) => ({
      id, status: "capturing", revision: 1,
      frames: [{ id: "frame_aaa", frame_ref: "frames/frame_aaa.jpg", timestamp_s: 1, width_px: 8, height_px: 8 }],
      latest_observation: null,
    }),
  });
  let releaseAnalyze;
  deps.api.analyzeFrame = () => new Promise((resolve) => { releaseAnalyze = resolve; });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  deps.timers.tick();
  await flush();
  assert.equal(ui.latestFrameId, "frame_aaa");
  const analyze = controller.analyzeLatest();
  await flush();
  assert.deepEqual(ui.observation, { pending: true });
  controller.stop();
  await flush();
  await controller.start();
  await flush();
  releaseAnalyze({ frame_id: "frame_aaa", source_frame_id: "frame_aaa", observation: { books: [] } });
  await analyze;
  await flush();
  assert.notDeepEqual(ui.observation, { frame_id: "frame_aaa", source_frame_id: "frame_aaa", observation: { books: [] } });
  assert.equal(ui.observation, null);
  controller.stop();
});

test("poll cannot erase a pending analysis; failed update keeps last success visible", async () => {
  const good = {
    frame_id: "frame_aaa", source_frame_id: "frame_aaa",
    observation: { books: [] }, box_mapping: {}, stage_ref: "stage_outputs/v.json",
  };
  const { deps, ui } = makeDeps({
    getSweep: async (id) => ({
      id, status: "capturing", revision: 1,
      frames: [{ id: "frame_aaa", frame_ref: "frames/frame_aaa.jpg", timestamp_s: 1, width_px: 8, height_px: 8 }],
      latest_observation: null,
    }),
    analyzeFrame: async () => good,
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  deps.timers.tick();
  await flush();
  await controller.analyzeLatest();
  await flush();
  assert.equal(ui.observation, good);

  let releaseAnalyze;
  deps.api.analyzeFrame = () => new Promise((resolve, reject) => { releaseAnalyze = reject; });
  const pending = controller.analyzeLatest();
  await flush();
  assert.deepEqual(ui.observation, { pending: true });
  deps.timers.tick();
  await flush();
  assert.deepEqual(ui.observation, { pending: true });
  releaseAnalyze(new Error("http 502"));
  await pending;
  await flush();
  assert.equal(ui.observation.error, "http 502");
  assert.equal(ui.observation.stale, good);
  controller.stop();
});

test("a failed manual analysis stays visible across later polls", async () => {
  const good = {
    frame_id: "frame_aaa", source_frame_id: "frame_aaa",
    observation: { books: [] }, box_mapping: {}, stage_ref: "stage_outputs/v.json",
  };
  const { deps, ui } = makeDeps({
    getSweep: async (id) => ({
      id, status: "capturing", revision: 1,
      frames: [{ id: "frame_aaa", frame_ref: "frames/frame_aaa.jpg", timestamp_s: 1, width_px: 8, height_px: 8 }],
      latest_observation: good,
    }),
    analyzeFrame: async () => { throw new Error("http 502"); },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  deps.timers.tick();
  await flush();
  assert.equal(ui.observation, good);
  const pending = controller.analyzeLatest();
  await flush();
  deps.timers.tick();
  await flush();
  await pending;
  await flush();
  assert.equal(ui.observation.error, "http 502");
  deps.timers.tick();
  await flush();
  assert.equal(ui.observation.error, "http 502");
  assert.equal(ui.observation.stale, good);
  controller.stop();
});

test("voice transcript persists by item_id and is idempotent", async () => {
  const { deps, calls } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  const ev = {
    type: "conversation.item.input_audio_transcription.completed",
    item_id: "provider-item-1",
    transcript: "exclude the top shelf",
  };
  channel.onmessage({ data: JSON.stringify(ev) });
  channel.onmessage({ data: JSON.stringify(ev) });
  await flush();
  assert.equal(calls.addTurn.length, 1);
  assert.equal(calls.addTurn[0].turn_id, "provider-item-1");
  assert.equal(calls.addTurn[0].source, "provider_user_transcript");
  assert.equal(calls.addTurn[0].text, "exclude the top shelf");
  controller.stop();
  controller.endCall();
});

test("apply_correction waits for its source transcript and returns backend result", async () => {
  const { deps, calls } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("turn-src-1"));
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "apply_correction",
      call_id: "call_1",
      arguments: JSON.stringify({
        type: "exclude_item",
        target_id: "book_1",
        value: true,
        source_turn_id: "turn-src-1",
        source_frame_id: "current",
      }),
    }),
  });
  await flush();
  assert.equal(calls.correct.length, 0);
  channel.onmessage({
    data: JSON.stringify({
      type: "conversation.item.input_audio_transcription.completed",
      item_id: "turn-src-1",
      transcript: "exclude that book",
    }),
  });
  await flush();
  deps.timers.fireTimeouts();
  await flush();
  assert.equal(calls.correct.length, 1);
  assert.equal(calls.correct[0].type, "exclude_item");
  assert.equal(calls.correct[0].source_turn_id, "turn-src-1");
  assert.equal(calls.correct[0].source_frame_id, "frame_a1");
  const output = channel.sent.find((m) => m.type === "conversation.item.create" && m.item && m.item.type === "function_call_output");
  assert.ok(output);
  assert.equal(output.item.call_id, "call_1");
  const result = JSON.parse(output.item.output);
  assert.equal(result.applied, true);
  assert.ok(channel.sent.some((m) => m.type === "response.create"));
  controller.stop();
  controller.endCall();
});

test("apply_correction without a source transcript returns clarification_required", async () => {
  const { deps, calls } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("ctx-turn"));
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "apply_correction",
      call_id: "call_2",
      arguments: JSON.stringify({
        type: "exclude_item",
        target_id: "book_1",
        value: true,
        source_turn_id: "ghost-turn",
        source_frame_id: "current",
      }),
    }),
  });
  await flush();
  deps.setNow(6000);
  deps.timers.fireTimeouts();
  await flush();
  assert.equal(calls.correct.length, 0);
  const output = channel.sent.find((m) => m.type === "conversation.item.create" && m.item && m.item.type === "function_call_output");
  assert.ok(output);
  const result = JSON.parse(output.item.output);
  assert.equal(result.applied, false);
  assert.equal(result.reason, "clarification_required");
  controller.stop();
  controller.endCall();
});

test("stop sweep keeps voice alive; end call tears it down", async () => {
  const { deps, streams } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const peer = deps.lastPeer();
  const channel = peer.channel;
  channel.open();
  controller.stop();
  await flush();
  const stream = streams[0];
  assert.equal(stream.getVideoTracks()[0].stopped, true);
  assert.equal(stream.getAudioTracks()[0].stopped, false);
  assert.equal(peer.closed, false);
  assert.equal(channel.readyState, "open");
  controller.endCall();
  assert.equal(peer.closed, true);
  assert.equal(stream.getAudioTracks()[0].stopped, true);
});

test("a late readSpines result cannot paint into a new run", async () => {
  const { deps, ui, calls } = makeDeps();
  ui.inventory = [];
  ui.setInventory = (state, ctx) => ui.inventory.push(state);
  ui.pendingCalls = [];
  ui.readingPending = (p) => { ui.pendingCalls.push(p); ui._pending = p; };
  const getResolvers = [];
  deps.api.getSweep = (id) => new Promise((resolve) => {
    getResolvers.push({ id, resolve });
  });
  const readResolvers = [];
  deps.api.readSpines = (id, refIds) => {
    calls.readSpines.push({ id, refIds });
    return new Promise((resolve) => { readResolvers.push(resolve); });
  };
  const withBooks = (id) => ({
    id, status: "capturing",
    books: [{ id: "book_1", excluded: false }], items: [], review_queue: [], revision: 1,
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const oldRead = controller.readSpines();
  await flush();
  assert.equal(ui._pending, true);
  getResolvers[0].resolve(withBooks("s1"));
  await flush();
  assert.equal(readResolvers.length, 1);
  readResolvers[0]({ readings: [], ref_ids: [], batches: 1, skipped: false });
  await flush();
  const s1Resolvers = getResolvers.filter((r) => r.id === "s1");
  assert.equal(s1Resolvers.length, 2);

  controller.stop();
  await flush();
  await controller.start();
  await flush();
  assert.equal(ui._pending, false);

  const newRead = controller.readSpines();
  await flush();
  assert.equal(ui._pending, true);
  getResolvers[2].resolve(withBooks("s2"));
  await flush();
  const paintedBefore = ui.inventory.length;

  s1Resolvers[1].resolve({
    id: "s1", status: "capturing",
    books: [{ id: "book_old" }], items: [], review_queue: [], revision: 2,
  });
  await oldRead;
  await flush();
  assert.equal(ui.inventory.length, paintedBefore);
  assert.equal(ui._pending, true);

  readResolvers[1]({ readings: [], ref_ids: [], batches: 1, skipped: false });
  await flush();
  const newGet = getResolvers.filter((r) => r.id === "s2");
  assert.equal(newGet.length, 2);
  newGet[1].resolve({
    id: "s2", status: "capturing", books: [], items: [], review_queue: [], revision: 1,
  });
  await newRead;
  await flush();
  assert.equal(ui._pending, false);
  assert.ok(ui.inventory.some((s) => s && s.id === "s2"));
  controller.endCall();
});

test("duplicate readSpines clicks collapse to one request", async () => {
  const { deps, ui, calls } = makeDeps();
  ui.readingPending = (p) => { ui._pending = p; };
  const readResolvers = [];
  deps.api.readSpines = (id, refIds) => {
    calls.readSpines.push({ id, refIds });
    return new Promise((resolve) => { readResolvers.push(resolve); });
  };
  deps.api.getSweep = async (id) => ({
    id, status: "capturing",
    books: [{ id: "book_1", excluded: false }], items: [], review_queue: [], revision: 2,
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const first = controller.readSpines();
  const dup = controller.readSpines();
  await flush();
  assert.equal(calls.readSpines.length, 1);
  readResolvers[0]({ readings: [], ref_ids: [], batches: 1, skipped: false });
  await first;
  await dup;
  await flush();
  assert.equal(ui._pending, false);
  const again = controller.readSpines();
  await flush();
  assert.equal(calls.readSpines.length, 2);
  readResolvers[1]({ readings: [], ref_ids: [], batches: 1, skipped: false });
  await again;
  await flush();
  assert.equal(ui._pending, false);
  controller.stop();
  controller.endCall();
});

test("endCall clears an owned read spinner so the saved run can be read again", async () => {
  const { deps, ui, calls } = makeDeps();
  ui.inventory = [];
  ui.setInventory = (state, ctx) => ui.inventory.push(state);
  ui.readingPending = (p) => { ui._pending = p; };
  const readResolvers = [];
  deps.api.readSpines = (id, refIds) => {
    calls.readSpines.push({ id, refIds });
    return new Promise((resolve) => { readResolvers.push(resolve); });
  };
  deps.api.getSweep = async (id) => ({
    id, status: "capturing",
    books: [{ id: "book_1", excluded: false }], items: [], review_queue: [], revision: 3,
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const staleRead = controller.readSpines();
  await flush();
  assert.equal(ui._pending, true);
  controller.endCall();
  assert.equal(ui._pending, false);
  const freshRead = controller.readSpines();
  await flush();
  assert.equal(calls.readSpines.length, 2);
  assert.equal(ui._pending, true);
  readResolvers[1]({ readings: [], ref_ids: [], batches: 1, skipped: false });
  await freshRead;
  await flush();
  assert.equal(ui._pending, false);
  assert.ok(ui.inventory.some((s) => s && s.id === "s1"));
  const paintedAfterFresh = ui.inventory.length;
  readResolvers[0]({ readings: [], ref_ids: [], batches: 1, skipped: false });
  await staleRead;
  await flush();
  assert.equal(ui._pending, false);
  assert.equal(ui.inventory.length, paintedAfterFresh);
});

test("a late priceBooks result cannot paint into a new run", async () => {
  const { deps, ui, calls } = makeDeps();
  ui.inventory = [];
  ui.setInventory = (state, ctx) => ui.inventory.push(state);
  ui.pendingCalls = [];
  ui.pricingPending = (p) => { ui.pendingCalls.push(p); ui._pending = p; };
  const getResolvers = [];
  deps.api.getSweep = (id) => new Promise((resolve) => {
    getResolvers.push({ id, resolve });
  });
  const priceResolvers = [];
  deps.api.priceBooks = (id, bookIds) => {
    calls.priceBooks.push({ id, bookIds });
    return new Promise((resolve) => { priceResolvers.push(resolve); });
  };
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const oldPrice = controller.priceBooks();
  await flush();
  assert.equal(ui._pending, true);
  priceResolvers[0]({ valuations: {}, reviews_added: 0 });
  await flush();
  assert.equal(getResolvers.filter((r) => r.id === "s1").length, 1);

  controller.stop();
  await flush();
  await controller.start();
  await flush();
  assert.equal(ui._pending, false);

  const newPrice = controller.priceBooks();
  await flush();
  assert.equal(ui._pending, true);
  const paintedBefore = ui.inventory.length;

  getResolvers.find((r) => r.id === "s1").resolve({
    id: "s1", status: "capturing",
    books: [{ id: "book_old" }], items: [], review_queue: [], revision: 2,
  });
  await oldPrice;
  await flush();
  assert.equal(ui.inventory.length, paintedBefore);
  assert.equal(ui._pending, true);

  priceResolvers[1]({ valuations: {}, reviews_added: 0 });
  await flush();
  const newGet = getResolvers.filter((r) => r.id === "s2");
  assert.equal(newGet.length, 1);
  newGet[0].resolve({
    id: "s2", status: "capturing", books: [], items: [], review_queue: [], revision: 1,
  });
  await newPrice;
  await flush();
  assert.equal(ui._pending, false);
  assert.equal(ui.inventory.filter((s) => s && s.id === "s2").length, 1);
  controller.endCall();
});

test("duplicate priceBooks clicks collapse to one request", async () => {
  const { deps, ui, calls } = makeDeps();
  ui.pricingPending = (p) => { ui._pending = p; };
  const priceResolvers = [];
  deps.api.priceBooks = (id, bookIds) => {
    calls.priceBooks.push({ id, bookIds });
    return new Promise((resolve) => { priceResolvers.push(resolve); });
  };
  deps.api.getSweep = async (id) => ({
    id, status: "capturing", books: [], items: [], review_queue: [], revision: 2,
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const first = controller.priceBooks();
  const dup = controller.priceBooks();
  await flush();
  assert.equal(calls.priceBooks.length, 1);
  priceResolvers[0]({ valuations: {}, reviews_added: 0 });
  await first;
  await dup;
  await flush();
  assert.equal(ui._pending, false);
  const again = controller.priceBooks();
  await flush();
  assert.equal(calls.priceBooks.length, 2);
  priceResolvers[1]({ valuations: {}, reviews_added: 0 });
  await again;
  await flush();
  assert.equal(ui._pending, false);
  controller.stop();
  controller.endCall();
});

test("endCall clears an owned price spinner so the saved run can be priced again", async () => {
  const { deps, ui, calls } = makeDeps();
  ui.inventory = [];
  ui.setInventory = (state, ctx) => ui.inventory.push(state);
  ui.pricingPending = (p) => { ui._pending = p; };
  const priceResolvers = [];
  deps.api.priceBooks = (id, bookIds) => {
    calls.priceBooks.push({ id, bookIds });
    return new Promise((resolve) => { priceResolvers.push(resolve); });
  };
  deps.api.getSweep = async (id) => ({
    id, status: "capturing", books: [], items: [], review_queue: [], revision: 3,
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const stalePrice = controller.priceBooks();
  await flush();
  assert.equal(ui._pending, true);
  controller.endCall();
  assert.equal(ui._pending, false);
  const freshPrice = controller.priceBooks();
  await flush();
  assert.equal(calls.priceBooks.length, 2);
  assert.equal(ui._pending, true);
  priceResolvers[1]({ valuations: {}, reviews_added: 0 });
  await freshPrice;
  await flush();
  assert.equal(ui._pending, false);
  assert.equal(ui.inventory.filter((s) => s && s.id === "s1").length, 1);
  priceResolvers[0]({ valuations: {}, reviews_added: 0 });
  await stalePrice;
  await flush();
  assert.equal(ui._pending, false);
});

test("endCall during capture persists a normal end before teardown", async () => {
  const { deps, calls, streams } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  controller.endCall();
  await flush();
  assert.equal(calls.endSweep.length, 1);
  assert.deepEqual(calls.interruptSweep, []);
  const stream = streams[0];
  assert.ok(stream.tracks.every((t) => t.stopped));
});

test("a delayed tool call never corrects after endCall", async () => {
  const { deps, calls } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "apply_correction",
      call_id: "call_9",
      arguments: JSON.stringify({
        type: "exclude_item",
        target_id: "book_x",
        value: true,
        source_turn_id: "ghost",
      }),
    }),
  });
  const staleOnMessage = channel.onmessage;
  controller.endCall();
  deps.setNow(9000);
  deps.timers.fireTimeouts();
  await flush();
  assert.equal(calls.correct.length, 0);
  staleOnMessage({
    data: JSON.stringify({
      type: "conversation.item.input_audio_transcription.completed",
      item_id: "late-transcript",
      transcript: "late words",
    }),
  });
  await flush();
  assert.equal(calls.addTurn.length, 0);
  assert.equal(calls.correct.length, 0);
});

test("sweep end publishes packet summary into the realtime conversation", async () => {
  const { deps, ui } = makeDeps({
    endSweep: async (id) => ({
      id,
      status: "partial",
      frames: [],
      postwork_status: "completed",
      packet: { summary: "This is a partial packet.", revision: 2, stale: false, status: "partial" },
    }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  controller.stop();
  await flush();
  assert.equal(ui.packet.summary, "This is a partial packet.");
  const item = channel.sent.find((m) => m.type === "conversation.item.create");
  assert.equal(item.item.role, "user");
  assert.equal(item.item.content[0].type, "input_text");
  assert.equal(item.item.content[0].text, "APP-VALIDATED-SUMMARY This is a partial packet.");
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, 2);
});

test("a stale packet summary is rendered but never sent to the model", async () => {
  const { deps, ui } = makeDeps({
    endSweep: async (id) => ({
      id,
      status: "partial",
      frames: [],
      postwork_status: "completed",
      packet: { summary: "old summary", revision: 1, stale: true, status: "partial" },
    }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  await flush();
  const responsesBefore = channel.sent.filter((m) => m.type === "response.create").length;
  controller.stop();
  await flush();
  assert.equal(ui.packet.stale, true);
  assert.equal(
    channel.sent.filter((m) => m.type === "response.create").length,
    responsesBefore
  );
  assert.ok(!channel.sent.some(
    (m) => m.type === "conversation.item.create" &&
      m.item.content && m.item.content[0] &&
      String(m.item.content[0].text).startsWith("APP-VALIDATED-SUMMARY")
  ));
});

test("manual packet button publishes for the owning sweep and sends the summary", async () => {
  const { deps, ui, calls } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  controller.stop();
  await flush();
  await controller.publishPacket();
  assert.equal(calls.packet.length, 1);
  assert.equal(calls.packet[0], ui.sweepId);
  const item = channel.sent.find((m) => m.type === "conversation.item.create");
  assert.ok(item.item.content[0].text.startsWith("APP-VALIDATED-SUMMARY "));
});

test("a late packet publish from an old run cannot paint or send", async () => {
  const { deps, ui, calls } = makeDeps();
  let release;
  deps.api.packet = (id) => {
    calls.packet.push(id);
    return new Promise((resolve) => { release = () => resolve({ summary: "late", revision: 9, stale: false, status: "partial" }); });
  };
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  controller.stop();
  await flush();
  const sentBefore = channel.sent.length;
  const pending = controller.publishPacket();
  controller.endCall();
  release();
  await pending;
  await flush();
  assert.ok(!ui.packet);
  assert.equal(channel.sent.length, sentBefore);
});

test("a held publish from an old generation does not block a new run's publish", async () => {
  const { deps, calls } = makeDeps();
  let release;
  deps.api.packet = (id) => {
    calls.packet.push(id);
    return new Promise((resolve) => { release = () => resolve({ summary: "held", revision: 1, stale: false, status: "partial" }); });
  };
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const pending = controller.publishPacket();
  controller.stop();
  await flush();
  await controller.start();
  await flush();
  deps.api.packet = async (id) => {
    calls.packet.push(id);
    return { summary: "fresh", revision: 4, stale: false, status: "partial" };
  };
  await controller.publishPacket();
  assert.equal(calls.packet.length, 2);
  release();
  await pending;
});

test("a failed end-of-sweep packet shows a warning instead of failing silently", async () => {
  const { deps, ui } = makeDeps({
    endSweep: async (id) => ({ id, status: "partial", frames: [], packet_error: "claim packet validation failed" }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  controller.stop();
  await flush();
  assert.ok(ui.warnings.some((w) => w.includes("claim packet failed")));
});

test("a packet revision older than the polled state revision is not sent", async () => {
  const { deps, calls, ui } = makeDeps({
    endSweep: async (id) => ({ id, status: "partial", frames: [], revision: 3 }),
    getSweep: async (id) => ({ id, status: "partial", frames: [], revision: 3 }),
    packet: async (id) => { calls.packet.push(id); return { summary: "old revision summary", revision: 2, stale: false, status: "partial" }; },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  controller.stop();
  await flush();
  channel.sent.length = 0;
  deps.timers.tick();
  await flush();
  assert.equal(ui.stage, "partial");
  await controller.priceBooks();
  await flush();
  assert.ok(!channel.sent.some((m) => m.type === "response.create"));
});

test("an out-of-order poll reply cannot overwrite a newer known revision", async () => {
  const { deps, ui } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  let releaseOld;
  deps.api.getSweep = (id) => new Promise((resolve) => {
    releaseOld = () => resolve({ id, status: "capturing", frames: [{ id: "frame_old" }], revision: 2 });
  });
  deps.timers.tick();
  await flush();
  deps.api.getSweep = async (id) => ({ id, status: "capturing", frames: [{ id: "a" }, { id: "b" }, { id: "c" }], revision: 5 });
  deps.timers.tick();
  await flush();
  assert.equal(ui.frameCount, 3);
  releaseOld();
  await flush();
  assert.equal(ui.frameCount, 3);
  controller.stop();
});

function withViewDeps(overrides = {}) {
  const ctx = makeDeps(overrides);
  ctx.viewRequests = [];
  ctx.deps.api.captureConfig = overrides.captureConfig || (async () => ({
    vision_enabled: false,
    visual_context: {
      maximum_images_per_sweep: 24,
      minimum_image_interval_ms: 1000,
      maximum_frame_age_s: 3,
      request_timeout_ms: 3000,
      maximum_message_bytes: 49152,
      maximum_targets_per_view: 64,
      allow_upscale: false,
      automatic_retries: 0,
    },
  }));
  ctx.deps.api.voiceView = overrides.voiceView || (async (sweepId, frameId, signal) => {
    ctx.viewRequests.push({ sweepId, frameId, signal });
    return {
      available: true,
      frame_id: frameId,
      frame_ref: "frames/" + frameId + ".jpg",
      timestamp_s: 0,
      image: "data:image/jpeg;base64,AAAA",
      image_width: 640,
      image_height: 360,
      targets: [{ id: "book_1", kind: "book", status: "unidentified", bbox: { x: 1, y: 2, w: 3, h: 4 } }],
      targets_truncated: false,
    };
  });
  ctx.deps.api.getSweep = overrides.getSweep || (async (id) => ({
    id,
    status: "capturing",
    revision: 1,
    frames: [{ id: "frame_a1", frame_ref: "frames/frame_a1.jpg", timestamp_s: 0, width_px: 640, height_px: 360 }],
    books: [{ id: "book_1", status: "unidentified", excluded: false, evidence: [{ frame_id: "frame_a1" }] }],
    items: [],
    review_queue: [],
  }));
  return ctx;
}

function commitEvent(itemId) {
  return { data: JSON.stringify({ type: "input_audio_buffer.committed", item_id: itemId }) };
}

function viewItems(channel) {
  return channel.sent.filter(
    (m) => m.type === "conversation.item.create" &&
      m.item.content && m.item.content[0] &&
      String(m.item.content[0].text).startsWith("APP-CURRENT-VIEW ")
  );
}

function stateItems(channel) {
  return channel.sent.filter((m) => m.item && m.item.content && String(m.item.content[0].text).startsWith("APP-STATE "));
}

test("frame-only polls do not flood the voice conversation with repeated state", async () => {
  let framesSaved = 1;
  let bookCount = 1;
  const { deps } = withViewDeps({ getSweep: async (id) => ({
    id, status: "capturing", revision: framesSaved, country: "AE", currency: "AED",
    frames: Array.from({ length: framesSaved }, (_, i) => ({ id: "frame_" + i, timestamp_s: i })),
    books: Array.from({ length: bookCount }, (_, i) => ({ id: "book_" + i, status: "unidentified" })),
    items: [], review_queue: [],
  }) });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  const appStates = () => stateItems(channel);
  deps.timers.tick();
  await flush();
  assert.equal(appStates().length, 1);
  framesSaved = 2;
  deps.timers.tick();
  await flush();
  assert.equal(appStates().length, 1);
  framesSaved = 3;
  bookCount = 2;
  deps.timers.tick();
  await flush();
  assert.equal(appStates().length, 2);
  controller.stop();
  controller.endCall();
});

test("a committed speech turn gets fresh state after frame-only polls", async () => {
  let framesSaved = 1;
  const { deps } = withViewDeps({ getSweep: async (id) => ({
    id, status: "capturing", revision: framesSaved, country: "AE", currency: "AED",
    frames: Array.from({ length: framesSaved }, (_, i) => ({ id: "frame_" + i, timestamp_s: i })),
    books: [], items: [], review_queue: [],
  }) });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  assert.equal(stateItems(channel).length, 1);
  framesSaved = 2;
  deps.timers.tick();
  await flush();
  assert.equal(stateItems(channel).length, 1);
  deps.preview.currentTime = 1;
  channel.onmessage(commitEvent("item-new-frame"));
  await flush();
  assert.equal(stateItems(channel).length, 2);
  const state = stateItems(channel).at(-1);
  assert.equal(JSON.parse(state.item.content[0].text.slice("APP-STATE ".length)).frames_saved, 2);
  assert.ok(channel.sent.indexOf(state) < channel.sent.indexOf(viewItems(channel).at(-1)));
  controller.stop();
  controller.endCall();
});

test("voice view content hides the saved frame identifier from model input", async () => {
  const { deps } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("item-private"));
  await flush();
  const sent = viewItems(channel);
  assert.equal(sent.length, 1);
  const meta = JSON.parse(sent[0].item.content[0].text.slice("APP-CURRENT-VIEW ".length));
  assert.equal(meta.frame_id, "current");
  assert.ok(!JSON.stringify(sent[0]).includes("frame_a1"));
  controller.stop();
  controller.endCall();
});

test("voice tool calls resolve the current alias to saved frame evidence", async () => {
  const { deps, calls } = withViewDeps({
    analyzeFrame: async (sweepId, frameId) => {
      calls.analyze = (calls.analyze || []).concat(frameId);
      return { frame_id: frameId, source_frame_id: frameId, observation: { books: [] } };
    },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("item-source"));
  await flush();
  channel.onmessage({ data: JSON.stringify({ type: "conversation.item.input_audio_transcription.completed", item_id: "item-source", transcript: "Exclude that book" }) });
  await flush();
  channel.onmessage({ data: JSON.stringify({ type: "response.function_call_arguments.done", name: "inspect_inventory_view", call_id: "inspect_current", arguments: JSON.stringify({ frame_id: "current" }) }) });
  await flush();
  assert.deepEqual(calls.analyze, ["frame_a1"]);
  const inspected = channel.sent.find((m) => m.item && m.item.call_id === "inspect_current");
  assert.equal(JSON.parse(inspected.item.output).frame_id, "current");
  assert.ok(!inspected.item.output.includes("frame_a1"));
  channel.onmessage({ data: JSON.stringify({ type: "response.function_call_arguments.done", name: "apply_correction", call_id: "correct_current", arguments: JSON.stringify({ type: "exclude_item", target_id: "book_1", value: true, source_turn_id: "item-source", source_frame_id: "current" }) }) });
  await flush();
  assert.equal(calls.correct.length, 1);
  assert.equal(calls.correct[0].source_frame_id, "frame_a1");
  controller.stop();
  controller.endCall();
});

test("a committed audio turn sends one view context with the image before response", async () => {
  const { deps, calls } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  const before = channel.sent.filter((m) => m.type === "response.create").length;
  channel.onmessage(commitEvent("item-1"));
  channel.onmessage(commitEvent("item-1"));
  await flush();
  const views = viewItems(channel);
  assert.equal(views.length, 1);
  const meta = JSON.parse(views[0].item.content[0].text.slice("APP-CURRENT-VIEW ".length));
  assert.equal(meta.available, true);
  assert.equal(meta.frame_id, "current");
  assert.equal(meta.user_item_id, "item-1");
  assert.equal(views[0].item.content[1].type, "input_image");
  assert.equal(views[0].item.content[1].image_url, "data:image/jpeg;base64,AAAA");
  const idx = channel.sent.indexOf(views[0]);
  const responses = channel.sent.filter((m) => m.type === "response.create");
  assert.equal(responses.length - before, 1);
  assert.ok(channel.sent.indexOf(responses[responses.length - 1]) > idx);
  controller.stop();
  controller.endCall();
});

test("a stale saved frame sends available=false context without an image", async () => {
  const { deps } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  deps.timers.tick();
  await flush();
  deps.preview.currentTime = 9;
  channel.onmessage(commitEvent("item-2"));
  await flush();
  const views = viewItems(channel);
  assert.equal(views.length, 1);
  const meta = JSON.parse(views[0].item.content[0].text.slice("APP-CURRENT-VIEW ".length));
  assert.equal(meta.available, false);
  assert.equal(views[0].item.content.length, 1);
  controller.stop();
  controller.endCall();
});

test("a newer committed turn supersedes an in-flight view request", async () => {
  const held = [];
  const { deps } = withViewDeps({
    voiceView: (sweepId, frameId, signal) =>
      new Promise((resolve) => {
        held.push({ signal, resolve, frameId });
      }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("item-a"));
  await flush();
  assert.equal(held.length, 1);
  channel.onmessage(commitEvent("item-b"));
  await flush();
  assert.equal(held.length, 2);
  assert.equal(held[0].signal.aborted, true);
  held[1].resolve({
    available: true,
    frame_id: "frame_a1",
    timestamp_s: 0,
    image: "data:image/jpeg;base64,BBBB",
    image_width: 640,
    image_height: 360,
    targets: [],
    targets_truncated: false,
  });
  await flush();
  const views = viewItems(channel);
  assert.equal(views.length, 1);
  assert.equal(views[0].item.content[1].image_url, "data:image/jpeg;base64,BBBB");
  held[0].resolve({
    available: true,
    frame_id: "frame_a1",
    timestamp_s: 0,
    image: "data:image/jpeg;base64,CCCC",
    image_width: 640,
    image_height: 360,
    targets: [],
    targets_truncated: false,
  });
  await flush();
  assert.equal(viewItems(channel).length, 1);
  controller.stop();
  controller.endCall();
});

test("a typed turn sends its text item then the view context and one response", async () => {
  const { deps, calls } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  const responsesBefore = channel.sent.filter((m) => m.type === "response.create").length;
  await controller.sendTurn("exclude that lamp");
  await flush();
  const items = channel.sent.filter((m) => m.type === "conversation.item.create");
  const typed = items.find((m) => m.item.id === calls.addTurn[0].turn_id);
  const view = viewItems(channel)[0];
  assert.ok(typed);
  assert.ok(view);
  assert.ok(channel.sent.indexOf(view) > channel.sent.indexOf(typed));
  const meta = JSON.parse(view.item.content[0].text.slice("APP-CURRENT-VIEW ".length));
  assert.equal(meta.user_item_id, calls.addTurn[0].turn_id);
  const responses = channel.sent.filter((m) => m.type === "response.create");
  assert.equal(responses.length - responsesBefore, 1);
  assert.ok(channel.sent.indexOf(responses[responses.length - 1]) > channel.sent.indexOf(view));
  controller.stop();
  controller.endCall();
});

test("apply_correction forwards source_frame_id and rejects a mismatched view frame", async () => {
  const { deps, calls } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("turn-v1"));
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "conversation.item.input_audio_transcription.completed",
      item_id: "turn-v1",
      transcript: "exclude that book",
    }),
  });
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "apply_correction",
      call_id: "call_v1",
      arguments: JSON.stringify({
        type: "exclude_item",
        target_id: "book_1",
        value: true,
        source_turn_id: "turn-v1",
        source_frame_id: "frame_zz",
      }),
    }),
  });
  await flush();
  assert.equal(calls.correct.length, 0);
  const out = channel.sent.find(
    (m) => m.item && m.item.type === "function_call_output" && m.item.call_id === "call_v1"
  );
  assert.equal(JSON.parse(out.item.output).applied, false);
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "apply_correction",
      call_id: "call_v2",
      arguments: JSON.stringify({
        type: "exclude_item",
        target_id: "book_1",
        value: true,
        source_turn_id: "turn-v1",
        source_frame_id: "current",
      }),
    }),
  });
  await flush();
  assert.equal(calls.correct.length, 1);
  assert.equal(calls.correct[0].source_frame_id, "frame_a1");
  controller.stop();
  controller.endCall();
});

test("inspect_inventory_view analyzes the supplied frame and returns same-frame targets", async () => {
  const { deps, calls } = withViewDeps({
    analyzeFrame: async (sweepId, frameId) => {
      calls.analyze = calls.analyze || [];
      calls.analyze.push(frameId);
      return { frame_id: frameId, source_frame_id: frameId, observation: { books: [] } };
    },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("turn-i1"));
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "inspect_inventory_view",
      call_id: "call_i1",
      arguments: JSON.stringify({ frame_id: "frame_zz" }),
    }),
  });
  await flush();
  assert.equal((calls.analyze || []).length, 0);
  const bad = channel.sent.find(
    (m) => m.item && m.item.type === "function_call_output" && m.item.call_id === "call_i1"
  );
  assert.equal(JSON.parse(bad.item.output).ok, false);
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "inspect_inventory_view",
      call_id: "call_i2",
      arguments: JSON.stringify({ frame_id: "current" }),
    }),
  });
  await flush();
  assert.deepEqual(calls.analyze, ["frame_a1"]);
  const good = channel.sent.find(
    (m) => m.item && m.item.type === "function_call_output" && m.item.call_id === "call_i2"
  );
  const out = JSON.parse(good.item.output);
  assert.equal(out.ok, true);
  assert.equal(out.targets[0].id, "book_1");
  assert.deepEqual(out.targets[0].bbox, { x: 1, y: 2, w: 3, h: 4 });
  assert.ok(!JSON.stringify(out).includes("base64"));
  controller.stop();
  controller.endCall();
});

test("a held response waits for the active response to finish", async () => {
  const { deps } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  deps.timers.tick();
  await flush();
  const activeR = beginResponse(channel);
  channel.onmessage(commitEvent("item-r1"));
  await flush();
  const creates = channel.sent.filter((m) => m.type === "response.create").length;
  assert.equal(viewItems(channel).length, 1);
  finishResponse(channel, activeR);
  await flush();
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, creates + 1);
  controller.stop();
  controller.endCall();
});

test("readSpines explains and skips when no book tracks exist", async () => {
  const { deps, ui, calls } = withViewDeps({
    getSweep: async (id) => ({ id, status: "capturing", frames: [], books: [], items: [], review_queue: [], revision: 1 }),
  });
  ui.readingPending = () => {};
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  deps.timers.tick();
  await flush();
  await controller.readSpines();
  await flush();
  assert.equal(calls.readSpines.length, 0);
  assert.ok(ui.warnings.some((w) => /book|track|analy/i.test(w)));
  controller.endCall();
});

test("endCall during active capture persists a normal end once", async () => {
  const { deps, calls } = makeDeps();
  const controller = createCaptureController(deps);
  deps.setNow(0);
  await controller.start();
  await flush();
  deps.setNow(5000);
  controller.endCall();
  await flush();
  assert.equal(calls.endSweep.length, 1);
  assert.equal(calls.endSweep[0].duration_s, 5);
  assert.equal(calls.endSweep[0].keepalive, true);
  assert.equal(calls.interruptSweep.length, 0);
});

test("stop then endCall keeps a single saved end", async () => {
  const { deps, calls } = makeDeps();
  const controller = createCaptureController(deps);
  deps.setNow(0);
  await controller.start();
  await flush();
  deps.setNow(2000);
  controller.stop();
  await flush();
  controller.endCall();
  await flush();
  assert.equal(calls.endSweep.length, 1);
  assert.equal(calls.endSweep[0].keepalive, true);
  assert.equal(calls.interruptSweep.length, 0);
});

test("stop then immediate pagehide keeps the held normal end", async () => {
  let releaseEnd;
  const { deps, calls } = makeDeps({
    endSweep: (id, duration_s, opts) => {
      calls.endSweep.push({ id, duration_s, keepalive: !!(opts && opts.keepalive) });
      return new Promise((resolve) => { releaseEnd = () => resolve({ id, status: "partial", frames: [] }); });
    },
  });
  const controller = createCaptureController(deps);
  deps.setNow(0);
  await controller.start();
  await flush();
  deps.setNow(3000);
  controller.stop();
  controller.pagehide();
  await flush();
  assert.equal(calls.endSweep.length, 1);
  assert.equal(calls.endSweep[0].duration_s, 3);
  assert.equal(calls.endSweep[0].keepalive, true);
  assert.equal(calls.interruptSweep.length, 0);
  releaseEnd();
  await flush();
});

test("endCall surfaces a failed normal end instead of claiming it saved", async () => {
  const { deps, ui, calls } = makeDeps({
    endSweep: async (id, duration_s, opts) => {
      calls.endSweep.push({ id, duration_s, keepalive: !!(opts && opts.keepalive) });
      throw new Error("server gone");
    },
  });
  const controller = createCaptureController(deps);
  deps.setNow(0);
  await controller.start();
  await flush();
  deps.setNow(4000);
  controller.endCall();
  await flush();
  assert.equal(calls.endSweep.length, 1);
  assert.ok(ui.warnings.some((w) => /end/i.test(w)));
});

test("pagehide during active capture persists a best-effort end, not interrupt", async () => {
  const { deps, calls } = makeDeps();
  const controller = createCaptureController(deps);
  deps.setNow(0);
  await controller.start();
  await flush();
  deps.setNow(3000);
  controller.pagehide();
  await flush();
  assert.equal(calls.endSweep.length, 1);
  assert.equal(calls.endSweep[0].duration_s, 3);
  assert.equal(calls.endSweep[0].keepalive, true);
  assert.equal(calls.interruptSweep.length, 0);
});

test("pagehide before capture started still interrupts", async () => {
  let releasePlay;
  const { deps, calls, preview } = makeDeps();
  preview.play = () => new Promise((resolve) => { releasePlay = resolve; });
  const controller = createCaptureController(deps);
  const started = controller.start();
  await flush();
  controller.pagehide();
  await flush();
  assert.deepEqual(calls.interruptSweep, ["s1"]);
  assert.equal(calls.endSweep.length, 0);
  releasePlay();
  await started;
});

test("a blank provider transcript is not persisted", async () => {
  const { deps, calls } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  channel.onmessage({
    data: JSON.stringify({
      type: "conversation.item.input_audio_transcription.completed",
      item_id: "blank-1",
      transcript: "   ",
    }),
  });
  channel.onmessage({
    data: JSON.stringify({
      type: "conversation.item.input_audio_transcription.completed",
      item_id: "good-1",
      transcript: "real words",
    }),
  });
  await flush();
  assert.equal(calls.addTurn.length, 1);
  assert.equal(calls.addTurn[0].turn_id, "good-1");
  controller.stop();
  controller.endCall();
});

test("a held view resolving after the frame went stale sends unavailable context", async () => {
  const held = [];
  const { deps } = withViewDeps({
    voiceView: (sweepId, frameId, signal) =>
      new Promise((resolve) => { held.push({ signal, resolve }); }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("item-stale"));
  await flush();
  assert.equal(held.length, 1);
  deps.preview.currentTime = 9;
  held[0].resolve({
    available: true, frame_id: "frame_a1", timestamp_s: 0,
    image: "data:image/jpeg;base64,AAAA", image_width: 640, image_height: 360,
    targets: [], targets_truncated: false,
  });
  await flush();
  const views = viewItems(channel);
  assert.equal(views.length, 1);
  const meta = JSON.parse(views[0].item.content[0].text.slice("APP-CURRENT-VIEW ".length));
  assert.equal(meta.available, false);
  assert.equal(views[0].item.content.length, 1);
  assert.ok(!channel.sent.some((m) => m.item && m.item.content && m.item.content.some((c) => c.type === "input_image")));
  controller.stop();
  controller.endCall();
});

test("Stop Sweep invalidates a pending view request without sending a stale image", async () => {
  const held = [];
  const { deps } = withViewDeps({
    voiceView: (sweepId, frameId, signal) =>
      new Promise((resolve) => { held.push({ signal, resolve }); }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("item-stop"));
  await flush();
  assert.equal(held.length, 1);
  controller.stop();
  held[0].resolve({
    available: true, frame_id: "frame_a1", timestamp_s: 0,
    image: "data:image/jpeg;base64,AAAA", image_width: 640, image_height: 360,
    targets: [], targets_truncated: false,
  });
  await flush();
  assert.equal(viewItems(channel).length, 0);
  assert.ok(held[0].signal.aborted);
  controller.endCall();
});

test("speech_started invalidates a held view and clears the current frame", async () => {
  const held = [];
  const { deps, calls } = withViewDeps({
    voiceView: (sweepId, frameId, signal) =>
      new Promise((resolve) => { held.push({ signal, resolve }); }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("item-a"));
  await flush();
  assert.equal(held.length, 1);
  channel.onmessage({ data: JSON.stringify({ type: "input_audio_buffer.speech_started" }) });
  held[0].resolve({
    available: true, frame_id: "frame_a1", timestamp_s: 0,
    image: "data:image/jpeg;base64,AAAA", image_width: 640, image_height: 360,
    targets: [{ id: "book_1", kind: "book" }], targets_truncated: false,
  });
  await flush();
  assert.equal(viewItems(channel).length, 0);
  assert.ok(held[0].signal.aborted);
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "apply_correction",
      call_id: "call_stale_view",
      arguments: JSON.stringify({
        type: "exclude_item",
        target_id: "book_1",
        value: true,
        source_turn_id: "item-a",
        source_frame_id: "current",
      }),
    }),
  });
  await flush();
  deps.setNow(6000);
  deps.timers.fireTimeouts();
  await flush();
  assert.equal(calls.correct.length, 0);
  deps.setNow(7000);
  channel.onmessage(commitEvent("item-b"));
  await flush();
  assert.equal(held.length, 2);
  held[1].resolve({
    available: true, frame_id: "frame_a1", timestamp_s: 0,
    image: "data:image/jpeg;base64,BBBB", image_width: 640, image_height: 360,
    targets: [{ id: "book_1", kind: "book" }], targets_truncated: false,
  });
  await flush();
  assert.equal(viewItems(channel).length, 1);
  controller.stop();
  controller.endCall();
});

test("a pending response during new speech is dropped, not replayed after response.done", async () => {
  const { deps } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  beginResponse(channel);
  channel.onmessage(commitEvent("item-p1"));
  await flush();
  assert.equal(viewItems(channel).length, 1);
  const before = channel.sent.filter((m) => m.type === "response.create").length;
  channel.onmessage({ data: JSON.stringify({ type: "input_audio_buffer.speech_started" }) });
  completeResponse(channel);
  await flush();
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, before);
  deps.setNow(3000);
  channel.onmessage(commitEvent("item-p2"));
  await flush();
  const views = viewItems(channel);
  assert.equal(views.length, 2);
  const creates = channel.sent.filter((m) => m.type === "response.create");
  assert.equal(creates.length, before + 1);
  assert.ok(channel.sent.indexOf(creates[creates.length - 1]) > channel.sent.indexOf(views[1]));
  controller.stop();
  controller.endCall();
});

test("apply_correction without a source_frame_id gets clarification without a backend call", async () => {
  const { deps, calls } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("turn-nf"));
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "conversation.item.input_audio_transcription.completed",
      item_id: "turn-nf",
      transcript: "exclude that book",
    }),
  });
  await flush();
  for (const [callId, frameArg] of [["call_nf1", undefined], ["call_nf2", null]]) {
    const args = {
      type: "exclude_item", target_id: "book_1", value: true,
      source_turn_id: "turn-nf",
    };
    if (frameArg !== undefined) args.source_frame_id = frameArg;
    channel.onmessage({
      data: JSON.stringify({
        type: "response.function_call_arguments.done",
        name: "apply_correction",
        call_id: callId,
        arguments: JSON.stringify(args),
      }),
    });
  }
  await flush();
  deps.timers.fireTimeouts();
  await flush();
  assert.equal(calls.correct.length, 0);
  for (const callId of ["call_nf1", "call_nf2"]) {
    const out = channel.sent.find(
      (m) => m.item && m.item.type === "function_call_output" && m.item.call_id === callId
    );
    assert.ok(out, "missing tool output for " + callId);
    assert.equal(JSON.parse(out.item.output).applied, false);
  }
  controller.stop();
  controller.endCall();
});

test("apply_correction to a target outside the current view is not applied", async () => {
  const { deps, calls } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("turn-ot"));
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "conversation.item.input_audio_transcription.completed",
      item_id: "turn-ot",
      transcript: "exclude that thing",
    }),
  });
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "apply_correction",
      call_id: "call_ot",
      arguments: JSON.stringify({
        type: "exclude_item",
        target_id: "book_not_in_view",
        value: true,
        source_turn_id: "turn-ot",
        source_frame_id: "current",
      }),
    }),
  });
  await flush();
  assert.equal(calls.correct.length, 0);
  const out = channel.sent.find(
    (m) => m.item && m.item.type === "function_call_output" && m.item.call_id === "call_ot"
  );
  assert.equal(JSON.parse(out.item.output).applied, false);
  controller.stop();
  controller.endCall();
});

test("a newer utterance during the source-transcript wait drops the correction", async () => {
  const { deps, calls } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("turn-w1"));
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "apply_correction",
      call_id: "call_w1",
      arguments: JSON.stringify({
        type: "exclude_item",
        target_id: "book_1",
        value: true,
        source_turn_id: "turn-w1",
        source_frame_id: "current",
      }),
    }),
  });
  await flush();
  channel.onmessage({ data: JSON.stringify({ type: "input_audio_buffer.speech_started" }) });
  channel.onmessage({
    data: JSON.stringify({
      type: "conversation.item.input_audio_transcription.completed",
      item_id: "turn-w1",
      transcript: "exclude that book",
    }),
  });
  await flush();
  deps.setNow(6000);
  deps.timers.fireTimeouts();
  await flush();
  assert.equal(calls.correct.length, 0);
  assert.ok(!channel.sent.some(
    (m) => m.item && m.item.type === "function_call_output" && m.item.call_id === "call_w1"
  ));
  controller.stop();
  controller.endCall();
});

test("inspect_inventory_view returns same-frame targets from a refreshed view", async () => {
  const { deps, calls } = withViewDeps({
    analyzeFrame: async (sweepId, frameId) => {
      calls.analyze = calls.analyze || [];
      calls.analyze.push(frameId);
      return { frame_id: frameId, source_frame_id: frameId, observation: { books: [] } };
    },
    voiceView: async (sweepId, frameId, signal) => ({
      available: true,
      frame_id: frameId,
      timestamp_s: 0,
      image: "data:image/jpeg;base64,QUJD",
      image_width: 640,
      image_height: 360,
      targets: [
        { id: "book_1", kind: "book", status: "unidentified", bbox: { x: 1, y: 2, w: 3, h: 4 }, observed_fragments: [{ field: "observed_title", text: "Alpha" }] },
        { id: "book_2", kind: "book", status: "unidentified", bbox: { x: 50, y: 2, w: 10, h: 4 }, observed_fragments: [{ field: "observed_title", text: "Beta" }] },
      ],
      targets_truncated: false,
    }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("turn-i9"));
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "inspect_inventory_view",
      call_id: "call_i9",
      arguments: JSON.stringify({ frame_id: "current" }),
    }),
  });
  await flush();
  assert.deepEqual(calls.analyze, ["frame_a1"]);
  const out = channel.sent.find(
    (m) => m.item && m.item.type === "function_call_output" && m.item.call_id === "call_i9"
  );
  const parsed = JSON.parse(out.item.output);
  assert.equal(parsed.ok, true);
  assert.equal(parsed.targets.length, 2);
  assert.equal(parsed.targets[0].id, "book_1");
  assert.deepEqual(parsed.targets[0].bbox, { x: 1, y: 2, w: 3, h: 4 });
  assert.equal(parsed.targets[1].id, "book_2");
  assert.deepEqual(parsed.targets[1].bbox, { x: 50, y: 2, w: 10, h: 4 });
  assert.ok(!("image" in parsed));
  assert.ok(!JSON.stringify(parsed).includes("base64"));
  controller.stop();
  controller.endCall();
});

test("a superseded inspection sends no late tool response", async () => {
  let releaseAnalyze;
  const { deps } = withViewDeps({
    analyzeFrame: () => new Promise((resolve) => { releaseAnalyze = resolve; }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("turn-sup"));
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "inspect_inventory_view",
      call_id: "call_sup",
      arguments: JSON.stringify({ frame_id: "current" }),
    }),
  });
  await flush();
  channel.onmessage({ data: JSON.stringify({ type: "input_audio_buffer.speech_started" }) });
  releaseAnalyze({ frame_id: "frame_a1", source_frame_id: "frame_a1", observation: { books: [] } });
  await flush();
  assert.ok(!channel.sent.some(
    (m) => m.item && m.item.type === "function_call_output" && m.item.call_id === "call_sup"
  ));
  controller.stop();
  controller.endCall();
});

test("a view payload exceeding the transport cap falls back to unavailable", async () => {
  const { deps, ui } = withViewDeps({
    voiceView: async (sweepId, frameId, signal) => ({
      available: true,
      frame_id: frameId,
      timestamp_s: 0,
      image: "data:image/jpeg;base64," + "É".repeat(150),
      image_width: 640,
      image_height: 360,
      targets: [{ id: "bücher_1", kind: "book", status: "unidentified" }],
      targets_truncated: false,
    }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  deps.lastPeer().sctp = { maxMessageSize: 500 };
  channel.onmessage(commitEvent("item-cap"));
  await flush();
  const views = viewItems(channel);
  assert.equal(views.length, 1);
  const meta = JSON.parse(views[0].item.content[0].text.slice("APP-CURRENT-VIEW ".length));
  assert.equal(meta.available, false);
  assert.equal(meta.reason, "message_cap");
  assert.equal(views[0].item.content.length, 1);
  assert.ok(ui.logLines.some((l) => l.event === "view_unavailable" && l.reason === "message_cap"));
  assert.ok(!JSON.stringify(ui.logLines).includes("data:image"));
  controller.stop();
  controller.endCall();
});

test("readSpines is blocked while a frame analysis is still pending", async () => {
  let readinessFetches = 0;
  const { deps, ui, calls } = makeDeps({
    getSweep: async (id) => {
      readinessFetches += 1;
      return {
        id, status: "capturing", revision: 1,
        frames: [{ id: "frame_a1", frame_ref: "frames/frame_a1.jpg", timestamp_s: 0, width_px: 640, height_px: 360 }],
        books: [{ id: "book_1", excluded: false }], items: [], review_queue: [],
      };
    },
    analyzeFrame: () => new Promise(() => {}),
  });
  ui.readingPending = (p) => { ui._pending = p; };
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  deps.timers.tick();
  await flush();
  controller.analyzeLatest();
  await flush();
  const fetchesBefore = readinessFetches;
  const read = controller.readSpines();
  await flush();
  assert.equal(calls.readSpines.length, 0);
  assert.equal(readinessFetches, fetchesBefore);
  assert.ok(ui.warnings.some((w) => /analy/i.test(w)));
  await read;
  controller.endCall();
});

test("readSpines re-reads fresh state so newly completed analysis becomes readable", async () => {
  let books = [];
  const { deps, ui, calls } = makeDeps({
    getSweep: async (id) => ({
      id, status: "capturing", revision: 2,
      frames: [{ id: "frame_a1", frame_ref: "frames/frame_a1.jpg", timestamp_s: 0, width_px: 640, height_px: 360 }],
      books, items: [], review_queue: [],
    }),
  });
  ui.readingPending = (p) => { ui._pending = p; };
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  deps.timers.tick();
  await flush();
  await controller.readSpines();
  await flush();
  assert.equal(calls.readSpines.length, 0);
  assert.ok(ui.warnings.some((w) => /book|track|analy/i.test(w)));
  books = [{ id: "book_1", excluded: false }];
  await controller.readSpines();
  await flush();
  assert.equal(calls.readSpines.length, 1);
  controller.endCall();
});

test("a skipped readSpines result is explained, not logged as a successful read", async () => {
  const { deps, ui, calls } = makeDeps({
    getSweep: async (id) => ({
      id, status: "capturing", revision: 1,
      frames: [],
      books: [{ id: "book_1", excluded: false }], items: [], review_queue: [],
    }),
    readSpines: async (id, refIds) => {
      calls.readSpines.push({ id, refIds });
      return { readings: [], ref_ids: [], batches: 0, skipped: true, reason: "no eligible book crops" };
    },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  await controller.readSpines();
  await flush();
  assert.equal(calls.readSpines.length, 1);
  assert.ok(ui.warnings.some((w) => /skipped|crops/i.test(w)));
  assert.ok(!ui.logLines.some((l) => l.event === "read_spines"));
  controller.endCall();
});

test("a transport send failure drops the view without throwing", async () => {
  const { deps, ui } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.send = () => { throw new Error("sctp gone"); };
  channel.onmessage(commitEvent("item-x"));
  await flush();
  assert.equal(viewItems(channel).length, 0);
  assert.ok(ui.logLines.some((l) => l.event === "view_unavailable" && l.reason === "transport"));
  controller.stop();
  controller.endCall();
});

test("httpError surfaces bounded sanitized validation detail without input values", async () => {
  const err = httpError(422, {
    detail: [
      {
        loc: ["body", "text"],
        msg: "String should have at least 1 character",
        type: "string_too_short",
        input: "secret transcript text",
      },
    ],
  });
  assert.equal(err.status, 422);
  assert.match(err.message, /422/);
  assert.match(err.message, /string_too_short|1 character/);
  assert.ok(!err.message.includes("secret transcript text"));
  const plain = httpError(503, { detail: "not configured" });
  assert.equal(plain.message, "http 503");
});

test("a correction naming an image invalidated by available=false is not applied", async () => {
  const { deps, calls } = withViewDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  deps.preview.currentTime = 9;
  channel.onmessage(commitEvent("turn-sv"));
  await flush();
  const views = viewItems(channel);
  assert.equal(views.length, 1);
  const meta = JSON.parse(views[0].item.content[0].text.slice("APP-CURRENT-VIEW ".length));
  assert.equal(meta.available, false);
  channel.onmessage({
    data: JSON.stringify({
      type: "conversation.item.input_audio_transcription.completed",
      item_id: "turn-sv",
      transcript: "exclude that book",
    }),
  });
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "apply_correction",
      call_id: "call_sv",
      arguments: JSON.stringify({
        type: "exclude_item",
        target_id: "book_1",
        value: true,
        source_turn_id: "turn-sv",
        source_frame_id: "current",
      }),
    }),
  });
  await flush();
  assert.equal(calls.correct.length, 0);
  const out = channel.sent.find(
    (m) => m.item && m.item.type === "function_call_output" && m.item.call_id === "call_sv"
  );
  assert.equal(JSON.parse(out.item.output).applied, false);
  assert.equal(JSON.parse(out.item.output).reason, "clarification_required");
  controller.stop();
  controller.endCall();
});

test("an old run's failed end cannot repaint the UI after a newer run ended", async () => {
  let rejectOld;
  const { deps, ui, calls } = makeDeps({
    endSweep: (id, duration_s, opts) => {
      calls.endSweep.push({ id, duration_s, keepalive: !!(opts && opts.keepalive) });
      if (calls.endSweep.length === 1) {
        return new Promise((resolve, reject) => { rejectOld = reject; });
      }
      return Promise.resolve({ id, status: "partial", frames: [] });
    },
  });
  const controller = createCaptureController(deps);
  deps.setNow(0);
  await controller.start();
  await flush();
  deps.setNow(2000);
  controller.endCall();
  await controller.start();
  await flush();
  controller.endCall();
  await flush();
  const savedStatus = ui.statusText;
  const savedWarnings = ui.warnings.length;
  rejectOld(new Error("server gone"));
  await flush();
  assert.equal(ui.statusText, savedStatus);
  assert.equal(ui.warnings.length, savedWarnings);
});

test("packet summary is sent only after processing settles, exactly once", async () => {
  let postwork = "running";
  const { deps, ui, calls } = makeDeps({
    getSweep: async (id) => ({
      id,
      status: postwork === "completed" ? "partial" : "processing",
      revision: 5,
      postwork_status: postwork,
      postwork_stage: postwork === "completed" ? null : "reading",
      frames: [],
      books: [],
      packet_ref: postwork === "completed" ? "claim_packet.json" : null,
    }),
    getPacket: async (id) => {
      calls.getPacket.push(id);
      return {
        summary: "partial sum",
        revision: 5,
        stale: false,
        status: "partial",
        packet_ref: "claim_packet.json",
        report_ref: "report.html",
      };
    },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  assert.equal(ui.stage, "processing:reading");
  assert.equal(calls.packet.length, 0);
  assert.equal(calls.getPacket.length, 0);
  controller.stop();
  await flush();
  assert.equal(calls.packet.length, 0);
  assert.equal(calls.getPacket.length, 0);
  postwork = "completed";
  deps.timers.tick();
  await flush();
  assert.equal(calls.packet.length, 0);
  assert.equal(calls.getPacket.length, 1);
  const summaries = channel.sent.filter(
    (m) =>
      m.type === "conversation.item.create" &&
      m.item.content &&
      m.item.content[0] &&
      String(m.item.content[0].text).startsWith("APP-VALIDATED-SUMMARY ")
  );
  assert.equal(summaries.length, 1);
  deps.timers.tick();
  await flush();
  assert.equal(calls.getPacket.length, 1);
  assert.equal(
    channel.sent.filter(
      (m) =>
        m.type === "conversation.item.create" &&
        m.item.content &&
        m.item.content[0] &&
        String(m.item.content[0].text).startsWith("APP-VALIDATED-SUMMARY ")
    ).length,
    1
  );
  controller.endCall();
});

test("inspect_inventory_view times out a hung refreshed view", async () => {
  const viewCalls = [];
  const { deps } = withViewDeps({
    analyzeFrame: async (sweepId, frameId) => ({ frame_id: frameId }),
    voiceView: (sweepId, frameId, signal) => {
      viewCalls.push({ frameId, signal });
      if (viewCalls.length === 1) {
        return Promise.resolve({
          available: true,
          frame_id: frameId,
          timestamp_s: 0,
          image: "data:image/jpeg;base64,QUJD",
          image_width: 640,
          image_height: 360,
          targets: [],
          targets_truncated: false,
        });
      }
      return new Promise(() => {});
    },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("turn-to"));
  await flush();
  assert.equal(viewCalls.length, 1);
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "inspect_inventory_view",
      call_id: "call_to",
      arguments: JSON.stringify({ frame_id: "current" }),
    }),
  });
  await flush();
  assert.equal(viewCalls.length, 2);
  deps.timers.fireTimeouts();
  await flush();
  const out = channel.sent.find(
    (m) => m.item && m.item.type === "function_call_output" && m.item.call_id === "call_to"
  );
  const parsed = JSON.parse(out.item.output);
  assert.equal(parsed.ok, false);
  assert.match(parsed.error, /timeout/i);
  controller.stop();
  controller.endCall();
});

test("a new utterance aborts a held inspection view fetch", async () => {
  const viewCalls = [];
  let releaseInspect;
  const { deps } = withViewDeps({
    analyzeFrame: async (sweepId, frameId) => ({ frame_id: frameId }),
    voiceView: (sweepId, frameId, signal) => {
      viewCalls.push({ frameId, signal });
      if (viewCalls.length === 1) {
        return Promise.resolve({
          available: true,
          frame_id: frameId,
          timestamp_s: 0,
          image: "data:image/jpeg;base64,QUJD",
          image_width: 640,
          image_height: 360,
          targets: [],
          targets_truncated: false,
        });
      }
      return new Promise((resolve) => { releaseInspect = resolve; });
    },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  deps.timers.tick();
  await flush();
  channel.onmessage(commitEvent("turn-ab"));
  await flush();
  channel.onmessage({
    data: JSON.stringify({
      type: "response.function_call_arguments.done",
      name: "inspect_inventory_view",
      call_id: "call_ab",
      arguments: JSON.stringify({ frame_id: "current" }),
    }),
  });
  await flush();
  assert.equal(viewCalls.length, 2);
  channel.onmessage({ data: JSON.stringify({ type: "input_audio_buffer.speech_started" }) });
  assert.equal(viewCalls[1].signal.aborted, true);
  releaseInspect({
    available: true,
    frame_id: "frame_a1",
    timestamp_s: 0,
    image_width: 640,
    image_height: 360,
    targets: [],
    targets_truncated: false,
  });
  await flush();
  assert.ok(
    !channel.sent.some(
      (m) => m.item && m.item.type === "function_call_output" && m.item.call_id === "call_ab"
    )
  );
  controller.stop();
  controller.endCall();
});

test("manual publish during processing shows the artifact but speaks no summary", async () => {
  const { deps, ui, calls } = makeDeps({
    endSweep: async (id) => ({ id, status: "processing", frames: [], postwork_status: "running", revision: 3 }),
    packet: async (id) => {
      calls.packet.push(id);
      return { summary: "mid-processing", revision: 4, stale: false, status: "partial", packet_ref: "claim_packet.json" };
    },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  controller.stop();
  await flush();
  await controller.publishPacket();
  assert.equal(calls.packet.length, 1);
  assert.equal(ui.packet.summary, "mid-processing");
  assert.ok(!channel.sent.some(
    (m) => m.type === "conversation.item.create" &&
      m.item.content && m.item.content[0] &&
      String(m.item.content[0].text).startsWith("APP-VALIDATED-SUMMARY")
  ));
  controller.endCall();
});

test("a held settled packet read resolves to exactly one summary", async () => {
  let postwork = "running";
  let release;
  const { deps, calls } = makeDeps({
    getSweep: async (id) => ({
      id,
      status: postwork === "completed" ? "partial" : "processing",
      revision: 5,
      postwork_status: postwork,
      frames: [],
      books: [],
    }),
    getPacket: async (id) => {
      calls.getPacket.push(id);
      return new Promise((resolve) => { release = resolve; });
    },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  controller.stop();
  await flush();
  postwork = "completed";
  deps.timers.tick();
  await flush();
  assert.equal(calls.getPacket.length, 1);
  deps.timers.tick();
  await flush();
  assert.equal(calls.getPacket.length, 1);
  release({ summary: "held final", revision: 5, stale: false, status: "partial" });
  await flush();
  const summaries = channel.sent.filter(
    (m) =>
      m.type === "conversation.item.create" &&
      m.item.content && m.item.content[0] &&
      String(m.item.content[0].text).startsWith("APP-VALIDATED-SUMMARY ")
  );
  assert.equal(summaries.length, 1);
  controller.endCall();
});

test("a transcript-bump revision with an unchanged summary sends no second announcement", async () => {
  let postwork = "running";
  let revision = 5;
  let packetRev = 5;
  const { deps, calls } = makeDeps({
    getSweep: async (id) => ({
      id,
      status: postwork === "completed" ? "partial" : "processing",
      revision,
      postwork_status: postwork,
      frames: [],
      books: [],
    }),
    getPacket: async (id) => {
      calls.getPacket.push(id);
      return { summary: "same final", revision: packetRev, stale: false, status: "partial" };
    },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  controller.stop();
  await flush();
  postwork = "completed";
  deps.timers.tick();
  await flush();
  assert.equal(calls.getPacket.length, 1);
  const count = () => channel.sent.filter(
    (m) =>
      m.type === "conversation.item.create" &&
      m.item.content && m.item.content[0] &&
      String(m.item.content[0].text).startsWith("APP-VALIDATED-SUMMARY ")
  ).length;
  assert.equal(count(), 1);
  revision = 6;
  packetRev = 6;
  deps.timers.tick();
  await flush();
  assert.equal(calls.getPacket.length, 2);
  assert.equal(count(), 1);
  controller.endCall();
});

test("a transient settled packet read failure retries on the next poll", async () => {
  let postwork = "running";
  let fail = true;
  const { deps, ui, calls } = makeDeps({
    getSweep: async (id) => ({
      id,
      status: postwork === "completed" ? "partial" : "processing",
      revision: 5,
      postwork_status: postwork,
      frames: [],
      books: [],
    }),
    getPacket: async (id) => {
      calls.getPacket.push(id);
      if (fail) throw new Error("temporary read failure");
      return { summary: "recovered", revision: 5, stale: false, status: "partial" };
    },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = deps.lastPeer().channel;
  channel.open();
  completeResponse(channel);
  controller.stop();
  await flush();
  postwork = "completed";
  deps.timers.tick();
  await flush();
  assert.equal(calls.getPacket.length, 1);
  assert.ok(ui.logLines.some((l) => l.event === "packet_read_failed"));
  fail = false;
  deps.timers.tick();
  await flush();
  assert.equal(calls.getPacket.length, 2);
  assert.ok(channel.sent.some(
    (m) =>
      m.type === "conversation.item.create" &&
      m.item.content && m.item.content[0] &&
      String(m.item.content[0].text).startsWith("APP-VALIDATED-SUMMARY ")
  ));
  controller.endCall();
});

test("overlapping restore settle polls keep revision order and show the real packet", async () => {
  const { deps, ui, storage, calls } = makeDeps();
  storage.set("sweepId", "s9");
  const sweeps = [];
  deps.api.getSweep = (id) => new Promise((resolve) => { sweeps.push(resolve); });
  deps.api.getPacket = async (id) => {
    calls.getPacket.push(id);
    return { summary: "s", revision: 8, stale: false, status: "partial", packet_ref: "claim_packet.json", report_ref: "report.html" };
  };
  const controller = createCaptureController(deps);
  const restore = controller.restoreSaved();
  sweeps[0]({ id: "s9", status: "processing", revision: 5, postwork_status: "running", postwork_stage: "reading", frames: [], books: [], packet_ref: null });
  await restore;
  await flush();
  deps.timers.tick();
  deps.timers.tick();
  assert.equal(sweeps.length, 3);
  sweeps[2]({ id: "s9", status: "partial", revision: 8, postwork_status: "completed", frames: [], books: [], packet_ref: "claim_packet.json" });
  await flush();
  assert.equal(ui.stage, "partial");
  assert.equal(ui.packet.revision, 8);
  assert.equal(ui.packet.stale, false);
  assert.equal(calls.getPacket.length, 1);
  sweeps[1]({ id: "s9", status: "processing", revision: 6, postwork_status: "running", postwork_stage: "pricing", frames: [], books: [] });
  await flush();
  assert.equal(ui.stage, "partial");
  assert.equal(ui.packet.revision, 8);
});

test("restore shows an existing stale packet with its actual revision", async () => {
  const { deps, ui, storage } = makeDeps({
    getSweep: async (id) => ({
      id,
      status: "partial",
      revision: 8,
      postwork_status: "completed",
      frames: [],
      books: [],
      packet_ref: "claim_packet.json",
      report_ref: "report.html",
    }),
    getPacket: async () => ({
      summary: "older",
      revision: 5,
      stale: true,
      status: "partial",
      packet_ref: "claim_packet.json",
      report_ref: "report.html",
    }),
  });
  storage.set("sweepId", "s7");
  const controller = createCaptureController(deps);
  await controller.restoreSaved();
  await flush();
  assert.equal(ui.packet.revision, 5);
  assert.equal(ui.packet.stale, true);
});

test("a late restore packet read cannot replace a newer displayed packet", async () => {
  const { deps, ui, storage, calls } = makeDeps();
  storage.set("sweepId", "s9");
  let postwork = "running";
  const packetReads = [];
  deps.api.getSweep = async (id) => ({
    id,
    status: postwork === "running" ? "processing" : "partial",
    revision: postwork === "running" ? 5 : 8,
    postwork_status: postwork,
    postwork_stage: postwork === "running" ? "reading" : null,
    frames: [],
    books: [],
    packet_ref: "claim_packet.json",
  });
  deps.api.getPacket = (id) => {
    calls.getPacket.push(id);
    return new Promise((resolve) => { packetReads.push(resolve); });
  };
  const controller = createCaptureController(deps);
  await controller.restoreSaved();
  await flush();
  assert.equal(ui.stage, "processing:reading");
  assert.equal(packetReads.length, 1);
  postwork = "completed";
  deps.timers.tick();
  await flush();
  assert.equal(packetReads.length, 2);
  packetReads[1]({ summary: "newer", revision: 8, stale: false, status: "partial", packet_ref: "claim_packet.json", report_ref: "report.html" });
  await flush();
  assert.equal(ui.packet.revision, 8);
  packetReads[0]({ summary: "older", revision: 5, stale: false, status: "partial", packet_ref: "claim_packet.json", report_ref: "report.html" });
  await flush();
  assert.equal(ui.packet.revision, 8);
  assert.equal(ui.packet.summary, "newer");
});

test("out-of-order settled packet reads keep the newest revision displayed", async () => {
  let postwork = "running";
  let revision = 7;
  const reads = [];
  const { deps, ui, calls } = makeDeps({
    getSweep: async (id) => ({
      id,
      status: postwork === "completed" ? "partial" : "processing",
      revision,
      postwork_status: postwork,
      frames: [],
      books: [],
    }),
    getPacket: async (id) => {
      calls.getPacket.push(id);
      return new Promise((resolve) => { reads.push(resolve); });
    },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  controller.stop();
  await flush();
  postwork = "completed";
  deps.timers.tick();
  await flush();
  assert.equal(reads.length, 1);
  revision = 8;
  deps.timers.tick();
  await flush();
  assert.equal(reads.length, 2);
  reads[1]({ summary: "rev8", revision: 8, stale: false, status: "partial" });
  await flush();
  assert.equal(ui.packet.revision, 8);
  reads[0]({ summary: "rev7", revision: 7, stale: false, status: "partial" });
  await flush();
  assert.equal(ui.packet.revision, 8);
  assert.equal(ui.packet.summary, "rev8");
  controller.endCall();
});

function guidanceDeps(overrides = {}) {
  const ctx = makeDeps(overrides);
  ctx.deps.api.captureConfig = overrides.captureConfig || (async () => ({
    vision_enabled: false,
    visual_context: {
      maximum_images_per_sweep: 24,
      minimum_image_interval_ms: 1000,
      maximum_frame_age_s: 3,
      request_timeout_ms: 3000,
      maximum_message_bytes: 49152,
      maximum_targets_per_view: 64,
      allow_upscale: false,
      automatic_retries: 0,
    },
    guidance: overrides.guidance || {
      enabled: true,
      maximum_responses_per_sweep: 6,
      minimum_response_interval_ms: 10000,
      maximum_event_age_ms: 3000,
    },
  }));
  ctx.deps.api.getSweep = overrides.getSweep || (async (id) => ({
    id,
    status: "capturing",
    revision: 1,
    frames: [{ id: "frame_g1", frame_ref: "frames/frame_g1.jpg", timestamp_s: 0, width_px: 640, height_px: 360 }],
    books: [],
    items: [],
    review_queue: [],
  }));
  return ctx;
}

function serverEvent(channel, ev) {
  channel.onmessage({ data: JSON.stringify(ev) });
}

async function greetingDone(ctx) {
  const channel = ctx.deps.lastPeer().channel;
  channel.open();
  const create = channel.sent.find((m) => m.type === "response.create");
  const reqId = create.response.metadata.app_request;
  serverEvent(channel, { type: "response.created", response: { id: "r_greet", metadata: { app_request: reqId, purpose: "user" } } });
  serverEvent(channel, { type: "response.done", response: { id: "r_greet" } });
  await flush();
  return channel;
}

test("peer failure marks voice unavailable without losing capture or calling interrupt", async () => {
  const { deps, ui, calls, samplers } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const peer = deps.lastPeer();
  peer.channel.open();
  peer.connectionState = "failed";
  peer.onconnectionstatechange();
  await flush();
  assert.match(ui.statusText, /PARTIAL MODE: camera only — voice unavailable/i);
  assert.equal(samplers[0].stopped, false);
  assert.equal(calls.interruptSweep.length, 0);
  controller.stop();
  await flush();
  assert.equal(calls.endSweep.length, 1);
});

test("disconnected and closed peers mark voice unavailable; reconnect restores only the owned peer", async () => {
  for (const state of ["disconnected", "closed"]) {
    const { deps, ui, calls } = makeDeps();
    const controller = createCaptureController(deps);
    await controller.start();
    await flush();
    const peer = deps.lastPeer();
    peer.channel.open();
    peer.connectionState = state;
    peer.onconnectionstatechange();
    assert.match(ui.statusText, /PARTIAL MODE/i, state);
    assert.equal(calls.interruptSweep.length, 0);
    peer.connectionState = "connected";
    peer.onconnectionstatechange();
    assert.match(ui.statusText, /camera \+ voice connected/i, state);
    controller.endCall();
  }
});

test("a late old-peer event cannot restore or mute the current run's voice state", async () => {
  const { deps, ui } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const old = deps.lastPeer();
  old.channel.open();
  const staleHandler = old.onconnectionstatechange;
  controller.endCall();
  await controller.start();
  await flush();
  const current = deps.lastPeer();
  assert.notEqual(current, old);
  assert.equal(old.onconnectionstatechange, null);
  old.connectionState = "failed";
  staleHandler.call(old);
  assert.match(ui.statusText, /voice connected|setting up voice/i);
  controller.endCall();
});

test("guidance stays silent when the shipped policy is disabled", async () => {
  const { deps, samplers } = guidanceDeps({ guidance: { enabled: false, maximum_responses_per_sweep: 6, minimum_response_interval_ms: 10000, maximum_event_age_ms: 3000 } });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 100 });
  await flush();
  assert.ok(!channel.sent.some((m) => m.item && m.item.content && String(m.item.content[0].text).startsWith("APP-CAPTURE-EVENT")));
  controller.stop();
});

test("a frozen-preview event produces one bounded guidance response with tool_choice none", async () => {
  const { deps, samplers } = guidanceDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 2000 });
  await flush();
  const eventItem = channel.sent.find((m) => m.item && m.item.content && String(m.item.content[0].text).startsWith("APP-CAPTURE-EVENT "));
  assert.ok(eventItem);
  const facts = JSON.parse(eventItem.item.content[0].text.slice("APP-CAPTURE-EVENT ".length));
  assert.equal(facts.issue, "preview_not_advancing");
  assert.equal(facts.frame_id, "capture_context");
  assert.equal(facts.event_id, "capture_event_1");
  assert.equal(facts.severity, "warning");
  const guidanceCreate = channel.sent.find((m) => m.type === "response.create" && m.response && m.response.metadata && m.response.metadata.purpose === "guidance");
  assert.ok(guidanceCreate);
  assert.equal(guidanceCreate.response.tool_choice, "none");
  assert.equal(typeof guidanceCreate.response.metadata.app_request, "string");
  assert.equal(guidanceCreate.response.metadata.event_id, "capture_event_1");
  const dispatched = deps.ui.logLines.filter((l) => l.event === "guidance_dispatched");
  assert.equal(dispatched.length, 1);
  assert.equal(dispatched[0].frame_id, "frame_g1");
  assert.equal(dispatched[0].event_id, "capture_event_1");
  const before = channel.sent.filter((m) => m.type === "response.create").length;
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 2500 });
  deps.timers.tick();
  await flush();
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, before);
  controller.stop();
});

test("unrelated frame-count polling sends no guidance or response", async () => {
  let framesSaved = 1;
  const { deps } = guidanceDeps({ getSweep: async (id) => ({
    id, status: "capturing", revision: framesSaved,
    frames: Array.from({ length: framesSaved }, (_, i) => ({ id: "frame_" + i, timestamp_s: i })),
    books: [], items: [], review_queue: [],
  }) });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  framesSaved = 3;
  deps.timers.tick();
  await flush();
  assert.ok(!channel.sent.some((m) => m.item && m.item.content && String(m.item.content[0].text).startsWith("APP-CAPTURE-EVENT")));
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, 1);
  controller.stop();
});

test("user speech cancels an active owned guidance response and clears buffered audio", async () => {
  const { deps, samplers } = guidanceDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 2000 });
  await flush();
  const rid = "r_guid";
  const reqId = createsOf(channel, "guidance")[0].response.metadata.app_request;
  serverEvent(channel, { type: "response.created", response: { id: rid, metadata: { app_request: reqId, purpose: "guidance" } } });
  serverEvent(channel, { type: "input_audio_buffer.speech_started" });
  const cancel = channel.sent.find((m) => m.type === "response.cancel");
  assert.ok(cancel);
  assert.equal(cancel.response_id, rid);
  assert.ok(channel.sent.some((m) => m.type === "output_audio_buffer.clear"));
  controller.stop();
});

test("speech before a late response.created still cancels the guidance response", async () => {
  const { deps, samplers } = guidanceDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 2000 });
  await flush();
  serverEvent(channel, { type: "input_audio_buffer.speech_started" });
  assert.ok(channel.sent.some((m) => m.type === "response.cancel"));
  serverEvent(channel, { type: "response.created", response: { id: "r_late" } });
  serverEvent(channel, { type: "response.cancelled", response: { id: "r_late" } });
  await flush();
  controller.stop();
});

const createsOf = (channel, purpose) =>
  channel.sent.filter((m) => {
    if (m.type !== "response.create") return false;
    const p = m.response && m.response.metadata && m.response.metadata.purpose;
    return purpose ? p === purpose : p !== "guidance";
  });

test("done before playback stopped holds the next dispatch until its own stopped event", async () => {
  const { deps, samplers } = guidanceDeps({
    guidance: { enabled: true, maximum_responses_per_sweep: 6, minimum_response_interval_ms: 0, maximum_event_age_ms: 3000 },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 2000 });
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 1);
  serverEvent(channel, { type: "response.created", response: { id: "r1", metadata: { app_request: createsOf(channel, "guidance")[0].response.metadata.app_request, purpose: "guidance" } } });
  serverEvent(channel, { type: "output_audio_buffer.started", response_id: "r1" });
  serverEvent(channel, { type: "response.done", response: { id: "r1" } });
  deps.setNow(5000);
  samplers[0].opts.onEvent({ event: "sampling_tick", video_time_s: 5 });
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 5000 });
  deps.timers.tick();
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 1);
  serverEvent(channel, { type: "output_audio_buffer.stopped", response_id: "r_stale" });
  deps.timers.tick();
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 1);
  serverEvent(channel, { type: "output_audio_buffer.stopped", response_id: "r1" });
  deps.timers.tick();
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 2);
  controller.stop();
});

test("stale done and created for an old response cannot release a newer request", async () => {
  const { deps } = guidanceDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  const before = channel.sent.filter((m) => m.type === "response.create").length;
  serverEvent(channel, { type: "input_audio_buffer.committed", item_id: "u1" });
  await flush();
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, before + 1);
  const reqId = channel.sent[channel.sent.length - 1].response.metadata.app_request;
  serverEvent(channel, { type: "response.done", response: { id: "r_stale" } });
  serverEvent(channel, { type: "response.created", response: { id: "r_stale" } });
  serverEvent(channel, { type: "response.done", response: { id: "r_stale" } });
  serverEvent(channel, { type: "input_audio_buffer.committed", item_id: "u2" });
  await flush();
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, before + 1);
  serverEvent(channel, { type: "response.created", response: { id: "r_new", metadata: { app_request: reqId, purpose: "user" } } });
  serverEvent(channel, { type: "response.done", response: { id: "r_new" } });
  await flush();
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, before + 2);
  controller.stop();
});

test("a tool completion follow-up precedes queued guidance", async () => {
  const { deps, samplers } = guidanceDeps();
  deps.api.voiceView = async () => ({
    available: true, frame_id: "frame_g1", timestamp_s: 0,
    image: "data:image/jpeg;base64,AAAA", image_width: 640, image_height: 360, targets: [],
  });
  deps.api.analyzeFrame = async () => ({ observation: {} });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  serverEvent(channel, { type: "input_audio_buffer.committed", item_id: "u1" });
  await flush();
  const u1Req = channel.sent[channel.sent.length - 1].response.metadata.app_request;
  serverEvent(channel, { type: "response.created", response: { id: "r_u1", metadata: { app_request: u1Req, purpose: "user" } } });
  serverEvent(channel, {
    type: "response.function_call_arguments.done",
    name: "inspect_inventory_view",
    call_id: "c1",
    arguments: JSON.stringify({ frame_id: "current" }),
    response: { id: "r_u1" },
  });
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 3000 });
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 0);
  const toolOutputIdx = channel.sent.findIndex((m) => m.item && m.item.type === "function_call_output");
  assert.ok(toolOutputIdx >= 0);
  assert.equal(channel.sent.findIndex((m, i) => i > toolOutputIdx && m.type === "response.create"), -1);
  serverEvent(channel, { type: "response.done", response: { id: "r_u1" } });
  await flush();
  const toolFollowupIdx = channel.sent.findIndex((m, i) => i > toolOutputIdx && m.type === "response.create");
  assert.ok(toolFollowupIdx > toolOutputIdx);
  assert.equal(channel.sent[toolFollowupIdx].response.metadata.purpose, "user");
  assert.equal(createsOf(channel, "guidance").length, 0);
  const reqId = channel.sent[toolFollowupIdx].response.metadata.app_request;
  serverEvent(channel, { type: "response.created", response: { id: "r_u2", metadata: { app_request: reqId, purpose: "user" } } });
  serverEvent(channel, { type: "response.done", response: { id: "r_u2" } });
  deps.timers.tick();
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 1);
  controller.stop();
});

test("terminal packet during user turn delivers one summary after the user finishes", async () => {
  const { deps } = guidanceDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  serverEvent(channel, { type: "input_audio_buffer.committed", item_id: "u1" });
  await flush();
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, 2);
  deps.api.endSweep = async (id) => ({
    id, status: "partial", frames: [], postwork_status: "completed",
    packet: { summary: "Settled summary.", revision: 2, stale: false, status: "partial" },
    revision: 2,
  });
  controller.stop();
  await flush();
  assert.ok(!channel.sent.some((m) => m.item && m.item.content && String(m.item.content[0].text).startsWith("APP-VALIDATED-SUMMARY")));
  const u1Req = channel.sent[channel.sent.length - 1].response.metadata.app_request;
  serverEvent(channel, { type: "response.created", response: { id: "r_u1", metadata: { app_request: u1Req, purpose: "user" } } });
  serverEvent(channel, { type: "response.done", response: { id: "r_u1" } });
  await flush();
  const summaryItems = channel.sent.filter((m) => m.item && m.item.content && String(m.item.content[0].text).startsWith("APP-VALIDATED-SUMMARY "));
  assert.equal(summaryItems.length, 1);
  const summaryCreates = createsOf(channel, "summary");
  assert.equal(summaryCreates.length, 1);
  controller.endCall();
});

test("a capped event item drops the guidance response without retrying", async () => {
  const { deps, samplers } = guidanceDeps({
    captureConfig: async () => ({
      vision_enabled: false,
      visual_context: { maximum_message_bytes: 400, maximum_frame_age_s: 3, minimum_image_interval_ms: 0, maximum_images_per_sweep: 24, request_timeout_ms: 3000, maximum_targets_per_view: 64, allow_upscale: false, automatic_retries: 0 },
      guidance: { enabled: true, maximum_responses_per_sweep: 6, minimum_response_interval_ms: 0, maximum_event_age_ms: 3000 },
    }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  const before = channel.sent.filter((m) => m.type === "response.create").length;
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 2000 });
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 0);
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, before);
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 2500 });
  deps.timers.tick();
  await flush();
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, before);
  assert.ok(deps.ui.logLines.some((l) => l.event === "guidance_dropped"));
  controller.stop();
});

test("overlapping view preparations hold guidance until the latest resolves", async () => {
  const { deps, samplers } = guidanceDeps({
    guidance: { enabled: true, maximum_responses_per_sweep: 6, minimum_response_interval_ms: 0, maximum_event_age_ms: 30000 },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  const releases = [];
  deps.api.voiceView = () => new Promise((resolve) => { releases.push(resolve); });
  serverEvent(channel, { type: "input_audio_buffer.committed", item_id: "u1" });
  serverEvent(channel, { type: "input_audio_buffer.committed", item_id: "u2" });
  await flush();
  assert.equal(releases.length, 2);
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 2000 });
  releases[0]({ available: true, frame_id: "frame_g1", timestamp_s: 0, image: "data:image/jpeg;base64,AAAA", image_width: 640, image_height: 360, targets: [] });
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 0);
  const userCreates = channel.sent.filter((m) => m.type === "response.create" && m.response.metadata.purpose === "user").length;
  releases[1]({ available: true, frame_id: "frame_g1", timestamp_s: 0, image: "data:image/jpeg;base64,AAAA", image_width: 640, image_height: 360, targets: [] });
  await flush();
  const userAfter = channel.sent.filter((m) => m.type === "response.create" && m.response.metadata.purpose === "user").length;
  assert.equal(userAfter, userCreates + 1);
  assert.equal(createsOf(channel, "guidance").length, 0);
  const lastReq = channel.sent[channel.sent.length - 1].response.metadata.app_request;
  serverEvent(channel, { type: "response.created", response: { id: "r_u", metadata: { app_request: lastReq, purpose: "user" } } });
  serverEvent(channel, { type: "response.done", response: { id: "r_u" } });
  deps.timers.tick();
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 1);
  controller.stop();
});

test("disconnected pauses guidance and reconnect resumes under the same cap", async () => {
  const { deps, samplers } = guidanceDeps({
    guidance: { enabled: true, maximum_responses_per_sweep: 2, minimum_response_interval_ms: 0, maximum_event_age_ms: 30000 },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 2000 });
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 1);
  const peer = deps.lastPeer();
  const reqId = createsOf(channel, "guidance")[0].response.metadata.app_request;
  serverEvent(channel, { type: "response.created", response: { id: "r_g1", metadata: { app_request: reqId, purpose: "guidance" } } });
  serverEvent(channel, { type: "response.done", response: { id: "r_g1" } });
  peer.connectionState = "disconnected";
  peer.onconnectionstatechange();
  samplers[0].opts.onEvent({ event: "sampling_tick", video_time_s: 3 });
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 3000 });
  deps.timers.tick();
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 1);
  peer.connectionState = "connected";
  peer.onconnectionstatechange();
  deps.timers.tick();
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 4000 });
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 2);
  deps.timers.tick();
  samplers[0].opts.onEvent({ event: "sampling_tick", video_time_s: 6 });
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 6000 });
  deps.timers.tick();
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 2);
  controller.endCall();
});

test("stop then end call after peer failure ends once and stays truthful", async () => {
  const { deps, ui, calls } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const peer = deps.lastPeer();
  peer.channel.open();
  peer.connectionState = "failed";
  peer.onconnectionstatechange();
  controller.stop();
  await flush();
  assert.match(ui.statusText, /voice unavailable/i);
  assert.equal(calls.endSweep.length, 1);
  controller.endCall();
  controller.endCall();
  await flush();
  assert.equal(calls.endSweep.length, 1);
  assert.match(ui.statusText, /call ended/i);
});

test("malformed response events cannot release a pending correlated request", async () => {
  const { deps } = guidanceDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  const before = channel.sent.filter((m) => m.type === "response.create").length;
  serverEvent(channel, { type: "input_audio_buffer.committed", item_id: "u1" });
  await flush();
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, before + 1);
  const reqId = channel.sent[channel.sent.length - 1].response.metadata.app_request;
  serverEvent(channel, { type: "response.created" });
  serverEvent(channel, { type: "response.done" });
  serverEvent(channel, { type: "response.created", response: { id: "r_bad" } });
  serverEvent(channel, { type: "response.done", response: { id: "r_bad" } });
  serverEvent(channel, { type: "input_audio_buffer.committed", item_id: "u2" });
  await flush();
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, before + 1);
  serverEvent(channel, { type: "response.created", response: { id: "r_ok", metadata: { app_request: reqId, purpose: "user" } } });
  serverEvent(channel, { type: "response.done", response: { id: "r_ok" } });
  await flush();
  assert.equal(channel.sent.filter((m) => m.type === "response.create").length, before + 2);
  controller.stop();
});

test("a queued summary goes stale on a newer revision and is never spoken", async () => {
  const { deps } = makeDeps();
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  serverEvent(channel, { type: "input_audio_buffer.committed", item_id: "u1" });
  await flush();
  deps.api.endSweep = async (id) => ({
    id, status: "partial", frames: [], postwork_status: "completed",
    packet: { summary: "Revision two summary.", revision: 2, stale: false, status: "partial" },
    revision: 2,
  });
  controller.stop();
  await flush();
  deps.api.getSweep = async (id) => ({
    id, status: "partial", frames: [], postwork_status: "completed",
    books: [], items: [], review_queue: [], revision: 3,
  });
  deps.api.getPacket = async (id) => ({
    summary: "Revision three summary.", revision: 3, stale: false, status: "partial",
    packet_ref: "claim_packet.json", report_ref: "report.html",
  });
  deps.timers.tick();
  await flush();
  const u1Req = createsOf(channel, "user").map((m) => m.response.metadata.app_request).pop();
  serverEvent(channel, { type: "response.created", response: { id: "r_u1", metadata: { app_request: u1Req, purpose: "user" } } });
  serverEvent(channel, { type: "response.done", response: { id: "r_u1" } });
  await flush();
  const summaries = channel.sent.filter((m) => m.item && m.item.content && String(m.item.content[0].text).startsWith("APP-VALIDATED-SUMMARY "));
  assert.equal(summaries.length, 1);
  assert.ok(summaries[0].item.content[0].text.includes("Revision three summary."));
  assert.equal(createsOf(channel, "summary").length, 1);
  controller.endCall();
});

test("a continuous blur issue suppresses repeats across preview and fresh sources", async () => {
  let obs = {
    frame_id: "frame_g1",
    observation: { capture_issues: [{ type: "blur", description: "motion blur" }] },
  };
  let frameTs = 0;
  const { deps, samplers, preview } = guidanceDeps({
    guidance: { enabled: true, maximum_responses_per_sweep: 6, minimum_response_interval_ms: 0, maximum_event_age_ms: 30000 },
    getSweep: async (id) => ({
      id, status: "capturing", revision: 1,
      frames: [{ id: "frame_g1", frame_ref: "frames/frame_g1.jpg", timestamp_s: frameTs, width_px: 640, height_px: 360 }],
      latest_observation: obs,
      books: [], items: [], review_queue: [],
    }),
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 1);
  const gReq = createsOf(channel, "guidance")[0].response.metadata.app_request;
  serverEvent(channel, { type: "response.created", response: { id: "r_g", metadata: { app_request: gReq, purpose: "guidance" } } });
  serverEvent(channel, { type: "response.done", response: { id: "r_g" } });
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 2000 });
  samplers[0].opts.onEvent({ event: "sampling_tick", video_time_s: 3 });
  deps.timers.tick();
  await flush();
  const pReq = createsOf(channel, "guidance").map((m) => m.response.metadata.app_request).pop();
  serverEvent(channel, { type: "response.created", response: { id: "r_p", metadata: { app_request: pReq, purpose: "guidance" } } });
  serverEvent(channel, { type: "response.done", response: { id: "r_p" } });
  await flush();
  const blurDispatches = () => deps.ui.logLines.filter((l) => l.event === "guidance_dispatched" && l.issue === "blur").length;
  assert.equal(blurDispatches(), 1);
  deps.timers.tick();
  await flush();
  assert.equal(blurDispatches(), 1);
  obs = { frame_id: "frame_g1", observation: { capture_issues: [] } };
  deps.timers.tick();
  await flush();
  deps.setNow(20000);
  frameTs = 19;
  preview.currentTime = 20;
  obs = { frame_id: "frame_g1", observation: { capture_issues: [{ type: "blur", description: "motion blur" }] } };
  deps.timers.tick();
  await flush();
  assert.equal(blurDispatches(), 2);
  controller.stop();
});

test("reconnect with the same continuous cue does not repeat the warning", async () => {
  const { deps, samplers } = guidanceDeps({
    guidance: { enabled: true, maximum_responses_per_sweep: 6, minimum_response_interval_ms: 0, maximum_event_age_ms: 30000 },
  });
  const controller = createCaptureController(deps);
  await controller.start();
  await flush();
  const channel = await greetingDone({ deps });
  deps.timers.tick();
  await flush();
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 2000 });
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 1);
  const peer = deps.lastPeer();
  const reqId = createsOf(channel, "guidance")[0].response.metadata.app_request;
  serverEvent(channel, { type: "response.created", response: { id: "r_g1", metadata: { app_request: reqId, purpose: "guidance" } } });
  serverEvent(channel, { type: "response.done", response: { id: "r_g1" } });
  peer.connectionState = "disconnected";
  peer.onconnectionstatechange();
  deps.timers.tick();
  await flush();
  peer.connectionState = "connected";
  peer.onconnectionstatechange();
  deps.timers.tick();
  samplers[0].opts.onEvent({ event: "freshness_warning", t_ms: 4000 });
  deps.timers.tick();
  await flush();
  assert.equal(createsOf(channel, "guidance").length, 1);
  controller.endCall();
});
