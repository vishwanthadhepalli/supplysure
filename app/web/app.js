/* SupplySure read-only web app.
   Every date and number comes from the API (the Python planner). This file only formats and draws.
   All server strings are escaped: supplier quotes are untrusted text. */
(function () {
  "use strict";

  /* ---------- helpers ---------- */
  var MN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function $(s, r) { return (r || document).querySelector(s); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmt(iso) { if (!iso) return "—"; var p = iso.slice(0, 10).split("-"); return MN[+p[1] - 1] + " " + (+p[2]); }
  function fmtTime(iso) { return iso ? fmt(iso) + ", " + iso.slice(11, 16) : "—"; }
  function plural(n) { return n + (Math.abs(n) === 1 ? " day" : " days"); }
  function f0(x) { return Number(x).toLocaleString("en-IN", { maximumFractionDigits: 2 }); }
  function slackText(n) { return n >= 0 ? plural(n) + " to spare" : plural(-n) + " late"; }
  function signed(n) { return (n > 0 ? "+" : "") + n + " d"; }
  // Day index for laying out the chart (display only; the plan itself is computed server-side).
  function dayNum(iso) { var p = iso.split("-"); return Math.round(Date.UTC(+p[0], +p[1] - 1, +p[2]) / 864e5); }
  function isoOf(n) { return new Date(n * 864e5).toISOString().slice(0, 10); }
  function statusChip(status) {
    return status === "tentative" ? '<span class="chip acc">New request</span>' : '<span class="chip idle">' + esc(status) + "</span>";
  }

  /* ---------- state ---------- */
  var KNIT_DOWN = "knit:2027-07-03:2027-07-10:2";
  var DEFAULT_FLAGS = { second_shift: false, new_request_ahead: false, yarn_delay_days: 0, knit_down: false };
  var state = {
    flags: Object.assign({}, DEFAULT_FLAGS),
    ledger: { status: "", tab: "promises" },
    brain: { sel: null, hl: null, hlEdges: null, answer: null, filter: {} },
    prod: { editing: null },
    gpt: { busy: null, error: null, lang: "unknown", draft: "", aloud: false },
    focus: null
  };
  var scenarios = [];
  var brainData = null;

  // What-if settings live in the URL hash (#/plan?yd=2&ss=1) so a scenario can be shared or reloaded.
  function hashFor(path, flags) {
    var p = new URLSearchParams();
    if (flags.yarn_delay_days) p.set("yd", String(flags.yarn_delay_days));
    if (flags.second_shift) p.set("ss", "1");
    if (flags.new_request_ahead) p.set("nra", "1");
    if (flags.knit_down) p.set("kd", "1");
    var q = p.toString();
    return "#/" + path + (q ? "?" + q : "");
  }
  function link(path) { return hashFor(path, state.flags); }
  function currentPath() { return location.hash.replace(/^#\/?/, "").split("?")[0] || "orders"; }
  function flagsFromHash() {
    var q = new URLSearchParams(location.hash.split("?")[1] || "");
    var yd = Math.max(0, Math.min(10, parseInt(q.get("yd") || "0", 10) || 0));
    return { second_shift: q.get("ss") === "1", new_request_ahead: q.get("nra") === "1", yarn_delay_days: yd, knit_down: q.get("kd") === "1" };
  }
  var keepScrollNext = false;
  function setFlags(f) {
    var h = hashFor(currentPath(), f);
    keepScrollNext = true;
    if (h === location.hash) render(true); else location.hash = h;
  }

  function flagsActive() {
    var f = state.flags;
    return f.second_shift || f.new_request_ahead || f.yarn_delay_days > 0 || f.knit_down;
  }
  function flagQuery() {
    var f = state.flags, p = new URLSearchParams();
    if (f.second_shift) p.set("second_shift", "true");
    if (f.new_request_ahead) p.set("new_request_ahead", "true");
    if (f.yarn_delay_days) p.set("yarn_delay_days", String(f.yarn_delay_days));
    if (f.knit_down) p.append("extra_down", KNIT_DOWN);
    return p.toString();
  }
  function get(path, withFlags) {
    var url = path, q = withFlags ? flagQuery() : "";
    if (q) url += (path.indexOf("?") >= 0 ? "&" : "?") + q;
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error(r.status + ": " + t); });
      return r.json();
    });
  }

  /* ---------- nav ---------- */
  var ICONS = {
    orders: '<path d="M6 4h12v16H6z"/><path d="M9 9h6M9 13h6M9 17h3"/>',
    plan: '<path d="M4 19h16"/><path d="M7 16V9M12 16V5M17 16v-4"/>',
    ledger: '<path d="M4 13l2-7h12l2 7v5H4z"/><path d="M4 13h5l1 2h4l1-2h5"/>',
    gpt: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
    production: '<path d="M4 6h4v4H4zM10 14h4v4h-4zM16 6h4v4h-4z"/><path d="M8 8h8M12 10v4"/>',
    machines: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
    brain: '<circle cx="5" cy="12" r="2"/><circle cx="12" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><circle cx="19" cy="12" r="2"/><path d="M7 11l3-4M7 13l3 4M14 7l3 4M14 17l3-4"/>'
  };
  var NAV = [["gpt", "SupplyGPT"], ["orders", "Orders"], ["production", "Production plan"], ["machines", "Machines"], ["plan", "Scenarios"], ["ledger", "Ledger"], ["brain", "Factory Brain"]];
  var TITLES = { gpt: "SupplyGPT", orders: "Orders", order: "Order", production: "Production plan", machines: "Machines", machine: "Machine", plan: "Scenarios and what-if", ledger: "Promise ledger", brain: "Factory Brain" };

  function renderNav(view, badge) {
    var cur = view === "order" ? "orders" : view === "machine" ? "machines" : view;
    var html = NAV.map(function (n) {
      var b = n[0] === "ledger" && badge ? '<span class="badge">' + badge + "</span>" : "";
      return '<a class="navbtn" href="' + link(n[0]) + '"' + (cur === n[0] ? ' aria-current="page"' : "") + '><svg viewBox="0 0 24 24" aria-hidden="true">' + ICONS[n[0]] + "</svg>" + n[1] + b + "</a>";
    }).join("");
    $("#nav-side").innerHTML = html;
    $("#nav-bottom").innerHTML = html;
  }

  /* ---------- what-if ---------- */
  function check(key, label) {
    return '<label class="check"><input type="checkbox" id="f-' + key + '" data-flag="' + key + '"' + (state.flags[key] ? " checked" : "") + "> " + label + "</label>";
  }
  function scenarioNote() {
    var f = state.flags, parts = [];
    if (f.yarn_delay_days) parts.push("every order's yarn " + plural(f.yarn_delay_days) + " late");
    if (f.second_shift) parts.push("second shift on knitting");
    if (f.new_request_ahead) parts.push("new request ahead of booked orders");
    if (f.knit_down) parts.push("2 knitting machines down Jul 3 to 10");
    return parts.length
      ? "Scenario: " + parts.join(", ") + ". Nothing is saved; this only changes what you see."
      : "Showing the base plan. Change a setting to see its effect on every order.";
  }
  function whatIfHtml() {
    var f = state.flags;
    return '<div class="card controls"><b>What if</b>' +
      '<div class="slider"><label for="f-yarn_delay_days">Yarn late by</label><input id="f-yarn_delay_days" data-flag="yarn_delay_days" type="range" min="0" max="10" value="' + f.yarn_delay_days + '"><span class="num" id="ydv" style="min-width:4.5em;font-weight:600">' + plural(f.yarn_delay_days) + "</span></div>" +
      check("second_shift", "Second shift on knitting") +
      check("new_request_ahead", "New request ahead of booked orders") +
      check("knit_down", "2 knitting machines down Jul 3 to 10") +
      '<button class="btn small" type="button" data-act="reset-flags"' + (flagsActive() ? "" : " disabled") + ">Reset</button>" +
      '<p class="advice">' + esc(scenarioNote()) + "</p></div>";
  }

  /* ---------- orders ---------- */
  function viewOrders() {
    return get("/api/plan", true).then(function (plan) {
      var h = '<div class="stack-v"><div><h2>Orders</h2><p class="muted">Each order is scheduled in priority order on shared machines. Dates come from the planner, not from a model.</p></div>' + whatIfHtml() + orderAddForm();
      plan.orders.forEach(function (o, i) {
        var was = o.baseline && o.baseline.ready !== o.ready ? ' <span class="was">(base plan ' + fmt(o.baseline.ready) + ")</span>" : "";
        h += '<a class="row-link" href="' + link("orders/" + encodeURIComponent(o.id)) + '"><div><div class="row"><h3>' + esc(o.id) + " · " + esc(o.buyer) + "</h3>" + statusChip(o.status) + '<span class="chip idle">Priority ' + (i + 1) + '</span></div><span class="muted">' + f0(o.pairs) + " pairs · ship by " + fmt(o.ship_by) + " · yarn " + fmt(o.yarn.date) + (o.yarn.source === "promise" ? " (promised)" : "") + '</span></div><div class="row"><span class="num">Ready ' + fmt(o.ready) + was + '</span><span class="chip ' + o.verdict.level + '">' + esc(o.verdict.label) + '</span><span class="chip ' + o.verdict.level + ' num">' + signed(o.slack_days) + "</span></div></a>";
      });
      if (plan.unplanned.length) h += '<p class="muted">Not planned (no yarn date yet): ' + plan.unplanned.map(esc).join(", ") + "</p>";
      return h + "</div>";
    });
  }

  function chartHtml(oid, plan) {
    var ch = plan.chart, x0 = dayNum(ch.start), x1 = dayNum(ch.end), days = x1 - x0 + 1;
    var lw = 150, dw = 16, rh = 58, top = 46, W = lw + days * dw + 40, H = top + ch.steps.length * rh + 18;
    var s = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" width="' + W + '" role="img" aria-label="Machine workload by day for each step">';
    for (var i = 0; i < days; i += 7) {
      var gx = lw + i * dw;
      s += '<line class="grid" x1="' + gx + '" y1="' + (top - 4) + '" x2="' + gx + '" y2="' + (H - 18) + '"/><text class="tick" x="' + (gx + 2) + '" y="' + (top - 8) + '">' + fmt(isoOf(x0 + i)) + "</text>";
    }
    ch.steps.forEach(function (st, ri) {
      var y0 = top + ri * rh, base = y0 + rh - 14, hmax = rh - 26;
      var maxCap = Math.max.apply(null, st.capacity.concat([st.nominal, 1]));
      s += '<text class="rowlab" x="0" y="' + (y0 + 18) + '">' + esc(st.name) + '</text><text class="rowsub" x="0" y="' + (y0 + 32) + '">up to ' + f0(maxCap) + " a day</text>";
      var shaded = false, path = "", dn = "";
      for (var d = 0; d < days; d++) {
        var x = lw + d * dw, c = st.capacity[d], iso = isoOf(x0 + d);
        if (c < st.nominal) {
          s += '<rect class="down" x="' + x + '" y="' + (y0 + 2) + '" width="' + dw + '" height="' + (rh - 14) + '"><title>' + fmt(iso) + ": " + f0(c) + " a day (machines down)</title></rect>";
          if (!shaded) { dn = '<text class="dnlab" x="' + (x + 2) + '" y="' + (y0 + 9) + '">machines down</text>'; shaded = true; }
        }
        var other = 0, mine = 0;
        plan.orders.forEach(function (o) {
          var u = o.steps[ri].usage[iso] || 0;
          if (o.id === oid) mine = u; else other += u;
        });
        var ho = other / maxCap * hmax, hm = mine / maxCap * hmax;
        if (ho > 0) s += '<rect class="bar-other" x="' + (x + 1) + '" y="' + (base - ho) + '" width="' + (dw - 3) + '" height="' + ho + '"><title>' + fmt(iso) + ": other orders " + f0(other) + " pairs</title></rect>";
        if (hm > 0) s += '<rect class="bar-this" x="' + (x + 1) + '" y="' + (base - ho - hm) + '" width="' + (dw - 3) + '" height="' + hm + '"><title>' + fmt(iso) + ": " + esc(oid) + " " + f0(mine) + " pairs</title></rect>";
        var cy = base - c / maxCap * hmax;
        path += d ? " V" + cy + " H" + (x + dw) : "M" + x + " " + cy + " H" + (x + dw);
      }
      s += '<path class="capline" d="' + path + '"/>' + dn;
    });
    var o = plan.orders.filter(function (x) { return x.id === oid; })[0];
    var clampX = function (iso) { return lw + Math.max(0, Math.min(days - 1, dayNum(iso) - x0)) * dw + dw / 2; };
    var sx = clampX(o.ship_by), rx = clampX(o.ready), lvl = o.verdict.level;
    s += '<line class="ship" x1="' + sx + '" y1="14" x2="' + sx + '" y2="' + (H - 18) + '"/><text class="shiplab" x="' + sx + '" y="11" text-anchor="' + (sx > rx ? "start" : "end") + '">Ship by ' + fmt(o.ship_by) + "</text>";
    s += '<line class="ready ' + lvl + '" x1="' + rx + '" y1="30" x2="' + rx + '" y2="' + (H - 18) + '"/><text class="readylab ' + lvl + '" x="' + rx + '" y="27" text-anchor="' + (rx >= sx ? "start" : "end") + '">Ready ' + fmt(o.ready) + "</text></svg>";
    return s;
  }

  function promiseCard(p) {
    var qty = p.quantity != null ? f0(p.quantity) + " " + esc(p.material.unit) : "Not stated";
    var date = p.promised_date ? fmt(p.promised_date) : '<span class="chip warn">Unclear</span>';
    var lvl = p.status === "needs_clarification" ? "warn" : p.status === "open" ? "acc" : p.status === "kept" ? "ok" : p.status === "slipped" ? "risk" : "idle";
    var src = esc(p.message.source) + " · " + (p.message.kind === "voice" ? "voice note" + (p.message.transcript_lang ? " (" + esc(p.message.transcript_lang) + ")" : "") : esc(p.message.kind)) + " · received " + fmtTime(p.message.received_at);
    var h = '<div class="card att ' + (lvl === "acc" ? "ok" : lvl) + '"><div class="row"><h3>' + esc(p.supplier.name) + '</h3><span class="muted">' + esc(p.material.name) + (p.order_id ? " · " + esc(p.order_id) : "") + '</span><span class="chip ' + lvl + '">' + esc(p.status.replace("_", " ")) + "</span></div>" +
      '<div class="fields"><div><small>Promised date</small><b class="num">' + date + '</b></div><div><small>Quantity</small><b class="num">' + qty + '</b></div><div><small>Order</small><b>' + (p.order_id ? '<a href="' + link("orders/" + encodeURIComponent(p.order_id)) + '">' + esc(p.order_id) + "</a>" : "Not linked") + '</b></div><div><small>Confidence</small><b class="num">' + Math.round(p.confidence * 100) + "%</b></div></div>" +
      '<p class="quote">"' + esc(p.quote) + '"</p><div class="row" style="margin-top:6px">' + (p.quote_verified ? '<span class="chip ok">Quote found in the source</span>' : '<span class="chip risk">Quote not verified</span>') + '<span class="muted">' + src + "</span></div>";
    if (p.clarification_question) {
      h += '<div class="draft"><div class="lab">Drafted question for the supplier (not sent)</div>' + esc(p.clarification_question) + '<p class="muted" style="margin-top:4px">Sending needs your approval in Discord. The bot is parked for now.</p></div>';
    }
    return h + "</div>";
  }

  function viewOrder(r) {
    return Promise.all([get("/api/orders/" + encodeURIComponent(r.id), true), get("/api/plan", true)]).then(function (res) {
      var d = res[0], plan = res[1], o = d.order;
      var h = '<div class="crumbs"><a href="' + link("orders") + '">Orders</a><span>/</span><span>' + esc(r.id) + '</span></div><div class="stack-v">';
      if (!o) return h + '<div class="empty">This order has no yarn date yet, so it cannot be planned. Add the yarn date below, or tell SupplyGPT.</div>' + orderEditForm(d.row) + "</div>";
      h += '<div class="card"><div class="row"><h2>' + esc(o.id) + " · " + esc(o.buyer) + "</h2>" + statusChip(o.status) + '</div><p class="muted">' + f0(o.pairs) + " pairs · ship by " + fmt(o.ship_by) + "</p></div>";
      h += whatIfHtml();
      var base = o.baseline && o.baseline.ready !== o.ready ? '<p class="muted" style="margin-top:4px">Base plan: ready ' + fmt(o.baseline.ready) + ", " + slackText(o.baseline.slack_days) + ".</p>" : "";
      h += '<div class="verdict ' + o.verdict.level + '"><div class="row"><span class="chip ' + o.verdict.level + '">' + esc(o.verdict.label) + "</span><b>" + esc(o.verdict.detail) + "</b></div>" + base + "</div>";
      var knit = o.steps[0];
      h += '<div class="stats"><div class="card stat"><small>Ready to ship</small><strong class="num">' + fmt(o.ready) + '</strong><span class="sub">Deadline ' + fmt(o.ship_by) + '</span></div><div class="card stat"><small>Buffer</small><strong class="num">' + slackText(o.slack_days) + '</strong><span class="sub">Deadline minus ready date</span></div><div class="card stat"><small>' + esc(knit.name) + ' window</small><strong class="num">' + fmt(knit.start) + " to " + fmt(knit.finish) + '</strong><span class="sub">' + plural(knit.days) + '</span></div><div class="card stat"><small>Yarn arrives</small><strong class="num">' + fmt(o.yarn.date) + '</strong><span class="sub">' + (o.yarn.source === "promise" ? "From a supplier promise (planned " + fmt(o.yarn.planned) + ")" : "Planned date, no promise yet") + "</span></div></div>";
      if (o.yarn.source === "promise") {
        h += '<div class="card"><div class="row"><span class="chip acc">Yarn date from the ledger</span><span class="muted">An open promise replaces the planned yarn date.</span></div><p class="quote">"' + esc(o.yarn.quote) + '"</p></div>';
      }
      h += '<div class="card"><div class="sect">Machines and workload</div><p class="muted" style="margin:4px 0 8px">Bars show pairs per day. Dark bars are this order, grey bars are the other orders, the dashed line is capacity.</p><div class="chartwrap">' + chartHtml(o.id, plan) + '</div><div class="legend"><span><i style="background:var(--accent)"></i>' + esc(o.id) + '</span><span><i style="background:var(--other)"></i>Other orders</span><span><i style="background:var(--warn-bg)"></i>Machines down</span></div></div>';
      h += '<div class="card"><div class="sect">Schedule</div><div class="tablewrap" style="margin-top:6px"><table><thead><tr><th>Step</th><th>Start</th><th>Finish</th><th class="r">Days</th></tr></thead><tbody>' +
        '<tr><td><b>Yarn dyeing</b> <span class="muted">(outside, batch ' + fmt(o.dye.start) + ')</span></td><td class="num">' + fmt(o.dye.start) + '</td><td class="num">' + fmt(o.dye.finish) + '</td><td class="r num">' + plan.process.dye_days + "</td></tr>" +
        o.steps.map(function (s) { return "<tr><td><b>" + esc(s.name) + '</b></td><td class="num">' + fmt(s.start) + '</td><td class="num">' + fmt(s.finish) + '</td><td class="r num">' + s.days + "</td></tr>"; }).join("") +
        '<tr><td><b>Dispatch</b></td><td class="num">' + fmt(o.dispatch.start) + '</td><td class="num">' + fmt(o.dispatch.finish) + '</td><td class="r num">' + plan.process.dispatch_days + "</td></tr></tbody></table></div></div>";
      if (d.materials.length) {
        h += '<div class="card"><div class="sect">Materials</div><div class="tablewrap" style="margin-top:6px"><table><thead><tr><th>Item</th><th class="r">Needed</th><th class="r">On hand</th><th class="r">MOQ</th><th class="r">Buy</th><th>Supplier</th><th>Expected</th></tr></thead><tbody>' +
          d.materials.map(function (m) { return "<tr><td><b>" + esc(m.name) + '</b></td><td class="r num">' + f0(m.need) + " " + esc(m.unit) + '</td><td class="r num">' + f0(m.on_hand) + '</td><td class="r num">' + f0(m.moq) + '</td><td class="r num"><b>' + f0(m.buy) + " " + esc(m.unit) + "</b></td><td>" + esc(m.supplier || "—") + '</td><td class="num">' + fmt(m.expected_arrival) + "</td></tr>"; }).join("") +
          '</tbody></table></div><p class="muted" style="margin-top:6px">Buy = need minus stock, rounded up to the minimum order quantity.</p></div>';
      } else {
        h += '<div class="card"><div class="sect">Materials</div><p class="muted" style="margin-top:6px">No material lines are recorded for this order yet.</p></div>';
      }
      if (o.risks.length) h += '<div class="card"><div class="sect">What could go wrong</div><ul class="risks">' + o.risks.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul></div>";
      h += orderEditForm(o);
      h += '<div class="sect">Supplier promises for this order</div>' + (d.promises.length ? d.promises.map(promiseCard).join("") : '<div class="empty">No promises linked to this order yet.</div>');
      return h + "</div>";
    });
  }

  /* ---------- plan ---------- */
  function viewPlan() {
    return Promise.all([get("/api/plan", true), get("/api/plan/scenarios")]).then(function (res) {
      var plan = res[0]; scenarios = res[1].scenarios;
      var ids = plan.orders.map(function (o) { return o.id; }).sort();
      var h = '<div class="stack-v"><div><h2>Scenarios and what-if</h2><p class="muted">One plan for all orders on shared machines. Try a change and every order updates.</p></div>' + whatIfHtml() + problemsHtml(plan.problems);
      h += '<div class="card"><div class="sect">Orders in priority order</div><div class="tablewrap" style="margin-top:6px"><table><thead><tr><th>#</th><th>Order</th><th>Ship by</th><th>Ready</th><th>Buffer</th><th>Against the base plan</th><th>Verdict</th></tr></thead><tbody>' +
        plan.orders.map(function (o, i) {
          var delta = o.baseline ? o.slack_days - o.baseline.slack_days : 0;
          var vs = !o.baseline || delta === 0 ? '<span class="muted">No change</span>' : '<span class="chip ' + (delta > 0 ? "ok" : "risk") + '">' + (delta > 0 ? plural(delta) + " earlier" : plural(-delta) + " later") + "</span>";
          return '<tr><td class="num">' + (i + 1) + '</td><td><a href="' + link("orders/" + encodeURIComponent(o.id)) + '"><b>' + esc(o.id) + '</b></a> <span class="muted">' + esc(o.buyer) + '</span></td><td class="num">' + fmt(o.ship_by) + '</td><td class="num">' + fmt(o.ready) + '</td><td class="num">' + slackText(o.slack_days) + "</td><td>" + vs + '</td><td><span class="chip ' + o.verdict.level + '">' + esc(o.verdict.label) + "</span></td></tr>";
        }).join("") + "</tbody></table></div></div>";
      h += '<div class="card"><div class="sect">Scenarios</div><p class="muted" style="margin:4px 0 6px">Ready date and buffer per order for the planner\'s seven tested cases. They match the golden file exactly unless a supplier promise has changed a yarn date.' + (function () {
        var hit = plan.orders.filter(function (o) { return o.yarn.source === "promise"; });
        return hit.length ? " <b>Here a promise has changed the yarn date for " + hit.map(function (o) { return esc(o.id) + " (" + fmt(o.yarn.planned) + " to " + fmt(o.yarn.date) + ")"; }).join(", ") + ".</b>" : "";
      })() + '</p><div class="tablewrap"><table><thead><tr><th>Scenario</th>' + ids.map(function (id) { return "<th>" + esc(id) + "</th>"; }).join("") + "<th></th></tr></thead><tbody>" +
        scenarios.map(function (sc) {
          return "<tr><td><b>" + esc(sc.label) + "</b></td>" + ids.map(function (id) {
            var x = sc.orders[id]; if (!x) return "<td>—</td>";
            return '<td class="num">' + fmt(x.ready) + ' <span class="chip ' + x.verdict + '">' + signed(x.slack_days) + "</span></td>";
          }).join("") + '<td><button class="btn small" type="button" data-act="apply-scenario" data-key="' + esc(sc.key) + '">Apply</button></td></tr>';
        }).join("") + "</tbody></table></div></div>";
      h += '<p class="muted">Machines, steps and outside stages are set in <a href="' + link("production") + '">Production plan</a> and <a href="' + link("machines") + '">Machines</a>.</p>';
      return h + "</div>";
    });
  }

  /* ---------- ledger ---------- */
  var ACTIONS = {
    message_received: ["Message received", "idle"],
    promise_created: ["Promise added", "ok"],
    promise_rejected: ["Promise rejected", "warn"],
    suspicious_message: ["Suspicious message blocked", "risk"],
    duplicate_message_ignored: ["Duplicate ignored", "idle"],
    quantity_dropped_not_in_quote: ["Quantity dropped", "warn"]
  };
  function viewLedger() {
    var L = state.ledger;
    var tabs = function (total) {
      return '<div class="tabs" role="tablist"><button class="tab" role="tab" type="button" data-act="ledger-tab" data-t="promises" aria-selected="' + (L.tab === "promises") + '">Promises' + (total != null ? " (" + total + ")" : "") + '</button><button class="tab" role="tab" type="button" data-act="ledger-tab" data-t="activity" aria-selected="' + (L.tab === "activity") + '">Activity log</button></div>';
    };
    var head = '<div><h2>Promise ledger</h2><p class="muted">Every promise a supplier made, with the exact words it came from. A promise is only stored if its quote is found in the message.</p></div>';
    if (L.tab === "activity") {
      return get("/api/activity?limit=100").then(function (d) {
        var h = '<div class="stack-v">' + head + tabs(null);
        if (!d.actions.length) return h + '<div class="empty">Nothing logged yet.</div></div>';
        return h + '<div class="card"><div class="tablewrap"><table><thead><tr><th>When</th><th>Who</th><th>What</th><th>Detail</th></tr></thead><tbody>' +
          d.actions.map(function (a) {
            var m = ACTIONS[a.action] || [a.action, "idle"];
            return '<tr><td class="num">' + fmtTime(a.at) + "</td><td>" + esc(a.actor) + '</td><td><span class="chip ' + m[1] + '">' + esc(m[0]) + '</span></td><td class="muted" style="font-size:12px">' + esc(a.payload ? JSON.stringify(a.payload) : "") + "</td></tr>";
          }).join("") + '</tbody></table></div></div><p class="muted">The activity log shows every intake step, including messages that were refused.</p></div>';
      });
    }
    return get("/api/promises" + (L.status ? "?status=" + encodeURIComponent(L.status) : "")).then(function (d) {
      var c = d.counts;
      var filt = [["", "All", d.total], ["open", "Open", c.open], ["needs_clarification", "Needs clarification", c.needs_clarification]];
      var h = '<div class="stack-v">' + head + tabs(d.total) + '<div class="filters">' + filt.map(function (f) {
        return '<button type="button" class="fchip" data-act="ledger-status" data-s="' + f[0] + '" aria-pressed="' + (L.status === f[0]) + '">' + f[1] + " (" + f[2] + ")</button>";
      }).join("") + "</div>";
      if (!d.promises.length) {
        return h + '<div class="empty">' + (d.total ? "No promises with this status." : "No promises yet. Run <code>python -m scripts.sample_messages</code> to replay the sample supplier messages.") + "</div></div>";
      }
      return h + d.promises.map(promiseCard).join("") + "</div>";
    });
  }

  /* ---------- brain ---------- */
  var BW = 124, BH = 46, CW = 150, X0 = 10, TOP = 30, GAP = 16;
  function brainLayout() {
    var cols = brainData.columns.filter(function (t) { return brainData.nodes.some(function (n) { return n.type === t; }); });
    var byCol = cols.map(function (t) { return brainData.nodes.filter(function (n) { return n.type === t; }); });
    var maxN = Math.max.apply(null, byCol.map(function (c) { return c.length; }).concat([1]));
    var height = maxN * (BH + GAP), pos = {};
    byCol.forEach(function (list, ci) {
      var off = (height - list.length * (BH + GAP)) / 2;
      list.forEach(function (n, i) { pos[n.id] = { x: X0 + ci * CW, y: TOP + off + i * (BH + GAP), col: ci }; });
    });
    return { cols: cols, pos: pos, width: X0 * 2 + cols.length * CW - (CW - BW), height: TOP + height + 10 };
  }
  function edgePath(pa, pb) {
    if (pa.col === pb.col) {
      var down = pb.y > pa.y;
      return "M" + (pa.x + BW / 2) + " " + (pa.y + (down ? BH : 0)) + " L" + (pb.x + BW / 2) + " " + (pb.y + (down ? 0 : BH));
    }
    var x1 = pa.x + BW, y1 = pa.y + BH / 2, x2 = pb.x, y2 = pb.y + BH / 2, dx = Math.max(24, (x2 - x1) / 2);
    if (x2 < x1) { x1 = pa.x; x2 = pb.x + BW; dx = -Math.max(24, (x1 - x2) / 2); }
    return "M" + x1 + " " + y1 + " C" + (x1 + dx) + " " + y1 + " " + (x2 - dx) + " " + y2 + " " + x2 + " " + y2;
  }
  function nodeById(id) { return brainData.nodes.filter(function (n) { return n.id === id; })[0]; }
  function drawBrain() {
    var svg = $("#svg"); if (!svg || !brainData) return;
    var L = brainLayout(), B = state.brain, hl = B.hl, s = "";
    svg.setAttribute("viewBox", "0 0 " + L.width + " " + L.height);
    L.cols.forEach(function (t, i) { s += '<text class="colhead" x="' + (X0 + i * CW) + '" y="16">' + esc(brainData.types[t]) + "</text>"; });
    brainData.edges.forEach(function (e, i) {
      var a = nodeById(e.src), b = nodeById(e.dst), pa = L.pos[e.src], pb = L.pos[e.dst];
      if (!a || !b) return;
      var vis = B.filter[a.type] !== false && B.filter[b.type] !== false;
      var on = hl ? hl[e.src] && hl[e.dst] && (!B.hlEdges || B.hlEdges[i]) : false;
      s += '<path class="edge' + (on ? " hl" : "") + ((hl && !on) || !vis ? " dim" : "") + '" d="' + edgePath(pa, pb) + '"><title>' + esc(a.label + " " + e.rel + " " + b.label) + "</title></path>";
    });
    brainData.nodes.forEach(function (n) {
      var p = L.pos[n.id], dim = (hl && !hl[n.id]) || B.filter[n.type] === false;
      s += '<g class="gn tp-' + n.type + (n.type === "event" ? " ev" : "") + (B.sel === n.id ? " sel" : "") + (dim ? " dim" : "") + '" data-node="' + esc(n.id) + '" tabindex="0" role="button" aria-label="' + esc(brainData.types[n.type] + ": " + n.label + (n.sub ? ", " + n.sub : "")) + '" transform="translate(' + p.x + " " + p.y + ')">' +
        '<rect class="body" width="' + BW + '" height="' + BH + '" rx="8"/><rect class="bar" x="0" y="0" width="6" height="' + BH + '" rx="3"/>' +
        (n.status ? '<rect class="ring ' + n.status + '" x="-2" y="-2" width="' + (BW + 4) + '" height="' + (BH + 4) + '" rx="10"/>' : "") +
        '<text class="name" x="14" y="20">' + esc(n.label.length > 17 ? n.label.slice(0, 16) + "…" : n.label) + '</text><text class="sub" x="14" y="35">' + esc((n.sub || "").length > 21 ? n.sub.slice(0, 20) + "…" : n.sub) + "</text></g>";
    });
    svg.innerHTML = s;
  }
  function relChips(ids) {
    return ids.length ? '<div class="rel">' + ids.map(function (id) { var n = nodeById(id); return '<button type="button" data-act="node" data-node="' + esc(id) + '">' + esc(n ? n.label : id) + "</button>"; }).join("") + "</div>" : '<p class="muted">None</p>';
  }
  function renderInspector() {
    var el = $("#inspect"); if (!el) return;
    var id = state.brain.sel, n = id && nodeById(id);
    if (!n) { el.innerHTML = '<h2>Inspector</h2><p class="muted" style="margin-top:6px">Select a node to see its facts and how it connects.</p>'; return; }
    var up = brainData.edges.filter(function (e) { return e.dst === id; }).map(function (e) { return e.src; });
    var dn = brainData.edges.filter(function (e) { return e.src === id; }).map(function (e) { return e.dst; });
    var lab = { ok: "Can be met", warn: "Tight or exposed", risk: "Will be missed" };
    el.innerHTML = '<div class="row"><h2><span class="tt" style="background:var(--t-' + n.type + ')"></span>' + esc(n.label) + '</h2><span class="chip idle">' + esc(brainData.types[n.type]) + "</span>" + (n.status ? '<span class="chip ' + n.status + '">' + lab[n.status] + "</span>" : "") + "</div>" +
      '<dl class="kv">' + Object.keys(n.attrs).map(function (k) { return "<dt>" + esc(k) + "</dt><dd>" + esc(n.attrs[k]) + "</dd>"; }).join("") + "</dl>" +
      '<div class="sub2">Depends on</div>' + relChips(up) + '<div class="sub2">Feeds</div>' + relChips(dn) +
      '<div class="row" style="margin-top:10px"><button class="btn small" type="button" data-act="trace" data-dir="up">Trace upstream</button><button class="btn small" type="button" data-act="trace" data-dir="down">Trace downstream</button><button class="btn small" type="button" data-act="brain-clear">Clear</button></div>';
  }
  function renderAnswer() {
    var el = $("#answer"); if (!el) return;
    el.innerHTML = state.brain.answer || '<p class="muted">Pick a question, or select a node and trace what it feeds or depends on.</p>';
  }
  function selectNode(id) {
    var B = state.brain, hl = {}; B.sel = id; hl[id] = true;
    brainData.edges.forEach(function (e) { if (e.src === id) hl[e.dst] = true; if (e.dst === id) hl[e.src] = true; });
    B.hl = hl; B.hlEdges = null; drawBrain(); renderInspector();
  }
  function runTrace(id, dir, title) {
    return get("/api/graph/trace?node=" + encodeURIComponent(id) + "&direction=" + dir, true).then(function (t) {
      var B = state.brain, hl = {}; hl[id] = true; t.reached.forEach(function (x) { hl[x] = true; });
      B.sel = id; B.hl = hl; B.hlEdges = null;
      var me = nodeById(id), names = t.reached.map(nodeById).filter(Boolean);
      var orders = t.orders.map(nodeById).filter(Boolean);
      B.answer = "<b>" + esc(title || (me.label + (dir === "up" ? " depends on " : " feeds ") + names.length + " other things.")) + "</b>" +
        (orders.length ? "<ul>" + orders.map(function (o) { return "<li>" + esc(o.label) + ": " + esc(o.sub) + "</li>"; }).join("") + "</ul>" : '<p class="muted" style="margin-top:4px">No orders reached.</p>') +
        '<p class="muted" style="margin-top:6px">' + names.length + " nodes on the path: " + esc(names.slice(0, 10).map(function (n) { return n.label; }).join(", ")) + (names.length > 10 ? ", and " + (names.length - 10) + " more" : "") + ".</p>";
      drawBrain(); renderInspector(); renderAnswer();
    });
  }
  function questions() {
    var qs = [];
    var ss = brainData.nodes.filter(function (n) { return n.type === "supplier" && n.attrs["Single source"] === "Yes"; })[0];
    if (ss) qs.push({ q: "Which orders depend on " + ss.label + "?", run: function () { return runTrace(ss.id, "down", "Orders that depend on " + ss.label + ":"); } });
    var ev = brainData.nodes.filter(function (n) { return n.type === "event"; })[0];
    if (ev) qs.push({ q: "What is exposed to the " + ev.label.toLowerCase() + " (" + ev.sub + ")?", run: function () { return runTrace(ev.id, "down", "Orders exposed to the " + ev.label.toLowerCase() + ", " + ev.sub + ":"); } });
    var nr = brainData.nodes.filter(function (n) { return n.type === "order" && n.attrs.Status === "tentative"; })[0];
    if (nr) qs.push({ q: "What does " + nr.label + " depend on?", run: function () { return runTrace(nr.id, "up", nr.label + " depends on:"); } });
    qs.push({ q: "Which materials have only one supplier?", run: function () {
      var B = state.brain, hl = {}, list = brainData.single_source.map(nodeById).filter(Boolean);
      list.forEach(function (m) { hl[m.id] = true; brainData.edges.forEach(function (e) { if (e.dst === m.id) hl[e.src] = true; }); });
      B.sel = null; B.hl = hl; B.hlEdges = null;
      B.answer = "<b>" + list.length + " materials come from a single-source supplier with no recorded alternative.</b><ul>" + list.map(function (m) { return "<li>" + esc(m.label) + " from " + esc(m.attrs.Supplier) + "</li>"; }).join("") + "</ul>";
      drawBrain(); renderInspector(); renderAnswer(); return Promise.resolve();
    } });
    return qs;
  }
  var brainQuestions = [];
  function viewBrain() {
    return get("/api/graph", true).then(function (g) {
      brainData = g;
      var B = state.brain;
      if (B.sel && !nodeById(B.sel)) B.sel = null;
      B.hl = null; B.hlEdges = null;
      brainQuestions = questions();
      var filters = Object.keys(g.types).filter(function (t) { return g.nodes.some(function (n) { return n.type === t; }); }).map(function (t) {
        return '<button type="button" class="fchip" data-act="filter" data-t="' + t + '" aria-pressed="' + (B.filter[t] !== false) + '"><i style="background:var(--t-' + t + ')"></i>' + esc(g.types[t]) + "</button>";
      }).join("");
      var late = g.nodes.filter(function (n) { return n.type === "order" && n.status === "risk"; }).length;
      var orders = g.nodes.filter(function (n) { return n.type === "order"; }).length;
      return '<div class="stack-v"><div class="row"><div style="margin-right:auto"><h2>Factory Brain</h2><p class="muted">One graph of suppliers, materials, machines, orders and buyers, built from the database.</p></div><span class="chip idle">' + g.nodes.length + " nodes</span><span class=\"chip idle\">" + g.edges.length + ' relations</span><span class="chip ' + (late ? "risk" : "ok") + '">Orders that miss their date: ' + late + " of " + orders + "</span></div>" +
        whatIfHtml() + '<div class="filters">' + filters + "</div>" +
        '<div class="bgrid"><div class="card"><div class="gwrap"><svg class="brain" id="svg" role="group" aria-label="Factory graph"></svg></div><div class="legend"><span><span class="rg" style="border-color:var(--ok)"></span>Date can be met</span><span><span class="rg" style="border-color:var(--warn)"></span>Tight or exposed</span><span><span class="rg" style="border-color:var(--risk)"></span>Date will be missed</span><span>Select any node to inspect it</span></div></div>' +
        '<aside class="stack-v"><section class="card"><h2>Ask the graph</h2><div class="qs">' + brainQuestions.map(function (q, i) { return '<button class="btn" type="button" data-act="question" data-i="' + i + '">' + esc(q.q) + "</button>"; }).join("") + '</div></section><section class="card answer" id="answer" aria-live="polite"></section><section class="card" id="inspect"></section></aside></div></div>';
    });
  }

  /* ---------- writes (factory setup only) ---------- */
  function send(method, path, body) {
    return fetch(path, {
      method: method,
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) {
      return r.text().then(function (t) {
        var data = null; try { data = t ? JSON.parse(t) : null; } catch (e) { data = null; }
        if (!r.ok) {
          var d = data && data.detail, msg = t;
          if (typeof d === "string") msg = d;
          else if (Array.isArray(d)) msg = d.map(function (x) { return (x.loc ? x.loc.slice(-1)[0] + ": " : "") + x.msg; }).join("; ");
          throw new Error(msg);
        }
        return data;
      });
    });
  }
  var toastTimer = null;
  function toast(msg, bad) {
    var el = $("#toast");
    el.textContent = msg; el.className = "toast" + (bad ? " bad" : ""); el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.hidden = true; }, bad ? 6000 : 2600);
  }
  function problemsHtml(list) {
    return list && list.length ? '<div class="err">' + list.map(esc).join("<br>") + "</div>" : "";
  }
  function pct(x) { return Math.round(x * 100) + "%"; }
  function loadBar(u) {
    var lvl = u >= 0.95 ? "risk" : u >= 0.85 ? "warn" : "";
    return '<div class="row" style="flex-wrap:nowrap"><div class="ubar ' + lvl + '" style="flex:1"><span style="width:' + Math.min(100, Math.round(u * 100)) + '%"></span></div><span class="num" style="min-width:3em">' + pct(u) + "</span></div>";
  }
  function field(label, html) { return '<label class="fld"><span>' + label + "</span>" + html + "</label>"; }
  function input(name, value, attrs) {
    return '<input name="' + name + '" value="' + esc(value == null ? "" : value) + '" ' + (attrs || "") + ">";
  }
  function stepSelect(steps, current) {
    return '<select name="step" required>' + steps.map(function (s) {
      return '<option value="' + esc(s.id) + '"' + (s.id === current ? " selected" : "") + ">" + esc(s.name) + "</option>";
    }).join("") + "</select>";
  }
  function formValues(form) {
    var out = {};
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name) return;
      if (el.type === "checkbox") out[el.name] = el.checked;
      else if (el.type === "number") out[el.name] = el.value === "" ? null : Number(el.value);
      else out[el.name] = el.value.trim() === "" ? null : el.value.trim();
    });
    return out;
  }

  /* ---------- orders: add and edit (D-60) ---------- */
  function orderAddForm() {
    return '<details class="card"><summary><b>Add an order</b> <span class="muted">or just tell SupplyGPT, e.g. "New order for Delhi Mart, 12,000 pairs, ship by 20 August"</span></summary>' +
      '<form class="form" data-form="order-add">' +
      field("Order number", input("id", "", 'maxlength="40" placeholder="Leave empty for the next SO number"')) +
      field("Buyer", input("buyer", "", 'required maxlength="120"')) +
      field("Pairs", input("pairs", "", 'type="number" min="1" required')) +
      field("Ship by", input("ship_by", "", 'type="date" required')) +
      field("Yarn arrives", input("yarn_arrival", "", 'type="date"')) +
      field("Status", '<select name="status"><option value="booked">Booked</option><option value="tentative">Tentative (enquiry)</option></select>') +
      field("Yarn per pair (g)", input("grams_per_pair", "", 'type="number" min="1" step="0.1" placeholder="Same as last order"')) +
      '<div class="actions"><button class="btn primary" type="submit">Add order</button></div></form>' +
      '<p class="muted">Without a yarn date the order is saved but not planned until you add one.</p></details>';
  }
  function orderEditForm(o) {
    if (!o) return "";
    return '<details class="card"><summary><b>Edit this order</b> <span class="muted">yarn date, ship-by, pairs, status</span></summary>' +
      '<form class="form" data-form="order-edit" data-id="' + esc(o.id) + '">' +
      field("Buyer", input("buyer", o.buyer, 'required maxlength="120"')) +
      field("Pairs", input("pairs", o.pairs, 'type="number" min="1" required')) +
      field("Ship by", input("ship_by", o.ship_by, 'type="date" required')) +
      field("Yarn arrives (planned)", input("yarn_arrival", (o.yarn && o.yarn.planned) || o.yarn_arrival || "", 'type="date"')) +
      field("Status", '<select name="status">' + ["booked", "tentative", "done"].map(function (st) { return '<option value="' + st + '"' + (o.status === st ? " selected" : "") + ">" + st + "</option>"; }).join("") + "</select>") +
      '<div class="actions"><button class="btn primary" type="submit">Save</button></div></form></details>';
  }

  /* ---------- machines ---------- */
  function machineForm(steps, m) {
    var edit = !!m; m = m || {};
    return '<form class="form" data-form="' + (edit ? "machine-edit" : "machine-add") + '"' + (edit ? ' data-id="' + esc(m.id) + '"' : "") + ">" +
      field("Name", input("name", m.name, 'required maxlength="80" placeholder="e.g. Knitting machines, new line"')) +
      field("Process step", stepSelect(steps, m.step && m.step.id)) +
      field("How many", input("count", m.count, 'type="number" min="1" max="10000" required')) +
      field("Pairs a day, each", input("rate_per_machine_day", m.rate_per_machine_day, 'type="number" min="1" required')) +
      field("Notes", input("notes", m.notes, 'maxlength="500" placeholder="Optional"')) +
      '<div class="actions"><button class="btn primary" type="submit">' + (edit ? "Save changes" : "Add machines") + "</button></div></form>";
  }
  function downtimeForm(m) {
    return '<form class="form" data-form="downtime-add" data-id="' + esc(m.id) + '">' +
      field("From", input("from_date", "", 'type="date" required')) +
      field("To", input("to_date", "", 'type="date" required')) +
      field("Machines out", input("machines_down", 1, 'type="number" min="1" max="' + m.count + '" required')) +
      field("Kind", '<select name="planned"><option value="true">Planned maintenance</option><option value="false">Probable breakdown</option></select>') +
      field("Reason", input("reason", "", 'maxlength="200" placeholder="e.g. Needle bed service"')) +
      '<div class="actions"><button class="btn primary" type="submit">Add downtime</button></div></form>';
  }
  function viewMachines() {
    return get("/api/machines", true).then(function (d) {
      var total = d.machines.reduce(function (a, m) { return a + m.count; }, 0);
      var dts = d.machines.reduce(function (a, m) { return a.concat(m.downtime); }, []);
      var probable = dts.filter(function (x) { return !x.planned; }).length;
      var bn = d.steps.filter(function (s) { return s.id === d.bottleneck; })[0];
      var h = '<div class="stack-v"><div><h2>Machines</h2><p class="muted">The machines, lines and people you have. For each group: the orders it works on, how loaded it is over the plan, and its downtime.' + (d.horizon ? " Plan window " + fmt(d.horizon.start) + " to " + fmt(d.horizon.end) + "." : "") + "</p></div>";
      h += whatIfHtml() + problemsHtml(d.problems);
      h += '<div class="stats"><div class="card stat"><small>Machine groups</small><strong class="num">' + d.machines.length + '</strong><span class="sub">' + f0(total) + ' machines, lines or people</span></div><div class="card stat"><small>Limit of the plant</small><strong>' + esc(bn ? bn.name : "—") + '</strong><span class="sub">The slowest step sets the pace</span></div><div class="card stat"><small>Downtime windows</small><strong class="num">' + dts.length + '</strong><span class="sub">' + probable + " probable breakdown" + (probable === 1 ? "" : "s") + "</span></div></div>";
      h += '<details class="card"' + (d.machines.length ? "" : " open") + '><summary><b>Add machines</b> <span class="muted">a new group for any step</span></summary>' + (d.steps.length ? machineForm(d.steps) : '<p class="muted">Add a process step in Production plan first.</p>') + "</details>";
      if (!d.machines.length) return h + '<div class="empty">No machines yet.</div></div>';
      var stepName = {}; d.steps.forEach(function (s) { stepName[s.id] = s.name; });
      var lastStep = null;
      d.machines.forEach(function (m) {
        if (m.step.id !== lastStep) { h += '<div class="sect">' + esc(m.step.name) + (m.step.id === d.bottleneck ? ' <span class="chip warn">Limit</span>' : "") + "</div>"; lastStep = m.step.id; }
        var orders = m.orders.length ? m.orders.map(function (o) { return '<span class="chip acc">' + esc(o.id) + "</span>"; }).join(" ") : '<span class="muted">No orders in the plan</span>';
        var dn = m.downtime.length ? '<span class="chip ' + (m.downtime.some(function (x) { return !x.planned; }) ? "risk" : "warn") + '">' + m.downtime.length + " downtime</span>" : "";
        h += '<a class="row-link" href="' + link("machines/" + encodeURIComponent(m.id)) + '"><div><div class="row"><h3>' + esc(m.name) + "</h3>" + dn + '</div><span class="muted num">' + f0(m.count) + " " + esc(m.step.unit) + " × " + f0(m.rate_per_machine_day) + " = " + f0(m.nominal) + ' a day</span><div class="row" style="margin-top:6px">' + orders + '</div></div><div style="min-width:200px"><small class="muted">Load over the plan</small>' + loadBar(m.utilisation) + "</div></a>";
      });
      return h + "</div>";
    });
  }
  function machineChart(m) {
    var days = m.daily, n = days.length;
    if (!n) return '<p class="muted">No plan window yet.</p>';
    var lw = 44, dw = 16, top = 24, hh = 120, W = lw + n * dw + 30, H = top + hh + 22;
    var maxCap = Math.max.apply(null, days.map(function (x) { return Math.max(x.capacity, x.used); }).concat([m.nominal, 1]));
    var s = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" width="' + W + '" role="img" aria-label="Daily load for ' + esc(m.name) + '">';
    var base = top + hh;
    for (var i = 0; i < n; i++) {
      var x = lw + i * dw, d = days[i];
      if (i % 7 === 0) s += '<line class="grid" x1="' + x + '" y1="' + (top - 6) + '" x2="' + x + '" y2="' + base + '"/><text class="tick" x="' + (x + 2) + '" y="' + (top - 10) + '">' + fmt(d.date) + "</text>";
      if (d.capacity < m.nominal) s += '<rect class="down" x="' + x + '" y="' + top + '" width="' + dw + '" height="' + hh + '"><title>' + fmt(d.date) + ": capacity " + f0(d.capacity) + " (downtime)</title></rect>";
      var hu = d.used / maxCap * hh;
      if (hu > 0) s += '<rect class="bar-this" x="' + (x + 1) + '" y="' + (base - hu) + '" width="' + (dw - 3) + '" height="' + hu + '"><title>' + fmt(d.date) + ": " + f0(Math.round(d.used)) + " of " + f0(Math.round(d.capacity)) + "</title></rect>";
      var cy = base - d.capacity / maxCap * hh;
      s += '<path class="capline" d="M' + x + " " + cy + " H" + (x + dw) + '"/>';
    }
    s += '<text class="rowsub" x="0" y="' + (top + 10) + '">' + f0(Math.round(maxCap)) + '</text><text class="rowsub" x="0" y="' + base + '">0</text></svg>';
    return s;
  }
  function viewMachine(r) {
    return Promise.all([get("/api/machines/" + encodeURIComponent(r.id), true), get("/api/production-plan")]).then(function (res) {
      var d = res[0], m = d.machine, steps = res[1].steps;
      var h = '<div class="crumbs"><a href="' + link("machines") + '">Machines</a><span>/</span><span>' + esc(m.name) + '</span></div><div class="stack-v">';
      h += '<div class="card"><div class="row"><h2>' + esc(m.name) + '</h2><span class="chip idle">' + esc(m.step.name) + "</span>" + (m.step.id === d.bottleneck ? '<span class="chip warn">Limit of the plant</span>' : "") + '</div><p class="muted">' + f0(m.count) + " " + esc(m.step.unit) + " × " + f0(m.rate_per_machine_day) + " pairs a day each" + (m.notes ? " · " + esc(m.notes) : "") + "</p></div>";
      h += whatIfHtml() + problemsHtml(d.problems);
      h += '<div class="stats"><div class="card stat"><small>Capacity</small><strong class="num">' + f0(m.nominal) + '</strong><span class="sub">a day with every machine up</span></div><div class="card stat"><small>Load over the plan</small><strong class="num">' + pct(m.utilisation) + '</strong><span class="sub">' + f0(Math.round(m.used)) + " of " + f0(Math.round(m.available)) + ' available</span></div><div class="card stat"><small>Busiest day</small><strong class="num">' + (m.peak.day ? fmt(m.peak.day) : "—") + '</strong><span class="sub">' + (m.peak.day ? pct(m.peak.utilisation) + " of that day's capacity" : "Nothing planned") + '</span></div><div class="card stat"><small>Orders</small><strong class="num">' + m.orders.length + '</strong><span class="sub">in the current plan</span></div></div>';
      h += '<div class="card"><div class="sect">Daily load</div><p class="muted" style="margin:4px 0 8px">Bars are planned work, the dashed line is capacity, shaded days have machines down.</p><div class="chartwrap">' + machineChart(m) + "</div></div>";
      h += '<div class="card"><div class="sect">Orders on these machines</div>' + (m.orders.length ? '<div class="tablewrap" style="margin-top:6px"><table><thead><tr><th>Order</th><th class="r">Pairs here</th><th>From</th><th>To</th></tr></thead><tbody>' + m.orders.map(function (o) { return '<tr><td><a href="' + link("orders/" + encodeURIComponent(o.id)) + '"><b>' + esc(o.id) + '</b></a></td><td class="r num">' + f0(o.pairs) + '</td><td class="num">' + fmt(o.start) + '</td><td class="num">' + fmt(o.finish) + "</td></tr>"; }).join("") + "</tbody></table></div>" : '<p class="muted" style="margin-top:6px">No orders use these machines in the current plan.</p>') + (m.orders.length && steps.some(function (s) { return s.id === m.step.id && s.machines.length > 1; }) ? '<p class="muted" style="margin-top:6px">This step has more than one machine group. Its work is shared between groups by their capacity each day.</p>' : "") + "</div>";
      h += '<div class="card"><div class="sect">Downtime</div>' + (m.downtime.length ? '<div class="tablewrap" style="margin-top:6px"><table><thead><tr><th>Kind</th><th>From</th><th>To</th><th class="r">Out</th><th>Reason</th><th class="r">Output lost</th><th>Orders running then</th><th></th></tr></thead><tbody>' + m.downtime.map(function (x) {
        return '<tr><td><span class="chip ' + (x.planned ? "warn" : "risk") + '">' + (x.planned ? "Planned" : "Probable") + '</span></td><td class="num">' + fmt(x.from) + '</td><td class="num">' + fmt(x.to) + '</td><td class="r num">' + x.machines_down + "</td><td>" + esc(x.reason || "—") + '</td><td class="r num">' + f0(x.lost) + "</td><td>" + (x.affected_orders.length ? x.affected_orders.map(esc).join(", ") : '<span class="muted">None</span>') + '</td><td><button class="btn small" type="button" data-act="downtime-delete" data-id="' + esc(x.id) + '">Remove</button></td></tr>';
      }).join("") + "</tbody></table></div>" : '<p class="muted" style="margin-top:6px">No downtime recorded.</p>') +
        '<details style="margin-top:10px"><summary><b>Add downtime</b> <span class="muted">planned maintenance or a probable breakdown</span></summary>' + downtimeForm(m) + "</details></div>";
      h += '<details class="card"><summary><b>Edit these machines</b></summary>' + machineForm(steps, m) + '<div class="actions"><button class="btn" type="button" data-act="machine-delete" data-id="' + esc(m.id) + '" data-name="' + esc(m.name) + '">Delete this machine group</button></div></details>';
      return h + "</div>";
    });
  }

  /* ---------- production plan ---------- */
  function ganttHtml(plan) {
    if (!plan.orders.length) return '<p class="muted">No orders can be planned yet.</p>';
    var all = [];
    plan.orders.forEach(function (o) { all.push(dayNum(o.dye.start), dayNum(o.ready), dayNum(o.ship_by)); });
    var x0 = Math.min.apply(null, all) - 1, x1 = Math.max.apply(null, all) + 1, days = x1 - x0 + 1;
    var lw = 190, dw = 14, rh = 18, top = 26, rows = [];
    plan.orders.forEach(function (o) {
      rows.push({ head: o });
      rows.push({ o: o, name: "Yarn dyeing (outside)", s: o.dye.start, f: o.dye.finish, cls: "g-ext" });
      o.steps.forEach(function (st) { rows.push({ o: o, name: st.name, s: st.start, f: st.finish, cls: "g-step" }); });
      rows.push({ o: o, name: "Dispatch", s: o.dispatch.start, f: o.dispatch.finish, cls: "g-ext" });
    });
    var W = lw + days * dw + 20, H = top + rows.length * rh + 8;
    var s = '<svg class="chart gantt" viewBox="0 0 ' + W + " " + H + '" width="' + W + '" role="img" aria-label="Production schedule per order">';
    for (var i = 0; i < days; i += 7) { var gx = lw + i * dw; s += '<line class="grid" x1="' + gx + '" y1="' + (top - 4) + '" x2="' + gx + '" y2="' + H + '"/><text class="tick" x="' + (gx + 2) + '" y="' + (top - 8) + '">' + fmt(isoOf(x0 + i)) + "</text>"; }
    var groupTop = 0;
    rows.forEach(function (r, ri) {
      var y = top + ri * rh;
      if (r.head) {
        groupTop = y;
        s += '<text class="rowlab" x="0" y="' + (y + 13) + '">' + esc(r.head.id) + " · " + esc(r.head.buyer) + '</text><text class="readylab ' + r.head.verdict.level + '" x="' + (lw - 8) + '" y="' + (y + 13) + '" text-anchor="end">' + signed(r.head.slack_days) + "</text>";
        var count = r.head.steps.length + 2, sx = lw + (dayNum(r.head.ship_by) - x0) * dw + dw / 2;
        s += '<line class="ship" x1="' + sx + '" y1="' + (y + 2) + '" x2="' + sx + '" y2="' + (y + (count + 1) * rh) + '"><title>' + esc(r.head.id) + " ship by " + fmt(r.head.ship_by) + "</title></line>";
        return;
      }
      var bx = lw + (dayNum(r.s) - x0) * dw, bw = (dayNum(r.f) - dayNum(r.s) + 1) * dw;
      s += '<text class="rowsub" x="12" y="' + (y + 12) + '">' + esc(r.name) + '</text><rect class="' + r.cls + '" x="' + bx + '" y="' + (y + 3) + '" width="' + bw + '" height="' + (rh - 6) + '" rx="3"><title>' + esc(r.o.id + " · " + r.name + ": " + fmt(r.s) + " to " + fmt(r.f)) + "</title></rect>";
    });
    return s + "</svg>";
  }
  function viewProduction() {
    return Promise.all([get("/api/production-plan", true), get("/api/plan", true)]).then(function (res) {
      var pp = res[0], plan = res[1], st = pp.settings, P = state.prod;
      var h = '<div class="stack-v"><div><h2>Production plan</h2><p class="muted">The route every order follows, and which machines do each step. Change it here and every order\'s plan updates.</p></div>' + problemsHtml(pp.problems);
      // flow
      var flow = '<div class="fnode ext"><b>Buy materials</b><span class="s">From suppliers</span><span class="cap">See the Ledger</span></div>' +
        '<div class="fnode ext"><b>Yarn dyeing</b><span class="s">Outside dye house</span><span class="cap">' + plural(st.dye_days) + ", batch days</span></div>";
      pp.steps.forEach(function (s) {
        flow += '<div class="fnode' + (s.bottleneck ? " bn" : "") + (s.machines.length ? "" : " bad") + '"><b>' + esc(s.name) + '</b><span class="s">' + (s.machines.length ? s.machines.map(function (m) { return f0(m.count) + " " + esc(m.name.toLowerCase()); }).join(", ") : "No machines") + '</span><span class="cap num">' + f0(s.nominal) + " a day" + (s.bottleneck ? " · limit" : "") + "</span></div>";
      });
      flow += '<div class="fnode ext"><b>Dispatch</b><span class="s">Truck, port paperwork</span><span class="cap">' + plural(st.dispatch_days) + "</span></div>";
      if (st.transit_days) flow += '<div class="fnode ext"><b>Transit</b><span class="s">To the buyer</span><span class="cap">' + plural(st.transit_days) + "</span></div>";
      h += '<div class="card"><div class="sect">Route</div><p class="muted" style="margin:4px 0 10px">' + pp.steps.length + " steps in your factory. The slowest step sets the pace of the whole plant.</p><div class=\"flow\">" + flow + "</div></div>";
      // route table
      h += '<div class="card"><div class="sect">Steps and machines</div><div class="tablewrap" style="margin-top:6px"><table><thead><tr><th>#</th><th>Step</th><th>Machines doing it</th><th class="r">Pairs a day</th><th style="min-width:150px">Load</th><th>Second shift</th><th></th></tr></thead><tbody>' +
        pp.steps.map(function (s, i) {
          var ms = s.machines.length ? s.machines.map(function (m) { return '<a href="' + link("machines/" + encodeURIComponent(m.id)) + '">' + esc(m.name) + "</a> <span class=\"muted num\">" + f0(m.count) + " × " + f0(m.rate_per_machine_day) + "</span>" + (m.downtime ? ' <span class="chip warn">' + m.downtime + " down</span>" : ""); }).join("<br>") : '<span class="chip risk">No machines</span>';
          return '<tr><td class="num">' + (i + 1) + "</td><td><b>" + esc(s.name) + '</b><br><span class="muted">' + esc(s.unit) + "</span>" + (s.bottleneck ? ' <span class="chip warn">Limit</span>' : "") + "</td><td>" + ms + '</td><td class="r num"><b>' + f0(s.nominal) + "</b></td><td>" + loadBar(s.utilisation) + "</td><td>" + (s.second_shift ? '<span class="chip acc">Yes</span>' : '<span class="muted">No</span>') + '</td><td class="nw"><button class="btn small" type="button" data-act="step-move" data-id="' + esc(s.id) + '" data-dir="up"' + (i === 0 ? " disabled" : "") + ' aria-label="Move ' + esc(s.name) + ' earlier">↑</button> <button class="btn small" type="button" data-act="step-move" data-id="' + esc(s.id) + '" data-dir="down"' + (i === pp.steps.length - 1 ? " disabled" : "") + ' aria-label="Move ' + esc(s.name) + ' later">↓</button> <button class="btn small" type="button" data-act="step-edit" data-id="' + esc(s.id) + '">Edit</button> <button class="btn small" type="button" data-act="step-delete" data-id="' + esc(s.id) + '" data-name="' + esc(s.name) + '">Delete</button></td></tr>' +
            (P.editing === s.id ? '<tr><td></td><td colspan="6"><form class="form" data-form="step-edit" data-id="' + esc(s.id) + '">' + field("Step name", input("name", s.name, 'required maxlength="80"')) + field("Counted in", input("unit", s.unit, 'required maxlength="30"')) + '<label class="check"><input type="checkbox" name="second_shift"' + (s.second_shift ? " checked" : "") + '> Can run a second shift</label><div class="actions"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" data-act="step-edit" data-id="">Cancel</button></div></form></td></tr>' : "");
        }).join("") + "</tbody></table></div>" +
        '<details style="margin-top:10px"><summary><b>Add a step</b> <span class="muted">added at the end of the route; use ↑ to move it</span></summary><form class="form" data-form="step-add">' + field("Step name", input("name", "", 'required maxlength="80" placeholder="e.g. Ironing"')) + field("Counted in", input("unit", "machines", 'required maxlength="30"')) + '<label class="check"><input type="checkbox" name="second_shift"> Can run a second shift</label><div class="actions"><button class="btn primary" type="submit">Add step</button></div></form><p class="muted">Then add the machines that do it in <a href="' + link("machines") + '">Machines</a>.</p></details></div>';
      // outside stages
      h += '<details class="card"><summary><b>Outside stages and shifts</b> <span class="muted">dye batch days ' + st.dye_slots.map(fmt).join(", ") + ", dispatch " + plural(st.dispatch_days) + '</span></summary><form class="form" data-form="settings">' +
        field("Dye batch days (YYYY-MM-DD, comma separated)", input("dye_slots", st.dye_slots.join(", "), 'required style="min-width:340px"')) +
        field("Dyeing takes (days)", input("dye_days", st.dye_days, 'type="number" min="0" max="60" required')) +
        field("Dispatch takes (days)", input("dispatch_days", st.dispatch_days, 'type="number" min="0" max="60" required')) +
        field("Transit to buyer (days)", input("transit_days", st.transit_days, 'type="number" min="0" max="180"')) +
        field("Second shift output ×", input("second_shift_multiplier", st.second_shift_multiplier, 'type="number" min="1" max="3" step="0.05" required')) +
        '<div class="actions"><button class="btn primary" type="submit">Save</button></div></form></details>';
      // schedule
      h += whatIfHtml();
      h += '<div class="card"><div class="sect">Production schedule per order</div><p class="muted" style="margin:4px 0 8px">Each order\'s run through the route, in priority order. Steps overlap because work flows to the next step a day after it starts. The dashed line is the ship-by date.</p><div class="chartwrap">' + ganttHtml(plan) + '</div><div class="legend"><span><i style="background:var(--accent)"></i>Your steps</span><span><i style="background:var(--other)"></i>Outside stages</span></div></div>';
      return h + "</div>";
    });
  }

  /* ---------- SupplyGPT ---------- */
  var GPT_EXAMPLES = [
    "We bought 4 new knitting machines that make 350 pairs a day each.",
    "Two knitting machines will be down next Tuesday to Thursday for needle servicing.",
    "Ravi called. The cotton yarn will come on the 28th.",
    "Which order is most at risk right now?"
  ];
  var GPT_STATUS = {
    pending: ["acc", "Waiting for you"], needs_clarification: ["warn", "Needs an answer"],
    invalid: ["idle", "Not used"], applying: ["acc", "Applying"], applied: ["ok", "Done"], rejected: ["idle", "Rejected"]
  };
  var rec = { recorder: null, chunks: [], started: 0, timer: null, stream: null };

  function proposalLink(p) {
    if (p.status !== "applied") return "";
    var to = p.type === "add_machine" ? "machines/" + encodeURIComponent(p.result_ref)
      : p.type === "update_machine" || p.type === "add_downtime" ? "machines/" + encodeURIComponent(p.payload.machine_id)
      : p.type === "add_step" ? "production" : p.type === "supplier_update" ? "ledger" : null;
    return to ? ' <a href="' + link(to) + '">See it</a>' : "";
  }
  function proposalCard(p) {
    var st = GPT_STATUS[p.status] || ["idle", p.status];
    var acts = "";
    if (p.status === "pending") acts = '<div class="actions"><button class="btn small primary" type="button" data-act="gpt-apply" data-id="' + esc(p.id) + '">Approve</button><button class="btn small" type="button" data-act="gpt-reject" data-id="' + esc(p.id) + '">Reject</button></div>';
    else if (p.status === "needs_clarification") acts = '<div class="actions"><button class="btn small" type="button" data-act="gpt-reject" data-id="' + esc(p.id) + '">Dismiss</button></div>';
    var why = p.reason ? '<p class="' + (p.status === "needs_clarification" ? "" : "muted") + '" style="margin-top:6px">' + (p.status === "needs_clarification" ? "<b>Question:</b> " : "") + esc(p.reason) + "</p>" : "";
    return '<div class="draft prop ' + esc(p.status) + '"><div class="row"><span class="chip ' + st[0] + '">' + st[1] + "</span><b>" + esc(p.summary) + "</b>" + proposalLink(p) + '</div><p class="quote">"' + esc(p.quote) + '"</p>' + why + acts + "</div>";
  }
  function gptTurn(i) {
    var voice = i.kind === "voice";
    var h = '<div class="msg user"><div class="meta">' + (voice ? "Voice" : "Typed") + " · " + esc(i.language || i.language_code || "") + " · " + fmtTime(i.created_at) + "</div>" + esc(i.original_text) +
      (i.english_text && i.english_text !== i.original_text ? '<div class="en">In English: ' + esc(i.english_text) + "</div>" : "") + "</div>";
    h += '<div class="msg bot"><button class="btn small play" type="button" data-act="gpt-play" data-id="' + esc(i.id) + '" aria-label="Play this reply">' + (voice.playing === i.id ? "■ Stop" : "▶ Play") + "</button>" + (i.reply_local ? "<p>" + esc(i.reply_local) + '</p><p class="muted" style="margin-top:4px">' + esc(i.reply) + "</p>" : "<p>" + esc(i.reply || "") + "</p>") +
      i.proposals.map(proposalCard).join("") + "</div>";
    return h;
  }
  function viewGpt() {
    return Promise.all([get("/api/supplygpt/status"), get("/api/supplygpt/history?limit=30")]).then(function (res) {
      var st = res[0], hist = res[1].inputs.slice().reverse(), G = state.gpt;
      var canRecord = !!(window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
      var h = '<div class="stack-v"><div><h2>SupplyGPT</h2><p class="muted">Tell SupplySure what changed: type or speak in your own language. I propose the changes; nothing happens until you approve. Today is ' + fmt(st.today) + ".</p></div>";
      if (!st.configured) h += '<div class="card att warn"><div class="row"><span class="chip warn">Sarvam not connected</span><b>Add your Sarvam API key to use SupplyGPT.</b></div><p class="muted" style="margin-top:4px">Put <code>SARVAM_API_KEY=...</code> in the <code>.env</code> file in the project folder, then restart the server. Voice, translation and understanding all run on Sarvam.</p></div>';
      h += '<div class="card chatcard"><div class="chat" id="gpt-chat">' + (hist.length ? hist.map(gptTurn).join("") : '<p class="muted">No messages yet. Try one of the examples below.</p>') +
        (G.busy ? '<div class="msg bot"><span class="chip acc pulse">' + esc(G.busy) + "</span></div>" : "") + "</div>" +
        (G.error ? '<div class="err" style="margin:0 12px 8px">' + esc(G.error) + "</div>" : "") +
        '<div class="chips">' + GPT_EXAMPLES.map(function (x, n) { return '<button class="fchip" type="button" data-act="gpt-example" data-i="' + n + '">' + esc(x) + "</button>"; }).join("") + "</div>" +
        '<form class="ask" data-form="gpt"><select name="language_code" id="gpt-lang" aria-label="Language">' + Object.keys(st.languages).map(function (k) { return '<option value="' + esc(k) + '"' + (k === G.lang ? " selected" : "") + ">" + esc(st.languages[k]) + "</option>"; }).join("") + "</select>" +
        '<textarea name="text" id="gpt-text" rows="2" maxlength="4000" placeholder="e.g. Two knitting machines will be down next Tuesday" aria-label="Message to SupplyGPT"' + (G.busy ? " disabled" : "") + ">" + esc(G.draft) + "</textarea>" +
        (canRecord ? '<button class="btn mic' + (rec.recorder ? " on" : "") + '" type="button" data-act="gpt-mic" id="gpt-mic"' + (G.busy || !st.configured ? " disabled" : "") + ' aria-label="Record a voice message">' + (rec.recorder ? "■ Stop" : "● Speak") + "</button>" : "") +
        '<button class="btn primary" type="submit"' + (G.busy || !st.configured ? " disabled" : "") + ">Send</button></form>" +
        '<label class="check" style="padding:0 12px 4px;font-size:13px"><input type="checkbox" id="gpt-aloud"' + (G.aloud ? " checked" : "") + "> Read replies aloud for typed messages too (voice messages always get a spoken reply)</label>" +
        '<p class="muted" style="padding:0 12px 10px;font-size:12px">Voice notes up to ' + st.max_audio_seconds + " seconds. " + (canRecord ? "" : "This browser cannot record audio; type instead. ") + "Every proposal quotes your words; numbers and dates are checked in code.</p></div></div>";
      return h;
    });
  }
  function afterGpt() {
    var c = $("#gpt-chat"); if (c) c.scrollTop = c.scrollHeight;
  }
  function gptSend(promise, busyLabel, speak) {
    state.gpt.busy = busyLabel; state.gpt.error = null; render(true);
    return promise.then(function (d) {
      state.gpt.busy = null; state.gpt.draft = "";
      return render(true).then(function () { if (speak && d && d.id) playReply(d.id); });
    }, function (e) {
      state.gpt.busy = null; state.gpt.error = e.message; render(true);
    });
  }
  function gptText(text, lang) {
    return gptSend(send("POST", "/api/supplygpt/text", { text: text, language_code: lang === "unknown" ? null : lang }), "Reading what you wrote", state.gpt.aloud);
  }
  var voice = { audio: null, url: null, playing: null };
  function markPlaying() {
    document.querySelectorAll('[data-act="gpt-play"]').forEach(function (b) {
      b.textContent = voice.playing === b.dataset.id ? "■ Stop" : "▶ Play";
      b.classList.toggle("on", voice.playing === b.dataset.id);
    });
  }
  function stopReply() {
    if (voice.audio) { voice.audio.pause(); voice.audio = null; }
    if (voice.url) { URL.revokeObjectURL(voice.url); voice.url = null; }
    voice.playing = null; markPlaying();
  }
  function playReply(id) {
    stopReply();
    voice.playing = id; markPlaying();
    return fetch("/api/supplygpt/inputs/" + encodeURIComponent(id) + "/speech").then(function (r) {
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) { throw new Error(d.detail || "Could not make the voice reply."); });
      return r.blob();
    }).then(function (blob) {
      if (voice.playing !== id) return;  // stopped while loading
      voice.url = URL.createObjectURL(blob);
      voice.audio = new Audio(voice.url);
      voice.audio.onended = stopReply;
      return voice.audio.play().catch(function () { stopReply(); toast("Press ▶ Play to hear the reply."); });
    }).catch(function (e) { stopReply(); toast(e.message, true); });
  }
  function encodeWav(samples, rate) {
    var buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf), o = 0;
    function str(s) { for (var i = 0; i < s.length; i++) v.setUint8(o++, s.charCodeAt(i)); }
    str("RIFF"); v.setUint32(o, 36 + samples.length * 2, true); o += 4; str("WAVEfmt ");
    v.setUint32(o, 16, true); o += 4; v.setUint16(o, 1, true); o += 2; v.setUint16(o, 1, true); o += 2;
    v.setUint32(o, rate, true); o += 4; v.setUint32(o, rate * 2, true); o += 4; v.setUint16(o, 2, true); o += 2; v.setUint16(o, 16, true); o += 2;
    str("data"); v.setUint32(o, samples.length * 2, true); o += 4;
    for (var i = 0; i < samples.length; i++, o += 2) { var s = Math.max(-1, Math.min(1, samples[i])); v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
    return new Blob([buf], { type: "audio/wav" });
  }
  function toWav16k(blob) {
    // Sarvam prefers 16 kHz mono WAV; fall back to the browser's own format if decoding fails.
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC || !window.OfflineAudioContext) return Promise.resolve(blob);
    return blob.arrayBuffer().then(function (ab) {
      var ctx = new AC();
      return ctx.decodeAudioData(ab).then(function (decoded) {
        ctx.close();
        var off = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * 16000)), 16000);
        var src = off.createBufferSource(); src.buffer = decoded; src.connect(off.destination); src.start();
        return off.startRendering().then(function (r) { return encodeWav(r.getChannelData(0), 16000); });
      });
    }).catch(function () { return blob; });
  }
  function stopRecording() {
    if (!rec.recorder) return;
    clearInterval(rec.timer);
    rec.recorder.stop();
  }
  function startRecording() {
    stopReply();  // do not record our own voice
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      rec.stream = stream; rec.chunks = [];
      var r = new MediaRecorder(stream);
      rec.recorder = r; rec.started = Date.now();
      r.ondataavailable = function (e) { if (e.data && e.data.size) rec.chunks.push(e.data); };
      r.onstop = function () {
        stream.getTracks().forEach(function (t) { t.stop(); });
        var blob = new Blob(rec.chunks, { type: r.mimeType || "audio/webm" });
        rec.recorder = null;
        var lang = ($("#gpt-lang") || {}).value || "unknown";
        gptSend(toWav16k(blob).then(function (audio) {
          var fd = new FormData(), type = (audio.type || "audio/webm").split(";")[0];
          fd.append("file", audio, type === "audio/wav" ? "voice.wav" : "voice." + (type.split("/")[1] || "webm"));
          fd.append("language_code", lang);
          return fetch("/api/supplygpt/voice", { method: "POST", body: fd }).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (d) {
              if (!resp.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Could not send the recording.");
              return d;
            });
          });
        }), "Listening to your voice note", true);
      };
      r.start();
      var btn = $("#gpt-mic"); if (btn) { btn.textContent = "■ Stop 0:00"; btn.classList.add("on"); }
      rec.timer = setInterval(function () {
        var s = Math.floor((Date.now() - rec.started) / 1000), b = $("#gpt-mic");
        if (b) b.textContent = "■ Stop 0:" + (s < 10 ? "0" : "") + s;
        if (s >= 29) stopRecording();  // Sarvam's sync limit is 30 seconds
      }, 250);
    }, function () { state.gpt.error = "Microphone permission was refused. Allow the mic for this site, or type instead."; render(true); });
  }

  /* ---------- router ---------- */
  function route() {
    state.flags = flagsFromHash();  // the URL is the source of truth for what-if settings
    var parts = currentPath().split("/").filter(Boolean);
    var view = parts[0] || "orders";
    if (view === "orders" && parts[1]) return { view: "order", id: decodeURIComponent(parts[1]) };
    if (view === "machines" && parts[1]) return { view: "machine", id: decodeURIComponent(parts[1]) };
    return { view: VIEWS[view] ? view : "orders" };
  }
  var VIEWS = { gpt: viewGpt, orders: viewOrders, order: viewOrder, production: viewProduction, machines: viewMachines, machine: viewMachine, plan: viewPlan, ledger: viewLedger, brain: viewBrain };
  var token = 0;
  function render(keepScroll) {
    var my = ++token, r = route(), main = $("#main"), scroll = keepScroll ? main.scrollTop : 0;
    var title = r.view === "order" ? r.id : r.view === "machine" ? "Machine" : TITLES[r.view];
    $("#title").textContent = title;
    document.title = title + " · SupplySure";
    get("/api/promises").then(function (d) { if (my === token) renderNav(r.view, d.counts.needs_clarification); }, function () { renderNav(r.view, 0); });
    renderNav(r.view, 0);
    return VIEWS[r.view](r).then(function (html) {
      if (my !== token) return;
      main.innerHTML = html;
      if (r.view === "brain") { drawBrain(); renderInspector(); renderAnswer(); }
      if (r.view === "gpt") afterGpt();
      main.scrollTop = scroll;
      if (state.focus) { var el = document.getElementById(state.focus); if (el) el.focus(); state.focus = null; }
    }).catch(function (e) {
      if (my !== token) return;
      main.innerHTML = '<div class="err">Could not load this view. ' + esc(e.message) + "</div>";
    });
  }

  /* ---------- events ---------- */
  var debounce = null;
  document.addEventListener("input", function (e) {
    var t = e.target;
    if (t.id === "gpt-text") state.gpt.draft = t.value;
    if (t.dataset && t.dataset.flag === "yarn_delay_days") {
      var v = $("#ydv"); if (v) v.textContent = plural(+t.value);
      clearTimeout(debounce);
      debounce = setTimeout(function () {
        state.focus = t.id;
        setFlags(Object.assign({}, state.flags, { yarn_delay_days: +t.value }));
      }, 160);
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.target && e.target.id === "gpt-text" && e.key === "Enter" && !e.shiftKey) {
      e.preventDefault(); var f = e.target.form; if (f) f.requestSubmit();
    }
  });
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t.id === "gpt-lang") state.gpt.lang = t.value;
    if (t.id === "gpt-aloud") state.gpt.aloud = t.checked;
    if (t.dataset && t.dataset.flag && t.type === "checkbox") {
      var next = Object.assign({}, state.flags); next[t.dataset.flag] = t.checked;
      state.focus = t.id; setFlags(next);
    }
  });
  document.addEventListener("click", function (e) {
    var g = e.target.closest && e.target.closest("g.gn");
    if (g) { selectNode(g.getAttribute("data-node")); return; }
    var b = e.target.closest && e.target.closest("[data-act]");
    if (!b) return;
    var act = b.dataset.act;
    if (act === "reset-flags") setFlags(Object.assign({}, DEFAULT_FLAGS));
    else if (act === "apply-scenario") {
      var sc = scenarios.filter(function (x) { return x.key === b.dataset.key; })[0];
      if (sc) setFlags({ second_shift: sc.flags.second_shift, new_request_ahead: sc.flags.new_request_ahead, yarn_delay_days: sc.flags.yarn_delay_days, knit_down: sc.flags.extra_down.indexOf(KNIT_DOWN) >= 0 });
    }
    else if (act === "ledger-tab") { state.ledger.tab = b.dataset.t; render(); }
    else if (act === "ledger-status") { state.ledger.status = b.dataset.s; render(true); }
    else if (act === "filter") { var t = b.dataset.t; state.brain.filter[t] = state.brain.filter[t] === false; b.setAttribute("aria-pressed", String(state.brain.filter[t] !== false)); drawBrain(); }
    else if (act === "node") selectNode(b.dataset.node);
    else if (act === "trace" && state.brain.sel) runTrace(state.brain.sel, b.dataset.dir);
    else if (act === "brain-clear") { state.brain.sel = null; state.brain.hl = null; state.brain.answer = null; drawBrain(); renderInspector(); renderAnswer(); }
    else if (act === "question") { var q = brainQuestions[+b.dataset.i]; if (q) q.run(); }
    else if (act === "gpt-example") { state.gpt.draft = GPT_EXAMPLES[+b.dataset.i]; render(true); var ta = $("#gpt-text"); if (ta) ta.focus(); }
    else if (act === "gpt-play") { if (voice.playing === b.dataset.id) stopReply(); else playReply(b.dataset.id); }
    else if (act === "gpt-mic") { if (rec.recorder) stopRecording(); else startRecording(); }
    else if (act === "gpt-apply") write("POST", "/api/supplygpt/proposals/" + encodeURIComponent(b.dataset.id) + "/apply", undefined, "Done. The plan has been updated.");
    else if (act === "gpt-reject") write("POST", "/api/supplygpt/proposals/" + encodeURIComponent(b.dataset.id) + "/reject", undefined, "Rejected.");
    else if (act === "step-edit") { state.prod.editing = b.dataset.id || null; render(true); }
    else if (act === "step-move") write("POST", "/api/process/steps/" + encodeURIComponent(b.dataset.id) + "/move", { direction: b.dataset.dir }, "Route updated.");
    else if (act === "step-delete") { if (confirm("Delete the step \"" + b.dataset.name + "\" from the route?")) write("DELETE", "/api/process/steps/" + encodeURIComponent(b.dataset.id), undefined, "Step deleted."); }
    else if (act === "downtime-delete") write("DELETE", "/api/downtime/" + encodeURIComponent(b.dataset.id), undefined, "Downtime removed.");
    else if (act === "machine-delete") {
      if (confirm("Delete \"" + b.dataset.name + "\" and its downtime?")) write("DELETE", "/api/machines/" + encodeURIComponent(b.dataset.id), undefined, "Machine group deleted.").then(function (ok) { if (ok) location.hash = link("machines"); });
    }
  });
  document.addEventListener("keydown", function (e) {
    var g = e.target.closest && e.target.closest("g.gn");
    if (g && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); selectNode(g.getAttribute("data-node")); }
  });
  function write(method, path, body, okMsg) {
    return send(method, path, body).then(function (data) { toast(okMsg); render(true); return data || true; }, function (e) { toast(e.message, true); return null; });
  }
  document.addEventListener("submit", function (e) {
    var f = e.target, kind = f.dataset && f.dataset.form;
    if (!kind) return;
    e.preventDefault();
    if (kind === "gpt") {
      var text = f.text.value.trim();
      if (text) { state.gpt.draft = text; state.gpt.lang = f.language_code.value; gptText(text, f.language_code.value); }
      return;
    }
    var v = formValues(f), id = f.dataset.id;
    if (kind === "order-add") {
      if (v.id) v.id = v.id.toUpperCase();
      write("POST", "/api/orders", v, "Order added.").then(function (d) { if (d && d.id) location.hash = link("orders/" + encodeURIComponent(d.id)); });
    }
    else if (kind === "order-edit") write("PATCH", "/api/orders/" + encodeURIComponent(id), v, "Order saved.");
    else if (kind === "machine-add") write("POST", "/api/machines", v, "Machines added.");
    else if (kind === "machine-edit") write("PUT", "/api/machines/" + encodeURIComponent(id), v, "Saved.");
    else if (kind === "downtime-add") { v.machine_id = id; v.planned = v.planned === "true"; write("POST", "/api/downtime", v, "Downtime added."); }
    else if (kind === "step-add") write("POST", "/api/process/steps", v, "Step added at the end of the route.");
    else if (kind === "step-edit") write("PUT", "/api/process/steps/" + encodeURIComponent(id), v, "Step saved.").then(function (ok) { if (ok) { state.prod.editing = null; render(true); } });
    else if (kind === "settings") {
      v.dye_slots = (v.dye_slots || "").split(/[\s,]+/).filter(Boolean);
      write("PUT", "/api/process/settings", v, "Settings saved.");
    }
  });
  window.addEventListener("hashchange", function () {
    if (currentPath().split("/")[0] !== "gpt") stopReply(); var k = keepScrollNext; keepScrollNext = false; render(k); });
  render();
})();
