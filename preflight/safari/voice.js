export function waitForConnection(peer, { signal, timeoutMs = 15000 } = {}) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => {
      clearTimeout(timer);
      peer.removeEventListener("connectionstatechange", onState);
      if (signal) signal.removeEventListener("abort", onAbort);
    };
    const settle = (fn, value) => {
      if (settled) return;
      settled = true;
      cleanup();
      fn(value);
    };
    const onState = () => {
      const state = peer.connectionState;
      if (state === "connected") {
        settle(resolve);
      } else if (state === "failed" || state === "closed") {
        settle(reject, new Error("peer " + state));
      }
    };
    const onAbort = () => settle(reject, new Error("aborted"));
    const timer = setTimeout(
      () => settle(reject, new Error("connection timeout")),
      timeoutMs
    );
    peer.addEventListener("connectionstatechange", onState);
    if (signal) {
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort);
    }
    onState();
  });
}
