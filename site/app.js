/* 链上预测市场 × 大宗商品盘口追踪 — 前端渲染
 * 纯静态:fetch ./data.json,用 Chart.js 双轴叠加渲染每个盘口。
 */
(() => {
  "use strict";

  const COLORS = { prob: "#2a78d6", price: "#d98515", thr: "#9a978d" };
  const state = { data: null, rangeDays: 90, commodity: "全部", charts: {}, detailChart: null };

  // ── 主题切换 ───────────────────────────────────────────────
  const root = document.documentElement;
  const themeBtn = document.getElementById("theme-toggle");
  themeBtn.addEventListener("click", () => {
    const cur = root.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : cur === "light" ? "auto" : "dark";
    root.setAttribute("data-theme", next);
    setTimeout(render, 30); // 让 Chart 用新色重绘
  });

  function isDark() {
    const t = root.getAttribute("data-theme");
    if (t === "dark") return true;
    if (t === "light") return false;
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  // ── 工具 ───────────────────────────────────────────────────
  const asNumber = (n) => {
    const x = Number(n);
    return Number.isFinite(x) ? x : null;
  };
  const fmtNum = (n, d = 2) =>
    asNumber(n) == null ? "—" : asNumber(n).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
  const fmtMoney = (n) => {
    const x = asNumber(n);
    return x == null ? "—" : "$" + fmtNum(x, x >= 100 ? 0 : 2);
  };
  const fmtVol = (n) => {
    const x = asNumber(n);
    return x == null ? "—" : x >= 1e6 ? "$" + (x / 1e6).toFixed(2) + "M" : x >= 1e3 ? "$" + (x / 1e3).toFixed(1) + "K" : "$" + x;
  };

  function cutoffDate(days) {
    if (!days) return null;
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d.toISOString().slice(0, 10);
  }
  const filterSeries = (series, cutoff) =>
    !cutoff ? series : (series || []).filter((p) => p.t >= cutoff);

  // ── 数据加载 ───────────────────────────────────────────────
  async function load() {
    try {
      const res = await fetch("./data.json", { cache: "no-store" });
      state.data = await res.json();
    } catch (e) {
      const grid = document.getElementById("grid");
      grid.textContent = "加载 data.json 失败:" + e;
      grid.className = "grid loading";
      return;
    }
    initHeader();
    initCommodityFilter();
    initRangeButtons();
    render();
  }

  function initHeader() {
    const d = state.data;
    const dt = new Date(d.generated_at);
    document.getElementById("updated").textContent =
      "更新于 " + dt.toLocaleString("zh-CN", { hour12: false }) + " · 共 " + d.market_count + " 个盘口";
  }

  function initCommodityFilter() {
    const set = new Set(["全部"]);
    state.data.markets.forEach((m) => m.commodity && set.add(m.commodity));
    const box = document.getElementById("commodity-buttons");
    box.innerHTML = "";
    [...set].forEach((name) => {
      const b = document.createElement("button");
      b.className = "chip" + (name === state.commodity ? " active" : "");
      b.textContent = name;
      b.addEventListener("click", () => {
        state.commodity = name;
        [...box.children].forEach((c) => c.classList.toggle("active", c === b));
        render();
      });
      box.appendChild(b);
    });
  }

  function initRangeButtons() {
    const box = document.getElementById("range-buttons");
    [...box.children].forEach((b) => {
      b.addEventListener("click", () => {
        state.rangeDays = Number(b.dataset.range);
        [...box.children].forEach((c) => c.classList.toggle("active", c === b));
        render();
      });
    });
  }

  // ── 渲染 ───────────────────────────────────────────────────
  function render() {
    const grid = document.getElementById("grid");
    Object.values(state.charts).forEach((ch) => ch.destroy());
    state.charts = {};
    grid.innerHTML = "";

    const markets = state.data.markets.filter(
      (m) => state.commodity === "全部" || m.commodity === state.commodity
    );
    if (!markets.length) {
      grid.innerHTML = '<div class="loading">没有匹配的盘口</div>';
      return;
    }
    markets.forEach((m) => grid.appendChild(buildCard(m)));
    // 用 setTimeout 而非 requestAnimationFrame:rAF 在页面不可见/后台标签时不触发,
    // 会导致图表始终不绘制。setTimeout 不依赖可见性。
    setTimeout(() =>
      markets.forEach((m) => {
        try {
          drawChart(m);
        } catch (e) {
          console.error("drawChart failed:", m.key, e);
        }
      }), 0);
  }

  function buildCard(m) {
    const card = document.createElement("div");
    card.className = "card";

    const diff = m.threshold != null && m.latest_price != null ? m.latest_price - m.threshold : null;
    const diffCls = diff == null ? "" : diff >= 0 ? "pos" : "neg";
    const diffTxt = diff == null ? "无阈值"
      : (diff >= 0 ? "高于阈值 +" : "低于阈值 −") + fmtMoney(Math.abs(diff));

    const subTags = [];
    if (m.commodity) subTags.push(`<span class="tag">${escapeHtml(m.commodity)}</span>`);
    if (m.end_date) subTags.push(`<span class="tag">到期 ${escapeHtml(m.end_date)}</span>`);
    if (m.threshold != null) subTags.push(`<span class="tag">阈值 ${fmtMoney(m.threshold)} ${escapeHtml(m.unit || "")}</span>`);
    if (m.stale) subTags.push('<span class="tag tag-stale">数据滞后</span>');

    card.innerHTML = `
      <div class="card-head"><h3 class="card-title">${escapeHtml(m.title || m.key)}</h3>
        <span class="expand-hint" title="点击展开详情" aria-hidden="true">⤢</span></div>
      <div class="card-sub">${subTags.join("")}</div>
      <div class="chart-wrap"><canvas id="c-${cssId(m.key)}"
           role="img" aria-label="${escapeHtml(m.title || m.key)} 的隐含概率与${escapeHtml(m.commodity || "标的物")}价格叠加图"></canvas></div>
      <div class="stats">
        <div class="stat"><span class="stat-label">隐含概率(${escapeHtml(m.track_outcome || "Yes")})</span>
          <span class="stat-value prob">${m.latest_prob == null ? "—" : fmtNum(m.latest_prob, 1) + "%"}</span></div>
        <div class="stat"><span class="stat-label">${escapeHtml(m.commodity || "标的")}现价</span>
          <span class="stat-value price">${fmtMoney(m.latest_price)}</span></div>
        <div class="stat"><span class="stat-label">与阈值</span>
          <span class="stat-value ${diffCls}" style="font-size:13px">${diffTxt}</span></div>
        <div class="stat"><span class="stat-label">24h 成交</span>
          <span class="stat-value" style="font-size:13px">${fmtVol(m.volume24hr)}</span></div>
        <div class="stat"><span class="stat-label">概率-价格相关</span>
          <span class="stat-value ${corrCls((m.stats || {}).corr_levels)}" style="font-size:13px">${fmtCorr((m.stats || {}).corr_levels)}</span></div>
      </div>
      <div class="card-link"><a href="${safeUrl(m.polymarket_url)}" target="_blank" rel="noopener">在 Polymarket 查看 ↗</a></div>`;
    card.addEventListener("click", (e) => {
      if (e.target.closest("a")) return;   // 点链接不弹窗
      openDetail(m);
    });
    return card;
  }

  function makeChart(canvasEl, m) {
    const cutoff = cutoffDate(state.rangeDays);
    const prob = filterSeries(m.prob, cutoff).map((p) => ({ x: p.t, y: p.v }));
    const price = filterSeries(m.price, cutoff).map((p) => ({ x: p.t, y: p.v }));

    const dark = isDark();
    const tick = dark ? "#84837b" : "#8a8980";
    const gridc = dark ? "rgba(255,255,255,.07)" : "rgba(0,0,0,.06)";

    const datasets = [
      { label: "隐含概率", yAxisID: "prob", data: prob, borderColor: COLORS.prob,
        backgroundColor: "rgba(42,120,214,.10)", borderWidth: 2, fill: true, tension: .25,
        pointRadius: 0, pointHoverRadius: 4 },
      { label: m.commodity || "价格", yAxisID: "price", data: price, borderColor: COLORS.price,
        borderWidth: 2, tension: .25, pointRadius: 0, pointHoverRadius: 4 },
    ];
    // 阈值参考线(价格轴上的水平虚线)
    if (m.threshold != null && price.length) {
      datasets.push({
        label: "阈值", yAxisID: "price", borderColor: COLORS.thr, borderWidth: 1.5,
        borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 0, fill: false,
        data: [{ x: price[0].x, y: m.threshold }, { x: price[price.length - 1].x, y: m.threshold }],
      });
    }

    return new Chart(canvasEl, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: true, labels: { boxWidth: 10, boxHeight: 10, color: tick, font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: (c) => {
                const v = c.parsed.y;
                if (c.dataset.yAxisID === "prob") return `隐含概率: ${v.toFixed(1)}%`;
                if (c.dataset.label === "阈值") return `阈值: $${v.toLocaleString()}`;
                return `${c.dataset.label}: $${v.toLocaleString()}`;
              },
            },
          },
        },
        scales: {
          x: { type: "time", time: { unit: "month", tooltipFormat: "yyyy-MM-dd" },
               grid: { display: false }, ticks: { color: tick, font: { size: 10 }, maxRotation: 0 } },
          prob: { position: "left", min: 0, max: 100, grid: { color: gridc },
                  ticks: { color: tick, font: { size: 10 }, callback: (v) => v + "%" } },
          price: { position: "right", grid: { drawOnChartArea: false },
                   ticks: { color: tick, font: { size: 10 }, callback: (v) => "$" + v } },
        },
      },
    });
  }

  function drawChart(m) {
    const ctx = document.getElementById("c-" + cssId(m.key));
    if (!ctx) return;
    state.charts[m.key] = makeChart(ctx, m);
  }

  // ── 详情弹窗 ───────────────────────────────────────────────
  function ensureModal() {
    let ov = document.getElementById("detail-overlay");
    if (ov) return ov;
    ov = document.createElement("div");
    ov.id = "detail-overlay";
    ov.className = "modal-overlay";
    ov.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <button class="modal-close" aria-label="关闭">×</button>
        <h2 id="modal-title" class="modal-title"></h2>
        <div id="modal-tags" class="card-sub"></div>
        <div class="modal-chart-wrap"><canvas id="detail-chart" role="img"></canvas></div>
        <div id="modal-stats" class="modal-stats"></div>
        <div id="modal-insight" class="modal-insight"></div>
        <div class="card-link"><a id="modal-link" href="#" target="_blank" rel="noopener">在 Polymarket 查看 ↗</a></div>
      </div>`;
    document.body.appendChild(ov);
    ov.addEventListener("click", (e) => { if (e.target === ov) closeDetail(); });
    ov.querySelector(".modal-close").addEventListener("click", closeDetail);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDetail(); });
    return ov;
  }

  function openDetail(m) {
    const ov = ensureModal();
    const diff = m.threshold != null && m.latest_price != null ? m.latest_price - m.threshold : null;
    const diffCls = diff == null ? "" : diff >= 0 ? "pos" : "neg";
    const diffTxt = diff == null ? "无阈值"
      : (diff >= 0 ? "高于阈值 +" : "低于阈值 −") + fmtMoney(Math.abs(diff));
    const s = m.stats || {};

    ov.querySelector("#modal-title").textContent = m.title || m.key;
    const tags = [];
    if (m.commodity) tags.push(`<span class="tag">${escapeHtml(m.commodity)}</span>`);
    if (m.end_date) tags.push(`<span class="tag">到期 ${escapeHtml(m.end_date)}</span>`);
    if (m.threshold != null) tags.push(`<span class="tag">阈值 ${fmtMoney(m.threshold)} ${escapeHtml(m.unit || "")}</span>`);
    if (m.price_source) tags.push(`<span class="tag">价格源 ${escapeHtml(m.price_source)}</span>`);
    if (m.stale) tags.push('<span class="tag tag-stale">数据滞后</span>');
    ov.querySelector("#modal-tags").innerHTML = tags.join("");

    const stat = (label, val, cls = "") =>
      `<div class="stat"><span class="stat-label">${label}</span><span class="stat-value ${cls}" style="font-size:15px">${val}</span></div>`;
    ov.querySelector("#modal-stats").innerHTML =
      stat(`隐含概率(${escapeHtml(m.track_outcome || "Yes")})`, m.latest_prob == null ? "—" : fmtNum(m.latest_prob, 1) + "%", "prob") +
      stat(`${escapeHtml(m.commodity || "标的")}现价`, fmtMoney(m.latest_price), "price") +
      stat("与阈值", diffTxt, diffCls) +
      stat("24h 成交", fmtVol(m.volume24hr), "") +
      stat("概率-价格相关", fmtCorr(s.corr_levels), corrCls(s.corr_levels)) +
      stat("日变动相关", fmtCorr(s.corr_changes), corrCls(s.corr_changes));

    ov.querySelector("#modal-insight").innerHTML =
      `<span class="insight-label">领先滞后</span> ${leadLagText(s)}` +
      `<div class="insight-note">水平相关:概率与价格的同向程度(越接近 ±1 越同步)。日变动相关与领先滞后基于每日变化,正的"价格领先"天数表示价格先动、概率随后跟上。样本 ${s.n || 0} 天。</div>`;

    ov.querySelector("#modal-link").href = safeUrl(m.polymarket_url);

    if (state.detailChart) { state.detailChart.destroy(); state.detailChart = null; }
    ov.classList.add("open");
    document.body.style.overflow = "hidden";
    setTimeout(() => {
      const cv = document.getElementById("detail-chart");
      cv.setAttribute("aria-label", (m.title || m.key) + " 的隐含概率与价格叠加详情图");
      try { state.detailChart = makeChart(cv, m); } catch (e) { console.error("detail chart failed", e); }
    }, 0);
  }

  function closeDetail() {
    const ov = document.getElementById("detail-overlay");
    if (!ov) return;
    ov.classList.remove("open");
    document.body.style.overflow = "";
    if (state.detailChart) { state.detailChart.destroy(); state.detailChart = null; }
  }

  // ── 小工具 ─────────────────────────────────────────────────
  const cssId = (s) => s.replace(/[^a-zA-Z0-9_-]/g, "_");
  const fmtCorr = (v) => (v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2));
  const corrCls = (v) => (v == null ? "" : v > 0.05 ? "pos" : v < -0.05 ? "neg" : "");
  function leadLagText(s) {
    if (s == null || s.lead_lag_days == null) return "数据不足";
    const k = s.lead_lag_days, r = s.lead_lag_corr;
    if (k === 0) return `基本同步变动(r=${fmtCorr(r)})`;
    if (k > 0) return `价格领先概率约 ${k} 天(r=${fmtCorr(r)})`;
    return `概率领先价格约 ${-k} 天(r=${fmtCorr(r)})`;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }
  function safeUrl(url) {
    try {
      const u = new URL(String(url || ""), window.location.href);
      if (u.protocol === "https:" && u.hostname === "polymarket.com") {
        return u.href;
      }
    } catch (e) {
      return "#";
    }
    return "#";
  }

  load();
})();
