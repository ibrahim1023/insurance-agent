import { createGuidanceScheduler } from "./guidance.js";

export const GUIDANCE_INSTRUCTIONS =
  "Give one brief capture-recovery instruction based only on the supplied APP-CAPTURE-EVENT. Treat it as a capture warning, not a new image or verified inventory fact. Do not read titles, state counts, mention identifiers, call tools, repeat the greeting or change the locale. Ask only for a hold, slower movement or a small re-aim within the current continuous pass. If the event is no longer actionable, stay silent.";

const VISION_ISSUES = new Set(["blur", "glare", "occlusion", "region_unclear"]);

export function httpError(status, body) {
  let detail = "";
  const items = body && body.detail;
  if (Array.isArray(items)) {
    detail = items
      .slice(0, 5)
      .map((d) => {
        if (!d || typeof d !== "object") return "";
        const loc = Array.isArray(d.loc) ? d.loc.join(".") : "";
        const msg = typeof d.msg === "string" ? d.msg : "";
        const type = typeof d.type === "string" ? d.type : "";
        return (loc ? loc + " " : "") + (msg || type) + (msg && type ? " (" + type + ")" : "");
      })
      .filter(Boolean)
      .join("; ")
      .slice(0, 300);
  }
  const err = new Error("http " + status + (detail ? ": " + detail : ""));
  err.status = status;
  return err;
}

export function createCaptureController(deps) {
  const {
    ui,
    fields,
    controls,
    api,
    media,
    preview,
    remoteAudio,
    samplerFactory,
    createPeer,
    waitForConnection,
    waitIceGathering = () => Promise.resolve(),
    storage,
    timers,
    clock,
    device,
    locale,
    newTurnId,
  } = deps;

  let generation = 0;
  let run = null;
  let lastSweepId = null;
  let latestFrameId = null;
  let lastGoodObs = null;
  let analysisPending = false;
  let analysisError = null;
  let readingPending = false;
  let pricingPending = false;
  let packetPending = false;
  let lastPacketSummaryKey = null;
  let lastKnownRevision = null;
  let shownPacketSweep = null;
  let shownPacketRevision = null;
  let bookTargets = -1;

  const live = (gen) => run !== null && generation === gen && !run.stopped;
  const owned = (localRun) => run === localRun && !localRun.stopped;
  const stageLabel = (state) =>
    state &&
    state.status === "processing" &&
    typeof state.postwork_stage === "string" &&
    state.postwork_stage
      ? "processing:" + state.postwork_stage
      : state
        ? state.status
        : "unknown";
  const msg = (e) => (e && e.message ? e.message : String(e));
  const voiceStatusText = (localRun) =>
    localRun.voiceAvailable
      ? localRun.captureStopped
        ? "sweep ended — voice still active for processing; press End call to finish"
        : "sweep running: camera + voice connected"
      : "PARTIAL MODE: camera only — voice unavailable";
  const utf8 = typeof TextEncoder === "function" ? new TextEncoder() : null;
  const utf8Bytes = (s) =>
    utf8 ? utf8.encode(s).length : unescape(encodeURIComponent(s)).length;

  function interruptLate(sweepId) {
    if (!sweepId) return;
    try {
      const pending = api.interruptSweep(sweepId);
      if (pending && typeof pending.catch === "function") pending.catch(() => {});
    } catch (e) {}
  }

  function closeVoice(localRun) {
    if (localRun.channel) {
      localRun.channel.onopen = null;
      localRun.channel.onmessage = null;
      try { localRun.channel.close(); } catch (e) {}
      localRun.channel = null;
    }
    if (localRun.peer) {
      localRun.peer.ontrack = null;
      localRun.peer.onconnectionstatechange = null;
      try { localRun.peer.close(); } catch (e) {}
      localRun.peer = null;
    }
    if (localRun.stream) {
      localRun.stream.getAudioTracks().forEach((t) => {
        try { t.stop(); } catch (e) {}
      });
    }
  }

  function cleanup(localRun) {
    localRun.stopped = true;
    localRun.abort.abort();
    if (localRun.guidance) localRun.guidance.stop();
    localRun.guidanceEvents.clear();
    if (localRun.viewCtl) {
      try { localRun.viewCtl.abort(); } catch (e) {}
      localRun.viewCtl = null;
    }
    if (localRun.sampler) {
      localRun.sampler.stop();
      localRun.sampler = null;
    }
    if (localRun.poller !== null && localRun.poller !== undefined) {
      timers.clearInterval(localRun.poller);
      localRun.poller = null;
    }
    closeVoice(localRun);
    if (localRun.stream) {
      localRun.stream.getTracks().forEach((t) => {
        try { t.stop(); } catch (e) {}
      });
      localRun.stream = null;
    }
    preview.srcObject = null;
    if (remoteAudio) remoteAudio.srcObject = null;
  }

  function finishDead(localRun) {
    interruptLate(localRun.sweepId);
    cleanup(localRun);
    if (run === localRun) run = null;
    fields.setEnabled(true);
    if (controls) controls.setRunning(false);
  }

  function waitForMetadata(signal, timeoutMs) {
    if (preview.readyState >= 1) return Promise.resolve();
    return new Promise((resolve, reject) => {
      let settled = false;
      const onReady = () => settle(resolve);
      const onAbort = () => settle(reject, new Error("preview setup aborted"));
      const teardown = () => {
        timers.clearTimeout(timer);
        preview.removeEventListener("loadedmetadata", onReady);
        signal.removeEventListener("abort", onAbort);
      };
      const settle = (fn, value) => {
        if (settled) return;
        settled = true;
        teardown();
        fn(value);
      };
      const timer = timers.setTimeout(
        () => settle(reject, new Error("preview ready timeout")),
        timeoutMs
      );
      preview.addEventListener("loadedmetadata", onReady);
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort);
    });
  }

  function persistVoiceTurn(localRun, itemId, text) {
    if (!itemId || !localRun.sweepId) return;
    if (localRun.turnIds.has(itemId) || localRun.turnsPending.has(itemId)) return;
    localRun.turnsPending.add(itemId);
    api
      .addTurn(localRun.sweepId, {
        turn_id: itemId,
        text: text,
        source: "provider_user_transcript",
      })
      .then(() => {
        localRun.turnsPending.delete(itemId);
        localRun.turnIds.add(itemId);
        if (owned(localRun)) ui.log("turn_saved", { turn_id: itemId });
      })
      .catch((e) => {
        localRun.turnsPending.delete(itemId);
        if (owned(localRun)) ui.log("turn_failed", { turn_id: itemId, error: msg(e) });
      });
  }

  function waitForSourceTurn(localRun, turnId, timeoutMs) {
    const t0 = clock();
    return new Promise((resolve) => {
      const tick = () => {
        if (!owned(localRun)) return resolve(false);
        if (localRun.turnIds.has(turnId)) return resolve(true);
        if (clock() - t0 >= timeoutMs) return resolve(false);
        timers.setTimeout(tick, 100);
      };
      tick();
    });
  }

  function requestResponse(localRun, epoch, kind) {
    if (localRun.speechActive) return;
    const ep = epoch === undefined ? localRun.utteranceEpoch : epoch;
    localRun.pendingResponseEpoch = ep;
    localRun.pendingResponseKind = kind || "user";
    tryDispatch(localRun);
  }

  function sendResponseCreate(localRun, kind, extraResponse) {
    if (!localRun.channel || localRun.channel.readyState !== "open") return false;
    const requestId = "req_" + ++localRun.requestSeq;
    const response = Object.assign({}, extraResponse || {});
    response.metadata = Object.assign(
      { app_request: requestId, purpose: kind },
      (extraResponse && extraResponse.metadata) || {}
    );
    const sent = sendChannelJSON(
      localRun,
      JSON.stringify({ type: "response.create", response })
    );
    if (sent < 0) return false;
    localRun.responseActive = true;
    localRun.responseKind = kind;
    localRun.awaitingCreate.set(requestId, kind);
    return true;
  }

  function guidanceReady(localRun) {
    if (
      !localRun.guidance ||
      !localRun.greetingSent ||
      localRun.captureStopped ||
      !localRun.channel ||
      localRun.channel.readyState !== "open"
    ) {
      return null;
    }
    return localRun.guidance.next({
      speechActive: false,
      responseActive: false,
      playbackActive: false,
      userTurnPending: false,
      voiceAvailable: true,
      captureActive: true,
    });
  }

  function summaryStale(localRun, pending) {
    return (
      pending.key === lastPacketSummaryKey ||
      !pending.key.startsWith(localRun.sweepId + ":") ||
      (typeof pending.revision === "number" &&
        lastKnownRevision !== null &&
        pending.revision < lastKnownRevision)
    );
  }

  function tryDispatch(localRun) {
    if (!owned(localRun) || !localRun.voiceAvailable) return;
    if (
      localRun.speechActive ||
      localRun.responseActive ||
      localRun.playingIds.size > 0 ||
      localRun.viewPrep.size > 0 ||
      localRun.toolPrep.size > 0
    ) {
      return;
    }
    if (!localRun.channel || localRun.channel.readyState !== "open") return;
    if (localRun.pendingResponseEpoch !== null) {
      const ep = localRun.pendingResponseEpoch;
      const kind = localRun.pendingResponseKind || "user";
      if (ep === localRun.utteranceEpoch) {
        localRun.pendingResponseEpoch = null;
        localRun.pendingResponseKind = null;
        if (sendResponseCreate(localRun, kind)) return;
      } else {
        localRun.pendingResponseEpoch = null;
        localRun.pendingResponseKind = null;
      }
    }
    const ev = guidanceReady(localRun);
    if (ev) {
      dispatchGuidance(localRun, ev);
      return;
    }
    const pending = localRun.pendingSummary;
    if (pending && summaryStale(localRun, pending)) {
      localRun.pendingSummary = null;
    } else if (
      pending &&
      localRun.captureStopped &&
      postworkSettled(localRun)
    ) {
      const sent = sendChannelJSON(
        localRun,
        JSON.stringify({
          type: "conversation.item.create",
          item: {
            type: "message",
            role: "user",
            content: [
              {
                type: "input_text",
                text: "APP-VALIDATED-SUMMARY " + pending.summary,
              },
            ],
          },
        })
      );
      if (sent < 0) return;
      if (sendResponseCreate(localRun, "summary")) {
        lastPacketSummaryKey = pending.key;
        localRun.pendingSummary = null;
        ui.log("packet_summary_sent", { revision: pending.revision });
      }
    }
  }

  function dispatchGuidance(localRun, ev) {
    const alias = "capture_event_" + ++localRun.guidanceEventSeq;
    const sent = sendChannelJSON(
      localRun,
      JSON.stringify({
        type: "conversation.item.create",
        item: {
          type: "message",
          role: "user",
          content: [
            {
              type: "input_text",
              text:
                "APP-CAPTURE-EVENT " +
                JSON.stringify({
                  event_id: alias,
                  issue: ev.issue,
                  row_id: null,
                  severity: ev.severity,
                  frame_id: "capture_context",
                }),
            },
          ],
        },
      })
    );
    if (sent < 0) {
      ui.log("guidance_dropped", {
        t_ms: clock(),
        source_event_id: ev.id,
        frame_id: ev.frame_id,
        issue: ev.issue,
        send_error: sent,
      });
      return;
    }
    if (
      !sendResponseCreate(localRun, "guidance", {
        instructions: GUIDANCE_INSTRUCTIONS,
        tool_choice: "none",
        metadata: { event_id: alias },
      })
    ) {
      ui.log("guidance_dropped", {
        t_ms: clock(),
        event_id: alias,
        source_event_id: ev.id,
        frame_id: ev.frame_id,
        issue: ev.issue,
        send_error: "response_create",
      });
      return;
    }
    ui.log("guidance_dispatched", {
      t_ms: clock(),
      event_id: alias,
      source_event_id: ev.id,
      frame_id: ev.frame_id,
      issue: ev.issue,
    });
  }

  function refreshGuidance(localRun) {
    if (!localRun.guidance) return;
    localRun.guidance.update([
      ...localRun.guidanceEvents.values(),
      ...issueEvents(localRun, localRun.latestState),
    ]);
    tryDispatch(localRun);
  }

  function issueEvents(localRun, state) {
    if (!localRun.guidance || localRun.captureStopped) return [];
    const obs = state && state.latest_observation;
    const scene = (obs && obs.observation) || {};
    const issues = Array.isArray(scene.capture_issues) ? scene.capture_issues : [];
    if (!issues.length || !obs || typeof obs.frame_id !== "string") return [];
    const frames = Array.isArray(state.frames) ? state.frames : [];
    const frame = frames.find((f) => f && f.id === obs.frame_id);
    if (!frame) return [];
    const policy = localRun.viewPolicy;
    const maxAge = policy && Number.isFinite(policy.maximum_frame_age_s)
      ? policy.maximum_frame_age_s
      : 3;
    const t = preview.currentTime;
    if (
      !Number.isFinite(t) ||
      !Number.isFinite(frame.timestamp_s) ||
      t - frame.timestamp_s < 0 ||
      t - frame.timestamp_s > maxAge
    ) {
      return [];
    }
    const now = clock();
    const seen = localRun.guidanceIssueSeen;
    const out = [];
    for (const issue of issues) {
      if (!issue || !VISION_ISSUES.has(issue.type)) continue;
      const k = obs.frame_id + "|" + issue.type;
      let first = seen.get(k);
      if (first === undefined) {
        first = now;
        seen.set(k, first);
      }
      const maxAge =
        localRun.guidancePolicy &&
        Number.isFinite(localRun.guidancePolicy.maximum_event_age_ms)
          ? localRun.guidancePolicy.maximum_event_age_ms
          : 3000;
      out.push({
        id: "issue_" + k,
        frame_id: obs.frame_id,
        observed_at_ms: first,
        expires_at_ms: now + maxAge,
        row_id: null,
        issue: issue.type,
        severity: "warning",
      });
    }
    return out;
  }

  function sendChannelJSON(localRun, payload) {
    if (!localRun.channel || localRun.channel.readyState !== "open") return -2;
    const bytes = utf8Bytes(payload);
    if (bytes > transportCap(localRun, localRun.viewPolicy)) return -1;
    try {
      localRun.channel.send(payload);
    } catch (e) {
      return -2;
    }
    return bytes;
  }

  function sendToolOutput(localRun, callId, result, epoch) {
    if (!localRun.channel || localRun.channel.readyState !== "open") return;
    let bytes = sendChannelJSON(
      localRun,
      JSON.stringify({
        type: "conversation.item.create",
        item: {
          type: "function_call_output",
          call_id: callId,
          output: JSON.stringify(result),
        },
      })
    );
    if (bytes === -1) {
      bytes = sendChannelJSON(
        localRun,
        JSON.stringify({
          type: "conversation.item.create",
          item: {
            type: "function_call_output",
            call_id: callId,
            output: JSON.stringify({ error: "tool output exceeded transport cap" }),
          },
        })
      );
    }
    if (bytes < 0) return;
    requestResponse(localRun, epoch);
  }

  async function handleToolCall(localRun, ev) {
    if (localRun.speechActive) return;
    const prepKey =
      typeof ev.call_id === "string" && ev.call_id
        ? ev.call_id
        : "tool_" + ++localRun.toolPrepSeq;
    localRun.toolPrep.add(prepKey);
    try {
      await handleToolCallInner(localRun, ev);
    } finally {
      localRun.toolPrep.delete(prepKey);
      tryDispatch(localRun);
    }
  }

  async function handleToolCallInner(localRun, ev) {
    const callId = ev.call_id;
    let args = {};
    try {
      args = JSON.parse(ev.arguments || "{}");
    } catch (e) {
      args = {};
    }
    const gen = generation;
    const ep = localRun.utteranceEpoch;
    const current = () =>
      owned(localRun) &&
      generation === gen &&
      localRun.utteranceEpoch === ep &&
      !localRun.speechActive;
    if (ev.name === "inspect_inventory_view") {
      const frameId = args && args.frame_id === "current" ? localRun.lastViewFrameId : null;
      let result;
      if (!frameId) {
        result = { ok: false, error: "frame_id does not match the supplied view" };
      } else {
        if (!current()) return;
        try {
          await api.analyzeFrame(localRun.sweepId, frameId);
          if (!current()) return;
          if (localRun.viewCtl) {
            try { localRun.viewCtl.abort(); } catch (e) {}
          }
          const ctl = new AbortController();
          localRun.viewCtl = ctl;
          const policy = localRun.viewPolicy;
          const view =
            policy && Number.isFinite(policy.request_timeout_ms)
              ? await timed(
                  api.voiceView(localRun.sweepId, frameId, ctl.signal),
                  policy.request_timeout_ms
                )
              : await api.voiceView(localRun.sweepId, frameId, ctl.signal);
          if (localRun.viewCtl === ctl) localRun.viewCtl = null;
          if (!current()) return;
          if (!view || view.available !== true || view.frame_id !== frameId) {
            result = { ok: false, error: "frame is no longer the current view" };
          } else {
            const targets = Array.isArray(view.targets) ? view.targets : [];
            const known = {};
            for (const t of targets) {
              if (t && typeof t.id === "string" && t.id) known[t.id] = t;
            }
            localRun.knownTargets = known;
            result = {
              ok: true,
              frame_id: "current",
              image_width: view.image_width,
              image_height: view.image_height,
              targets: targets,
              targets_truncated: !!view.targets_truncated,
            };
          }
        } catch (e) {
          if (!current()) return;
          result = { ok: false, error: msg(e) };
        }
      }
      if (!current()) return;
      sendToolOutput(localRun, callId, result, ep);
      return;
    }
    if (ev.name !== "apply_correction") return;
    const source = args && typeof args.source_turn_id === "string" ? args.source_turn_id : "";
    const sourceFrame = args.source_frame_id === "current" ? localRun.lastViewFrameId : null;
    const targetId = args && typeof args.target_id === "string" ? args.target_id : "";
    const targetKnown =
      args && args.type === "exclude_region"
        ? true
        : !!(localRun.knownTargets && localRun.knownTargets[targetId]);
    let result;
    if (!sourceFrame || sourceFrame !== localRun.lastViewFrameId || !targetKnown) {
      result = { applied: false, reason: "clarification_required" };
    } else if (!source || !(await waitForSourceTurn(localRun, source, 5000))) {
      result = { applied: false, reason: "clarification_required" };
    } else {
      if (!current()) return;
      if (sourceFrame !== localRun.lastViewFrameId) {
        result = { applied: false, reason: "clarification_required" };
      } else {
        try {
          result = await api.correct(localRun.sweepId, {
            type: args.type,
            target_id: args.target_id,
            value: args.value === undefined ? null : args.value,
            source_turn_id: source,
            source_frame_id: sourceFrame,
          });
        } catch (e) {
          result = { applied: false, error: msg(e) };
        }
      }
    }
    if (!current()) return;
    sendToolOutput(localRun, callId, result, ep);
  }

  function sendAppState(localRun, state, force = false) {
    if (!localRun.channel || localRun.channel.readyState !== "open") return;
    const obs = state.latest_observation || null;
    const scene = (obs && obs.observation) || {};
    const context = {
      sweep_status: state.status,
      country: state.country,
      currency: state.currency,
      frames_saved: (state.frames || []).length,
      known_targets: {
        books: (state.books || []).map((b) => ({
          id: b.id,
          shelf: b.shelf,
          status: b.status,
        })),
        items: (state.items || []).map((i) => ({
          id: i.id,
          category: i.category,
          status: i.status,
        })),
        regions: (state.regions || []).map((r) => ({ id: r.id, excluded: r.excluded })),
      },
      capture_issues: scene.capture_issues || [],
      known_user_turn_ids: [...localRun.turnIds],
    };
    const signature = JSON.stringify({ ...context, frames_saved: null });
    if (!force && signature === localRun.lastAppStateSignature) return;
    const sent = sendChannelJSON(
      localRun,
      JSON.stringify({
        type: "conversation.item.create",
        item: {
          type: "message",
          role: "user",
          content: [
            { type: "input_text", text: "APP-STATE " + JSON.stringify(context) },
          ],
        },
      })
    );
    if (sent >= 0) localRun.lastAppStateSignature = signature;
  }

  function maybeGreet(localRun) {
    if (localRun.greetingSent || localRun.stopped) return;
    if (
      localRun.channel &&
      localRun.channel.readyState === "open" &&
      localRun.peer &&
      localRun.peer.connectionState === "connected"
    ) {
      localRun.greetingSent = true;
      requestResponse(localRun);
    }
  }

  function sendViewItem(localRun, meta, imageUrl) {
    const content = [
      { type: "input_text", text: "APP-CURRENT-VIEW " + JSON.stringify(meta) },
    ];
    if (imageUrl) {
      content.push({ type: "input_image", image_url: imageUrl });
    }
    return sendChannelJSON(
      localRun,
      JSON.stringify({
        type: "conversation.item.create",
        item: { type: "message", role: "user", content },
      })
    );
  }

  function transportCap(localRun, policy) {
    let cap =
      policy && typeof policy.maximum_message_bytes === "number"
        ? policy.maximum_message_bytes
        : Infinity;
    const sctp = localRun.peer && localRun.peer.sctp;
    if (sctp && typeof sctp.maxMessageSize === "number" && sctp.maxMessageSize > 0) {
      cap = Math.min(cap, sctp.maxMessageSize);
    }
    return cap;
  }

  function timed(promise, ms) {
    return new Promise((resolve, reject) => {
      let done = false;
      const id = timers.setTimeout(() => {
        if (!done) {
          done = true;
          reject(new Error("timeout"));
        }
      }, ms);
      promise.then(
        (v) => {
          if (!done) {
            done = true;
            timers.clearTimeout(id);
            resolve(v);
          }
        },
        (e) => {
          if (!done) {
            done = true;
            timers.clearTimeout(id);
            reject(e);
          }
        }
      );
    });
  }

  async function prepareVisualTurn(localRun, userItemId) {
    const gen = generation;
    const seq = ++localRun.viewSeq;
    localRun.utteranceEpoch += 1;
    localRun.viewPrep.add(seq);
    try {
      await prepareVisualTurnInner(localRun, gen, seq, userItemId);
    } finally {
      localRun.viewPrep.delete(seq);
      tryDispatch(localRun);
    }
  }

  async function prepareVisualTurnInner(localRun, gen, seq, userItemId) {
    const ep = localRun.utteranceEpoch;
    localRun.lastViewFrameId = null;
    localRun.knownTargets = {};
    if (localRun.viewCtl) {
      try { localRun.viewCtl.abort(); } catch (e) {}
    }
    const ctl = new AbortController();
    localRun.viewCtl = ctl;
    const still = () =>
      owned(localRun) &&
      generation === gen &&
      localRun.viewSeq === seq &&
      localRun.utteranceEpoch === ep;
    const sendUnavailable = (reason, frameId) => {
      if (!still()) return;
      localRun.lastViewFrameId = null;
      localRun.knownTargets = {};
      const bytes = sendViewItem(localRun, {
        available: false,
        reason: reason,
        user_item_id: userItemId,
      });
      ui.log("view_unavailable", {
        reason: reason,
        frame_id: frameId || null,
        bytes: bytes,
        user_item_id: userItemId,
      });
      if (bytes >= 0) requestResponse(localRun, ep);
    };
    if (!localRun.channel || localRun.channel.readyState !== "open") return;
    if (localRun.latestState) sendAppState(localRun, localRun.latestState, true);
    const policy = localRun.viewPolicy;
    const frame = localRun.lastFrame;
    const frameFresh = (ts) => {
      const t = preview.currentTime;
      return (
        Number.isFinite(t) &&
        Number.isFinite(ts) &&
        t - ts >= 0 &&
        t - ts <= policy.maximum_frame_age_s
      );
    };
    let reason = null;
    if (!policy || typeof api.voiceView !== "function") {
      reason = "visual_context_unavailable";
    } else if (localRun.captureStopped) {
      reason = "capture_stopped";
    } else if (localRun.previewFrozen) {
      reason = "preview_frozen";
    } else if (!frame) {
      reason = "no_saved_frame";
    } else if (localRun.imagesSent >= policy.maximum_images_per_sweep) {
      reason = "image_budget_exhausted";
    } else if (
      localRun.lastImageAt !== null &&
      clock() - localRun.lastImageAt < policy.minimum_image_interval_ms
    ) {
      reason = "image_interval";
    } else if (!frameFresh(frame.timestamp_s)) {
      reason = "frame_stale";
    }
    if (reason !== null) {
      sendUnavailable(reason, frame && frame.id);
      return;
    }
    let view;
    try {
      view = await timed(
        api.voiceView(localRun.sweepId, frame.id, ctl.signal),
        policy.request_timeout_ms
      );
    } catch (e) {
      sendUnavailable("view_unavailable", frame.id);
      return;
    }
    if (!still()) return;
    if (!localRun.channel || localRun.channel.readyState !== "open") return;
    let postReason = null;
    if (localRun.captureStopped) {
      postReason = "capture_stopped";
    } else if (localRun.previewFrozen) {
      postReason = "preview_frozen";
    } else if (
      !view ||
      view.available !== true ||
      view.frame_id !== frame.id ||
      view.timestamp_s !== frame.timestamp_s
    ) {
      postReason = "view_unavailable";
    } else if (!frameFresh(view.timestamp_s)) {
      postReason = "frame_stale";
    }
    if (postReason !== null) {
      sendUnavailable(postReason, frame.id);
      return;
    }
    const meta = {
      available: true,
      frame_id: "current",
      timestamp_s: view.timestamp_s,
      image_width: view.image_width,
      image_height: view.image_height,
      targets: view.targets,
      targets_truncated: !!view.targets_truncated,
      user_item_id: userItemId,
    };
    const sentBytes = sendViewItem(localRun, meta, view.image);
    if (sentBytes < 0) {
      if (sentBytes === -1) sendUnavailable("message_cap", frame.id);
      else ui.log("view_unavailable", { reason: "transport", frame_id: frame.id, bytes: -1, user_item_id: userItemId });
      return;
    }
    const known = {};
    for (const t of Array.isArray(view.targets) ? view.targets : []) {
      if (t && typeof t.id === "string" && t.id) known[t.id] = t;
    }
    localRun.imagesSent += 1;
    localRun.lastImageAt = clock();
    localRun.lastViewFrameId = view.frame_id;
    localRun.knownTargets = known;
    ui.log("view_sent", {
      frame_id: view.frame_id,
      image_width: view.image_width,
      image_height: view.image_height,
      targets: known ? Object.keys(known).length : 0,
      bytes: sentBytes,
      user_item_id: userItemId,
    });
    requestResponse(localRun, ep);
  }

  async function setupVoice(localRun) {
    const peer = createPeer();
    localRun.peer = peer;
    const audioTrack = localRun.stream.getAudioTracks()[0];
    if (audioTrack) peer.addTrack(audioTrack, localRun.stream);
    const channel = peer.createDataChannel("oai-events");
    localRun.channel = channel;
    channel.onopen = () => {
      if (owned(localRun)) maybeGreet(localRun);
    };
    channel.onmessage = (event) => {
      if (!owned(localRun)) return;
      let ev;
      try {
        ev = JSON.parse(event.data);
      } catch (e) {
        return;
      }
      if (!ev || typeof ev.type !== "string") return;
      ui.log("voice_event", { type: ev.type });
      if (ev.type === "conversation.item.input_audio_transcription.completed") {
        if (typeof ev.transcript === "string" && ev.transcript.trim()) {
          persistVoiceTurn(localRun, ev.item_id, ev.transcript);
        }
      } else if (ev.type === "input_audio_buffer.speech_started") {
        localRun.speechActive = true;
        localRun.utteranceEpoch += 1;
        localRun.viewSeq += 1;
        if (localRun.viewCtl) {
          try { localRun.viewCtl.abort(); } catch (e) {}
          localRun.viewCtl = null;
        }
        localRun.lastViewFrameId = null;
        localRun.knownTargets = {};
        localRun.pendingResponseEpoch = null;
        localRun.pendingResponseKind = null;
        if (localRun.responseActive && localRun.responseKind === "guidance") {
          const cancel = { type: "response.cancel" };
          if (localRun.activeResponseId) cancel.response_id = localRun.activeResponseId;
          sendChannelJSON(localRun, JSON.stringify(cancel));
          sendChannelJSON(localRun, JSON.stringify({ type: "output_audio_buffer.clear" }));
        } else {
          let guidancePlaying = false;
          for (const kind of localRun.playingIds.values()) {
            if (kind === "guidance") guidancePlaying = true;
          }
          if (guidancePlaying) {
            sendChannelJSON(localRun, JSON.stringify({ type: "output_audio_buffer.clear" }));
          }
        }
      } else if (ev.type === "input_audio_buffer.committed") {
        if (
          typeof ev.item_id === "string" &&
          ev.item_id &&
          !localRun.committedIds.has(ev.item_id)
        ) {
          localRun.committedIds.add(ev.item_id);
          localRun.speechActive = false;
          localRun.activeUserItemId = ev.item_id;
          prepareVisualTurn(localRun, ev.item_id);
        }
      } else if (ev.type === "response.created") {
        const rid =
          ev.response && typeof ev.response.id === "string" ? ev.response.id : null;
        const meta = ev.response && ev.response.metadata;
        const reqId =
          meta && typeof meta.app_request === "string" ? meta.app_request : null;
        if (rid === null || reqId === null) return;
        if (localRun.retiredResponses.has(rid)) return;
        const kind = localRun.awaitingCreate.get(reqId);
        if (!kind) return;
        localRun.awaitingCreate.delete(reqId);
        localRun.activeResponseId = rid;
        localRun.activeRequestId = reqId;
        localRun.responseKinds.set(rid, kind);
        localRun.responseActive = true;
      } else if (
        ev.type === "response.done" ||
        ev.type === "response.cancelled" ||
        ev.type === "response.failed"
      ) {
        const doneId = ev.response && typeof ev.response.id === "string" ? ev.response.id : null;
        if (doneId === null) return;
        if (localRun.retiredResponses.has(doneId)) return;
        if (doneId !== localRun.activeResponseId) return;
        localRun.retiredResponses.add(doneId);
        localRun.activeResponseId = null;
        localRun.activeRequestId = null;
        ui.log("response_finished", {
          t_ms: clock(),
          response_id: doneId,
          kind: localRun.responseKind,
          event: ev.type,
        });
        localRun.responseActive = false;
        localRun.responseKind = null;
        tryDispatch(localRun);
      } else if (ev.type === "output_audio_buffer.started") {
        if (typeof ev.response_id === "string" && ev.response_id) {
          localRun.playingIds.set(ev.response_id, localRun.responseKinds.get(ev.response_id) || "user");
          ui.log("playback_started", { t_ms: clock(), response_id: ev.response_id });
        }
      } else if (
        ev.type === "output_audio_buffer.stopped" ||
        ev.type === "output_audio_buffer.cleared"
      ) {
        ui.log("playback_finished", { t_ms: clock(), response_id: typeof ev.response_id === "string" ? ev.response_id : null });
        if (typeof ev.response_id === "string" && ev.response_id) {
          localRun.playingIds.delete(ev.response_id);
        } else {
          localRun.playingIds.clear();
        }
        tryDispatch(localRun);
      } else if (ev.type === "response.function_call_arguments.done") {
        handleToolCall(localRun, ev);
      }
    };
    peer.ontrack = (e) => {
      if (!owned(localRun)) return;
      remoteAudio.srcObject = e.streams[0];
      if (ui.audioReady) ui.audioReady();
      remoteAudio.play().catch((err) => {
        if (owned(localRun)) ui.log("audio_playback_issue", { error: msg(err) });
      });
    };
    peer.onconnectionstatechange = () => {
      if (!owned(localRun) || localRun.peer !== peer) return;
      const state = peer.connectionState;
      ui.log("voice_event", { type: "peer_state", state: state });
      if (state === "failed" || state === "closed" || state === "disconnected") {
        localRun.voiceAvailable = false;
        localRun.speechActive = false;
        localRun.responseActive = false;
        localRun.responseKind = null;
        localRun.activeResponseId = null;
        localRun.activeRequestId = null;
        localRun.awaitingCreate.clear();
        localRun.playingIds.clear();
        localRun.responseKinds.clear();
        localRun.pendingResponseEpoch = null;
        localRun.pendingResponseKind = null;
        if (state !== "disconnected") {
          localRun.guidanceEvents.clear();
          if (localRun.guidance) localRun.guidance.stop();
        }
        if (controls && controls.setVoiceActive) controls.setVoiceActive(false);
        ui.setStatus("PARTIAL MODE: camera only — voice unavailable", "partial");
        ui.warn("voice connection lost; capture continues without audio");
      } else if (state === "connected") {
        localRun.voiceAvailable = true;
        if (controls && controls.setVoiceActive) controls.setVoiceActive(true);
        ui.setStatus(voiceStatusText(localRun));
        maybeGreet(localRun);
        tryDispatch(localRun);
      }
    };
    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    await waitIceGathering(peer, 4000);
    const answer = await api.realtime(
      localRun.sweepId,
      peer.localDescription.sdp,
      localRun.abort.signal
    );
    await peer.setRemoteDescription({ type: "answer", sdp: answer });
    await waitForConnection(peer, { signal: localRun.abort.signal, timeoutMs: 15000 });
  }

  function poll(localRun, gen) {
    api.getSweep(localRun.sweepId)
      .then((state) => {
        if (generation !== gen || localRun.stopped) return;
        if (!acceptRevision(state)) return;
        localRun.latestState = state;
        ui.setStage(stageLabel(state));
        ui.setFrameCount(state.frames.length);
        latestFrameId = state.frames.length
          ? state.frames[state.frames.length - 1].id
          : null;
        localRun.lastFrame = state.frames.length
          ? state.frames[state.frames.length - 1]
          : null;
        bookTargets = (state.books || []).filter((b) => !b.excluded).length;
        if (ui.setLatestFrameId) ui.setLatestFrameId(latestFrameId);
        if (ui.setInventory) ui.setInventory(state, { sweepId: localRun.sweepId });
        const obs = state.latest_observation || null;
        if (obs) lastGoodObs = obs;
        if (ui.setObservation && !analysisPending && analysisError === null) {
          const stale =
            obs && latestFrameId !== null && obs.frame_id !== latestFrameId;
          ui.setObservation(stale ? Object.assign({ stale: true }, obs) : obs, {
            sweepId: localRun.sweepId,
          });
        }
        sendAppState(localRun, state);
        refreshGuidance(localRun);
        if (typeof state.postwork_status === "string") {
          localRun.postworkStatus = state.postwork_status;
        }
        if (
          localRun.postworkStatus === "completed" ||
          localRun.postworkStatus === "failed" ||
          localRun.postworkStatus === "timed_out"
        ) {
          if (
            state.revision !== localRun.settledPacketRevision &&
            state.revision !== localRun.settledReadPending
          ) {
            localRun.settledReadPending = state.revision;
            readSettledPacket(
              localRun.sweepId,
              localRun,
              gen,
              state.revision
            ).finally(() => {
              if (localRun.settledReadPending === state.revision) {
                localRun.settledReadPending = null;
              }
            });
          }
        }
      })
      .catch(() => {});
  }

  async function analyzeLatest() {
    const ownerRun = run;
    const sweepId = lastSweepId;
    const frameId = latestFrameId;
    if (!sweepId || !frameId || analysisPending) return;
    analysisPending = true;
    analysisError = null;
    if (ui.analysisPending) ui.analysisPending(true);
    if (ui.setLatestFrameId) ui.setLatestFrameId(null);
    if (ui.setObservation) ui.setObservation({ pending: true }, { sweepId });
    const gen = generation;
    try {
      const reply = await api.analyzeFrame(sweepId, frameId);
      if (run !== ownerRun || generation !== gen) return;
      analysisPending = false;
      if (ui.analysisPending) ui.analysisPending(false);
      lastGoodObs = reply;
      if (ui.setObservation) ui.setObservation(reply, { sweepId });
    } catch (e) {
      if (run !== ownerRun || generation !== gen) return;
      analysisPending = false;
      if (ui.analysisPending) ui.analysisPending(false);
      analysisError = msg(e);
      if (ui.setObservation) {
        ui.setObservation({ error: analysisError, stale: lastGoodObs }, { sweepId });
      }
    } finally {
      if (run === ownerRun && generation === gen && ui.setLatestFrameId) {
        ui.setLatestFrameId(latestFrameId);
      }
    }
  }

  async function readSpines() {
    const ownerRun = run;
    const sweepId = sweepTarget();
    if (!sweepId || readingPending) return;
    if (analysisPending) {
      ui.warn("a frame analysis is still running — wait for it before reading spines");
      return;
    }
    readingPending = true;
    const gen = generation;
    if (ui.readingPending) ui.readingPending(true);
    try {
      const fresh = await api.getSweep(sweepId);
      if (run !== ownerRun || generation !== gen) return;
      if (!acceptRevision(fresh)) return;
      bookTargets = ((fresh && fresh.books) || []).filter((b) => !b.excluded).length;
      if (ui.setInventory) ui.setInventory(fresh, { sweepId });
      if (bookTargets === 0) {
        ui.warn("no book tracks yet — analyze a saved frame before reading spines");
        return;
      }
      const result = await api.readSpines(sweepId, null);
      if (run !== ownerRun || generation !== gen) return;
      if (result && result.skipped) {
        ui.warn(
          "spine reading skipped: " + (result.reason || "no eligible book crops")
        );
      } else {
        ui.log("read_spines", { batches: result.batches, readings: result.ref_ids });
      }
      const state = await api.getSweep(sweepId);
      if (run !== ownerRun || generation !== gen) return;
      if (!acceptRevision(state)) return;
      if (ui.setInventory) ui.setInventory(state, { sweepId });
      if (state.status !== "capturing") refreshPacket(sweepId, ownerRun, gen);
    } catch (e) {
      if (run !== ownerRun || generation !== gen) return;
      ui.warn("spine reading failed: " + msg(e));
    } finally {
      if (run === ownerRun && generation === gen) {
        readingPending = false;
        if (ui.readingPending) ui.readingPending(false);
      }
    }
  }

  async function priceBooks() {
    const ownerRun = run;
    const sweepId = sweepTarget();
    if (!sweepId || pricingPending) return;
    pricingPending = true;
    const gen = generation;
    if (ui.pricingPending) ui.pricingPending(true);
    try {
      const result = await api.priceBooks(sweepId, null);
      if (run !== ownerRun || generation !== gen) return;
      ui.log("price_books", { valuations: Object.keys(result.valuations || {}).length });
      const state = await api.getSweep(sweepId);
      if (run !== ownerRun || generation !== gen) return;
      if (!acceptRevision(state)) return;
      if (ui.setInventory) ui.setInventory(state, { sweepId });
      if (state.status !== "capturing") refreshPacket(sweepId, ownerRun, gen);
    } catch (e) {
      if (run !== ownerRun || generation !== gen) return;
      ui.warn("book pricing failed: " + msg(e));
    } finally {
      if (run === ownerRun && generation === gen) {
        pricingPending = false;
        if (ui.pricingPending) ui.pricingPending(false);
      }
    }
  }

  function acceptRevision(state) {
    const rev = state && state.revision;
    if (typeof rev !== "number") return true;
    if (lastKnownRevision !== null && rev < lastKnownRevision) return false;
    lastKnownRevision = rev;
    return true;
  }

  function sweepTarget() {
    if (run) return run.sweepId;
    return lastSweepId;
  }

  function postworkSettled(localRun) {
    const pw = localRun && localRun.postworkStatus;
    return pw === "completed" || pw === "failed" || pw === "timed_out";
  }

  function sendPacketSummary(localRun, gen, packet) {
    if (!packet || typeof packet.summary !== "string" || !packet.summary) return;
    if (packet.stale) return;
    if (
      lastKnownRevision !== null &&
      typeof packet.revision === "number" &&
      packet.revision < lastKnownRevision
    ) {
      return;
    }
    if (!owned(localRun) || generation !== gen) return;
    if (!localRun.captureStopped || !postworkSettled(localRun)) return;
    const key = localRun.sweepId + ":" + packet.summary;
    if (key === lastPacketSummaryKey) return;
    if (
      localRun.pendingSummary === null ||
      localRun.pendingSummary.key !== key
    ) {
      localRun.pendingSummary = {
        key,
        summary: packet.summary,
        revision: packet.revision,
      };
    }
    tryDispatch(localRun);
  }

  function showPacket(packet, sweepId) {
    if (!ui.setPacket) return;
    if (packet && typeof packet.revision === "number") {
      if (sweepId !== shownPacketSweep) {
        shownPacketSweep = sweepId;
        shownPacketRevision = null;
      }
      if (
        shownPacketRevision !== null &&
        packet.revision < shownPacketRevision
      ) {
        return;
      }
      shownPacketRevision = packet.revision;
      if (
        lastKnownRevision !== null &&
        packet.revision < lastKnownRevision &&
        !packet.stale
      ) {
        packet = Object.assign({}, packet, { stale: true });
      }
    }
    ui.setPacket(packet);
  }

  async function readSettledPacket(sweepId, ownerRun, gen, atRevision) {
    try {
      const packet = await api.getPacket(sweepId);
      if (run !== ownerRun || generation !== gen) return;
      if (
        ownerRun.settledPacketRevision === null ||
        atRevision >= ownerRun.settledPacketRevision
      ) {
        ownerRun.settledPacketRevision = atRevision;
      }
      if (!packet) return;
      showPacket(packet, sweepId);
      if (!packet.stale) sendPacketSummary(ownerRun, gen, packet);
    } catch (e) {
      if (run !== ownerRun || generation !== gen) return;
      ui.log("packet_read_failed", { error: msg(e) });
    }
  }

  async function refreshPacket(sweepId, ownerRun, gen) {
    if (packetPending) return;
    packetPending = true;
    try {
      const packet = await api.packet(sweepId);
      if (run !== ownerRun || generation !== gen) return;
      showPacket(packet, sweepId);
      sendPacketSummary(ownerRun, gen, packet);
    } catch (e) {
      if (run !== ownerRun || generation !== gen) return;
      ui.log("packet_failed", { error: msg(e) });
    } finally {
      if (run === ownerRun && generation === gen) packetPending = false;
    }
  }

  async function publishPacket() {
    const ownerRun = run;
    const sweepId = sweepTarget();
    if (!sweepId || packetPending) return;
    packetPending = true;
    const gen = generation;
    try {
      const packet = await api.packet(sweepId);
      if (run !== ownerRun || generation !== gen) return;
      showPacket(packet, sweepId);
      sendPacketSummary(ownerRun, gen, packet);
    } catch (e) {
      if (run !== ownerRun || generation !== gen) return;
      ui.warn("claim packet failed: " + msg(e));
    } finally {
      if (run === ownerRun && generation === gen) packetPending = false;
    }
  }

  function onSamplerEvent(localRun, gen, e) {
    if (generation !== gen || localRun.stopped) return;
    if (e.event === "freshness_warning") {
      localRun.previewFrozen = true;
      ui.warn("preview frames are not advancing — captured image may be repeated");
      if (localRun.guidance && localRun.lastFrame && typeof localRun.lastFrame.id === "string") {
        const now = clock();
        const maxAge =
          localRun.guidancePolicy &&
          Number.isFinite(localRun.guidancePolicy.maximum_event_age_ms)
            ? localRun.guidancePolicy.maximum_event_age_ms
            : 3000;
        localRun.guidanceEvents.set("preview", {
          id: "preview_" + localRun.sweepId,
          frame_id: localRun.lastFrame.id,
          observed_at_ms: now,
          expires_at_ms: now + maxAge,
          row_id: null,
          issue: "preview_not_advancing",
          severity: "warning",
        });
        refreshGuidance(localRun);
      }
    }
    if (e.event === "sampling_tick" && !e.skip_reason) {
      localRun.previewFrozen = false;
      if (localRun.guidanceEvents.delete("preview")) refreshGuidance(localRun);
    }
    ui.log(e.event, e);
  }

  async function start() {
    if (run && !run.captureStopped) return;
    if (run && run.captureStopped) endCall();
    const gen = ++generation;
    const localRun = {
      abort: new AbortController(),
      stream: null,
      peer: null,
      channel: null,
      sampler: null,
      poller: null,
      sweepId: null,
      t0: null,
      stopped: false,
      greetingSent: false,
      captureStopped: false,
      endRequested: false,
      turnIds: new Set(),
      turnsPending: new Set(),
      committedIds: new Set(),
      activeUserItemId: null,
      utteranceEpoch: 0,
      speechActive: false,
      viewSeq: 0,
      viewCtl: null,
      viewPolicy: null,
      imagesSent: 0,
      lastImageAt: null,
      lastViewFrameId: null,
      knownTargets: {},
      lastFrame: null,
      latestState: null,
      lastAppStateSignature: null,
      previewFrozen: false,
      responseActive: false,
      pendingResponseEpoch: null,
      pendingResponseKind: null,
      postworkStatus: "not_started",
      settledPacketRevision: null,
      settledReadPending: null,
      voiceAvailable: true,
      responseKind: null,
      activeResponseId: null,
      responseKinds: new Map(),
      playingIds: new Map(),
      viewPrep: new Set(),
      toolPrep: new Set(),
      toolPrepSeq: 0,
      requestSeq: 0,
      awaitingCreate: new Map(),
      activeRequestId: null,
      retiredResponses: new Set(),
      pendingSummary: null,
      guidanceEventSeq: 0,
      guidance: null,
      guidancePolicy: null,
      guidanceEvents: new Map(),
      guidanceIssueSeen: new Map(),
    };
    run = localRun;
    const signal = localRun.abort.signal;
    latestFrameId = null;
    lastGoodObs = null;
    bookTargets = -1;
    analysisPending = false;
    analysisError = null;
    if (ui.analysisPending) ui.analysisPending(false);
    readingPending = false;
    if (ui.readingPending) ui.readingPending(false);
    pricingPending = false;
    if (ui.pricingPending) ui.pricingPending(false);
    packetPending = false;
    lastPacketSummaryKey = null;
    lastKnownRevision = null;
    shownPacketSweep = null;
    shownPacketRevision = null;
    if (ui.setPacket) ui.setPacket(null);
    if (ui.setLatestFrameId) ui.setLatestFrameId(null);
    if (ui.setObservation) ui.setObservation(null, {});
    fields.setEnabled(false);
    if (controls) controls.setRunning(true);
    ui.setStatus("requesting camera + microphone...");
    ui.setStage("starting");
    let stream;
    try {
      stream = await media.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: { ideal: "environment" } },
        audio: true,
      });
    } catch (e) {
      if (live(gen)) {
        ui.setStatus("device error: " + msg(e), "error");
        finishDead(localRun);
      }
      return;
    }
    if (!live(gen)) {
      stream.getTracks().forEach((t) => {
        try { t.stop(); } catch (e) {}
      });
      return;
    }
    localRun.stream = stream;
    let state;
    try {
      const { country, currency } = locale();
      state = await api.createSweep({ country, currency, device: device() }, signal);
    } catch (e) {
      if (live(gen)) {
        ui.setStatus("could not create sweep: " + msg(e), "error");
        finishDead(localRun);
      }
      return;
    }
    if (!live(gen)) {
      interruptLate(state && state.id);
      return;
    }
    localRun.sweepId = state.id;
    lastSweepId = state.id;
    ui.setSweepId(state.id);
    storage.set("sweepId", state.id);
    ui.setStage(state.status);
    preview.srcObject = stream;
    try {
      await waitForMetadata(signal, 8000);
      if (!live(gen)) return;
      await preview.play();
    } catch (e) {
      if (live(gen)) {
        ui.setStatus("camera preview failed: " + msg(e), "error");
        finishDead(localRun);
      }
      return;
    }
    if (!live(gen)) return;
    localRun.t0 = clock();
    localRun.sampler = samplerFactory({
      video: preview,
      upload: (blob, ts, sig) => api.uploadFrame(localRun.sweepId, blob, ts, sig),
      onEvent: (e) => onSamplerEvent(localRun, gen, e),
    });
    localRun.sampler.start();
    localRun.poller = timers.setInterval(() => poll(localRun, gen), 1000);
    if (typeof api.captureConfig === "function") {
      try {
        const cfg = await api.captureConfig(signal);
        if (!live(gen)) return;
        localRun.viewPolicy = (cfg && cfg.visual_context) || null;
        localRun.guidancePolicy = (cfg && cfg.guidance) || null;
        localRun.guidance = createGuidanceScheduler({
          clock,
          policy: localRun.guidancePolicy,
        });
      } catch (e) {
        if (!live(gen)) return;
        localRun.viewPolicy = null;
        localRun.guidance = createGuidanceScheduler({ clock, policy: null });
      }
    }
    if (controls && controls.setVoiceActive) controls.setVoiceActive(true);
    ui.setStatus("sweep running; setting up voice...");
    try {
      await setupVoice(localRun);
      if (!live(gen)) return;
      ui.setStatus("sweep running: camera + voice connected");
      maybeGreet(localRun);
    } catch (e) {
      if (!live(gen)) return;
      closeVoice(localRun);
      localRun.voiceAvailable = false;
      if (controls && controls.setVoiceActive) controls.setVoiceActive(false);
      ui.log("setup_failed", { stage: "voice", error: msg(e) });
      ui.setStatus("PARTIAL MODE: camera only — voice unavailable: " + msg(e), "partial");
      ui.warn("voice not connected; capture continues without audio");
    }
  }

  function stop() {
    const localRun = run;
    if (!localRun || localRun.captureStopped) return;
    if (localRun.t0 === null) {
      const pendingSweep = localRun.sweepId;
      generation += 1;
      cleanup(localRun);
      run = null;
      fields.setEnabled(true);
      if (controls) controls.setRunning(false);
      ui.setStage("ended");
      if (!pendingSweep) {
        ui.setStatus("stopped");
        return;
      }
      api.endSweep(pendingSweep, 0, { keepalive: true })
        .then((state) => {
          if (run) return;
          ui.setStage(state.status);
          if (state && state.frames) ui.setFrameCount(state.frames.length);
          ui.setStatus("stopped — sweep ended; press Start for a new sweep");
        })
        .catch(() => {
          if (run) return;
          ui.warn("sweep end could not be saved — the server may still show this sweep as capturing");
          ui.setStatus("stopped — END NOT SAVED", "error");
        });
      return;
    }
    const gen = generation;
    const sweepId = localRun.sweepId;
    const duration = Math.max(0, (clock() - localRun.t0) / 1000);
    localRun.captureStopped = true;
    localRun.viewSeq += 1;
    localRun.guidanceEvents.clear();
    if (localRun.viewCtl) {
      try { localRun.viewCtl.abort(); } catch (e) {}
      localRun.viewCtl = null;
    }
    if (localRun.sampler) {
      localRun.sampler.stop();
      localRun.sampler = null;
    }
    if (localRun.stream) {
      localRun.stream.getVideoTracks().forEach((t) => {
        try { t.stop(); } catch (e) {}
      });
    }
    preview.srcObject = null;
    if (controls) controls.setRunning(false);
    if (controls && controls.setVoiceActive) controls.setVoiceActive(true);
    ui.setStage("ended");
    if (!sweepId) {
      ui.setStatus("stopped");
      return;
    }
    localRun.endRequested = true;
    api.endSweep(sweepId, duration, { keepalive: true })
      .then((state) => {
        if (generation !== gen || run !== localRun) return;
        if (!acceptRevision(state)) return;
        ui.setStage(stageLabel(state));
        if (state && state.frames) ui.setFrameCount(state.frames.length);
        bookTargets = ((state && state.books) || []).filter((b) => !b.excluded).length;
        if (ui.setInventory) ui.setInventory(state, { sweepId: state.id });
        if (state && typeof state.postwork_status === "string") {
          localRun.postworkStatus = state.postwork_status;
        }
        if (state.packet_error) {
          ui.warn("claim packet failed: " + state.packet_error);
        }
        if (state.packet) {
          showPacket(state.packet, sweepId);
          sendPacketSummary(localRun, gen, state.packet);
        }
        ui.setStatus(
          localRun.voiceAvailable
            ? "sweep ended — voice still active for processing; press End call to finish"
            : "sweep ended — voice unavailable; press End call to finish"
        );
      })
      .catch(() => {
        if (generation !== gen || run !== localRun) return;
        ui.warn("sweep end could not be saved — the server may still show this sweep as capturing");
        ui.setStatus("stopped — END NOT SAVED", "error");
      });
  }

  function endCall() {
    const localRun = run;
    if (!localRun) return;
    generation += 1;
    const gen = generation;
    let endSaveFailed = false;
    if (localRun.sweepId && !localRun.endRequested) {
      localRun.endRequested = true;
      if (localRun.t0 !== null && !localRun.captureStopped) {
        const duration = Math.max(0, (clock() - localRun.t0) / 1000);
        const endFailed = () => {
          if (generation !== gen || run !== null) return;
          ui.warn("sweep end could not be saved — the server may still show this sweep as capturing");
          ui.setStatus("call ended — END NOT SAVED", "error");
        };
        try {
          const pending = api.endSweep(localRun.sweepId, duration, { keepalive: true });
          if (pending && typeof pending.catch === "function") pending.catch(endFailed);
        } catch (e) {
          endSaveFailed = true;
        }
      } else {
        interruptLate(localRun.sweepId);
      }
    }
    cleanup(localRun);
    run = null;
    analysisPending = false;
    if (ui.analysisPending) ui.analysisPending(false);
    readingPending = false;
    if (ui.readingPending) ui.readingPending(false);
    pricingPending = false;
    if (ui.pricingPending) ui.pricingPending(false);
    packetPending = false;
    lastPacketSummaryKey = null;
    lastKnownRevision = null;
    shownPacketSweep = null;
    shownPacketRevision = null;
    fields.setEnabled(true);
    if (controls) {
      controls.setRunning(false);
      if (controls.setVoiceActive) controls.setVoiceActive(false);
    }
    if (endSaveFailed) {
      ui.warn("sweep end could not be saved — the server may still show this sweep as capturing");
      ui.setStatus("call ended — END NOT SAVED", "error");
    } else {
      ui.setStatus("call ended — saved sweep preserved; press Start for a new sweep");
    }
  }

  async function sendTurn(text) {
    const localRun = run;
    if (!localRun || !localRun.sweepId || !text || !text.trim()) return;
    const gen = generation;
    const turnId = newTurnId();
    try {
      await api.addTurn(localRun.sweepId, { turn_id: turnId, text: text, source: "typed_user" });
    } catch (e) {
      if (owned(localRun)) ui.log("turn_failed", { turn_id: turnId, error: msg(e) });
      return;
    }
    if (generation !== gen || localRun.stopped) return;
    localRun.turnIds.add(turnId);
    ui.log("turn_saved", { turn_id: turnId });
    if (localRun.channel && localRun.channel.readyState === "open") {
      const bytes = sendChannelJSON(localRun, JSON.stringify({
        type: "conversation.item.create",
        item: {
          id: turnId,
          type: "message",
          role: "user",
          content: [{ type: "input_text", text: text }],
        },
      }));
      if (bytes >= 0) {
        ui.log("turn_forwarded", { turn_id: turnId });
        await prepareVisualTurn(localRun, turnId);
      }
    }
  }

  function pagehide() {
    const localRun = run;
    if (!localRun) return;
    generation += 1;
    const sweepId = localRun.sweepId;
    const captureStarted = localRun.t0 !== null && !localRun.captureStopped;
    const duration = captureStarted
      ? Math.max(0, (clock() - localRun.t0) / 1000)
      : 0;
    cleanup(localRun);
    run = null;
    if (sweepId && !localRun.endRequested) {
      localRun.endRequested = true;
      try {
        if (captureStarted) {
          const pending = api.endSweep(sweepId, duration, { keepalive: true });
          if (pending && typeof pending.catch === "function") pending.catch(() => {});
        } else {
          api.interruptSweep(sweepId);
        }
      } catch (e) {}
    }
  }

  async function restoreSaved() {
    const id = storage.get("sweepId");
    if (!id) return;
    const gen = generation;
    try {
      const state = await api.getSweep(id);
      if (generation !== gen || run) return;
      if (!acceptRevision(state)) return;
      lastSweepId = state.id;
      latestFrameId = state.frames.length
        ? state.frames[state.frames.length - 1].id
        : null;
      bookTargets = (state.books || []).filter((b) => !b.excluded).length;
      lastGoodObs = state.latest_observation || null;
      ui.setSweepId(state.id);
      ui.setFrameCount(state.frames.length);
      if (ui.setLatestFrameId) ui.setLatestFrameId(latestFrameId);
      if (ui.setInventory) ui.setInventory(state, { sweepId: state.id });
      if (ui.setObservation && !analysisPending && analysisError === null) {
        ui.setObservation(lastGoodObs, { sweepId: state.id });
      }
      ui.setStage(stageLabel(state));
      if (state.packet_ref && ui.setPacket && typeof api.getPacket === "function") {
        api.getPacket(state.id).then((packet) => {
          if (generation !== gen || run) return;
          if (packet) showPacket(packet, state.id);
        }).catch(() => {});
      }
      ui.setStatus(
        "previous sweep " +
          state.status +
          (state.postwork_status === "running" ? " — processing saved sweep" : "") +
          " — press Start for a new sweep"
      );
      if (state.postwork_status === "running") {
        const settlePoll = timers.setInterval(async () => {
          try {
            const fresh = await api.getSweep(id);
            if (generation !== gen || run) {
              timers.clearInterval(settlePoll);
              return;
            }
            if (!acceptRevision(fresh)) return;
            ui.setStage(stageLabel(fresh));
            if (ui.setInventory) ui.setInventory(fresh, { sweepId: id });
            if (fresh.postwork_status !== "running") {
              timers.clearInterval(settlePoll);
              ui.setStage(stageLabel(fresh));
              if (fresh.packet_ref && ui.setPacket && typeof api.getPacket === "function") {
                try {
                  const packet = await api.getPacket(id);
                  if (generation !== gen || run) return;
                  if (packet) showPacket(packet, id);
                } catch (e) {}
              }
              ui.setStatus(
                "previous sweep " +
                  fresh.status +
                  " — processing " +
                  fresh.postwork_status +
                  "; press Start for a new sweep"
              );
            }
          } catch (e) {}
        }, 2000);
      }
    } catch (e) {}
  }

  return { start, stop, endCall, sendTurn, pagehide, restoreSaved, analyzeLatest, readSpines, priceBooks, publishPacket };
}
