const form = document.getElementById("search-form");
const symbolInput = document.getElementById("symbol");
const yearsSelect = document.getElementById("years");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");

const charts = { main: null, rsi: null, macd: null, kd: null, adx: null, obv: null };

// ===== 清單（localStorage） =====
const LS_WATCH = "stocks5.watchlist";
const LS_HOLD = "stocks5.holdings";
let currentData = null;  // 目前主畫面顯示的股票（用於加入清單）

function normalizeSymClient(s) {
  s = s.trim().toUpperCase();
  if (/^\d+$/.test(s)) return s + ".TW";
  return s;
}
function loadWatchlist() {
  try { return JSON.parse(localStorage.getItem(LS_WATCH) || "[]"); }
  catch { return []; }
}
function saveWatchlist(list) { localStorage.setItem(LS_WATCH, JSON.stringify(list)); }
function loadHoldings() {
  try { return JSON.parse(localStorage.getItem(LS_HOLD) || "[]"); }
  catch { return []; }
}
function saveHoldings(list) { localStorage.setItem(LS_HOLD, JSON.stringify(list)); }

function addWatch(sym) {
  const s = normalizeSymClient(sym);
  const list = loadWatchlist();
  if (!list.includes(s)) { list.push(s); saveWatchlist(list); }
  refreshLists();
  updateAddButtons();
}
function removeWatch(sym) {
  saveWatchlist(loadWatchlist().filter(x => x !== sym));
  refreshLists();
  updateAddButtons();
}
function addHold(sym, cost) {
  const s = normalizeSymClient(sym);
  const list = loadHoldings();
  const ex = list.find(h => h.symbol === s);
  if (ex) ex.cost = cost; else list.push({ symbol: s, cost });
  saveHoldings(list);
  refreshLists();
  updateAddButtons();
}
function removeHold(sym) {
  saveHoldings(loadHoldings().filter(h => h.symbol !== sym));
  refreshLists();
  updateAddButtons();
}

async function scanSymbols(syms) {
  if (syms.length === 0) return {};
  const resp = await fetch(`/api/scan?symbols=${encodeURIComponent(syms.join(","))}`);
  const data = await resp.json();
  const lookup = {};
  for (const r of data.results || []) {
    if (r.error) {
      lookup[r.requested] = { error: r.error, requested: r.requested };
    } else {
      // 同時用 requested 與 symbol 當 key（用戶可能存了未正規化或 .TWO 重定向後的形態）
      lookup[r.requested] = r;
      lookup[r.symbol] = r;
    }
  }
  return lookup;
}

async function refreshLists() {
  const watch = loadWatchlist();
  const hold = loadHoldings();
  const allSyms = [...new Set([...watch, ...hold.map(h => h.symbol)])];
  const lookup = await scanSymbols(allSyms);
  renderWatchlist(watch, lookup);
  renderHoldings(hold, lookup);
}

function renderWatchlist(syms, lookup) {
  const ul = document.getElementById("watchlist");
  const empty = document.getElementById("watchlist-empty");
  ul.innerHTML = "";
  if (syms.length === 0) { empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden");

  for (const sym of syms) {
    const info = lookup[sym];
    const li = makeRow(sym, info, /*isHolding=*/false);
    ul.appendChild(li);
  }
}

function renderHoldings(items, lookup) {
  const ul = document.getElementById("holdings");
  const empty = document.getElementById("holdings-empty");
  const summary = document.getElementById("holdings-summary");
  ul.innerHTML = "";
  if (items.length === 0) {
    empty.classList.remove("hidden");
    summary.textContent = "";
    return;
  }
  empty.classList.add("hidden");

  let totalCost = 0, totalNow = 0, hasValid = 0;
  for (const item of items) {
    const info = lookup[item.symbol];
    const li = makeRow(item.symbol, info, /*isHolding=*/true, item.cost);
    ul.appendChild(li);
    if (info && !info.error && item.cost > 0) {
      totalCost += item.cost;
      totalNow += info.price;
      hasValid++;
    }
  }
  if (hasValid > 0) {
    const pct = (totalNow - totalCost) / totalCost * 100;
    summary.textContent = `共 ${items.length} 檔，平均 ${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
    summary.className = pct >= 0 ? "muted pnl-up" : "muted pnl-dn";
  } else {
    summary.textContent = `共 ${items.length} 檔`;
  }
}

function makeRow(sym, info, isHolding, cost) {
  const li = document.createElement("li");
  li.className = "li-row";

  const nameDiv = document.createElement("div");
  nameDiv.className = "li-symbol-name";
  const nameText = info && !info.error ? info.name : "—";
  const symText = info && !info.error ? info.symbol : sym;
  nameDiv.innerHTML = `<span class="li-name">${nameText}</span><span class="li-symbol">${symText}</span>`;
  li.appendChild(nameDiv);

  const price = document.createElement("span");
  price.className = "li-price";
  price.textContent = info && !info.error ? fmt(info.price) : "—";
  li.appendChild(price);

  if (isHolding) {
    const costEl = document.createElement("span");
    costEl.className = "li-pnl";
    if (info && !info.error && cost > 0) {
      const pnl = (info.price - cost) / cost * 100;
      costEl.textContent = `${pnl >= 0 ? "+" : ""}${pnl.toFixed(1)}%`;
      costEl.classList.add(pnl >= 0 ? "up" : "dn");
      costEl.title = `成本 ${fmt(cost)}`;
    } else {
      costEl.textContent = cost > 0 ? `成本 ${fmt(cost)}` : "—";
    }
    li.appendChild(costEl);
  }

  const signal = document.createElement("span");
  if (info && !info.error && info.signal) {
    signal.className = "li-signal " + info.signal.class;
    const score = info.signal.score >= 0 ? `+${info.signal.score}` : `${info.signal.score}`;
    signal.textContent = `${info.signal.label} ${score}`;
  } else {
    signal.className = "li-signal hold";
    signal.textContent = info && info.error ? "ERR" : "...";
    if (info && info.error) signal.title = info.error;
  }
  li.appendChild(signal);

  const x = document.createElement("button");
  x.className = "li-x";
  x.textContent = "×";
  x.title = "移除";
  x.addEventListener("click", (e) => {
    e.stopPropagation();
    if (isHolding) removeHold(sym);
    else removeWatch(sym);
  });
  li.appendChild(x);

  li.addEventListener("click", () => {
    search(info && !info.error ? info.symbol : sym);
  });
  return li;
}

function updateAddButtons() {
  const w = document.getElementById("btn-add-watch");
  const h = document.getElementById("btn-add-hold");
  if (!currentData) {
    w.classList.remove("added"); w.textContent = "+ 加入觀察清單";
    h.classList.remove("added"); h.textContent = "+ 加入持有清單";
    return;
  }
  const sym = currentData.symbol;
  const inWatch = loadWatchlist().includes(sym);
  const inHold = loadHoldings().some(x => x.symbol === sym);

  w.classList.toggle("added", inWatch);
  w.textContent = inWatch ? "✓ 已在觀察清單（再次點擊移除）" : "+ 加入觀察清單";

  h.classList.toggle("added", inHold);
  h.textContent = inHold ? "✓ 已在持有清單（再次點擊移除）" : "+ 加入持有清單";
}

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("error", isError);
}

function fmt(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString(undefined, {
    maximumFractionDigits: 2, minimumFractionDigits: 2,
  });
}

async function fetchStock(symbol, years) {
  const url = `/api/stock?symbol=${encodeURIComponent(symbol)}&years=${years}`;
  setStatus(`載入 ${symbol} ...`);
  const resp = await fetch(url);
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || "查詢失敗");
  return data;
}

function renderSignal(sig) {
  const banner = document.getElementById("signal-banner");
  const left = banner.querySelector(".signal-left");
  // 重設 class（保留 signal-left）
  left.className = "signal-left " + sig.class;
  document.getElementById("signal-label").textContent = sig.label;
  const sscore = sig.score >= 0 ? `+${sig.score}` : `${sig.score}`;
  document.getElementById("signal-score").textContent = sscore;

  const grid = document.getElementById("reason-grid");
  grid.innerHTML = "";
  sig.reasons.forEach((r) => {
    const cls = r.score > 0 ? "pos" : r.score < 0 ? "neg" : "neu";
    const scoreStr = r.score > 0 ? `+${r.score}` : `${r.score}`;
    const el = document.createElement("div");
    el.className = `reason ${cls}`;
    el.innerHTML = `
      <div class="r-name">
        <span>${r.name}</span>
        <span class="r-score ${cls}">${scoreStr}</span>
      </div>
      <div class="r-detail">${r.detail}</div>
    `;
    grid.appendChild(el);
  });
}

function pct(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
  return s;
}

function upsideClass(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "neu";
  if (v > 1) return "pos";
  if (v < -1) return "neg";
  return "neu";
}

function zoneClass(zone) {
  if (!zone) return "fair";
  if (/低估|極低/.test(zone) || /低位/.test(zone)) return "cheap";
  if (/高估|偏貴|極高|高位/.test(zone)) return "pricey";
  return "fair";
}

function buildLevels(levels, currentPrice, orderHigh2Low) {
  const items = orderHigh2Low.map((key) => ({
    key, val: levels[key],
  })).filter(o => typeof o.val === "number");
  items.sort((a, b) => b.val - a.val);  // 高到低
  const classes = ["lvl-up2", "lvl-up1", "lvl-mid", "lvl-dn1", "lvl-dn2"];
  return items.map((o, i) => {
    const cls = classes[i] || "lvl-mid";
    // 是否「現價在此區間之內」(此值 ≥ current ≥ next value)
    const next = items[i + 1];
    const inThisBand = (o.val >= currentPrice) && (!next || next.val <= currentPrice);
    const mark = inThisBand ? `<span class="cur-marker">← 現價 ${fmt(currentPrice)}</span>` : "";
    return `<li class="${cls}"><span>${o.key}</span><b>${fmt(o.val)}${mark}</b></li>`;
  }).join("");
}

function renderValuation(val, currentPrice) {
  const card = document.getElementById("val-card");
  if (!val || (!val.eps_based && !val.percentile)) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");

  // EPS 估值
  const epsEl = document.getElementById("val-eps");
  if (val.eps_based) {
    epsEl.classList.remove("hidden");
    const e = val.eps_based;
    // 優先用校準後的 levels（基於個股自身 P/E 分布）
    const lvls = e.levels_calibrated || e.levels_standard;
    const label = e.levels_calibrated ? "（自身歷史 P/E 校準）" : "（樂活大叔倍率）";
    document.getElementById("val-eps-zone").textContent = e.zone;
    document.getElementById("val-eps-zone").className = "zone-pill " + zoneClass(e.zone);
    const stats = `EPS ${e.eps_min.toFixed(2)} ~ ${e.eps_max.toFixed(2)}（${e.years_used} 年）${label}`
      + (e.pe_stats ? `．自身 P/E P25/P50/P75 = ${e.pe_stats.p25.toFixed(1)}/${e.pe_stats.p50.toFixed(1)}/${e.pe_stats.p75.toFixed(1)}`
                    + (e.pe_stats.current_pe ? `，現 P/E ${e.pe_stats.current_pe.toFixed(1)}` : "") : "");
    document.getElementById("val-eps-stats").textContent = stats;
    const order = ["昂貴價", "相對昂貴", "合理價", "相對便宜", "便宜價"];
    document.getElementById("val-eps-levels").innerHTML = buildLevels(lvls, currentPrice, order);
  } else {
    epsEl.classList.add("hidden");
  }

  // 歷史百分位
  const pctEl = document.getElementById("val-pct");
  if (val.percentile) {
    pctEl.classList.remove("hidden");
    const p = val.percentile;
    document.getElementById("val-pct-zone").textContent = p.zone;
    document.getElementById("val-pct-zone").className = "zone-pill " + zoneClass(p.zone);
    document.getElementById("val-pct-stats").textContent =
      `現價 ${currentPrice.toFixed(2)} 落在歷史 ${p.current_percentile.toFixed(1)}%  (${p.samples} 個交易日樣本)`;
    const order = ["P90 (極高)", "P75 (高)", "P50 (中)", "P25 (低)", "P10 (極低)"];
    document.getElementById("val-pct-levels").innerHTML = buildLevels(p.levels, currentPrice, order);
  } else {
    pctEl.classList.add("hidden");
  }
}

function fmtLots(shares) {
  // shares → 張 (1 張 = 1000 股)
  if (shares === null || shares === undefined) return "—";
  const lots = shares / 1000;
  const s = lots >= 0 ? "+" : "";
  if (Math.abs(lots) >= 10000) return s + (lots/10000).toFixed(1) + "萬張";
  return s + lots.toLocaleString(undefined, {maximumFractionDigits: 0}) + " 張";
}

function colorClass(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "";
  if (v > 0) return "pos";
  if (v < 0) return "neg";
  return "";
}

function renderChip(chip) {
  const card = document.getElementById("chip-card");
  if (!chip || (!chip.institutional && !chip.holding && !chip.margin)) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");

  const inst = chip.institutional || {};
  document.getElementById("chip-date").textContent =
    inst.latest_date ? `資料日 ${inst.latest_date}` : "";

  const setFlow = (id, v) => {
    const el = document.getElementById(id);
    el.textContent = fmtLots(v);
    el.className = colorClass(v);
  };
  setFlow("chip-foreign-5d",  inst.foreign_5d);
  setFlow("chip-foreign-20d", inst.foreign_20d);
  setFlow("chip-trust-20d",   inst.trust_20d);
  setFlow("chip-dealer-20d",  inst.dealer_20d);

  const h = chip.holding || {};
  if (h.foreign_ratio !== undefined && h.foreign_ratio !== null) {
    document.getElementById("chip-foreign-ratio").textContent = h.foreign_ratio.toFixed(2) + "%";
    const chg = h.foreign_ratio_change;
    const chgEl = document.getElementById("chip-foreign-ratio-chg");
    if (chg !== null && chg !== undefined) {
      chgEl.textContent = `30 日 ${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`;
      chgEl.className = "muted " + (chg > 0 ? "pos" : chg < 0 ? "neg" : "");
    } else {
      chgEl.textContent = "";
    }
  } else {
    document.getElementById("chip-foreign-ratio").textContent = "—";
    document.getElementById("chip-foreign-ratio-chg").textContent = "";
  }

  const m = chip.margin || {};
  if (m.margin_balance !== undefined) {
    document.getElementById("chip-margin").textContent = m.margin_balance.toLocaleString() + " 張";
    const c20 = m.margin_change_20d;
    document.getElementById("chip-margin-chg").textContent =
      c20 !== null && c20 !== undefined
        ? `20 日 ${c20 >= 0 ? "+" : ""}${c20.toLocaleString()} 張` : "";
    document.getElementById("chip-margin-chg").className = "muted " + (c20 < 0 ? "pos" : c20 > 0 ? "neg" : "");

    document.getElementById("chip-short").textContent = (m.short_balance ?? 0).toLocaleString() + " 張";
    const s5 = m.short_change_5d;
    document.getElementById("chip-short-chg").textContent =
      s5 !== null && s5 !== undefined
        ? `5 日 ${s5 >= 0 ? "+" : ""}${s5.toLocaleString()} 張` : "";

    const ratio = m.short_resistance_ratio;
    document.getElementById("chip-ratio").textContent =
      ratio !== null && ratio !== undefined ? ratio.toFixed(2) + "%" : "—";
  } else {
    ["chip-margin", "chip-margin-chg", "chip-short", "chip-short-chg", "chip-ratio"]
      .forEach(id => document.getElementById(id).textContent = "—");
  }
}

function renderTarget(t) {
  const card = document.getElementById("target-card");
  if (!t || t.mean === null) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");

  const pill = document.getElementById("target-rec");
  pill.textContent = t.recommendation_label || "—";
  pill.className = "rec-pill " + (t.recommendation_class || "hold");

  const cnt = document.getElementById("target-count");
  cnt.textContent = t.count ? `${t.count} 位分析師` : "";

  const set = (idVal, idUp, value, upside) => {
    document.getElementById(idVal).textContent = fmt(value);
    const up = document.getElementById(idUp);
    up.textContent = pct(upside);
    up.className = "upside " + upsideClass(upside);
  };
  set("t-low",    "t-low-up",    t.low,    t.upside_low);
  set("t-mean",   "t-mean-up",   t.mean,   t.upside_mean);
  set("t-median", "t-median-up", t.median, t.upside_median);
  set("t-high",   "t-high-up",   t.high,   t.upside_high);
}

function timeAgo(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!t) return "";
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return "剛剛";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`;
  if (diff < 604800) return `${Math.floor(diff / 86400)} 天前`;
  return new Date(t).toLocaleDateString();
}

function renderNews(news) {
  const list = document.getElementById("news-list");
  const empty = document.getElementById("news-empty");
  list.innerHTML = "";
  if (!news || news.length === 0) {
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  news.forEach((n) => {
    const li = document.createElement("li");
    li.className = "news-item";
    const thumb = n.thumbnail
      ? `<img class="news-thumb" src="${n.thumbnail}" alt="" loading="lazy" onerror="this.remove()">`
      : "";
    li.innerHTML = `
      ${thumb}
      <div class="news-body">
        <a class="news-title" href="${n.url}" target="_blank" rel="noopener noreferrer">${
          n.title.replace(/[<>]/g, "")
        }</a>
        <div class="news-meta">
          <span>${n.publisher || "—"}</span>
          <span class="dot">·</span>
          <span>${timeAgo(n.published)}</span>
        </div>
      </div>
    `;
    list.appendChild(li);
  });
}

function render(data) {
  document.getElementById("stock-name").textContent = data.name;
  document.getElementById("stock-symbol").textContent = data.symbol;
  document.getElementById("current-price").textContent = fmt(data.current_price);
  document.getElementById("currency").textContent = data.currency || "";

  document.getElementById("lvl-u2").textContent = fmt(data.levels["樂觀價"]);
  document.getElementById("lvl-u1").textContent = fmt(data.levels["相對高價"]);
  document.getElementById("lvl-mid").textContent = fmt(data.levels["趨勢價"]);
  document.getElementById("lvl-d1").textContent = fmt(data.levels["相對低價"]);
  document.getElementById("lvl-d2").textContent = fmt(data.levels["悲觀價"]);

  currentData = data;
  updateAddButtons();
  renderSignal(data.signal);
  renderTarget(data.target);
  renderValuation(data.valuation, data.current_price);
  renderChip(data.chip);
  renderNews(data.news);
  resultEl.classList.remove("hidden");

  drawMain(data);
  drawRSI(data);
  drawMACD(data);
  drawKD(data);
  drawADX(data);
  drawOBV(data);
  renderStaleBanner(data);
}

function renderStaleBanner(data) {
  const el = document.getElementById("stale-banner");
  if (!el) return;
  if (data._stale) {
    const mins = Math.round((data._age_sec || 0) / 60);
    el.textContent = `⚠️ Yahoo Finance 暫時限流，顯示 ${mins} 分鐘前的快取資料`;
    el.classList.remove("hidden");
  } else {
    el.classList.add("hidden");
  }
}

function ds(label, dates, values, color, opts = {}) {
  return {
    label,
    data: values.map((y, i) => ({ x: dates[i], y })),
    borderColor: color,
    backgroundColor: opts.bg || color,
    borderWidth: opts.bw ?? 1.2,
    borderDash: opts.dash,
    pointRadius: 0,
    tension: 0,
    fill: opts.fill || false,
    hidden: opts.hidden || false,
    spanGaps: false,
  };
}

const baseScales = (yOpts = {}) => ({
  x: {
    type: "time",
    time: { unit: "month", tooltipFormat: "yyyy-MM-dd" },
    ticks: { color: "#8a93a6", maxTicksLimit: 10 },
    grid: { color: "rgba(255,255,255,0.05)" },
  },
  y: Object.assign({
    ticks: { color: "#8a93a6" },
    grid: { color: "rgba(255,255,255,0.05)" },
  }, yOpts),
});

const baseOpts = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: "index", intersect: false },
  plugins: {
    legend: { labels: { color: "#e6e9ef", boxWidth: 14, boxHeight: 2 } },
    tooltip: {
      callbacks: {
        label: (item) =>
          `${item.dataset.label}: ${item.parsed.y === null ? "—" : fmt(item.parsed.y)}`,
      },
    },
  },
};

function drawMain(data) {
  const ctx = document.getElementById("chart").getContext("2d");
  if (charts.main) charts.main.destroy();
  charts.main = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        ds("樂觀價 (+2σ)",  data.dates, data.upper2, "#ef4444", { dash: [4, 4] }),
        ds("相對高價 (+1σ)", data.dates, data.upper1, "#f59e0b", { dash: [4, 4] }),
        ds("趨勢價",        data.dates, data.trend,  "#4f9cf9", { bw: 1.4 }),
        ds("相對低價 (−1σ)", data.dates, data.lower1, "#84cc16", { dash: [4, 4] }),
        ds("悲觀價 (−2σ)",  data.dates, data.lower2, "#22c55e", { dash: [4, 4] }),
        ds("MA5",  data.dates, data.ma5,  "#fbbf24", { bw: 1, hidden: true }),
        ds("MA20", data.dates, data.ma20, "#a78bfa", { bw: 1.1 }),
        ds("MA60", data.dates, data.ma60, "#06b6d4", { bw: 1.1 }),
        ds("BB 上軌", data.dates, data.bb_upper, "#94a3b8", { bw: 0.9, dash: [2, 3], hidden: true }),
        ds("BB 下軌", data.dates, data.bb_lower, "#94a3b8", { bw: 0.9, dash: [2, 3], hidden: true }),
        ds("收盤價", data.dates, data.prices, "#e6e9ef", { bw: 1.8 }),
      ],
    },
    options: Object.assign({}, baseOpts, { scales: baseScales() }),
  });
}

function drawRSI(data) {
  const ctx = document.getElementById("chart-rsi").getContext("2d");
  if (charts.rsi) charts.rsi.destroy();
  const n = data.dates.length;
  const line70 = Array(n).fill(70);
  const line30 = Array(n).fill(30);
  charts.rsi = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        ds("超買 70", data.dates, line70, "#ef4444", { dash: [3, 3], bw: 1 }),
        ds("超賣 30", data.dates, line30, "#22c55e", { dash: [3, 3], bw: 1 }),
        ds("RSI(14)", data.dates, data.rsi, "#fbbf24", { bw: 1.6 }),
      ],
    },
    options: Object.assign({}, baseOpts, {
      scales: baseScales({ min: 0, max: 100 }),
    }),
  });
}

function drawMACD(data) {
  const ctx = document.getElementById("chart-macd").getContext("2d");
  if (charts.macd) charts.macd.destroy();
  // 柱狀體：紅綠依正負分色
  const histColors = data.macd_hist.map((v) =>
    v === null ? "rgba(0,0,0,0)" : v >= 0 ? "rgba(34,197,94,0.7)" : "rgba(239,68,68,0.7)");

  charts.macd = new Chart(ctx, {
    data: {
      datasets: [
        {
          type: "bar",
          label: "MACD Histogram",
          data: data.macd_hist.map((y, i) => ({ x: data.dates[i], y })),
          backgroundColor: histColors,
          borderWidth: 0,
        },
        Object.assign(ds("MACD", data.dates, data.macd, "#4f9cf9", { bw: 1.4 }), { type: "line" }),
        Object.assign(ds("Signal", data.dates, data.macd_signal, "#f59e0b", { bw: 1.4 }), { type: "line" }),
      ],
    },
    options: Object.assign({}, baseOpts, { scales: baseScales() }),
  });
}

function drawKD(data) {
  const ctx = document.getElementById("chart-kd").getContext("2d");
  if (charts.kd) charts.kd.destroy();
  const n = data.dates.length;
  const line80 = Array(n).fill(80);
  const line20 = Array(n).fill(20);
  charts.kd = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        ds("超買 80", data.dates, line80, "#ef4444", { dash: [3, 3], bw: 1 }),
        ds("超賣 20", data.dates, line20, "#22c55e", { dash: [3, 3], bw: 1 }),
        ds("K", data.dates, data.k, "#4f9cf9", { bw: 1.6 }),
        ds("D", data.dates, data.d, "#f97316", { bw: 1.6 }),
      ],
    },
    options: Object.assign({}, baseOpts, {
      scales: baseScales({ min: 0, max: 100 }),
    }),
  });
}

function drawADX(data) {
  const ctx = document.getElementById("chart-adx").getContext("2d");
  if (charts.adx) charts.adx.destroy();
  const n = data.dates.length;
  const line25 = Array(n).fill(25);
  charts.adx = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        ds("有趨勢 25", data.dates, line25, "#94a3b8", { dash: [3, 3], bw: 1 }),
        ds("ADX",   data.dates, data.adx,      "#fbbf24", { bw: 1.8 }),
        ds("+DI",   data.dates, data.plus_di,  "#22c55e", { bw: 1.2 }),
        ds("−DI",   data.dates, data.minus_di, "#ef4444", { bw: 1.2 }),
      ],
    },
    options: Object.assign({}, baseOpts, {
      scales: baseScales({ min: 0, max: 100 }),
    }),
  });
}

function drawOBV(data) {
  const ctx = document.getElementById("chart-obv").getContext("2d");
  if (charts.obv) charts.obv.destroy();
  // 量能用柱、OBV 用線（雙 y 軸）
  const volColors = data.prices.map((p, i) => {
    const prev = data.prices[i - 1];
    if (prev == null || p == null) return "rgba(148,163,184,0.5)";
    return p >= prev ? "rgba(34,197,94,0.5)" : "rgba(239,68,68,0.5)";
  });
  charts.obv = new Chart(ctx, {
    data: {
      datasets: [
        {
          type: "bar",
          label: "成交量",
          data: data.volume.map((y, i) => ({ x: data.dates[i], y })),
          backgroundColor: volColors,
          yAxisID: "yVol",
          borderWidth: 0,
        },
        Object.assign(
          ds("OBV", data.dates, data.obv, "#4f9cf9", { bw: 1.6 }),
          { type: "line", yAxisID: "yObv" }
        ),
      ],
    },
    options: Object.assign({}, baseOpts, {
      scales: {
        x: {
          type: "time",
          time: { unit: "month", tooltipFormat: "yyyy-MM-dd" },
          ticks: { color: "#8a93a6", maxTicksLimit: 10 },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
        yVol: {
          position: "right",
          ticks: { color: "#8a93a6", callback: (v) => {
            if (Math.abs(v) >= 1e9) return (v/1e9).toFixed(1)+"B";
            if (Math.abs(v) >= 1e6) return (v/1e6).toFixed(1)+"M";
            if (Math.abs(v) >= 1e3) return (v/1e3).toFixed(1)+"K";
            return v;
          }},
          grid: { drawOnChartArea: false },
        },
        yObv: {
          position: "left",
          ticks: { color: "#8a93a6", callback: (v) => {
            if (Math.abs(v) >= 1e9) return (v/1e9).toFixed(1)+"B";
            if (Math.abs(v) >= 1e6) return (v/1e6).toFixed(1)+"M";
            if (Math.abs(v) >= 1e3) return (v/1e3).toFixed(1)+"K";
            return v;
          }},
          grid: { color: "rgba(255,255,255,0.05)" },
        },
      },
    }),
  });
}

async function search(symbol) {
  try {
    symbolInput.value = symbol;
    const data = await fetchStock(symbol, yearsSelect.value);
    setStatus("");
    render(data);
  } catch (e) {
    setStatus(e.message, true);
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const sym = symbolInput.value.trim();
  if (sym) search(sym);
});

document.querySelectorAll(".chip").forEach((btn) => {
  btn.addEventListener("click", () => search(btn.dataset.symbol));
});

yearsSelect.addEventListener("change", () => {
  const sym = symbolInput.value.trim();
  if (sym) search(sym);
});

// ===== 加入清單按鈕 =====
document.getElementById("btn-add-watch").addEventListener("click", () => {
  if (!currentData) return;
  const sym = currentData.symbol;
  if (loadWatchlist().includes(sym)) removeWatch(sym);
  else addWatch(sym);
});

document.getElementById("btn-add-hold").addEventListener("click", () => {
  if (!currentData) return;
  const sym = currentData.symbol;
  const holdings = loadHoldings();
  const existing = holdings.find(h => h.symbol === sym);
  if (existing) {
    if (confirm(`要把 ${sym} 從持有清單移除嗎？`)) removeHold(sym);
    return;
  }
  const defaultCost = currentData.current_price.toFixed(2);
  const input = prompt(`輸入 ${sym} 的平均成本（每股）：\n（按取消可不填，僅顯示訊號）`, defaultCost);
  if (input === null) return;
  const cost = parseFloat(input);
  addHold(sym, isNaN(cost) || cost <= 0 ? 0 : cost);
});

document.getElementById("refresh-lists").addEventListener("click", () => refreshLists());

// 預設先查 AAPL；同時載入清單
search("AAPL");
refreshLists();
