// Builds SRM_SE7EN.pptx - the DUET deck. Every number lives in N below.
// Rebuild after numbers change:  cd tools/deck && npm install pptxgenjs && node build_deck.js ../../SRM_SE7EN.pptx
const pptxgen = require("pptxgenjs");
const path = require("path");

const OUT = process.argv[2] || "SRM_SE7EN.pptx";

const N = {
  weighted: "97.4", baseline: 57, weightedNum: 97.4, killswitch: "89.1", killswitchNum: 89.1,
  chaosFresh: "99.7", chaosFreshDetail: "120 scenarios, 8 templates, a seed never used before",
  chaosFirst: "88.1", conformance: "23", lintScenarios: "32", tests: "373",
  worstHandler: "0.47 ms",
};

const INK = "14213D", CUT = "D8472C", OK = "2A9D8F", HOLD = "E9A23B";
const TEXT = "1F2937", MUTED = "5B6472", TINT = "F1F4F8", WHITE = "FFFFFF", SOFT = "C9D3E3";
const HEAD = "Cambria", BODY = "Calibri", MONO = "Courier New";

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9"; // 10 x 5.625 in
pres.title = "DUET - making voice agents safe to interrupt";
pres.author = "Team SE7EN, SRM";

function title(slide, text, opts = {}) {
  slide.addText(text, {
    x: 0.5, y: 0.32, w: 9.0, h: 0.75, fontFace: HEAD, fontSize: opts.size || 28, bold: true,
    color: opts.color || INK, margin: 0, isTextBox: true, valign: "top",
  });
}
function body(slide, text, x, y, w, h, o = {}) {
  slide.addText(text, {
    x, y, w, h, fontFace: BODY, fontSize: o.size || 14, color: o.color || TEXT, margin: o.margin ?? 0,
    valign: o.valign || "top", bold: o.bold || false, italic: o.italic || false,
    align: o.align || "left", isTextBox: true, paraSpaceAfter: o.paraSpaceAfter || 0,
  });
}
function bar(slide, x, y, w, kind, label, onDark = false) {
  // the motif: a tool call on a lane
  const h = 0.26;
  if (kind === "done") {
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.06,
      fill: { color: OK }, line: { color: OK } });
  } else {
    const color = kind === "cut" ? CUT : HOLD;
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.06,
      fill: { color: WHITE, transparency: 100 }, line: { color, width: 1.75, dashType: "dash" } });
  }
  if (label) {
    slide.addText(label, { x: x + 0.06, y, w: Math.max(w, 2.6), h, fontFace: BODY, fontSize: 9.5,
      color: (kind === "done" || onDark) ? WHITE : TEXT, margin: 0, valign: "middle", isTextBox: true,
      bold: kind === "done" });
  }
}
function vline(slide, x, y1, y2, color, dash) {
  slide.addShape(pres.shapes.LINE, { x, y: y1, w: 0, h: y2 - y1,
    line: { color, width: 1.25, dashType: dash ? "dash" : "solid" } });
}
function dot(slide, x, y, color, r = 0.07) {
  slide.addShape(pres.shapes.OVAL, { x: x - r, y: y - r, w: 2 * r, h: 2 * r,
    fill: { color }, line: { color } });
}
function badge(slide, x, y, text, fill, d = 0.42, size = 13) {
  slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill } });
  slide.addText(text, { x, y, w: d, h: d, fontFace: BODY, fontSize: size, bold: true, color: WHITE,
    align: "center", valign: "middle", margin: 0, isTextBox: true });
}
function card(slide, x, y, w, h, fill = TINT) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08,
    fill: { color: fill }, line: { color: fill } });
}

// ------------------------------------------------------------------ 1 title
{
  const s = pres.addSlide(); s.background = { color: INK };
  s.addText("DUET", { x: 0.6, y: 0.7, w: 6, h: 1.1, fontFace: HEAD, fontSize: 66, bold: true,
    color: WHITE, margin: 0, isTextBox: true });
  body(s, "Making voice agents safe to interrupt", 0.6, 1.8, 8.5, 0.6, { size: 26, color: WHITE });
  body(s, "Samsung PRISM GenAI Hackathon 3.0  ·  Theme 05: Interruptible Real-Time Agents",
    0.6, 2.55, 8.8, 0.35, { size: 14, color: SOFT });
  body(s, "Team SE7EN  ·  SRM", 0.6, 2.9, 8.8, 0.35, { size: 14, color: SOFT, bold: true });
  // motif: two lanes, one call cut at an interruption, a fresh call after it
  const y0 = 3.95;
  body(s, "user", 0.6, y0 - 0.05, 0.8, 0.3, { size: 10, color: SOFT });
  body(s, "tools", 0.6, y0 + 0.5, 0.8, 0.3, { size: 10, color: SOFT });
  dot(s, 2.0, y0 + 0.08, "8FB0E6"); dot(s, 5.2, y0 + 0.08, CUT, 0.09);
  bar(s, 2.0, y0 + 0.45, 3.2, "cut", "  search(Boston)", true);
  bar(s, 5.35, y0 + 0.45, 2.9, "done", "  search(New York)");
  vline(s, 5.2, y0 - 0.15, y0 + 0.95, CUT, true);
  body(s, "\"wait - make it New York\"", 5.3, y0 - 0.12, 3.5, 0.3, { size: 10.5, color: WHITE, italic: true });
  s.addNotes("DUET: the Duplex Utterance-Epoch Transaction runtime. A conversation is a duet, not two monologues. " +
    "The picture is the whole idea: the user changes their mind while work is in flight, and the stale work is cut.");
}

// ------------------------------------------------------------------ 2 problem
{
  const s = pres.addSlide(); s.background = { color: WHITE };
  title(s, "Full-duplex speech is solved. Full-duplex action is not.");
  const steps = [
    ["1", "\"Book a flight to Boston\"", "The agent starts searching - and may go on to book."],
    ["2", "\"Wait - make it New York\"", "The user changes their mind while that work is in flight."],
    ["3", "Without coordination", "The Boston result is still spoken; a booking can still commit - or commit twice."],
  ];
  steps.forEach(([n, head, line], i) => {
    const y = 1.35 + i * 1.05;
    badge(s, 0.5, y, n, i === 2 ? CUT : INK);
    body(s, head, 1.1, y - 0.02, 4.3, 0.35, { size: 16, bold: true, color: INK });
    body(s, line, 1.1, y + 0.34, 4.3, 0.55, { size: 13, color: MUTED });
  });
  // right: the failure, drawn
  card(s, 5.7, 1.3, 3.8, 3.0);
  body(s, "work already in flight", 5.95, 1.45, 3.4, 0.3, { size: 11, color: MUTED, bold: true });
  vline(s, 7.3, 1.85, 3.75, CUT, true);
  body(s, "interruption", 7.36, 1.83, 1.6, 0.3, { size: 10, color: CUT, bold: true });
  bar(s, 5.95, 2.3, 3.2, "cut", "  search(Boston)");
  bar(s, 5.95, 2.85, 3.3, "cut", "  book(FL-BOS)");
  bar(s, 7.4, 3.4, 1.85, "done", "  search(New York)");
  body(s, "both calls outlive the change of mind", 5.95, 3.88, 3.4, 0.3, { size: 10.5, color: CUT, italic: true });
  body(s, "Samsung's harness grades exactly this: stale work, state after the interruption, latency, and duplicate commits.",
    0.5, 4.6, 9.0, 0.6, { size: 13, color: INK, italic: true });
  s.addNotes("GPT-Live and others solved simultaneous speech. What happens to work already in flight when the user changes " +
    "their mind is what the 2026 literature is only now benchmarking (Full-Duplex-Bench v3, EchoChain).");
}

// ------------------------------------------------------------------ 3 guarantees
{
  const s = pres.addSlide(); s.background = { color: WHITE };
  title(s, "Four guarantees, held together");
  const g = [
    ["Never silent", "A truthful, content-aware reply tens of milliseconds after every turn or interruption - however slow the real work.", INK],
    ["Always interruptible", "In-flight work is invalidated mid-execution; its results are never spoken and never committed.", CUT],
    ["Never double-commits", "No irreversible action runs twice - not under retries, cancellations or re-plans.", OK],
    ["Honest about uncertainty", "When perception is unsure it asks, and names what: \"Sorry, did you say Austin?\"", HOLD],
  ];
  g.forEach(([head, line, color], i) => {
    const x = 0.5 + i * 2.3;
    card(s, x, 1.35, 2.1, 3.3);
    badge(s, x + 0.2, 1.55, String(i + 1), color, 0.5, 15);
    body(s, head, x + 0.2, 2.2, 1.75, 0.65, { size: 15, bold: true, color: INK });
    body(s, line, x + 0.2, 2.9, 1.75, 1.6, { size: 12, color: TEXT });
  });
  body(s, "No shipping assistant guarantees all four at once. The harness measures each of them.",
    0.5, 4.85, 9, 0.4, { size: 13, color: MUTED, italic: true });
  s.addNotes("These map directly onto the scorer: latency, recovery, safety, and the clarification checkpoints plus the quality judge's truthfulness dimension.");
}

// ------------------------------------------------------------------ 4 architecture
{
  const s = pres.addSlide(); s.background = { color: WHITE };
  title(s, "Architecture: one dispatcher, one exit");
  const box = (x, y, w, h, head, line, fill, color = WHITE) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.06, fill: { color: fill }, line: { color: fill } });
    body(s, head, x + 0.1, y + 0.06, w - 0.2, 0.3, { size: 12, bold: true, color });
    if (line) body(s, line, x + 0.1, y + 0.36, w - 0.2, h - 0.4, { size: 10, color });
  };
  box(0.5, 1.25, 5.9, 0.62, "DISPATCHER  ·  runtime.py", "synchronous · never awaits slow work · < 20 ms per event", INK);
  const cols = [
    ["FAST PATH", "repairs, extraction, acknowledgments"],
    ["COORDINATOR", "epochs (M1), ledger + gate (M2)"],
    ["SLOW PATH", "speech, frames, tools"],
    ["STATE", "slots + provenance (M3), undo (M6)"],
  ];
  cols.forEach(([h, l], i) => box(0.5 + i * 1.5, 2.1, 1.4, 1.15, h, l, TINT, INK));
  box(0.5, 3.5, 5.9, 0.62, "EMITTER  ·  the only queue write", "snapshot · validation · no repeats · no premature claims", OK);
  body(s, "events in  →", 0.5, 4.3, 3, 0.3, { size: 11, color: MUTED });
  body(s, "→  actions out", 3.4, 4.3, 3, 0.3, { size: 11, color: MUTED, align: "right" });
  // invariants
  card(s, 6.75, 1.25, 2.75, 3.0);
  body(s, "Two invariants, enforced by tests", 6.95, 1.4, 2.4, 0.5, { size: 13, bold: true, color: INK });
  body(s, [
    { text: "The dispatcher never blocks.", options: { bold: true, breakLine: true } },
    { text: "Slowest handler " + N.worstHandler + " against a 20 ms budget.", options: { breakLine: true } },
    { text: " ", options: { breakLine: true } },
    { text: "Every action leaves through one function.", options: { bold: true, breakLine: true } },
    { text: "put_nowait appears exactly once in the engine." },
  ], 6.95, 1.95, 2.4, 2.2, { size: 12 });
  s.addNotes("The harness runs our agent on its own event loop: one blocking call delays event delivery and makes the scorer blame us. " +
    "Every safety rule lives in the emitter once, so it cannot be forgotten at a call site.");
}

// ------------------------------------------------------------------ 5 mechanisms
{
  const s = pres.addSlide(); s.background = { color: WHITE };
  title(s, "Six mechanisms");
  const m = [
    ["M1", "Epoch-versioned state", "An interruption starts a new epoch; everything conceived earlier is orphaned in one step."],
    ["M2", "Reversibility-gated commitment", "Read-only work fires freely; irreversible work passes a gate and an idempotency ledger."],
    ["M3", "Slot provenance", "Each value remembers where it came from, so a correction rewrites one slot and nothing else."],
    ["M4", "Perception-ahead", "A frame is read when it arrives; a question about it waits for the reading, not the voice."],
    ["M5", "Calibrated abstention", "Confidence per word, not per sentence; a doubted value is confirmed by name."],
    ["M6", "Conversational undo", "\"Go back to what I said\" restores an epoch checkpoint."],
  ];
  m.forEach(([tag, head, line], i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.5 + col * 3.05, y = 1.3 + row * 1.85;
    card(s, x, y, 2.85, 1.65);
    badge(s, x + 0.18, y + 0.2, tag, [INK, OK, INK, HOLD, CUT, INK][i], 0.5, 12);
    body(s, head, x + 0.8, y + 0.22, 1.95, 0.5, { size: 13.5, bold: true, color: INK, valign: "middle" });
    body(s, line, x + 0.18, y + 0.82, 2.5, 0.8, { size: 11.5, color: TEXT });
  });
  s.addNotes("Literature anchors: M1 EchoChain; M2 Act While Thinking and speculative tool calling; M3 Full-Duplex-Bench v3; " +
    "M4 RelayS2S; M5 the theme's accessibility case; M6 falls out of M1.");
}

// ------------------------------------------------------------------ 6 M1 in action
{
  const s = pres.addSlide(); s.background = { color: WHITE };
  title(s, "M1 in action: two corrections during a booking");
  const X = (sec) => 1.3 + (sec / 4.2) * 8.0;
  // lanes
  body(s, "user", 0.5, 1.45, 0.8, 0.3, { size: 11, color: MUTED, bold: true });
  body(s, "tools", 0.5, 2.25, 0.8, 0.3, { size: 11, color: MUTED, bold: true });
  [["book the 8 AM for Alice", 0.9], ["make it 2 PM", 2.5], ["for Priya, not Alice", 2.65]].forEach(([t, sec], i) => {
    dot(s, X(sec), 1.6, i === 0 ? INK : CUT);
  });
  body(s, "\"find a flight to Denver and book the 8 AM one for Alice\"", X(0.9) - 0.05, 1.12, 4.2, 0.3, { size: 10, color: INK, italic: true });
  body(s, "\"make it the 2 PM\"  then  \"for Priya, not Alice\"", X(2.65) + 0.12, 1.47, 3.6, 0.3, { size: 10, color: CUT, italic: true });
  vline(s, X(2.5), 1.4, 4.0, CUT, true); vline(s, X(2.65), 1.4, 4.0, CUT, true);
  bar(s, X(0.9), 2.2, X(2.1) - X(0.9), "done", "  search(Denver)");
  bar(s, X(2.1), 2.65, X(2.5) - X(2.1), "cut", "");
  body(s, "book(8 AM, Alice) - cancelled", X(2.1) - 1.95, 2.65, 1.9, 0.26, { size: 9.5, color: CUT, align: "right", valign: "middle" });
  bar(s, X(2.5), 3.1, X(2.77) - X(2.5), "hold", "");
  body(s, "book(2 PM, Alice) - held, dropped, never sent", X(2.5) - 2.95, 3.1, 2.9, 0.26, { size: 9.5, color: HOLD, align: "right", valign: "middle" });
  bar(s, X(2.95), 3.55, X(3.95) - X(2.95), "done", "  book(2 PM, Priya)");
  [0, 1, 2, 3, 4].forEach((t) => body(s, t + " s", X(t) - 0.2, 4.05, 0.5, 0.25, { size: 9, color: MUTED, align: "center" }));
  body(s, "Every computation carries the epoch it was conceived under. The second correction lands inside the 250 ms hold on irreversible work, so the held booking is dropped before it is ever sent. One booking goes out - 2 PM, for Priya - and \"not Alice\" never enters the state.",
    0.5, 4.4, 9.0, 0.9, { size: 12.5, color: TEXT });
  s.addNotes("This is conformance scenario conf_23, scored 100. tools/timeline.py renders it from a live run.");
}

// ------------------------------------------------------------------ 7 tools + honesty
{
  const s = pres.addSlide(); s.background = { color: WHITE };
  title(s, "Tools it has never seen, handled honestly");
  card(s, 0.5, 1.3, 4.5, 3.6);
  body(s, "A schema DUET met a moment ago", 0.7, 1.42, 4.1, 0.3, { size: 12, bold: true, color: MUTED });
  s.addText("reserve_table  (state_modifying)\n  restaurant : string, required\n  party_size : number, required", {
    x: 0.7, y: 1.8, w: 4.1, h: 0.9, fontFace: MONO, fontSize: 11, color: INK, margin: 0, isTextBox: true });
  body(s, "\"Could you get us a table at The Olive Room for six, around 7 PM?\"", 0.7, 2.8, 4.1, 0.55, { size: 12, italic: true, color: TEXT });
  s.addText("reserve_table(restaurant=\"The Olive Room\",\n              party_size=6)", {
    x: 0.7, y: 3.4, w: 4.1, h: 0.55, fontFace: MONO, fontSize: 11, color: OK, bold: true, margin: 0, isTextBox: true });
  body(s, "\"All set - that went through, reservation reference RS-0042.\"", 0.7, 4.1, 4.1, 0.6, { size: 12, italic: true, color: INK });
  const rules = [
    ["No tool names in the engine", "Chosen and bound from the schema alone; chaining falls out of which arguments can be filled."],
    ["A timeout is not a failure", "\"...timed out, so I cannot tell whether it went through. I will not send it again without checking with you.\""],
    ["Promises, never premature claims", "\"Booking flight FL-DEN-8AM now\" before the tool reports; \"booked\" only after."],
  ];
  rules.forEach(([h, l], i) => {
    const y = 1.35 + i * 1.2;
    badge(s, 5.35, y + 0.02, String(i + 1), [INK, HOLD, OK][i], 0.38, 12);
    body(s, h, 5.9, y, 3.6, 0.35, { size: 14, bold: true, color: INK });
    body(s, l, 5.9, y + 0.36, 3.6, 0.8, { size: 11.5, color: TEXT });
  });
  s.addNotes("About ten hidden tools arrive only as a schema. A grep test fails the build if any tool name appears in duet/.");
}

// ------------------------------------------------------------------ 8 perception
{
  const s = pres.addSlide(); s.background = { color: WHITE };
  title(s, "Hearing and seeing: measured, not assumed");
  // audio chart
  body(s, "Speech: confidence per word", 0.5, 1.2, 4.4, 0.35, { size: 15, bold: true, color: INK });
  s.addChart(pres.charts.BAR, [{ name: "word probability", labels: ["I", "broke", "off", "my", "head", "to", "Austin"],
    values: [0.23, 0.16, 0.49, 0.33, 0.34, 0.78, 0.47] }], {
    x: 0.4, y: 1.55, w: 4.6, h: 2.2, barDir: "col", chartColors: [SOFT],
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 9, dataLabelColor: TEXT, dataLabelFormatCode: "0.00",
    catAxisLabelColor: MUTED, catAxisLabelFontSize: 10, valAxisHidden: true, valAxisMaxVal: 1, valAxisMinVal: 0,
    valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false,
    showTitle: true, title: "pub_05, first clip (large-v3-turbo)", titleFontSize: 10, titleColor: MUTED,
  });
  body(s, "\"Austin\" at 0.47 is below the 0.65 bar, so DUET asks by name: \"Sorry, did you say Austin?\" - then searches Boston when the user says so.",
    0.5, 3.85, 4.4, 1.0, { size: 12, color: TEXT });
  // vision tiles
  body(s, "Vision: a model we tested and rejected", 5.3, 1.2, 4.3, 0.35, { size: 15, bold: true, color: INK });
  [["full frame", false], ["centre crop", true], ["lower half", false], ["mirror", false]].forEach(([l, ok], i) => {
    const x = 5.3 + (i % 2) * 2.1, y = 1.65 + Math.floor(i / 2) * 0.95;
    card(s, x, y, 1.95, 0.8, ok ? "E3F2EF" : "FBE7E3");
    body(s, l, x + 0.12, y + 0.08, 1.7, 0.3, { size: 11, color: MUTED });
    body(s, ok ? "\"HDMI port\"" : "\"USB-C port\"", x + 0.12, y + 0.38, 1.7, 0.35, { size: 13, bold: true, color: ok ? OK : CUT });
  });
  body(s, "Qwen2.5-VL-3B named pub_07's HDMI port correctly on 1 of 4 variants and invented a printed label to match. A wrong answer fails two checks instead of one, so vision reading ships off: DUET says it cannot tell, names the candidate pages, and asks.",
    5.3, 3.6, 4.3, 1.4, { size: 12, color: TEXT });
  s.addNotes("The confidence thresholds were calibrated on the production GPU model, not just the CPU fallback - the two take different branches on the same clip (finding F17). Vision: finding F18.");
}

// ------------------------------------------------------------------ 9 results
{
  const s = pres.addSlide(); s.background = { color: WHITE };
  title(s, "Results");
  const stats = [
    [N.weighted, "official weighted score,\npublic set (evaluator, scale 1)", INK],
    [N.chaosFresh, "mean on " + N.chaosFreshDetail, OK],
    [N.killswitch, "with every model switched off -\nnever silent, never crashes", HOLD],
  ];
  stats.forEach(([v, l, c], i) => {
    const y = 1.25 + i * 1.3;
    body(s, v, 0.5, y, 2.1, 0.8, { size: 44, bold: true, color: c });
    body(s, l, 2.6, y + 0.08, 2.4, 0.9, { size: 12, color: TEXT });
  });
  s.addChart(pres.charts.BAR, [{ name: "score", labels: ["Kit reference agent (~57)", "DUET, no models", "DUET"],
    values: [N.baseline, N.killswitchNum, N.weightedNum] }], {
    x: 5.2, y: 1.2, w: 4.4, h: 3.2, barDir: "bar", chartColors: [SOFT, HOLD, OK],
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 11, dataLabelColor: TEXT, dataLabelFormatCode: "0.0",
    catAxisLabelColor: TEXT, catAxisLabelFontSize: 11, valAxisHidden: true, valAxisMaxVal: 110, valAxisMinVal: 0,
    valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false,
    showTitle: true, title: "Public set, out of 100", titleFontSize: 11, titleColor: MUTED,
  });
  body(s, "0 crashes · 0 protocol errors · 0 abandoned calls across every run.  " + N.tests + " tests.",
    0.5, 4.95, 9.0, 0.35, { size: 12, color: MUTED, italic: true });
  s.addNotes("Every number is produced by a command in the repository, listed in README.md. The only unmet public checkpoint is naming the port in pub_07's photo.");
}

// ------------------------------------------------------------------ 10 testing the tests
{
  const s = pres.addSlide(); s.background = { color: WHITE };
  title(s, "How we know: testing the tests");
  const steps = [
    ["Conformance", N.conformance + " scenarios the kit does not cover. Each interruption case is proven to fail an agent that does not cancel."],
    ["Transcript lint", "The score cannot hear the agent. The first read found a garbled final answer in 6 of 17 scenarios that all scored 100. Now 0 of " + N.lintScenarios + "."],
    ["Our own chaos", "Randomized paraphrase, pivot, booking and unseen-tool templates: " + N.chaosFirst + " on first run, six general fixes, " + N.chaosFresh + " on a seed never used before."],
    ["Degradation + pins", "89.1 with no models. Every dependency and model pinned; the GPU stack proves itself in setup() or falls back."],
  ];
  steps.forEach(([h, l], i) => {
    const x = 0.5 + i * 2.3;
    badge(s, x, 1.35, String(i + 1), [INK, CUT, OK, HOLD][i], 0.55, 16);
    if (i < 3) s.addShape(pres.shapes.LINE, { x: x + 0.6, y: 1.625, w: 1.6, h: 0, line: { color: SOFT, width: 1.5 } });
    body(s, h, x, 2.05, 2.1, 0.4, { size: 15, bold: true, color: INK });
    body(s, l, x, 2.5, 2.1, 2.2, { size: 11.5, color: TEXT });
  });
  body(s, "Rule we kept: if a change improves the public nine but worsens unseen scenarios, it is a hardcode and it is reverted.",
    0.5, 4.3, 9.0, 0.45, { size: 12.5, italic: true, color: INK });
  s.addNotes("Findings F15-F23 in notes/FINDINGS.md record each defect these found, with evidence.");
}

// ------------------------------------------------------------------ 11 limits + next
{
  const s = pres.addSlide(); s.background = { color: WHITE };
  title(s, "Honest limits, and where DUET goes next");
  card(s, 0.5, 1.3, 4.3, 2.95);
  body(s, "Limits we measured", 0.72, 1.45, 3.9, 0.35, { size: 15, bold: true, color: CUT });
  body(s, [
    { text: "Naming a component in a photo: the 3B vision model was wrong 3 times in 4, so it ships off.", options: { bullet: true, breakLine: true } },
    { text: "A self-repair whose trigger word is lost in heavy noise can leave the abandoned value in place.", options: { bullet: true, breakLine: true } },
    { text: "The planner is rules, not a language model: deterministic and fast, but its phrasing coverage is what we have tested.", options: { bullet: true } },
  ], 0.72, 1.9, 3.9, 3.0, { size: 12, paraSpaceAfter: 8 });
  card(s, 5.2, 1.3, 4.3, 2.95, "E3F2EF");
  body(s, "Next, as a PRISM worklet", 5.42, 1.45, 3.9, 0.35, { size: 15, bold: true, color: OK });
  body(s, [
    { text: "DUET as the coordination layer between Bixby's speech and SmartThings actions - any device manifest, zero retraining.", options: { bullet: true, breakLine: true } },
    { text: "A larger vision model, admitted only once it passes our vision bench on device photos.", options: { bullet: true, breakLine: true } },
    { text: "A language-model planner raced behind the rules with a hard timeout - rules stay the floor.", options: { bullet: true } },
  ], 5.42, 1.9, 3.9, 3.0, { size: 12, paraSpaceAfter: 8 });
  body(s, "Every limit is written up with its evidence in notes/FINDINGS.md (F18, F21), next to what we tried.", 0.5, 4.5, 9.0, 0.4, { size: 12, italic: true, color: MUTED });
  s.addNotes("FAQ Q24: the jury weighs how a solution could be taken further as a PRISM worklet.");
}

// ------------------------------------------------------------------ 12 close
{
  const s = pres.addSlide(); s.background = { color: INK };
  s.addText("Full-duplex speech is solved.", { x: 0.6, y: 1.2, w: 8.8, h: 0.7, fontFace: HEAD, fontSize: 32,
    color: SOFT, margin: 0, isTextBox: true });
  s.addText("DUET makes full-duplex action safe.", { x: 0.6, y: 1.9, w: 8.8, h: 0.8, fontFace: HEAD, fontSize: 36,
    bold: true, color: WHITE, margin: 0, isTextBox: true });
  const y0 = 3.35;
  bar(s, 0.6, y0, 2.6, "cut", "  stale work: cancelled", true);
  bar(s, 3.35, y0, 2.6, "hold", "  irreversible: held, then once", true);
  bar(s, 6.1, y0, 2.9, "done", "  the answer: grounded");
  body(s, "Team SE7EN  ·  SRM  ·  entry point agent.agent:ParticipantAgent  ·  README.md reproduces every number",
    0.6, 4.55, 8.8, 0.4, { size: 12, color: SOFT });
  s.addNotes("Thank you.");
}

pres.writeFile({ fileName: OUT }).then((f) => console.log("wrote " + f));
