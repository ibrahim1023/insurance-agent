import assert from "node:assert/strict";
import test from "node:test";
import { observationView, renderInventory, renderObservation } from "../../backend/web/proposals.js";

const LABEL = "Current-view proposals — not final room count";

function reply(overrides = {}) {
  return {
    frame_id: "frame_" + "1".repeat(32),
    source_frame_id: "frame_" + "1".repeat(32),
    observation: {
      books: [
        {
          bbox: { left: 100, top: 100, right: 300, bottom: 900 },
          orientation: "upright",
          observed_title: "Test Volume",
          observed_author: "Test Author",
          observed_publisher: "",
          observed_edition: "",
          observed_isbn: "",
          title_legible: true,
          author_legible: true,
          existence_confidence: 0.95,
          text_confidence: 0.9,
          special_flags: [],
          visible_evidence: "visible printed spine",
        },
      ],
      items: [
        {
          bbox: { left: 0, top: 0, right: 50, bottom: 50 },
          category: "shelving",
          description: "wooden shelf",
          material: "wood",
          brand_model: "",
          confidence: 0.8,
        },
      ],
      unresolved_groups: [{ bbox: { left: 0, top: 0, right: 10, bottom: 10 }, reason: "stack overlap" }],
      capture_issues: [{ type: "blur", description: "motion blur lower third" }],
    },
    box_mapping: { boxes: [[1, 1, 4, 10]], accepted_indices: [0], rejected_indices: [], proposal_count: 1 },
    stage_ref: "stage_outputs/vision_x.json",
    model: "gpt-5.4-2026-03-05",
    cost_usd_estimate: "0.003775",
    elapsed_ms: 12.5,
    usage: { input_tokens: 1000, output_tokens: 100 },
    image_width: 16,
    image_height: 12,
    sent_image_width: 16,
    sent_image_height: 12,
    coordinate_system: "normalized_1000",
    cached: false,
    ...overrides,
  };
}

function makeEl() {
  const el = (tag) => {
    const node = {
      tag,
      children: [],
      attrs: {},
      _text: "",
      appendChild(child) {
        this.children.push(child);
      },
      setAttribute(name, value) {
        this.attrs[name] = value;
      },
      set textContent(value) {
        this._text = String(value);
      },
      get textContent() {
        return this._text;
      },
    };
    return node;
  };
  return { el };
}

function makeContainer() {
  return {
    children: [],
    _html: undefined,
    appendChild(child) {
      this.children.push(child);
    },
    set innerHTML(value) {
      this._html = value;
    },
    set textContent(value) {
      this.children = [];
      this._text = String(value);
    },
  };
}

function texts(container) {
  return container.children.map((c) => c.textContent).join("\n");
}

test("observation view carries the not-final-count label and proposal fields", () => {
  const view = observationView(reply());
  assert.equal(view.label, LABEL);
  assert.equal(view.state, "ready");
  assert.equal(view.books.length, 1);
  assert.equal(view.books[0].title, "Test Volume");
  assert.equal(view.books[0].accepted, true);
  assert.equal(view.items.length, 1);
  assert.equal(view.issues.length, 1);
  assert.equal(view.cached, false);
});

test("pending, idle and error states are explicit", () => {
  assert.equal(observationView(null).state, "idle");
  assert.equal(observationView({ pending: true }).state, "pending");
  const failed = observationView({ error: "OPENAI_API_KEY not configured" });
  assert.equal(failed.state, "error");
  assert.match(failed.message, /not configured/);
});

test("cached reuse reports the source frame", () => {
  const view = observationView(reply({ cached: true, source_frame_id: "frame_" + "9".repeat(32) }));
  assert.equal(view.cached, true);
  assert.equal(view.source_frame_id, "frame_" + "9".repeat(32));
});

test("renderObservation writes text nodes only — provider text is not executed as HTML", () => {
  const hostile = reply();
  hostile.observation.books[0].observed_title = '<img src=x onerror="alert(1)">';
  const { el } = makeEl();
  const container = makeContainer();
  renderObservation(container, hostile, { el });
  assert.equal(container._html, undefined);
  const all = texts(container);
  assert.match(all, /Current-view proposals/);
  assert.ok(all.includes('<img src=x onerror="alert(1)">'));
  assert.match(all, /capture issue: blur/);
  assert.match(all, /frame frame_1{32}/);
});

test("renderObservation labels pending and error states", () => {
  const { el } = makeEl();
  const container = makeContainer();
  renderObservation(container, { pending: true }, { el });
  assert.match(texts(container), /in progress/);
  renderObservation(container, { error: "vision provider unavailable" }, { el });
  assert.match(texts(container), /unavailable/);
});

function makeCanvas() {
  const ctx = { strokes: [], fills: [], strokeRect(x, y, w, h) { this.strokes.push([x, y, w, h]); }, fillText(t, x, y) { this.fills.push([t, x, y]); } };
  const canvas = { ctx, width: 0, height: 0, style: {}, getContext(kind) { return kind === "2d" ? ctx : null; } };
  return canvas;
}

function makeImg(naturalWidth, naturalHeight, clientWidth, clientHeight) {
  const listeners = {};
  return {
    naturalWidth, naturalHeight, clientWidth, clientHeight,
    complete: true, src: "",
    addEventListener(name, fn) { listeners[name] = fn; },
    listeners,
  };
}

test("renderObservation draws numbered boxes on a canvas over the saved frame", () => {
  const { el } = makeEl();
  const container = makeContainer();
  const canvas = makeCanvas();
  const img = makeImg(16, 12, 320, 240);
  const nodes = { img, canvas };
  const elWithMedia = (tag) => nodes[tag] || el(tag);
  renderObservation(container, reply(), {
    el: elWithMedia,
    frameUrl: (fid) => "/api/sweeps/s1/frames/" + fid + ".jpg",
  });
  assert.equal(img.src, "/api/sweeps/s1/frames/frame_" + "1".repeat(32) + ".jpg");
  assert.equal(canvas.width, 320);
  assert.equal(canvas.height, 240);
  assert.deepEqual(canvas.ctx.strokes, [[20, 20, 80, 200]]);
  assert.deepEqual(canvas.ctx.fills, [["1", 22, 32]]);
  assert.equal(container._html, undefined);
  assert.match(texts(container), /source frame/i);
});

test("cached view labels the original cost as no new charge", () => {
  const { el } = makeEl();
  const container = makeContainer();
  renderObservation(container, reply({ cached: true, source_frame_id: "frame_" + "9".repeat(32) }), { el });
  const all = texts(container);
  assert.match(all, /original call/i);
  assert.match(all, /no new charge/i);
  assert.match(all, /0\.003775/);
});

test("stale and last-successful observations are labelled", () => {
  const { el } = makeEl();
  const container = makeContainer();
  renderObservation(container, reply({ stale: true, frame_id: "frame_" + "2".repeat(32) }), { el });
  assert.match(texts(container), /not the latest frame|earlier frame/i);
  const prior = reply({ frame_id: "frame_" + "3".repeat(32) });
  renderObservation(container, { error: "http 502", stale: prior }, { el });
  const all = texts(container);
  assert.match(all, /http 502/);
  assert.match(all, /last successful observation/i);
  assert.match(all, /frame_3{32}/);
});

function invState() {
  return {
    books: [
      {
        id: "book_aaa", shelf: "item_shelf1", position: 1, status: "unidentified",
        title: "", excluded: false, potential_duplicate: false,
        frame_ref: "frames/frame_1.jpg", crop_refs: ["crops/crop_book_aaa_original_x.jpg"],
        evidence: [{ frame_id: "frame_1" }],
      },
      {
        id: "book_bbb", shelf: null, position: null, status: "identified",
        title: "<img onerror=alert(1)>", excluded: true, potential_duplicate: true,
        frame_ref: "frames/frame_2.jpg", crop_refs: [],
        evidence: [{ frame_id: "frame_2" }],
      },
      {
        id: "book_ccc", shelf: "item_shelf1", position: 2, status: "needs_appraisal",
        title: "Rare Folio", excluded: false, potential_duplicate: false,
        frame_ref: "frames/frame_1.jpg", crop_refs: [],
        evidence: [{ frame_id: "frame_1" }],
      },
    ],
    items: [
      {
        id: "item_shelf1", category: "shelving", status: "range",
        valuation_state: "unpriced", description: "wooden shelf",
        excluded: false, potential_duplicate: false, evidence: [{ frame_id: "frame_1" }],
      },
      {
        id: "item_art1", category: "art", status: "needs_appraisal",
        valuation_state: "unpriced", description: "framed piece <script>x</script>",
        excluded: false, potential_duplicate: false, evidence: [{ frame_id: "frame_2" }],
      },
    ],
    review_queue: [{ ref_id: "book_bbb", reason: "possible duplicate" }],
    inventory_complete: false,
  };
}

test("renderInventory reports aggregate counts and never claims complete", () => {
  const { el } = makeEl();
  const container = makeContainer();
  renderInventory(container, invState(), {
    el,
    frameUrl: (fid) => "/api/sweeps/s/frames/" + fid + ".jpg",
    cropUrl: (ref) => "/api/sweeps/s/crops/" + ref.split("/").pop(),
  });
  const all = texts(container);
  assert.match(all, /not a final count/i);
  assert.match(all, /not complete/i);
  assert.match(all, /3 recorded/);
  assert.match(all, /1 identified/);
  assert.match(all, /1 unidentified/);
  assert.match(all, /1 appraisal/);
  assert.match(all, /1 excluded/);
  assert.match(all, /possible duplicate/i);
  assert.match(all, /unpriced/);
  assert.match(all, /review/i);
  const links = [];
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (node.attrs && node.attrs.href) links.push(node.attrs.href);
    (node.children || []).forEach(walk);
  };
  container.children.forEach(walk);
  assert.ok(links.includes("/api/sweeps/s/frames/frame_1.jpg"));
  assert.ok(links.includes("/api/sweeps/s/crops/crop_book_aaa_original_x.jpg"));
});

test("renderInventory shows reviews when there are no tracks", () => {
  const { el } = makeEl();
  const container = makeContainer();
  renderInventory(
    container,
    {
      books: [],
      items: [],
      review_queue: [{ ref_id: "book_missing", reason: "shelf position unresolved: incomplete same-frame view" }],
      inventory_complete: false,
    },
    { el, frameUrl: null, cropUrl: null },
  );
  const all = texts(container);
  assert.match(all, /0 recorded/);
  assert.match(all, /not complete/i);
  assert.match(all, /incomplete same-frame view/);
});

function links(container) {
  const found = [];
  const walk = (node) => {
    (node.children || []).forEach((child) => {
      if (child.tag === "a") found.push(child);
      walk(child);
    });
  };
  walk(container);
  return found;
}

test("renderInventory shows amounts, currency, source and null plainly", () => {
  const { el } = makeEl();
  const container = makeContainer();
  const state = invState();
  state.books[1].replacement_cost = {
    amount: "10.00",
    currency: "USD",
    url: "https://books.example/listing/clean-code",
    retrieved_at: "2026-10-08T12:00:00+00:00",
    converted: false,
    foreign_market_fallback: false,
    source_quote: { market_country: "US", url: "https://books.example/listing/clean-code", retrieved_at: "2026-10-08T12:00:00+00:00" },
  };
  state.books[1].used_value = {
    amount: "7.50",
    currency: "GBP",
    url: "https://uk.example/listing/clean-code",
    retrieved_at: "2026-10-08T12:00:00+00:00",
    converted: true,
    foreign_market_fallback: true,
    foreign_market_same_currency: true,
    source_quote: { market_country: "GB" },
  };
  renderInventory(container, state, { el, frameUrl: null, cropUrl: null });
  const all = texts(container);
  assert.match(all, /new: 10\.00 USD/);
  assert.match(all, /market US/);
  assert.match(all, /retrieved 2026-10-08T12:00:00\+00:00/);
  assert.match(all, /used: 7\.50 GBP/);
  assert.match(all, /foreign market/);
  assert.match(all, /same currency/);
  assert.match(all, /converted/);
  assert.match(all, /new: unsupported/);
});

test("renderInventory renders source url as a safe clickable https link", () => {
  const { el } = makeEl();
  const container = makeContainer();
  const state = invState();
  state.books[1].replacement_cost = {
    amount: "10.00",
    currency: "USD",
    url: "https://books.example/listing/clean-code",
    retrieved_at: "2026-10-08T12:00:00+00:00",
    source_quote: { market_country: "US" },
  };
  renderInventory(container, state, { el, frameUrl: null, cropUrl: null });
  const anchors = links(container).filter(
    (a) => a.attrs.href === "https://books.example/listing/clean-code"
  );
  assert.equal(anchors.length, 1);
  assert.equal(anchors[0].textContent, "https://books.example/listing/clean-code");
  assert.equal(anchors[0].attrs.rel, "noopener noreferrer");
  assert.equal(container._html, undefined);
});

test("renderInventory does not link non-https urls", () => {
  const { el } = makeEl();
  const container = makeContainer();
  const state = invState();
  state.books[1].replacement_cost = {
    amount: "10.00",
    currency: "USD",
    url: "javascript:alert(1)",
    retrieved_at: "2026-10-08T12:00:00+00:00",
    source_quote: { market_country: "US" },
  };
  renderInventory(container, state, { el, frameUrl: null, cropUrl: null });
  const bad = links(container).filter((a) => String(a.attrs.href).includes("javascript"));
  assert.equal(bad.length, 0);
  assert.match(texts(container), /javascript:alert\(1\)/);
});

test("renderInventory shows FX rate, data date and attribution link", () => {
  const { el } = makeEl();
  const container = makeContainer();
  const state = invState();
  state.books[1].replacement_cost = {
    amount: "10.00",
    currency: "USD",
    url: "https://souq.example/book",
    retrieved_at: "2026-10-08T12:00:00+00:00",
    converted: true,
    source_quote: {
      market_country: "AE",
      amount: "36.70",
      currency: "AED",
      condition: "new",
      url: "https://souq.example/book",
    },
    fx_evidence: {
      kind: "provider_feed",
      source_url: "https://open.er-api.com/v6/latest/USD",
      data_at: "2026-10-08T11:00:00+00:00",
      retrieved_at: "2026-10-08T12:00:00+00:00",
      source_currency: "AED",
      target_currency: "USD",
      source_rate: "3.67",
      target_rate: "1",
      cross_rate: "0.2724795640326975476839237057",
      attribution: "Rates By Exchange Rate API",
      attribution_url: "https://www.exchangerate-api.com",
    },
    edition_equivalence: "current_print_equivalent",
  };
  renderInventory(container, state, { el, frameUrl: null, cropUrl: null });
  const all = texts(container);
  assert.match(all, /FX rate 0\.2724/);
  assert.match(all, /FX data 2026-10-08T11:00:00/);
  assert.match(all, /Rates By Exchange Rate API/);
  assert.match(all, /source amount 36\.70 AED/);
  assert.match(all, /condition new/);
  assert.match(all, /current print equivalent/);
  const attr = links(container).filter(
    (a) => a.attrs.href === "https://www.exchangerate-api.com"
  );
  assert.equal(attr.length, 1);
});

test("renderInventory keeps source evidence visible for null amounts", () => {
  const { el } = makeEl();
  const container = makeContainer();
  const state = invState();
  state.books[1].replacement_cost = {
    amount: null,
    currency: "USD",
    url: "https://souq.example/book",
    retrieved_at: "2026-10-08T12:00:00+00:00",
    source_quote: {
      market_country: "AE",
      amount: "36.70",
      currency: "AED",
      condition: "new",
      url: "https://souq.example/book",
      retrieved_at: "2026-10-08T12:00:00+00:00",
    },
  };
  renderInventory(container, state, { el, frameUrl: null, cropUrl: null });
  const all = texts(container);
  assert.match(all, /new: unsupported/);
  assert.match(all, /source amount 36\.70 AED/);
  const anchors = links(container).filter(
    (a) => a.attrs.href === "https://souq.example/book"
  );
  assert.equal(anchors.length, 1);
});

test("renderInventory keeps provider price strings inert text", () => {
  const { el } = makeEl();
  const container = makeContainer();
  const state = invState();
  state.books[0].replacement_cost = {
    amount: "10.00",
    currency: "USD",
    url: "https://evil.example/<img src=x onerror=alert(1)>",
    retrieved_at: "2026-10-08T12:00:00+00:00",
    converted: false,
    source_quote: { market_country: "US", listing_title: "<script>alert(1)</script>" },
  };
  renderInventory(container, state, { el, frameUrl: null, cropUrl: null });
  const all = texts(container);
  assert.ok(all.includes("<img src=x onerror=alert(1)>"));
  assert.equal(container._html, undefined);
});

test("renderInventory keeps provider strings inert text", () => {
  const { el } = makeEl();
  const container = makeContainer();
  renderInventory(container, invState(), { el, frameUrl: null, cropUrl: null });
  const all = texts(container);
  assert.ok(all.includes("<img onerror=alert(1)>"));
  assert.ok(all.includes("<script>"));
  assert.equal(container._html, undefined);
});
