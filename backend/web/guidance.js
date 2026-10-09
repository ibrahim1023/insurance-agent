const SEVERITIES = { warning: 0, critical: 1 };

export function createGuidanceScheduler({ clock, policy }) {
  const enabled = !!(
    policy &&
    policy.enabled === true &&
    Number.isInteger(policy.maximum_responses_per_sweep) &&
    policy.maximum_responses_per_sweep > 0 &&
    Number.isFinite(policy.minimum_response_interval_ms) &&
    policy.minimum_response_interval_ms >= 0 &&
    Number.isFinite(policy.maximum_event_age_ms) &&
    policy.maximum_event_age_ms >= 0
  );
  const episodes = new Map();
  let dispatched = 0;
  let lastDispatchAt = null;
  let stopped = false;

  const keyOf = (e) =>
    e.issue + "|" + (e.row_id === undefined || e.row_id === null ? "" : String(e.row_id));

  function validEvent(e, now) {
    return !!(
      e &&
      typeof e.id === "string" &&
      e.id &&
      typeof e.frame_id === "string" &&
      e.frame_id &&
      typeof e.issue === "string" &&
      e.issue &&
      SEVERITIES[e.severity] !== undefined &&
      Number.isFinite(e.observed_at_ms) &&
      Number.isFinite(e.expires_at_ms) &&
      e.observed_at_ms <= now &&
      e.expires_at_ms >= e.observed_at_ms
    );
  }

  function update(events) {
    if (stopped) return;
    const now = clock();
    const fresh = new Map();
    for (const e of Array.isArray(events) ? events : []) {
      if (!validEvent(e, now)) continue;
      const k = keyOf(e);
      const prev = fresh.get(k);
      if (!prev || e.observed_at_ms >= prev.observed_at_ms) fresh.set(k, e);
    }
    for (const k of [...episodes.keys()]) {
      if (!fresh.has(k)) episodes.delete(k);
    }
    for (const [k, e] of fresh) {
      const ep = episodes.get(k);
      if (ep) {
        ep.event = e;
        ep.firstSeenAt = Math.min(ep.firstSeenAt, e.observed_at_ms);
        if (e.severity === "critical") ep.severity = "critical";
      } else {
        episodes.set(k, {
          issue: e.issue,
          row_id: e.row_id === undefined ? null : e.row_id,
          severity: e.severity,
          event: e,
          dispatched: false,
          firstSeenAt: e.observed_at_ms,
        });
      }
    }
  }

  function next(context) {
    if (stopped || !enabled) return null;
    const ctx = context || {};
    if (
      ctx.speechActive ||
      ctx.responseActive ||
      ctx.playbackActive ||
      ctx.userTurnPending ||
      !ctx.voiceAvailable ||
      !ctx.captureActive
    ) {
      return null;
    }
    if (dispatched >= policy.maximum_responses_per_sweep) return null;
    const now = clock();
    if (
      lastDispatchAt !== null &&
      now - lastDispatchAt < policy.minimum_response_interval_ms
    ) {
      return null;
    }
    const ready = [...episodes.values()].filter(
      (ep) =>
        !ep.dispatched &&
        ep.event.expires_at_ms > now &&
        now - ep.event.observed_at_ms <= policy.maximum_event_age_ms
    );
    if (!ready.length) return null;
    ready.sort(
      (a, b) =>
        SEVERITIES[b.severity] - SEVERITIES[a.severity] ||
        a.firstSeenAt - b.firstSeenAt ||
        String(a.event.id).localeCompare(String(b.event.id))
    );
    const ep = ready[0];
    ep.dispatched = true;
    dispatched += 1;
    lastDispatchAt = now;
    return ep.event;
  }

  function stop() {
    stopped = true;
    episodes.clear();
  }

  return { update, next, stop };
}
