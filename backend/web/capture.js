export function createSampler({
  video,
  canvas,
  upload,
  onEvent = () => {},
  clock = () => 0,
  setInterval: setInt,
  clearInterval: clearInt,
  intervalMs = 1000,
}) {
  let timer = null;
  let controller = null;
  let inFlight = false;
  let lastTime = null;
  let generation = 0;

  const emit = (event, facts) => onEvent(Object.assign({ t_ms: clock(), event }, facts || {}));

  async function captureOnce(myGeneration, signal) {
    const live = () => myGeneration === generation && !signal.aborted;
    const emitLive = (event, facts) => {
      if (live()) emit(event, facts);
    };
    const ts = video.currentTime;
    if (!live()) return;
    try {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
    } catch (e) {
      emitLive("upload_failed", { error: "draw_failed: " + ((e && e.message) || e) });
      return;
    }
    let blob;
    const encodeStart = clock();
    try {
      blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
    } catch (e) {
      emitLive("upload_failed", { error: "encode_failed: " + ((e && e.message) || e) });
      return;
    }
    const encodeMs = clock() - encodeStart;
    if (!live()) return;
    if (!blob) {
      emitLive("upload_failed", { error: "encode_failed" });
      return;
    }
    const encodedBytes =
      typeof blob.size === "number"
        ? blob.size
        : typeof blob.byteLength === "number"
          ? blob.byteLength
          : null;
    emitLive("upload_started", {
      video_time_s: ts,
      width: canvas.width,
      height: canvas.height,
      encoded_bytes: encodedBytes,
      encode_ms: encodeMs,
    });
    if (!live()) return;
    const uploadStart = clock();
    try {
      const reply = await upload(blob, ts, signal);
      emitLive("upload_success", {
        video_time_s: ts,
        upload_ms: clock() - uploadStart,
        encoded_bytes: encodedBytes,
        frame_id: reply && typeof reply.frame_id === "string" ? reply.frame_id : null,
        frame_ref: reply && typeof reply.frame_ref === "string" ? reply.frame_ref : null,
      });
    } catch (e) {
      if (signal.aborted || (e && e.name === "AbortError")) return;
      emitLive("upload_failed", { video_time_s: ts, error: String((e && e.message) || e) });
    }
  }

  function emitSettings() {
    try {
      const src = video && video.srcObject;
      const track =
        src && typeof src.getVideoTracks === "function"
          ? src.getVideoTracks()[0]
          : null;
      const raw =
        track && typeof track.getSettings === "function"
          ? track.getSettings()
          : null;
      if (!raw || typeof raw !== "object") return;
      const settings = {};
      for (const k of ["width", "height", "frameRate", "aspectRatio", "facingMode"]) {
        if (raw[k] !== undefined) settings[k] = raw[k];
      }
      emit("capture_settings", settings);
    } catch (e) {}
  }

  function tick() {
    const facts = { video_time_s: video.currentTime, ready_state: video.readyState };
    if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
      emit("sampling_tick", { ...facts, skip_reason: "preview_not_ready" });
      return;
    }
    if (lastTime !== null && video.currentTime <= lastTime) {
      emit("sampling_tick", { ...facts, skip_reason: "preview_time_not_advancing" });
      emit("freshness_warning", { video_time_s: video.currentTime });
      return;
    }
    lastTime = video.currentTime;
    if (inFlight) {
      emit("sampling_tick", { ...facts, skip_reason: "upload_in_flight" });
      return;
    }
    emit("sampling_tick", facts);
    inFlight = true;
    const myGeneration = generation;
    const signal = controller.signal;
    Promise.resolve()
      .then(() => captureOnce(myGeneration, signal))
      .finally(() => {
        if (myGeneration === generation) inFlight = false;
      });
  }

  function start() {
    stop();
    generation += 1;
    controller = new AbortController();
    lastTime = null;
    inFlight = false;
    emitSettings();
    timer = setInt(tick, intervalMs);
  }

  function stop() {
    generation += 1;
    if (timer !== null) {
      clearInt(timer);
      timer = null;
    }
    if (controller) {
      controller.abort();
      controller = null;
    }
    inFlight = false;
  }

  return { start, stop };
}
