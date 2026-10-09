import { waitForConnection } from "/voice.js";
import { createSampler } from "/capture.js";
import { createCaptureController, httpError } from "/lifecycle.js";
import { renderObservation, renderInventory } from "/proposals.js";

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const keyStateEl = $("keyState");
const stageEl = $("stage");
const sweepIdEl = $("sweepId");
const frameCountEl = $("frameCount");
const preview = $("preview");
const remoteAudio = $("remoteAudio");
const canvas = $("grab");
const countryInput = $("country");
const currencyInput = $("currency");
const startBtn = $("startBtn");
const audioBtn = $("audioBtn");
const stopBtn = $("stopBtn");
const turnInput = $("turnInput");
const turnBtn = $("turnBtn");
const analyzeBtn = $("analyzeBtn");
const readBtn = $("readBtn");
const priceBtn = $("priceBtn");
const packetBtn = $("packetBtn");
const packetEl = $("packet");
const endCallBtn = $("endCallBtn");
const proposalsEl = $("proposals");
const inventoryEl = $("inventory");
const warningsEl = $("warnings");
const logEl = $("log");

let t0 = null;
let readBusy = false;
let analyzeBusy = false;
let shownSweepId = "";

function updateReadBtn() {
  readBtn.disabled = readBusy || analyzeBusy || !shownSweepId;
}

function rec(name, facts) {
  const line = JSON.stringify(
    Object.assign({ t_ms: t0 === null ? 0 : Math.round(performance.now() - t0), event: name }, facts || {})
  );
  logEl.textContent = (logEl.textContent ? logEl.textContent + "\n" : "") + line;
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || "";
}

function warn(text) {
  const div = document.createElement("div");
  div.textContent = text;
  warningsEl.appendChild(div);
}

function errMsg(e) {
  return (e && e.name ? e.name + ": " : "") + (e && e.message ? e.message : String(e));
}

fetch("/api/health").then((r) => r.json()).then((h) => {
  keyStateEl.textContent = String(h.openai_key_present === true);
}).catch(() => {
  keyStateEl.textContent = "unknown";
});

function waitIceGathering(peer, timeoutMs) {
  return new Promise((resolve) => {
    if (peer.iceGatheringState === "complete") return resolve();
    const onChange = () => {
      if (peer.iceGatheringState === "complete") {
        peer.removeEventListener("icegatheringstatechange", onChange);
        clearTimeout(timer);
        resolve();
      }
    };
    const timer = setTimeout(() => {
      peer.removeEventListener("icegatheringstatechange", onChange);
      resolve();
    }, timeoutMs);
    peer.addEventListener("icegatheringstatechange", onChange);
  });
}

async function jsonOrThrow(resp) {
  if (resp.ok) return resp.json();
  let body = null;
  try {
    body = await resp.json();
  } catch (e) {}
  throw httpError(resp.status, body);
}

const api = {
  createSweep: async ({ country, currency, device }, signal) =>
    jsonOrThrow(await fetch("/api/sweeps", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ country, currency, device }),
      signal,
    })),
  getSweep: async (id) =>
    jsonOrThrow(await fetch("/api/sweeps/" + encodeURIComponent(id))),
  uploadFrame: async (sweepId, blob, ts, signal) =>
    jsonOrThrow(await fetch(
      "/api/sweeps/" + encodeURIComponent(sweepId) + "/frames?ts=" + encodeURIComponent(ts),
      { method: "POST", headers: { "content-type": "image/jpeg" }, body: blob, signal }
    )),
  addTurn: async (sweepId, turn) =>
    jsonOrThrow(await fetch(
      "/api/sweeps/" + encodeURIComponent(sweepId) + "/turns",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(turn),
      }
    )),
  endSweep: async (sweepId, durationS, opts) =>
    jsonOrThrow(await fetch(
      "/api/sweeps/" + encodeURIComponent(sweepId) + "/end",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ duration_s: durationS }),
        keepalive: !!(opts && opts.keepalive),
      }
    )),
  captureConfig: async (signal) =>
    jsonOrThrow(await fetch("/api/capture_config", { signal })),
  voiceView: async (sweepId, frameId, signal) =>
    jsonOrThrow(await fetch(
      "/api/sweeps/" + encodeURIComponent(sweepId) + "/voice_view?frame_id=" + encodeURIComponent(frameId),
      { signal }
    )),
  interruptSweep: (sweepId) =>
    fetch("/api/sweeps/" + encodeURIComponent(sweepId) + "/interrupt", {
      method: "POST",
      keepalive: true,
    }).catch(() => {}),
  analyzeFrame: async (sweepId, frameId) =>
    jsonOrThrow(await fetch(
      "/api/sweeps/" + encodeURIComponent(sweepId) + "/analyze_frame",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ frame_id: frameId }),
      }
    )),
  readSpines: async (sweepId, refIds) =>
    jsonOrThrow(await fetch(
      "/api/sweeps/" + encodeURIComponent(sweepId) + "/read_spines",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ref_ids: refIds }),
      }
    )),
  priceBooks: async (sweepId, bookIds) =>
    jsonOrThrow(await fetch(
      "/api/sweeps/" + encodeURIComponent(sweepId) + "/price_books",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ book_ids: bookIds }),
      }
    )),
  packet: async (sweepId) =>
    jsonOrThrow(await fetch(
      "/api/sweeps/" + encodeURIComponent(sweepId) + "/packet",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      }
    )),
  getPacket: async (sweepId) => {
    const resp = await fetch(
      "/api/sweeps/" + encodeURIComponent(sweepId) + "/claim_packet.json"
    );
    if (resp.status === 404) return null;
    const packet = await jsonOrThrow(resp);
    const headerRevision = parseInt(resp.headers.get("x-packet-revision") || "", 10);
    const revision = Number.isFinite(headerRevision)
      ? headerRevision
      : packet && packet.sweep && typeof packet.sweep.revision === "number"
        ? packet.sweep.revision
        : null;
    return {
      packet_ref: "claim_packet.json",
      report_ref: "report.html",
      status: packet && packet.status,
      summary: packet && packet.summary,
      revision,
      stale: resp.headers.get("x-packet-stale") === "true",
    };
  },
  correct: async (sweepId, intent) =>
    jsonOrThrow(await fetch(
      "/api/sweeps/" + encodeURIComponent(sweepId) + "/corrections",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(intent),
      }
    )),
  realtime: async (sweepId, sdp, signal) => {
    const resp = await fetch(
      "/api/sweeps/" + encodeURIComponent(sweepId) + "/realtime",
      {
        method: "POST",
        headers: { "content-type": "application/sdp" },
        body: sdp,
        signal,
      }
    );
    if (!resp.ok) throw new Error("realtime setup failed: http " + resp.status);
    return resp.text();
  },
};

const controller = createCaptureController({
  ui: {
    setStatus,
    setStage: (text) => { stageEl.textContent = text; },
    setSweepId: (id) => {
      sweepIdEl.textContent = id;
      shownSweepId = id;
      updateReadBtn();
      priceBtn.disabled = !id;
      packetBtn.disabled = !id;
    },
    readingPending: (pending) => {
      readBusy = pending;
      updateReadBtn();
    },
    analysisPending: (pending) => {
      analyzeBusy = pending;
      updateReadBtn();
    },
    pricingPending: (pending) => {
      priceBtn.disabled = pending;
    },
    setInventory: (state, ctx) => {
      const sweepId = ctx && ctx.sweepId;
      renderInventory(inventoryEl, state, {
        el: (tag) => document.createElement(tag),
        frameUrl: sweepId
          ? (fid) =>
              "/api/sweeps/" + encodeURIComponent(sweepId) + "/frames/" + encodeURIComponent(fid) + ".jpg"
          : null,
        cropUrl: sweepId
          ? (ref) =>
              "/api/sweeps/" + encodeURIComponent(sweepId) + "/crops/" + encodeURIComponent(ref.split("/").pop())
          : null,
      });
    },
    setFrameCount: (n) => { frameCountEl.textContent = String(n); },
    warn,
    log: rec,
    audioReady: () => { audioBtn.disabled = false; },
    setLatestFrameId: (id) => {
      analyzeBtn.disabled = !id;
    },
    setObservation: (obs, ctx) => {
      const sweepId = ctx && ctx.sweepId;
      renderObservation(proposalsEl, obs, {
        el: (tag) => document.createElement(tag),
        frameUrl: sweepId
          ? (fid) =>
              "/api/sweeps/" + encodeURIComponent(sweepId) + "/frames/" + encodeURIComponent(fid) + ".jpg"
          : null,
      });
    },
    setPacket: (packet) => {
      packetEl.textContent = "";
      if (!packet) return;
      const sweepId = sweepIdEl.textContent;
      const note = document.createElement("div");
      note.textContent =
        "claim packet " + packet.status +
        (packet.stale ? " — STALE (sweep changed since generation)" : "") +
        " — revision " + packet.revision;
      packetEl.appendChild(note);
      for (const [label, ref] of [
        ["claim_packet.json", packet.packet_ref],
        ["report.html", packet.report_ref],
      ]) {
        if (!ref) continue;
        const link = document.createElement("a");
        link.setAttribute(
          "href",
          "/api/sweeps/" + encodeURIComponent(sweepId) + "/" + ref
        );
        link.setAttribute("rel", "noopener noreferrer");
        link.setAttribute("target", "_blank");
        link.textContent = label;
        packetEl.appendChild(link);
        packetEl.appendChild(document.createTextNode(" "));
      }
    },
  },
  fields: {
    setEnabled: (enabled) => {
      countryInput.disabled = !enabled;
      currencyInput.disabled = !enabled;
    },
  },
  controls: {
    setRunning: (running) => {
      startBtn.disabled = running;
      stopBtn.disabled = !running;
      turnInput.disabled = !running;
      turnBtn.disabled = !running;
      if (running) audioBtn.disabled = true;
    },
    setVoiceActive: (active) => {
      endCallBtn.disabled = !active;
    },
  },
  api,
  media: { getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints) },
  preview,
  remoteAudio,
  samplerFactory: (cfg) => createSampler({
    ...cfg,
    canvas,
    clock: () => performance.now() - t0,
    setInterval: (fn, ms) => window.setInterval(fn, ms),
    clearInterval: (id) => window.clearInterval(id),
    intervalMs: 1000,
  }),
  createPeer: () => new RTCPeerConnection(),
  waitForConnection,
  waitIceGathering,
  storage: {
    get: (key) => sessionStorage.getItem(key),
    set: (key, value) => sessionStorage.setItem(key, value),
  },
  timers: {
    setInterval: (fn, ms) => window.setInterval(fn, ms),
    clearInterval: (id) => window.clearInterval(id),
    setTimeout: (fn, ms) => window.setTimeout(fn, ms),
    clearTimeout: (id) => window.clearTimeout(id),
  },
  clock: () => performance.now(),
  device: () => navigator.userAgent.slice(0, 200),
  locale: () => ({ country: countryInput.value, currency: currencyInput.value }),
  newTurnId: () => crypto.randomUUID().replace(/-/g, ""),
});

window.addEventListener("pagehide", () => controller.pagehide());

startBtn.onclick = () => {
  t0 = performance.now();
  controller.start();
};
stopBtn.onclick = () => controller.stop();
endCallBtn.onclick = () => controller.endCall();
readBtn.onclick = () => controller.readSpines();
priceBtn.onclick = () => controller.priceBooks();
turnBtn.onclick = () => {
  const text = turnInput.value;
  turnInput.value = "";
  controller.sendTurn(text);
};
analyzeBtn.onclick = () => controller.analyzeLatest();
packetBtn.onclick = () => controller.publishPacket();
audioBtn.onclick = () => {
  remoteAudio.play().catch((e) => rec("audio_playback_issue", { error: errMsg(e) }));
};

controller.restoreSaved();
