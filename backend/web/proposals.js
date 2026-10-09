const VIEW_LABEL = "Current-view proposals — not final room count";

export function observationView(reply) {
  if (reply === null || reply === undefined) {
    return { state: "idle", label: VIEW_LABEL };
  }
  if (reply.error) {
    return {
      state: "error",
      label: VIEW_LABEL,
      message: String(reply.error),
      stale: reply.stale || null,
    };
  }
  if (reply.pending) {
    return { state: "pending", label: VIEW_LABEL };
  }
  const scene = reply.observation || {};
  const mapping = reply.box_mapping || {};
  const books = (scene.books || []).map((book, index) => ({
    index: index + 1,
    title: book.observed_title || "(title not legible)",
    author: book.observed_author || "(author not legible)",
    orientation: book.orientation || "unclear",
    existence_confidence: book.existence_confidence,
    text_confidence: book.text_confidence,
    title_legible: !!book.title_legible,
    author_legible: !!book.author_legible,
    flags: book.special_flags || [],
    accepted: (mapping.accepted_indices || []).includes(index),
    box: (mapping.boxes || [])[(mapping.accepted_indices || []).indexOf(index)] || null,
  }));
  const items = (scene.items || []).map((item) => ({
    category: item.category || "other",
    description: item.description || "",
    material: item.material || "",
    brand_model: item.brand_model || "",
    confidence: item.confidence,
  }));
  const unresolved = (scene.unresolved_groups || []).map((group) => ({
    reason: group.reason || "",
  }));
  const issues = (scene.capture_issues || []).map((issue) => ({
    type: issue.type || "region_unclear",
    description: issue.description || "",
  }));
  return {
    state: "ready",
    label: VIEW_LABEL,
    frame_id: reply.frame_id,
    source_frame_id: reply.source_frame_id || reply.frame_id,
    cached: !!reply.cached,
    stale: !!reply.stale,
    model: reply.model || null,
    cost_usd_estimate: reply.cost_usd_estimate || null,
    image_width: reply.image_width || null,
    image_height: reply.image_height || null,
    coordinate_system: reply.coordinate_system || null,
    books,
    items,
    unresolved,
    issues,
    proposal_count: mapping.proposal_count || 0,
  };
}

function line(el, tag, text) {
  const node = el(tag);
  node.textContent = text;
  return node;
}

export function drawOverlay(img, canvas, view) {
  const draw = () => {
    const w = img.clientWidth || img.naturalWidth || 0;
    const h = img.clientHeight || img.naturalHeight || 0;
    if (!w || !h || !view.image_width || !view.image_height) return;
    canvas.width = w;
    canvas.height = h;
    canvas.style.position = "absolute";
    canvas.style.left = "0";
    canvas.style.top = "0";
    const ctx = canvas.getContext && canvas.getContext("2d");
    if (!ctx) return;
    const sx = w / view.image_width;
    const sy = h / view.image_height;
    view.books.forEach((book) => {
      if (!book.box) return;
      const [x, y, bw, bh] = book.box;
      ctx.strokeRect(x * sx, y * sy, bw * sx, bh * sy);
      ctx.fillText(String(book.index), x * sx + 2, y * sy + 12);
    });
  };
  if (img.complete && img.naturalWidth) {
    draw();
  } else if (img.addEventListener) {
    img.addEventListener("load", draw, { once: true });
  }
}

function renderReady(container, view, el, deps) {
  if (view.stale) {
    container.appendChild(
      line(el, "div", "earlier frame observation — not the latest captured frame")
    );
  }
  if (view.books.length === 0) {
    container.appendChild(line(el, "div", "no book proposals in this frame"));
  }
  view.books.forEach((book) => {
    const conf = (v) => (v === null || v === undefined ? "?" : Number(v).toFixed(2));
    container.appendChild(
      line(
        el,
        "div",
        `book ${book.index}: ${book.title} / ${book.author} — ` +
          `orientation ${book.orientation}, existence ${conf(book.existence_confidence)}, ` +
          `text ${conf(book.text_confidence)}` +
          (book.flags.length ? `, flags ${book.flags.join("+")}` : "") +
          (book.accepted ? "" : " [below confidence]")
      )
    );
  });
  view.items.forEach((item) => {
    container.appendChild(
      line(
        el,
        "div",
        `item: ${item.category} — ${item.description}` +
          (item.material ? ` (${item.material})` : "") +
          (item.brand_model ? ` [${item.brand_model}]` : "")
      )
    );
  });
  view.unresolved.forEach((group) => {
    container.appendChild(line(el, "div", `unresolved group: ${group.reason}`));
  });
  view.issues.forEach((issue) => {
    container.appendChild(line(el, "div", `capture issue: ${issue.type} — ${issue.description}`));
  });
  const meta = [];
  if (view.model) meta.push("model " + view.model);
  if (view.cost_usd_estimate) {
    meta.push(
      view.cached
        ? `cost est. $${view.cost_usd_estimate} (original call — no new charge)`
        : "cost est. $" + view.cost_usd_estimate + " (usage-based estimate)"
    );
  }
  if (view.cached) meta.push("reused observation of " + view.source_frame_id);
  meta.push("frame " + view.frame_id);
  container.appendChild(line(el, "div", meta.join(" · ")));
  if (deps.frameUrl) {
    const wrap = el("div");
    if (wrap.style) wrap.style.position = "relative";
    const img = el("img");
    img.src = deps.frameUrl(view.source_frame_id);
    img.alt = "analyzed frame";
    const canvas = el("canvas");
    wrap.appendChild(img);
    wrap.appendChild(canvas);
    container.appendChild(wrap);
    drawOverlay(img, canvas, view);
    const link = el("a");
    link.setAttribute("href", deps.frameUrl(view.source_frame_id));
    link.textContent = "source frame";
    container.appendChild(link);
  }
}

export function renderInventory(container, state, deps) {
  const el = deps.el;
  container.textContent = "";
  container.appendChild(
    line(el, "div", "Provisional inventory — recorded tracks, not a final count")
  );
  const books = (state && state.books) || [];
  const items = (state && state.items) || [];
  const reviews = (state && state.review_queue) || [];
  const frameUrl = deps && deps.frameUrl;
  const cropUrl = deps && deps.cropUrl;
  container.appendChild(
    line(
      el,
      "div",
      state && state.inventory_complete
        ? "inventory marked complete"
        : "inventory not complete — provisional"
    )
  );
  const recorded = books.length;
  const excluded = books.filter((b) => b.excluded).length;
  const identified = books.filter((b) => b.status === "identified").length;
  const unidentified = books.filter((b) => b.status === "unidentified").length;
  const appraisal = books.filter((b) => b.status === "needs_appraisal").length;
  container.appendChild(
    line(
      el,
      "div",
      `books: ${recorded} recorded — ${recorded - excluded} active, ` +
        `${excluded} excluded, ${identified} identified, ` +
        `${unidentified} unidentified, ${appraisal} appraisal`
    )
  );
  const duplicates =
    books.filter((b) => b.potential_duplicate).length +
    items.filter((i) => i.potential_duplicate).length;
  if (duplicates) {
    container.appendChild(
      line(el, "div", `warning: ${duplicates} possible duplicate track(s)`)
    );
  }
  if (!books.length && !items.length) {
    container.appendChild(
      line(el, "div", "no recorded tracks yet — analysis runs only when requested")
    );
  }
  const evidenceLink = (track) => {
    if (!frameUrl) return;
    const evidence = track.evidence || [];
    const last = evidence.length ? evidence[evidence.length - 1] : null;
    const frameId = last ? last.frame_id : null;
    if (!frameId) return;
    const link = el("a");
    link.setAttribute("href", frameUrl(frameId));
    link.textContent = "frame evidence";
    container.appendChild(link);
  };
  books.forEach((book, index) => {
    const bits = [
      `book ${index + 1} [${book.id}]`,
      `shelf ${book.shelf || "unassigned"}`,
      book.position ? `pos ${book.position}` : "pos unassigned",
      book.status,
      book.title || "(unidentified)",
    ];
    if (book.excluded) bits.push("excluded");
    if (book.potential_duplicate) bits.push("possible duplicate");
    const row = line(el, "div", bits.join(" — "));
    container.appendChild(row);
    evidenceLink(book);
    (book.crop_refs || []).forEach((ref) => {
      if (!cropUrl) return;
      const link = el("a");
      link.setAttribute("href", cropUrl(ref));
      link.textContent = "crop " + ref.split("/").pop();
      container.appendChild(link);
    });
    const safeLink = (url) => {
      if (typeof url !== "string" || !url.startsWith("https://")) return null;
      const link = el("a");
      link.setAttribute("href", url);
      link.setAttribute("rel", "noopener noreferrer");
      link.setAttribute("target", "_blank");
      link.textContent = url;
      return link;
    };
    const priceLine = (label, price) => {
      const row = el("div");
      const bits = [];
      const quote = (price && price.source_quote) || {};
      const fx = (price && price.fx_evidence) || {};
      if (!price || price.amount === null || price.amount === undefined) {
        bits.push(`${label}: unsupported`);
      } else {
        bits.push(`${label}: ${price.amount} ${price.currency || ""}`.trim());
      }
      if (price && price.foreign_market_fallback) bits.push("foreign market");
      if (price && price.foreign_market_same_currency) bits.push("same currency");
      if (price && price.converted) bits.push("converted");
      if (quote.market_country) bits.push(`market ${quote.market_country}`);
      if (quote.condition) bits.push(`condition ${quote.condition}`);
      if (price && price.edition_equivalence) {
        bits.push(String(price.edition_equivalence).replaceAll("_", " "));
      }
      if (
        quote.amount !== null &&
        quote.amount !== undefined &&
        quote.currency
      ) {
        bits.push(`source amount ${quote.amount} ${quote.currency}`);
      }
      const when = (price && price.retrieved_at) || quote.retrieved_at;
      if (when) bits.push(`retrieved ${when}`);
      if (fx.cross_rate) bits.push(`FX rate ${fx.cross_rate}`);
      if (fx.data_at) bits.push(`FX data ${fx.data_at}`);
      if (fx.attribution) bits.push(`FX ${fx.attribution}`);
      const url = (price && price.url) || quote.url;
      if (url) bits.push(`source ${url}`);
      row.textContent = bits.join(" — ");
      if (url) {
        const link = safeLink(url);
        if (link) row.appendChild(link);
      }
      const fxUrl = fx.attribution_url || fx.source_url;
      if (fxUrl) {
        const link = safeLink(fxUrl);
        if (link) row.appendChild(link);
      }
      container.appendChild(row);
    };
    priceLine("new", book.replacement_cost);
    priceLine("used", book.used_value);
  });
  items.forEach((item, index) => {
    const bits = [
      `item ${index + 1} [${item.id}]`,
      item.category,
      item.status,
      item.valuation_state || "unpriced",
      item.description || "",
    ];
    if (item.excluded) bits.push("excluded");
    if (item.potential_duplicate) bits.push("possible duplicate");
    container.appendChild(line(el, "div", bits.join(" — ")));
    evidenceLink(item);
  });
  reviews.forEach((review) => {
    container.appendChild(
      line(el, "div", `review: ${review.ref_id} — ${review.reason}`)
    );
  });
}

export function renderObservation(container, reply, deps) {
  const el = deps.el;
  container.textContent = "";
  const view = observationView(reply);
  container.appendChild(line(el, "div", view.label));
  if (view.state === "idle") {
    container.appendChild(line(el, "div", "No observation yet — analysis runs only when requested."));
    return view;
  }
  if (view.state === "pending") {
    container.appendChild(line(el, "div", "analysis in progress..."));
    return view;
  }
  if (view.state === "error") {
    container.appendChild(line(el, "div", "observation unavailable: " + view.message));
    if (view.stale) {
      const staleView = observationView(view.stale);
      if (staleView.state === "ready") {
        container.appendChild(
          line(el, "div", "last successful observation — not the current frame:")
        );
        renderReady(container, staleView, el, deps);
      }
    }
    return view;
  }
  renderReady(container, view, el, deps);
  return view;
}
