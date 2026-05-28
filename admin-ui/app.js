(function () {
  "use strict";

  // ----------------------------------------------------------------- helpers
  function h(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        const v = attrs[k];
        if (v == null || v === false) continue;
        if (k === "class") e.className = v;
        else if (k === "html") e.innerHTML = v;
        else if (k === "text") e.textContent = v;
        else if (k === "dataset") { for (const d in v) e.dataset[d] = v[d]; }
        else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
        else if (k === "value") e.value = v;
        else if (k === "checked") e.checked = !!v;
        else e.setAttribute(k, v);
      }
    }
    if (children != null) append(e, children);
    return e;
  }
  function append(parent, child) {
    if (child == null || child === false) return;
    if (Array.isArray(child)) { child.forEach((c) => append(parent, c)); return; }
    if (child instanceof Node) { parent.appendChild(child); return; }
    parent.appendChild(document.createTextNode(String(child)));
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function $(sel, root) { return (root || document).querySelector(sel); }

  // ----------------------------------------------------------------- api client
  async function api(path, opts) {
    opts = opts || {};
    const init = {
      method: opts.method || "GET",
      credentials: "same-origin",
      headers: {},
    };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    let res;
    try {
      res = await fetch("/api" + path, init);
    } catch (e) {
      setConn(false, "network error");
      throw new Error("Network error: " + e.message);
    }
    setConn(res.ok, res.ok ? "connected" : "HTTP " + res.status);
    if (!res.ok) {
      let detail = "HTTP " + res.status;
      try { const j = await res.json(); if (j && j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) {}
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    const ct = res.headers.get("content-type") || "";
    return ct.includes("application/json") ? res.json() : res.text();
  }

  function setConn(ok, text) {
    const c = $("#conn"); if (!c) return;
    c.className = "conn " + (ok ? "ok" : "err");
    $("#connText").textContent = text;
  }

  // ----------------------------------------------------------------- toast / modal
  function toast(title, msg, type) {
    const root = $("#toastRoot");
    const t = h("div", { class: "toast " + (type || "") }, [
      h("div", { class: "t-title", text: title }),
      msg ? h("div", { class: "t-msg", text: msg }) : null,
    ]);
    root.appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 300); }, 3800);
  }

  function confirmModal(opts) {
    return new Promise((resolve) => {
      const root = $("#modalRoot");
      function close(v) { clear(root); root.style.pointerEvents = "none"; resolve(v); }
      const overlay = h("div", { class: "modal-overlay", onclick: (e) => { if (e.target === overlay) close(false); } }, [
        h("div", { class: "modal" }, [
          h("div", { class: "modal-head", text: opts.title || "Confirm" }),
          h("div", { class: "modal-body", text: opts.message || "" }),
          h("div", { class: "modal-foot" }, [
            h("button", { class: "btn btn-ghost", onclick: () => close(false), text: opts.cancel || "Cancel" }),
            h("button", { class: "btn " + (opts.danger ? "btn-danger" : "btn-primary"), onclick: () => close(true), text: opts.confirm || "Confirm" }),
          ]),
        ]),
      ]);
      root.style.pointerEvents = "auto";
      clear(root).appendChild(overlay);
    });
  }

  // ----------------------------------------------------------------- formatting
  function fmtNum(n) { return n == null ? "—" : Number(n).toLocaleString(); }
  function fmtPrice(p) { return p == null || p === "" ? "—" : "$" + Number(p).toFixed(2); }
  function fmtDate(s) {
    if (!s) return "—";
    const d = new Date(s);
    if (isNaN(d)) return s;
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function fmtDur(s) { return s == null ? "—" : Number(s).toFixed(1) + "s"; }
  function ago(s) {
    if (!s) return "—";
    const diff = (Date.now() - new Date(s).getTime()) / 1000;
    if (isNaN(diff)) return s;
    if (diff < 60) return Math.floor(diff) + "s ago";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
  }
  function statusBadge(s) {
    const map = { done: "green", running: "blue", error: "red", sent: "green", pending: "amber", failed: "red" };
    return h("span", { class: "badge " + (map[s] || "") }, [h("span", { class: "dot" }), s || "—"]);
  }

  // ----------------------------------------------------------------- tag input
  function tagInput(values, placeholder) {
    const state = { values: Array.isArray(values) ? values.slice() : [] };
    const wrap = h("div", { class: "taginput" });
    const input = h("input", { type: "text", placeholder: placeholder || "Type and press Enter…" });
    function render() {
      clear(wrap);
      state.values.forEach((val, i) => {
        wrap.appendChild(h("span", { class: "tag" }, [
          val,
          h("button", { type: "button", text: "×", onclick: () => { state.values.splice(i, 1); render(); } }),
        ]));
      });
      wrap.appendChild(input);
    }
    function add() {
      const v = input.value.trim();
      if (v && state.values.indexOf(v) === -1) { state.values.push(v); }
      input.value = ""; render(); input.focus();
    }
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
      else if (e.key === "Backspace" && !input.value && state.values.length) { state.values.pop(); render(); }
    });
    input.addEventListener("blur", add);
    wrap.addEventListener("click", () => input.focus());
    render();
    return { el: wrap, get: () => state.values.slice() };
  }

  // ----------------------------------------------------------------- form bits
  function field(label, control, opts) {
    opts = opts || {};
    return h("div", { class: "field" + (opts.full ? " full" : ""), style: opts.span ? "grid-column: span " + opts.span : null }, [
      h("label", {}, [label, opts.desc ? h("span", { class: "desc", text: "  " + opts.desc }) : null]),
      control,
    ]);
  }
  function checkbox(label, checked) {
    const input = h("input", { type: "checkbox", checked: !!checked });
    const row = h("div", { class: "checkbox-row" }, [input, h("label", { onclick: () => { input.checked = !input.checked; }, text: label })]);
    return { el: row, get: () => input.checked, input: input };
  }

  // ----------------------------------------------------------------- chart helper
  const charts = [];
  function destroyCharts() { while (charts.length) { try { charts.pop().destroy(); } catch (e) {} } }
  function makeChart(canvas, config) {
    if (typeof Chart === "undefined") return null;
    Chart.defaults.color = "#9aa6bd";
    Chart.defaults.borderColor = "#232c40";
    Chart.defaults.font.family = "Inter, system-ui, sans-serif";
    const c = new Chart(canvas, config);
    charts.push(c);
    return c;
  }

  // ----------------------------------------------------------------- view shell
  const view = () => $("#view");
  function setCrumbs(parts) {
    const c = $("#crumbs"); clear(c);
    parts.forEach((p, i) => {
      if (i > 0) c.appendChild(h("span", { class: "muted", text: "  /  " }));
      if (p.href) c.appendChild(h("a", { href: p.href, text: p.text }));
      else c.appendChild(h("span", { text: p.text }));
    });
  }
  function setActions(nodes) { const a = clear($("#topbarActions")); append(a, nodes); }
  function showLoading() { clear(view()).appendChild(h("div", { class: "loading", text: "Loading…" })); }
  function showError(e) {
    clear(view()).appendChild(h("div", { class: "error-box" }, [
      h("strong", { text: "Failed to load. " }),
      h("span", { text: e.message || String(e) }),
    ]));
  }
  function emptyState(msg) {
    return h("div", { class: "empty" }, [h("div", { class: "big", text: "∅" }), h("div", { text: msg })]);
  }

  // ================================================================= PAGES

  // ---------------------------------------------------------- Dashboard
  async function renderDashboard() {
    setCrumbs([{ text: "Dashboard" }]); setActions(null); showLoading();
    let summary, runs, metrics;
    try {
      [summary, runs, metrics] = await Promise.all([
        api("/dashboard/summary"),
        api("/dashboard/runs?limit=25"),
        api("/dashboard/metrics/timeseries?limit=40"),
      ]);
    } catch (e) { showError(e); return; }

    const cards = [
      { label: "Active Groups", value: summary.active_groups, cls: "" },
      { label: "Tracked Products", value: summary.tracked_products, cls: "" },
      { label: "In Stock", value: summary.in_stock, cls: "accent-green" },
      { label: "Alerts Pending", value: summary.alerts_pending, cls: "accent-amber" },
      { label: "Alerts Sent (24h)", value: summary.alerts_sent_24h, cls: "accent-green" },
      { label: "Jobs In Flight", value: summary.jobs_in_flight, cls: summary.jobs_in_flight ? "accent-amber" : "" },
    ];

    const root = clear(view());
    root.appendChild(h("div", { class: "cards" }, cards.map((c) =>
      h("div", { class: "card " + c.cls }, [h("div", { class: "label", text: c.label }), h("div", { class: "value num", text: fmtNum(c.value) })])
    )));

    // charts
    const chartsWrap = h("div", { class: "chart-grid" });
    root.appendChild(chartsWrap);
    const noChart = typeof Chart === "undefined";
    function chartBox(title, builder) {
      const canvas = h("canvas");
      const box = h("div", { class: "chart-box" }, [h("h4", { text: title }),
        noChart ? h("div", { class: "muted", text: "Chart library unavailable." }) : h("div", { class: "chart-canvas-wrap" }, canvas)]);
      chartsWrap.appendChild(box);
      if (!noChart && metrics.length) builder(canvas);
      else if (!noChart) box.querySelector(".chart-canvas-wrap").replaceWith(h("div", { class: "muted", text: "No metrics yet." }));
    }
    const labels = metrics.map((m) => fmtDate(m.started_at));
    chartBox("Run Duration (sec)", (cv) => makeChart(cv, {
      type: "line",
      data: { labels: labels, datasets: [{ label: "duration_sec", data: metrics.map((m) => m.duration_sec), borderColor: "#5b8cff", backgroundColor: "rgba(91,140,255,.15)", fill: true, tension: 0.3, pointRadius: 2 }] },
      options: chartOpts(),
    }));
    chartBox("Items OK vs Skipped", (cv) => makeChart(cv, {
      type: "line",
      data: { labels: labels, datasets: [
        { label: "items_ok", data: metrics.map((m) => m.items_ok), borderColor: "#34d399", backgroundColor: "rgba(52,211,153,.12)", fill: true, tension: 0.3, pointRadius: 2 },
        { label: "items_skipped", data: metrics.map((m) => m.items_skipped), borderColor: "#fbbf24", backgroundColor: "rgba(251,191,36,.10)", fill: true, tension: 0.3, pointRadius: 2 },
      ] },
      options: chartOpts(true),
    }));
    chartBox("Alerts Emitted", (cv) => makeChart(cv, {
      type: "bar",
      data: { labels: labels, datasets: [{ label: "alerts_emitted", data: metrics.map((m) => m.alerts_emitted), backgroundColor: "#7c5cff", borderRadius: 4 }] },
      options: chartOpts(),
    }));
    chartBox("CAPTCHA Count", (cv) => makeChart(cv, {
      type: "bar",
      data: { labels: labels, datasets: [{ label: "captcha", data: metrics.map((m) => m.captcha), backgroundColor: "#f87171", borderRadius: 4 }] },
      options: chartOpts(),
    }));

    // recent runs table
    const panel = h("div", { class: "panel" }, [
      h("div", { class: "panel-head" }, [h("h3", { text: "Recent Runs" }), h("span", { class: "hint", text: runs.length + " runs" })]),
    ]);
    if (!runs.length) panel.appendChild(h("div", { class: "panel-body" }, emptyState("No runs recorded yet.")));
    else {
      const rows = runs.map((r) => h("tr", {}, [
        h("td", { class: "num", text: "#" + r.id }),
        h("td", { text: r.group_name || ("group " + r.group_id) }),
        h("td", {}, statusBadge(r.status)),
        h("td", { text: r.trigger || "—" }),
        h("td", { class: "num", text: fmtDur(r.duration_sec) }),
        h("td", { class: "num", text: r.net_kb == null ? "—" : fmtNum(r.net_kb) }),
        h("td", { class: "num", text: fmtNum(r.blocked_heavy) }),
        h("td", { class: "num", text: r.items_ok == null ? "—" : r.items_ok + " / " + fmtNum(r.items_skipped) }),
        h("td", { class: "num", text: fmtNum(r.alerts_emitted) }),
        h("td", { class: "num", text: r.captcha ? "⚠ " + r.captcha : "0" }),
        h("td", { class: "muted", text: ago(r.started_at) }),
        h("td", { class: "muted cell-title", title: r.error || "", text: r.error || "" }),
      ]));
      panel.appendChild(h("div", { class: "table-wrap" }, h("table", { class: "tbl" }, [
        h("thead", {}, h("tr", {}, ["Run", "Group", "Status", "Trigger", "Duration", "Net KB", "Blocked", "OK / Skip", "Alerts", "CAPTCHA", "Started", "Error"].map((t) => h("th", { text: t })))),
        h("tbody", {}, rows),
      ])));
    }
    root.appendChild(panel);
  }

  function chartOpts(legend) {
    return {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: !!legend, labels: { boxWidth: 10, font: { size: 11 } } } },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 6, font: { size: 10 } } },
        y: { beginAtZero: true, grid: { color: "rgba(35,44,64,.5)" }, ticks: { font: { size: 10 } } },
      },
    };
  }

  // ---------------------------------------------------------- Groups list
  async function renderGroups() {
    setCrumbs([{ text: "Groups" }]);
    setActions(h("button", { class: "btn btn-primary", onclick: () => { location.hash = "#/groups/new"; } }, [h("span", { class: "ico", text: "＋" }), "New Group"]));
    showLoading();
    let groups;
    try { groups = await api("/groups"); } catch (e) { showError(e); return; }

    const root = clear(view());
    root.appendChild(h("div", { class: "page-head" }, [
      h("div", {}, [h("h2", { text: "Scrape Groups" }), h("div", { class: "sub", text: "Configure what, where and how products are tracked." })]),
    ]));

    if (!groups.length) {
      const p = h("div", { class: "panel" }, h("div", { class: "panel-body" }, emptyState("No groups yet. Create your first group to start tracking.")));
      root.appendChild(p); return;
    }

    const rows = groups.map((g) => {
      const toggle = h("input", { type: "checkbox", checked: g.enabled });
      toggle.addEventListener("change", async () => {
        try { await api("/groups/" + g.id, { method: "PUT", body: { enabled: toggle.checked } }); toast("Group updated", g.name + (toggle.checked ? " enabled" : " disabled"), "success"); }
        catch (e) { toggle.checked = !toggle.checked; toast("Update failed", e.message, "error"); }
      });
      return h("tr", { class: "clickable", onclick: (ev) => { if (ev.target.closest(".no-nav")) return; location.hash = "#/groups/" + g.id; } }, [
        h("td", { class: "num", text: "#" + g.id }),
        h("td", {}, h("strong", { text: g.name })),
        h("td", {}, h("span", { class: "badge " + (g.kind === "serp" ? "blue" : ""), text: g.kind })),
        h("td", { text: g.niche || "—" }),
        h("td", {}, h("span", { class: "badge", text: g.cadence })),
        h("td", { class: "num", text: g.kind === "serp" ? fmtNum(g.serp_count) + " serp" : fmtNum(g.pdp_count) + " asin" }),
        h("td", { class: "no-nav" }, h("label", { class: "switch" }, [toggle, h("span", { class: "slider" })])),
        h("td", { class: "no-nav cell-actions" }, [
          h("button", { class: "btn btn-sm btn-ghost", onclick: () => { location.hash = "#/groups/" + g.id; }, text: "Edit" }),
          h("button", { class: "btn btn-sm btn-danger", onclick: () => deleteGroup(g) }, "Delete"),
        ]),
      ]);
    });
    root.appendChild(h("div", { class: "panel" }, h("div", { class: "table-wrap" }, h("table", { class: "tbl" }, [
      h("thead", {}, h("tr", {}, ["ID", "Name", "Kind", "Niche", "Cadence", "Targets", "Enabled", ""].map((t) => h("th", { text: t })))),
      h("tbody", {}, rows),
    ]))));
  }

  async function deleteGroup(g) {
    const ok = await confirmModal({ title: "Delete group?", message: 'Permanently delete "' + g.name + '" and all its targets, products and history.', confirm: "Delete", danger: true });
    if (!ok) return;
    try { await api("/groups/" + g.id, { method: "DELETE" }); toast("Deleted", g.name + " removed", "success"); renderGroups(); }
    catch (e) { toast("Delete failed", e.message, "error"); }
  }

  // ---------------------------------------------------------- Group editor (builder)
  async function renderGroupEditor(id) {
    const isNew = id === "new";
    setCrumbs([{ text: "Groups", href: "#/groups" }, { text: isNew ? "New Group" : "Edit #" + id }]);
    setActions(null); showLoading();

    let group, profiles, defaults, targets = { pdp: [], serp: [] };
    try {
      profiles = await api("/selector_profiles");
      if (isNew) {
        defaults = await api("/filter_defaults");
        group = { name: "", kind: "pdp", niche: "", cadence: "short", interval_minutes: null, enabled: true, headless: true, max_concurrent: 2, selector_profile_id: null, notify_channel: "", filter: defaults };
      } else {
        group = await api("/groups/" + id);
        targets = await api("/groups/" + id + "/targets");
        if (!group.filter) { defaults = await api("/filter_defaults"); group.filter = defaults; }
      }
    } catch (e) { showError(e); return; }

    const f = group.filter || {};
    const root = clear(view());
    root.appendChild(h("div", { class: "page-head" }, [
      h("div", {}, [h("h2", { text: isNew ? "New Group" : "Edit: " + group.name }), h("div", { class: "sub", text: "Group Builder — define basics, filters and targets." })]),
      h("div", { class: "toolbar" }, [
        h("button", { class: "btn btn-ghost", onclick: () => { location.hash = "#/groups"; }, text: "Back" }),
        h("button", { class: "btn btn-primary", id: "saveGroup" }, [h("span", { class: "ico", text: "✓" }), isNew ? "Create Group" : "Save Changes"]),
      ]),
    ]));

    // --- Basic section ---
    const nameI = h("input", { type: "text", value: group.name || "", placeholder: "e.g. Tech Deals — Newest" });
    const kindSel = h("select", {}, [opt("pdp", "PDP (track ASINs)"), opt("serp", "SERP (search pages)")]); kindSel.value = group.kind;
    const nicheI = h("input", { type: "text", value: group.niche || "", placeholder: "e.g. electronics" });
    const cadenceSel = h("select", {}, [opt("short", "Short"), opt("long", "Long")]); cadenceSel.value = group.cadence;
    const intervalI = h("input", { type: "number", min: "1", value: group.interval_minutes != null ? group.interval_minutes : "", placeholder: "auto" });
    const concurrentI = h("input", { type: "number", min: "1", value: group.max_concurrent != null ? group.max_concurrent : 2 });
    const notifyI = h("input", { type: "text", value: group.notify_channel || "", placeholder: "WhatsApp group id…" });
    const profSel = h("select", {}, [opt("", "— none —")].concat(profiles.map((p) => opt(String(p.id), p.name + (p.is_default ? " (default)" : "")))));
    profSel.value = group.selector_profile_id != null ? String(group.selector_profile_id) : "";
    const enabledCb = checkbox("Enabled", group.enabled);
    const headlessCb = checkbox("Headless browser", group.headless);

    root.appendChild(formSection("Basics", null,
      h("div", { class: "form-grid" }, [
        field("Name", nameI),
        field("Kind", kindSel, { desc: isNew ? "" : "(fixed after creation)" }),
        field("Niche", nicheI),
        field("Cadence", cadenceSel),
        field("Interval (minutes)", intervalI, { desc: "optional override" }),
        field("Max concurrent", concurrentI),
        field("Selector profile", profSel),
        field("Notify channel", notifyI),
        h("div", { class: "full checkbox-grid" }, [enabledCb.el, headlessCb.el]),
      ])
    ));
    if (!isNew) kindSel.disabled = true;

    // --- Filters section ---
    const accepted = tagInput(f.accepted_sellers, "Add seller and press Enter");
    const reqKw = tagInput(f.required_keywords, "Required keyword…");
    const blKw = tagInput(f.blacklist_keywords, "Blacklisted keyword…");
    const blAsin = tagInput(f.blacklist_asins, "Blacklisted ASIN…");
    const minP = h("input", { type: "number", step: "0.01", value: f.min_price != null ? f.min_price : "", placeholder: "min" });
    const maxP = h("input", { type: "number", step: "0.01", value: f.max_price != null ? f.max_price : "", placeholder: "max" });
    const dropP = h("input", { type: "number", step: "0.5", value: f.price_drop_percent != null ? f.price_drop_percent : 10 });
    const cFreeShip = checkbox("Require free shipping", f.require_free_shipping);
    const cShipSig = checkbox("Require shipping signal", f.require_shipping_signal);
    const cShippable = checkbox("Require shippable", f.require_shippable);
    const cAlertNew = checkbox("Alert on new product", f.alert_new);
    const cAlertStock = checkbox("Alert on back in stock", f.alert_back_in_stock);
    const cAlertDrop = checkbox("Alert on price drop", f.alert_price_drop);

    root.appendChild(formSection("Filters", "Decide which products qualify and when to alert.",
      [
        h("div", { class: "form-grid" }, [
          field("Accepted sellers", accepted.el, { full: true, desc: "empty = any seller" }),
          field("Required keywords", reqKw.el, { full: true }),
          field("Blacklist keywords", blKw.el, { full: true }),
          field("Blacklist ASINs", blAsin.el, { full: true }),
          field("Min price", minP),
          field("Max price", maxP),
          field("Price drop %", dropP, { desc: "threshold for price-drop alerts" }),
        ]),
        h("div", { class: "form-section", style: "margin-top:16px" }, [
          h("div", { class: "form-section-head" }, h("h4", { text: "Shipping requirements" })),
          h("div", { class: "form-section-body checkbox-grid" }, [cFreeShip.el, cShipSig.el, cShippable.el]),
        ]),
        h("div", { class: "form-section" }, [
          h("div", { class: "form-section-head" }, h("h4", { text: "Alert triggers" })),
          h("div", { class: "form-section-body checkbox-grid" }, [cAlertNew.el, cAlertStock.el, cAlertDrop.el]),
        ]),
      ]
    ));

    // --- Targets section ---
    const targetsHost = h("div");
    root.appendChild(targetsHost);
    function renderTargets() {
      clear(targetsHost);
      const kind = kindSel.value;
      if (isNew) {
        targetsHost.appendChild(formSection("Targets", null, h("div", { class: "muted", text: "Save the group first, then add " + (kind === "serp" ? "search URLs." : "ASINs.") })));
        return;
      }
      if (kind === "pdp") targetsHost.appendChild(pdpTargets(id, targets.pdp));
      else targetsHost.appendChild(serpTargets(id, targets.serp));
    }
    kindSel.addEventListener("change", renderTargets);
    renderTargets();

    // --- Save handler ---
    $("#saveGroup").addEventListener("click", async () => {
      const btn = $("#saveGroup"); btn.disabled = true;
      const filterBody = {
        accepted_sellers: accepted.get(), required_keywords: reqKw.get(),
        blacklist_keywords: blKw.get(), blacklist_asins: blAsin.get(),
        min_price: numOrNull(minP.value), max_price: numOrNull(maxP.value),
        require_free_shipping: cFreeShip.get(), require_shipping_signal: cShipSig.get(),
        require_shippable: cShippable.get(), price_drop_percent: numOrNull(dropP.value) ?? 10,
        alert_new: cAlertNew.get(), alert_back_in_stock: cAlertStock.get(), alert_price_drop: cAlertDrop.get(),
      };
      if (!nameI.value.trim()) { toast("Name required", "Please enter a group name.", "error"); btn.disabled = false; nameI.focus(); return; }
      try {
        if (isNew) {
          const body = {
            name: nameI.value.trim(), kind: kindSel.value, niche: nicheI.value.trim() || null,
            cadence: cadenceSel.value, interval_minutes: intOrNull(intervalI.value), enabled: enabledCb.get(),
            selector_profile_id: profSel.value ? Number(profSel.value) : null, headless: headlessCb.get(),
            max_concurrent: intOrNull(concurrentI.value) ?? 2, notify_channel: notifyI.value.trim() || null,
            filter: filterBody,
          };
          const created = await api("/groups", { method: "POST", body: body });
          toast("Group created", created.name, "success");
          location.hash = "#/groups/" + created.id;
        } else {
          const body = {
            name: nameI.value.trim(), niche: nicheI.value.trim() || null, cadence: cadenceSel.value,
            interval_minutes: intOrNull(intervalI.value), enabled: enabledCb.get(),
            selector_profile_id: profSel.value ? Number(profSel.value) : null, headless: headlessCb.get(),
            max_concurrent: intOrNull(concurrentI.value) ?? 2, notify_channel: notifyI.value.trim() || null,
            filter: filterBody,
          };
          await api("/groups/" + id, { method: "PUT", body: body });
          toast("Saved", "Group updated", "success");
        }
      } catch (e) { toast("Save failed", e.message, "error"); }
      finally { btn.disabled = false; }
    });
  }

  function pdpTargets(groupId, list) {
    const asinI = h("input", { type: "text", placeholder: "ASIN e.g. B0XXXXXXX" });
    const notesI = h("input", { type: "text", placeholder: "Notes (optional)" });
    const body = h("div", { class: "form-section-body" });
    const sec = h("div", { class: "form-section" }, [
      h("div", { class: "form-section-head" }, [h("h4", { text: "PDP Targets — ASINs" }), h("span", { class: "tag-kind", text: list.length + " tracked" })]),
      body,
    ]);
    const addBtn = h("button", { class: "btn btn-primary", text: "Add" });
    body.appendChild(h("div", { class: "target-add" }, [
      field("ASIN", asinI), field("Notes", notesI), addBtn,
    ]));
    const listHost = h("div"); body.appendChild(listHost);
    function renderList() {
      clear(listHost);
      if (!list.length) { listHost.appendChild(h("div", { class: "muted", style: "padding:8px 0", text: "No ASINs yet." })); return; }
      listHost.appendChild(h("div", { class: "table-wrap" }, h("table", { class: "tbl" }, [
        h("thead", {}, h("tr", {}, ["ASIN", "Enabled", "Notes", ""].map((t) => h("th", { text: t })))),
        h("tbody", {}, list.map((t) => h("tr", {}, [
          h("td", { class: "mono", text: t.asin }),
          h("td", {}, t.enabled ? statusBadge("done") : h("span", { class: "badge", text: "off" })),
          h("td", { text: t.notes || "—" }),
          h("td", { class: "cell-actions" }, h("button", { class: "btn btn-sm btn-danger", onclick: () => delTarget("pdp", t) }, "Remove")),
        ]))),
      ])));
    }
    async function delTarget(kind, t) {
      try { await api("/pdp_targets/" + t.id, { method: "DELETE" }); list.splice(list.indexOf(t), 1); renderList(); toast("Removed", t.asin, "success"); }
      catch (e) { toast("Failed", e.message, "error"); }
    }
    addBtn.addEventListener("click", async () => {
      const asin = asinI.value.trim().toUpperCase();
      if (!asin) { asinI.focus(); return; }
      addBtn.disabled = true;
      try {
        await api("/groups/" + groupId + "/pdp_targets", { method: "POST", body: { asin: asin, enabled: true, notes: notesI.value.trim() || null } });
        const fresh = await api("/groups/" + groupId + "/targets");
        list.length = 0; fresh.pdp.forEach((x) => list.push(x));
        asinI.value = ""; notesI.value = ""; renderList(); toast("Added", asin, "success");
      } catch (e) { toast("Add failed", e.message, "error"); }
      finally { addBtn.disabled = false; }
    });
    renderList();
    return sec;
  }

  function serpTargets(groupId, list) {
    const urlI = h("input", { type: "url", placeholder: "https://www.amazon.com/s?k=…" });
    const labelI = h("input", { type: "text", placeholder: "Label" });
    const modeSel = h("select", {}, [opt("newest_front", "newest_front"), opt("featured_full", "featured_full")]);
    const pagesI = h("input", { type: "number", min: "1", value: "1" });
    const addBtn = h("button", { class: "btn btn-primary", text: "Add" });
    const body = h("div", { class: "form-section-body" });
    const sec = h("div", { class: "form-section" }, [
      h("div", { class: "form-section-head" }, [h("h4", { text: "SERP Targets — Search URLs" }), h("span", { class: "tag-kind", text: list.length + " configured" })]),
      body,
    ]);
    body.appendChild(h("div", { class: "target-add serp" }, [
      field("Search URL", urlI), field("Label", labelI), field("Mode", modeSel), field("Pages", pagesI), addBtn,
    ]));
    const listHost = h("div"); body.appendChild(listHost);
    function renderList() {
      clear(listHost);
      if (!list.length) { listHost.appendChild(h("div", { class: "muted", style: "padding:8px 0", text: "No search URLs yet." })); return; }
      listHost.appendChild(h("div", { class: "table-wrap" }, h("table", { class: "tbl" }, [
        h("thead", {}, h("tr", {}, ["Label", "URL", "Mode", "Pages", ""].map((t) => h("th", { text: t })))),
        h("tbody", {}, list.map((t) => h("tr", {}, [
          h("td", { text: t.label || "—" }),
          h("td", { class: "cell-title mono", title: t.search_url }, h("a", { href: t.search_url, target: "_blank", text: t.search_url })),
          h("td", {}, h("span", { class: "badge", text: t.scrape_mode })),
          h("td", { class: "num", text: t.max_pages }),
          h("td", { class: "cell-actions" }, h("button", { class: "btn btn-sm btn-danger", onclick: () => del(t) }, "Remove")),
        ]))),
      ])));
    }
    async function del(t) {
      try { await api("/serp_targets/" + t.id, { method: "DELETE" }); list.splice(list.indexOf(t), 1); renderList(); toast("Removed", t.label || t.search_url, "success"); }
      catch (e) { toast("Failed", e.message, "error"); }
    }
    addBtn.addEventListener("click", async () => {
      const url = urlI.value.trim();
      if (!url) { urlI.focus(); return; }
      addBtn.disabled = true;
      try {
        await api("/groups/" + groupId + "/serp_targets", { method: "POST", body: { search_url: url, label: labelI.value.trim() || null, scrape_mode: modeSel.value, max_pages: intOrNull(pagesI.value) ?? 1, enabled: true } });
        const fresh = await api("/groups/" + groupId + "/targets");
        list.length = 0; fresh.serp.forEach((x) => list.push(x));
        urlI.value = ""; labelI.value = ""; pagesI.value = "1"; renderList(); toast("Added", "Search target", "success");
      } catch (e) { toast("Add failed", e.message, "error"); }
      finally { addBtn.disabled = false; }
    });
    renderList();
    return sec;
  }

  // ---------------------------------------------------------- Selector Profiles
  async function renderProfiles() {
    setCrumbs([{ text: "Selector Profiles" }]);
    setActions(h("button", { class: "btn btn-primary", onclick: () => openProfileEditor(null), text: "＋ New Profile" }));
    showLoading();
    let profiles;
    try { profiles = await api("/selector_profiles"); } catch (e) { showError(e); return; }

    const root = clear(view());
    root.appendChild(h("div", { class: "page-head" }, h("div", {}, [
      h("h2", { text: "Selector Profiles" }), h("div", { class: "sub", text: "Reusable CSS/XPath selector sets per marketplace." }),
    ])));

    if (!profiles.length) { root.appendChild(h("div", { class: "panel" }, h("div", { class: "panel-body" }, emptyState("No selector profiles yet.")))); return; }

    root.appendChild(h("div", { class: "panel" }, h("div", { class: "table-wrap" }, h("table", { class: "tbl" }, [
      h("thead", {}, h("tr", {}, ["ID", "Name", "Marketplace", "Locale", "Version", "Default", "Keys", ""].map((t) => h("th", { text: t })))),
      h("tbody", {}, profiles.map((p) => h("tr", { class: "clickable", onclick: (e) => { if (e.target.closest(".no-nav")) return; openProfileEditor(p); } }, [
        h("td", { class: "num", text: "#" + p.id }),
        h("td", {}, h("strong", { text: p.name })),
        h("td", { text: p.marketplace }),
        h("td", { text: p.locale }),
        h("td", { class: "num", text: "v" + p.version }),
        h("td", {}, p.is_default ? h("span", { class: "badge green", text: "default" }) : h("span", { class: "muted", text: "—" })),
        h("td", { class: "num muted", text: p.selectors ? Object.keys(p.selectors).length : 0 }),
        h("td", { class: "no-nav cell-actions" }, h("button", { class: "btn btn-sm btn-ghost", onclick: () => openProfileEditor(p), text: "Edit" })),
      ]))),
    ]))));
  }

  function openProfileEditor(p) {
    const isNew = !p;
    const root = clear(view());
    setCrumbs([{ text: "Selector Profiles", href: "#/profiles" }, { text: isNew ? "New" : p.name }]);
    setActions(null);
    const nameI = h("input", { type: "text", value: isNew ? "" : p.name, placeholder: "profile name" });
    const mktI = h("input", { type: "text", value: isNew ? "amazon.com" : p.marketplace });
    const localeI = h("input", { type: "text", value: isNew ? "en-IL" : p.locale });
    const defCb = checkbox("Set as default profile", isNew ? false : p.is_default);
    const jsonTa = h("textarea", { rows: "18" });
    jsonTa.value = JSON.stringify(isNew ? { title: "#productTitle", price: ".a-price .a-offscreen" } : (p.selectors || {}), null, 2);
    const errLine = h("div", { class: "desc", style: "color:var(--red)" });

    root.appendChild(h("div", { class: "page-head" }, [
      h("div", {}, [h("h2", { text: isNew ? "New Selector Profile" : "Edit: " + p.name }), h("div", { class: "sub", text: "Selectors are stored as JSON." })]),
      h("div", { class: "toolbar" }, [
        h("button", { class: "btn btn-ghost", onclick: () => { location.hash = "#/profiles"; }, text: "Back" }),
        h("button", { class: "btn btn-ghost", onclick: () => { try { jsonTa.value = JSON.stringify(JSON.parse(jsonTa.value), null, 2); errLine.textContent = ""; } catch (e) { errLine.textContent = "Invalid JSON: " + e.message; } }, text: "Format JSON" }),
        h("button", { class: "btn btn-primary", id: "saveProfile", text: isNew ? "Create" : "Save" }),
      ]),
    ]));

    root.appendChild(formSection("Profile", null, h("div", { class: "form-grid" }, [
      field("Name", nameI), field("Marketplace", mktI), field("Locale", localeI),
      h("div", { class: "full" }, defCb.el),
      field("Selectors (JSON)", h("div", {}, [jsonTa, errLine]), { full: true }),
    ])));

    $("#saveProfile").addEventListener("click", async () => {
      let selectors;
      try { selectors = JSON.parse(jsonTa.value); }
      catch (e) { errLine.textContent = "Invalid JSON: " + e.message; toast("Invalid JSON", "Fix selectors before saving.", "error"); return; }
      if (typeof selectors !== "object" || Array.isArray(selectors)) { errLine.textContent = "Selectors must be a JSON object."; return; }
      if (!nameI.value.trim()) { toast("Name required", "", "error"); return; }
      const btn = $("#saveProfile"); btn.disabled = true;
      try {
        if (isNew) {
          await api("/selector_profiles", { method: "POST", body: { name: nameI.value.trim(), marketplace: mktI.value.trim(), locale: localeI.value.trim(), selectors: selectors, is_default: defCb.get() } });
          toast("Created", nameI.value.trim(), "success");
        } else {
          await api("/selector_profiles/" + p.id, { method: "PUT", body: { name: nameI.value.trim(), marketplace: mktI.value.trim(), locale: localeI.value.trim(), selectors: selectors, is_default: defCb.get() } });
          toast("Saved", "Profile updated", "success");
        }
        location.hash = "#/profiles";
      } catch (e) { toast("Save failed", e.message, "error"); }
      finally { btn.disabled = false; }
    });
  }

  // ---------------------------------------------------------- Alerts
  async function renderAlerts() {
    setCrumbs([{ text: "Alerts" }]); setActions(null); showLoading();
    let alerts;
    try { alerts = await api("/dashboard/alerts?limit=150"); } catch (e) { showError(e); return; }
    const root = clear(view());
    root.appendChild(h("div", { class: "page-head" }, h("div", {}, [
      h("h2", { text: "Alerts" }), h("div", { class: "sub", text: alerts.length + " most recent alerts." }),
    ])));
    if (!alerts.length) { root.appendChild(h("div", { class: "panel" }, h("div", { class: "panel-body" }, emptyState("No alerts yet.")))); return; }
    root.appendChild(h("div", { class: "panel" }, h("div", { class: "table-wrap" }, h("table", { class: "tbl" }, [
      h("thead", {}, h("tr", {}, ["Type", "ASIN", "Title", "Old → New", "Group", "Status", "Created"].map((t) => h("th", { text: t })))),
      h("tbody", {}, alerts.map((a) => h("tr", {}, [
        h("td", {}, h("span", { class: "badge blue", text: a.alert_type })),
        h("td", { class: "mono" }, a.product_url ? h("a", { href: a.product_url, target: "_blank", text: a.asin || "—" }) : (a.asin || "—")),
        h("td", { class: "cell-title", title: a.title || "", text: a.title || "—" }),
        h("td", { class: "num" }, priceDelta(a.old_price, a.new_price)),
        h("td", { class: "muted", text: a.group_name || "—" }),
        h("td", {}, statusBadge(a.status)),
        h("td", { class: "muted", text: fmtDate(a.created_at) }),
      ]))),
    ]))));
  }

  function priceDelta(oldP, newP) {
    if (oldP == null && newP == null) return h("span", { class: "muted", text: "—" });
    const frag = h("span", {}, [h("span", { class: "muted", text: fmtPrice(oldP) }), h("span", { class: "arrow", text: "→" }), h("span", { class: "price-chip", text: fmtPrice(newP) })]);
    return frag;
  }

  // ---------------------------------------------------------- Products
  async function renderProducts() {
    setCrumbs([{ text: "Products" }]); setActions(null); showLoading();
    let products;
    try { products = await api("/dashboard/products?limit=300"); } catch (e) { showError(e); return; }
    const root = clear(view());
    root.appendChild(h("div", { class: "page-head" }, h("div", {}, [
      h("h2", { text: "Products" }), h("div", { class: "sub", text: products.length + " tracked products (latest state)." }),
    ])));
    if (!products.length) { root.appendChild(h("div", { class: "panel" }, h("div", { class: "panel-body" }, emptyState("No products tracked yet.")))); return; }
    root.appendChild(h("div", { class: "panel" }, h("div", { class: "table-wrap" }, h("table", { class: "tbl" }, [
      h("thead", {}, h("tr", {}, ["", "ASIN", "Title", "Price", "Stock", "Seller", "Last Seen"].map((t) => h("th", { text: t })))),
      h("tbody", {}, products.map((p) => h("tr", { class: "clickable", onclick: () => openPriceHistory(p) }, [
        h("td", {}, p.image_url ? h("img", { class: "thumb", src: p.image_url, loading: "lazy", alt: "" }) : h("div", { class: "thumb" })),
        h("td", { class: "mono" }, p.product_url ? h("a", { href: p.product_url, target: "_blank", onclick: (e) => e.stopPropagation(), text: p.asin }) : p.asin),
        h("td", { class: "cell-title", title: p.title || "", text: p.title || "—" }),
        h("td", { class: "num price-chip", text: fmtPrice(p.price) }),
        h("td", {}, p.in_stock ? h("span", { class: "badge green", text: "in stock" }) : h("span", { class: "badge red", text: "out" })),
        h("td", { class: "muted", text: p.seller || "—" }),
        h("td", { class: "muted", text: ago(p.last_seen) }),
      ]))),
    ]))));
  }

  async function openPriceHistory(p) {
    const root = $("#modalRoot");
    function close() { destroyHistChart(); clear(root); root.style.pointerEvents = "none"; }
    let histChart = null;
    function destroyHistChart() { if (histChart) { try { histChart.destroy(); } catch (e) {} histChart = null; } }
    const bodyHost = h("div", { class: "modal-body" }, h("div", { class: "loading", text: "Loading price history…" }));
    const overlay = h("div", { class: "modal-overlay", onclick: (e) => { if (e.target === overlay) close(); } }, [
      h("div", { class: "modal", style: "width:min(620px,100%)" }, [
        h("div", { class: "modal-head", text: (p.title || p.asin) }),
        bodyHost,
        h("div", { class: "modal-foot" }, h("button", { class: "btn btn-ghost", onclick: close, text: "Close" })),
      ]),
    ]);
    root.style.pointerEvents = "auto"; clear(root).appendChild(overlay);
    let hist;
    try { hist = await api("/dashboard/price_history?group_id=" + p.group_id + "&asin=" + encodeURIComponent(p.asin)); }
    catch (e) { clear(bodyHost).appendChild(h("div", { class: "error-box", text: e.message })); return; }
    clear(bodyHost);
    bodyHost.appendChild(h("div", { class: "row-flex", style: "margin-bottom:12px" }, [
      h("span", { class: "mono muted", text: p.asin }), h("span", { class: "spacer" }),
      h("span", { class: "price-chip", text: "Current: " + fmtPrice(p.price) }),
    ]));
    if (!hist.length) { bodyHost.appendChild(emptyState("No price history recorded.")); return; }
    if (typeof Chart !== "undefined") {
      const cv = h("canvas");
      bodyHost.appendChild(h("div", { class: "chart-canvas-wrap", style: "height:200px" }, cv));
      histChart = makeChart(cv, {
        type: "line",
        data: { labels: hist.map((x) => fmtDate(x.observed_at)), datasets: [{ label: "price", data: hist.map((x) => x.price), borderColor: "#5b8cff", backgroundColor: "rgba(91,140,255,.15)", fill: true, tension: 0.25, pointRadius: 2 }] },
        options: chartOpts(),
      });
    }
    bodyHost.appendChild(h("div", { class: "table-wrap", style: "max-height:200px;overflow:auto;margin-top:12px" }, h("table", { class: "tbl" }, [
      h("thead", {}, h("tr", {}, ["Observed", "Price", "Stock"].map((t) => h("th", { text: t })))),
      h("tbody", {}, hist.slice().reverse().map((x) => h("tr", {}, [
        h("td", { class: "muted", text: fmtDate(x.observed_at) }),
        h("td", { class: "num price-chip", text: fmtPrice(x.price) }),
        h("td", {}, x.in_stock ? h("span", { class: "badge green", text: "in" }) : h("span", { class: "badge red", text: "out" })),
      ]))),
    ])));
  }

  // ----------------------------------------------------------------- small utils
  function opt(value, label) { return h("option", { value: value, text: label }); }
  function numOrNull(v) { if (v === "" || v == null) return null; const n = Number(v); return isNaN(n) ? null : n; }
  function intOrNull(v) { if (v === "" || v == null) return null; const n = parseInt(v, 10); return isNaN(n) ? null : n; }
  function formSection(title, hint, content) {
    return h("div", { class: "form-section" }, [
      h("div", { class: "form-section-head" }, [h("h4", { text: title }), hint ? h("span", { class: "tag-kind", text: hint }) : null]),
      h("div", { class: "form-section-body" }, content),
    ]);
  }

  // ----------------------------------------------------------------- router
  const routes = [
    { re: /^\/dashboard$/, fn: () => renderDashboard(), nav: "dashboard" },
    { re: /^\/groups$/, fn: () => renderGroups(), nav: "groups" },
    { re: /^\/groups\/(.+)$/, fn: (m) => renderGroupEditor(m[1]), nav: "groups" },
    { re: /^\/profiles$/, fn: () => renderProfiles(), nav: "profiles" },
    { re: /^\/alerts$/, fn: () => renderAlerts(), nav: "alerts" },
    { re: /^\/products$/, fn: () => renderProducts(), nav: "products" },
  ];

  function router() {
    destroyCharts();
    let hash = location.hash.replace(/^#/, "");
    if (!hash) { location.hash = "#/dashboard"; return; }
    let matched = null, params = null;
    for (const r of routes) { const m = hash.match(r.re); if (m) { matched = r; params = m; break; } }
    if (!matched) { location.hash = "#/dashboard"; return; }
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === matched.nav));
    try { matched.fn(params); } catch (e) { showError(e); }
  }

  window.addEventListener("hashchange", router);
  window.addEventListener("DOMContentLoaded", () => {
    $("#refreshBtn").addEventListener("click", router);
    router();
  });
})();
