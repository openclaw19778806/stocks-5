const form = document.getElementById("search-form");
const symbolInput = document.getElementById("symbol");
const yearsSelect = document.getElementById("years");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");

const charts = { main: null, rsi: null, macd: null, kd: null, adx: null, obv: null };

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

  renderSignal(data.signal);
  renderTarget(data.target);
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

// 預設先查 AAPL
search("AAPL");
