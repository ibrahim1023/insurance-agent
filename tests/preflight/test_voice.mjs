import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { waitForConnection } from "../../preflight/safari/voice.js";

class FakePeer extends EventTarget {
  constructor() {
    super();
    this.connectionState = "new";
    this.added = 0;
    this.removed = 0;
    const origAdd = this.addEventListener.bind(this);
    const origRemove = this.removeEventListener.bind(this);
    this.addEventListener = (t, l, o) => { this.added++; return origAdd(t, l, o); };
    this.removeEventListener = (t, l) => { this.removed++; return origRemove(t, l); };
  }
  setState(s) {
    this.connectionState = s;
    this.dispatchEvent(new Event("connectionstatechange"));
  }
}

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

test("resolves when peer reaches connected", async () => {
  const peer = new FakePeer();
  const p = waitForConnection(peer, { timeoutMs: 1000 });
  await delay(5);
  peer.setState("connected");
  await assert.doesNotReject(p);
});

test("resolves immediately if already connected", async () => {
  const peer = new FakePeer();
  peer.connectionState = "connected";
  await assert.doesNotReject(waitForConnection(peer, { timeoutMs: 1000 }));
});

test("rejects when peer fails", async () => {
  const peer = new FakePeer();
  const p = waitForConnection(peer, { timeoutMs: 1000 });
  await delay(5);
  peer.setState("failed");
  await assert.rejects(p, /failed/);
});

test("rejects when peer closes", async () => {
  const peer = new FakePeer();
  const p = waitForConnection(peer, { timeoutMs: 1000 });
  await delay(5);
  peer.setState("closed");
  await assert.rejects(p, /closed/);
});

test("rejects on timeout", async () => {
  const peer = new FakePeer();
  await assert.rejects(waitForConnection(peer, { timeoutMs: 20 }), /timeout/i);
});

test("rejects on abort signal", async () => {
  const peer = new FakePeer();
  const controller = new AbortController();
  const p = waitForConnection(peer, { signal: controller.signal, timeoutMs: 1000 });
  await delay(5);
  controller.abort();
  await assert.rejects(p, /abort/i);
});

test("removes listeners and timer after settle", async () => {
  const peer = new FakePeer();
  const p = waitForConnection(peer, { timeoutMs: 1000 });
  peer.setState("connected");
  await p;
  assert.equal(peer.removed, peer.added);
  assert.ok(peer.added > 0);
  peer.setState("failed");
  await assert.doesNotReject(p);
});

test("disconnected does not settle the wait", async () => {
  const peer = new FakePeer();
  const p = waitForConnection(peer, { timeoutMs: 60 });
  await delay(5);
  peer.setState("disconnected");
  await delay(15);
  peer.setState("connected");
  await assert.doesNotReject(p);
});

test("camera preflight does not call a billed voice session without opt-in", async () => {
  const html = readFileSync(new URL("../../preflight/safari/index.html", import.meta.url), "utf8");
  const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^\s*import[^\n]+\n/, "");
  const elements = new Map();
  for (const id of ["status", "keyState", "preview", "remoteAudio", "grab", "startBtn", "audioBtn", "speakBtn", "issueBtn", "interruptBtn", "stopBtn", "downloadBtn", "log", "voiceMode"]) {
    elements.set(id, { disabled: false, checked: false, addEventListener() {} });
  }
  const preview = elements.get("preview");
  Object.assign(preview, { readyState: 4, videoWidth: 640, videoHeight: 480, currentTime: 0, async play() {} });
  Object.assign(elements.get("grab"), { getContext: () => ({ drawImage() {} }), toBlob: (resolve) => resolve({ size: 256 }) });
  const video = { readyState: "live", label: "camera", getSettings: () => ({ width: 640, height: 480 }), addEventListener() {} };
  const audio = { readyState: "live", label: "mic" };
  const stream = { getVideoTracks: () => [video], getAudioTracks: () => [audio] };
  const requests = [];
  let sampledTime = 100;
  let samplingTick;
  const context = {
    document: { getElementById: (id) => elements.get(id) },
    navigator: { userAgent: "test", mediaDevices: { async getUserMedia(constraints) { requests.push(constraints); return stream; } } },
    performance: { now: () => { sampledTime += 15; return sampledTime; } },
    fetch: async (url) => {
      requests.push(url);
      return url.startsWith("/preflight/frame")
        ? { ok: true, status: 200, json: async () => ({ frame_id: "frame_000000" }) }
        : { json: async () => ({ openai_key_present: false }) };
    },
    RTCPeerConnection: class { constructor() { throw Error("voice must be opt-in"); } },
    waitForConnection, AbortController, Date, JSON, setInterval: (fn) => { samplingTick = fn; return 1; }, clearInterval() {},
  };
  runInNewContext(script, context);

  await elements.get("startBtn").onclick();

  assert.equal(elements.get("status").textContent, "running: camera + microphone local; voice not connected");
  assert.equal(requests.filter((entry) => typeof entry === "string" && entry.includes("/preflight/session")).length, 0);
  assert.deepEqual(JSON.parse(JSON.stringify(requests.find((entry) => typeof entry !== "string"))), { video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: true });
  assert.match(elements.get("log").textContent, /"voice_skipped"/);
  preview.currentTime = 1;
  samplingTick();
  await delay(0);
  samplingTick();
  const events = elements.get("log").textContent.split("\n").map(JSON.parse);
  assert.equal(events.find((entry) => entry.event === "capture_started").video_track.settings.width, 640);
  assert.match(elements.get("log").textContent, /"freshness_warning"/);
  const delivered = events.find((entry) => entry.event === "upload_success");
  assert.equal(delivered.width_px, 640);
  assert.equal(delivered.height_px, 480);
  assert.equal(delivered.bytes, 256);
  assert.ok(delivered.latency_ms > 0);
});
