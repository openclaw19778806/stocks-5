"""
樂活五線譜 + 多指標 風格的股票走勢查詢網站
支援美股 (AAPL, TSLA...) 與台股 (2330.TW, 0050.TW...)

指標：五線譜 / MA / RSI / MACD / KD / 布林帶 / ADX / OBV / 相對大盤強度 / 分析師共識
依各指標加權分數 → BUY / HOLD / SELL 建議
"""
from flask import Flask, render_template, request, jsonify
import yfinance as yf
import numpy as np
import pandas as pd
import requests
import os
import time
import threading
from datetime import datetime, timedelta

app = Flask(__name__)

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()


# ===== TTL 快取（含 stale-on-error 退避） =====
# yfinance 新版自帶 curl_cffi 偽裝瀏覽器，session 由它處理。
_CACHE: dict = {}
_CACHE_TTL = 900          # 15 分鐘內視為「新鮮」
_STALE_MAX = 6 * 3600     # 6 小時內的舊資料可作為失敗時 fallback
_CACHE_LOCK = threading.Lock()


def cache_get(key):
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item and time.time() - item[0] < _CACHE_TTL:
            return item[1]
        return None


def cache_get_stale(key):
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item:
            age = time.time() - item[0]
            if age < _STALE_MAX:
                return item[1], age
        return None, None


def cache_set(key, value):
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)
        if len(_CACHE) > 200:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)


# ===== 工具 =====
REC_LABEL = {
    "strong_buy":   ("強力買進", "strong-buy"),
    "buy":          ("買進",     "buy"),
    "hold":         ("持有",     "hold"),
    "underperform": ("劣於大盤", "sell"),
    "sell":         ("賣出",     "sell"),
    "strong_sell":  ("強力賣出", "strong-sell"),
    "none":         ("—",       "hold"),
}


def normalize_symbol(raw: str) -> str:
    s = raw.strip().upper()
    if s.isdigit():
        return f"{s}.TW"
    return s


def is_tw(symbol: str) -> bool:
    return symbol.endswith(".TW") or symbol.endswith(".TWO") or symbol == "^TWII"


def benchmark_for(symbol: str) -> str:
    return "0050.TW" if is_tw(symbol) else "SPY"


def benchmark_label(sym: str) -> str:
    return {"SPY": "S&P 500 (SPY)", "0050.TW": "台灣 50 (0050.TW)"}.get(sym, sym)


def to_jsonable(series: pd.Series) -> list:
    return [None if pd.isna(v) else float(v) for v in series]


def extract_target(info: dict, current: float) -> dict:
    def num(k):
        v = info.get(k)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    mean = num("targetMeanPrice")
    median = num("targetMedianPrice")
    high = num("targetHighPrice")
    low = num("targetLowPrice")
    count = info.get("numberOfAnalystOpinions")
    rec = (info.get("recommendationKey") or "none").lower()
    label, cls = REC_LABEL.get(rec, REC_LABEL["none"])

    def upside(v):
        if v is None or current <= 0:
            return None
        return (v - current) / current * 100

    return {
        "mean": mean, "median": median, "high": high, "low": low,
        "count": count,
        "recommendation": rec,
        "recommendation_label": label,
        "recommendation_class": cls,
        "recommendation_mean": num("recommendationMean"),
        "upside_mean": upside(mean),
        "upside_median": upside(median),
        "upside_high": upside(high),
        "upside_low": upside(low),
    }


def extract_news(ticker, limit: int = 10) -> list:
    try:
        raw = ticker.news or []
    except Exception:
        return []

    out = []
    for item in raw[:limit]:
        title = url = publisher = pub = thumb = None
        if "content" in item:
            c = item.get("content") or {}
            title = c.get("title")
            publisher = (c.get("provider") or {}).get("displayName")
            pub = c.get("pubDate")
            url = ((c.get("canonicalUrl") or {}).get("url")
                   or (c.get("clickThroughUrl") or {}).get("url"))
            res = ((c.get("thumbnail") or {}).get("resolutions") or [])
            if res:
                thumb = res[-1].get("url")
        else:
            title = item.get("title")
            publisher = item.get("publisher")
            url = item.get("link")
            t = item.get("providerPublishTime")
            if t:
                pub = datetime.utcfromtimestamp(t).isoformat() + "Z"

        if title and url:
            out.append({
                "title": title,
                "publisher": publisher or "",
                "url": url,
                "published": pub or "",
                "thumbnail": thumb,
            })
    return out


# ===== FinMind 籌碼分析（台股） =====
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"


def finmind_get(dataset, data_id, days_back=40):
    """從 FinMind 抓某 dataset 的最近 days_back 天資料。"""
    end = datetime.now()
    start = end - timedelta(days=days_back)
    params = {
        "dataset": dataset,
        "data_id": data_id,
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
    }
    try:
        r = requests.get(FINMIND_API, params=params, timeout=10)
        if r.status_code != 200:
            return None
        j = r.json()
        if j.get("status") != 200:
            return None
        return j.get("data") or []
    except Exception:
        return None


def fetch_chip_data(stock_no: str):
    """抓單一台股的籌碼資料：法人 + 持股比 + 融資融券。返回整理後的 dict。"""
    inst = finmind_get("TaiwanStockInstitutionalInvestorsBuySell", stock_no)
    shr  = finmind_get("TaiwanStockShareholding", stock_no, days_back=60)
    mgn  = finmind_get("TaiwanStockMarginPurchaseShortSale", stock_no)

    if not inst and not shr and not mgn:
        return None

    # ----- 法人：依日期累計 -----
    # FinMind name 欄位有：Foreign_Investor / Investment_Trust / Dealer_self / Dealer_Hedging / Foreign_Dealer_Self
    from collections import defaultdict
    daily = defaultdict(lambda: {"foreign": 0, "trust": 0, "dealer": 0, "vol": 0})
    if inst:
        for r in inst:
            name = r.get("name", "")
            net = (r.get("buy") or 0) - (r.get("sell") or 0)
            date = r.get("date")
            if name in ("Foreign_Investor", "Foreign_Dealer_Self"):
                daily[date]["foreign"] += net
            elif name == "Investment_Trust":
                daily[date]["trust"] += net
            elif name in ("Dealer_self", "Dealer_Hedging"):
                daily[date]["dealer"] += net
            daily[date]["vol"] += (r.get("buy") or 0) + (r.get("sell") or 0)

    dates_sorted = sorted(daily.keys())
    # 取最近 5 / 20 個交易日累計
    def cumsum(n, key):
        return sum(daily[d][key] for d in dates_sorted[-n:])

    inst_summary = {
        "latest_date": dates_sorted[-1] if dates_sorted else None,
        "foreign_5d":  cumsum(5,  "foreign"),
        "foreign_20d": cumsum(20, "foreign"),
        "trust_5d":    cumsum(5,  "trust"),
        "trust_20d":   cumsum(20, "trust"),
        "dealer_5d":   cumsum(5,  "dealer"),
        "dealer_20d":  cumsum(20, "dealer"),
        "vol_20d":     cumsum(20, "vol"),
    }

    # ----- 外資持股比率 -----
    holding = None
    if shr:
        shr_sorted = sorted(shr, key=lambda r: r.get("date", ""))
        latest = shr_sorted[-1]
        ratio_now = latest.get("ForeignInvestmentSharesRatio")
        ratio_30d_ago = None
        if len(shr_sorted) >= 21:
            ratio_30d_ago = shr_sorted[-21].get("ForeignInvestmentSharesRatio")
        elif len(shr_sorted) > 1:
            ratio_30d_ago = shr_sorted[0].get("ForeignInvestmentSharesRatio")
        holding = {
            "latest_date": latest.get("date"),
            "foreign_ratio": ratio_now,
            "foreign_ratio_change": (ratio_now - ratio_30d_ago) if (ratio_now is not None and ratio_30d_ago is not None) else None,
        }

    # ----- 融資融券 -----
    margin = None
    if mgn:
        mgn_sorted = sorted(mgn, key=lambda r: r.get("date", ""))
        latest = mgn_sorted[-1]
        m_now = latest.get("MarginPurchaseTodayBalance") or 0
        s_now = latest.get("ShortSaleTodayBalance") or 0
        m_5d_ago = mgn_sorted[-6].get("MarginPurchaseTodayBalance") if len(mgn_sorted) >= 6 else None
        s_5d_ago = mgn_sorted[-6].get("ShortSaleTodayBalance") if len(mgn_sorted) >= 6 else None
        m_20d_ago = mgn_sorted[-21].get("MarginPurchaseTodayBalance") if len(mgn_sorted) >= 21 else None
        margin = {
            "latest_date": latest.get("date"),
            "margin_balance": m_now,
            "margin_change_5d": (m_now - m_5d_ago) if m_5d_ago is not None else None,
            "margin_change_20d": (m_now - m_20d_ago) if m_20d_ago is not None else None,
            "short_balance": s_now,
            "short_change_5d": (s_now - s_5d_ago) if s_5d_ago is not None else None,
            "short_resistance_ratio": (s_now / m_now * 100) if m_now > 0 else None,
        }

    return {"institutional": inst_summary, "holding": holding, "margin": margin}


def compute_chip_reasons(chip: dict) -> list:
    """依籌碼資料產生 ±5 維度的訊號明細。"""
    reasons = []
    if not chip:
        return reasons

    inst = chip.get("institutional") or {}
    f20 = inst.get("foreign_20d") or 0
    f5  = inst.get("foreign_5d") or 0
    t20 = inst.get("trust_20d") or 0
    vol20 = inst.get("vol_20d") or 0

    # 外資 20 日累計 / 20 日總成交量 → 強度比
    if vol20 > 0:
        f_intensity = f20 / vol20 * 100
        if f_intensity >= 15:
            sc = 2; det = f"外資 20 日大幅買超（淨買 {f20/1000:+,.0f} 張，占成交 {f_intensity:+.1f}%）"
        elif f_intensity >= 5:
            sc = 1; det = f"外資 20 日買超（淨買 {f20/1000:+,.0f} 張）"
        elif f_intensity <= -15:
            sc = -2; det = f"外資 20 日大幅賣超（淨賣 {f20/1000:+,.0f} 張）"
        elif f_intensity <= -5:
            sc = -1; det = f"外資 20 日賣超（淨賣 {f20/1000:+,.0f} 張）"
        else:
            sc = 0; det = f"外資 20 日中性（淨 {f20/1000:+,.0f} 張）"
    else:
        sc, det = 0, "外資資料不足"
    reasons.append({"score": sc, "name": "外資 20d", "detail": det})

    # 投信 20 日（權重較小，±1）
    if vol20 > 0:
        t_intensity = t20 / vol20 * 100
        if t_intensity >= 3:
            sc = 1; det = f"投信買超（淨買 {t20/1000:+,.0f} 張）"
        elif t_intensity <= -3:
            sc = -1; det = f"投信賣超（淨賣 {t20/1000:+,.0f} 張）"
        else:
            sc = 0; det = f"投信中性（淨 {t20/1000:+,.0f} 張）"
    else:
        sc, det = 0, "投信資料不足"
    reasons.append({"score": sc, "name": "投信 20d", "detail": det})

    # 外資持股比 30 日變化
    h = chip.get("holding") or {}
    chg = h.get("foreign_ratio_change")
    if chg is not None:
        if chg >= 0.5:
            sc = 1; det = f"外資持股比上升 {chg:+.2f}%（買盤累積中）"
        elif chg <= -0.5:
            sc = -1; det = f"外資持股比下降 {chg:+.2f}%（外資出場）"
        else:
            sc = 0; det = f"外資持股比變動小 ({chg:+.2f}%)"
        reasons.append({"score": sc, "name": "外資持股比", "detail": det})

    # 融資 20 日變化（散戶情緒：融資大幅減少代表斷頭洗清，反向 +1）
    m = chip.get("margin") or {}
    m20 = m.get("margin_change_20d")
    if m20 is not None and m.get("margin_balance"):
        pct = m20 / (m.get("margin_balance") - m20) * 100 if (m.get("margin_balance") - m20) > 0 else 0
        if pct <= -10:
            sc = 1; det = f"融資 20 日大降 {pct:+.1f}%（散戶斷頭，籌碼洗清）"
        elif pct >= 15:
            sc = -1; det = f"融資 20 日大增 {pct:+.1f}%（散戶追高，潛在套牢）"
        else:
            sc = 0; det = f"融資變動小（{pct:+.1f}%）"
        reasons.append({"score": sc, "name": "融資 20d", "detail": det})

    return reasons


# ===== Finnhub 補強（新聞、推薦評等） =====
def finnhub_get(path, params, timeout=8):
    if not FINNHUB_KEY:
        return None
    try:
        params = {**params, "token": FINNHUB_KEY}
        r = requests.get(f"https://finnhub.io/api/v1{path}",
                         params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def finnhub_news(symbol):
    """美股新聞最豐富；台股通常為空，由 yfinance 補。"""
    end = datetime.now()
    start = end - timedelta(days=14)
    data = finnhub_get("/company-news", {
        "symbol": symbol,
        "from": start.strftime("%Y-%m-%d"),
        "to": end.strftime("%Y-%m-%d"),
    })
    if not isinstance(data, list) or not data:
        return None
    out = []
    for item in data[:10]:
        ts = item.get("datetime")
        try:
            iso = datetime.utcfromtimestamp(int(ts)).isoformat() + "Z" if ts else ""
        except Exception:
            iso = ""
        title = item.get("headline", "")
        url = item.get("url", "")
        if title and url:
            out.append({
                "title": title,
                "publisher": item.get("source", ""),
                "url": url,
                "published": iso,
                "thumbnail": item.get("image") or None,
            })
    return out or None


def finnhub_recommendation(symbol):
    """回傳最新月份的分析師人頭分布 + 推導 recommendation_key。"""
    data = finnhub_get("/stock/recommendation", {"symbol": symbol})
    if not isinstance(data, list) or not data:
        return None
    latest = data[0]
    sb = int(latest.get("strongBuy", 0) or 0)
    b  = int(latest.get("buy", 0) or 0)
    h  = int(latest.get("hold", 0) or 0)
    s  = int(latest.get("sell", 0) or 0)
    ss = int(latest.get("strongSell", 0) or 0)
    total = sb + b + h + s + ss
    if total == 0:
        return None

    bull = sb + b
    bear = s + ss
    if sb >= total * 0.5 and bull >= total * 0.7:
        rec = "strong_buy"
    elif bull >= total * 0.6:
        rec = "buy"
    elif ss >= total * 0.5 and bear >= total * 0.7:
        rec = "strong_sell"
    elif bear >= total * 0.5:
        rec = "sell"
    else:
        rec = "hold"
    return {
        "recommendation": rec,
        "count": total,
        "strong_buy": sb, "buy": b, "hold": h, "sell": s, "strong_sell": ss,
        "period": latest.get("period"),
    }


# ===== 抓資料（含 session、benchmark cache） =====
def fetch_history(symbol, start, end):
    ticker = yf.Ticker(symbol)
    hist = ticker.history(start=start, end=end, auto_adjust=True)
    return ticker, hist


def fetch_benchmark_return(bench_sym: str, days: int = 60):
    """近 days 日 benchmark 報酬率（%）。獨立快取 30 分鐘。"""
    key = ("bench", bench_sym, days)
    cached = cache_get(key)
    if cached is not None:
        return cached
    end = datetime.now()
    start = end - timedelta(days=int(days * 2))
    try:
        _, h = fetch_history(bench_sym, start, end)
        h = h.dropna(subset=["Close"])
        if len(h) < days + 1:
            return None
        ret = float(h["Close"].iloc[-1] / h["Close"].iloc[-days] - 1) * 100
        cache_set(key, ret)
        return ret
    except Exception:
        return None


# ===== 指標計算 =====
def compute_indicators(hist: pd.DataFrame) -> dict:
    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"]

    # 均線
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    # RSI(14) Wilder
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)

    # MACD(12,26,9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line

    # KD(9,3,3)
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()

    # 布林帶(20, 2σ)
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std(ddof=0)
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    pct_b = (close - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)

    # ADX(14)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )
    atr_safe = atr.replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_safe
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()

    # OBV + Volume MA20
    direction = np.sign(close.diff().fillna(0))
    obv = (direction * volume).cumsum()
    vol_ma20 = volume.rolling(20).mean()

    return {
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "rsi": rsi,
        "macd": macd_line, "signal": signal_line, "macd_hist": macd_hist,
        "k": k, "d": d,
        "bb_upper": bb_upper, "bb_mid": bb_mid, "bb_lower": bb_lower, "pct_b": pct_b,
        "adx": adx, "plus_di": plus_di, "minus_di": minus_di,
        "obv": obv, "volume": volume, "vol_ma20": vol_ma20,
    }


# ===== 訊號彙整 =====
def _safe(s, idx=-1, default=0.0):
    try:
        v = s.iloc[idx]
        return float(v) if not pd.isna(v) else default
    except Exception:
        return default


def build_signal(close, ind, z, target=None,
                 relative_return=None, benchmark_name=None) -> dict:
    reasons = []
    score = 0

    # 1. 五線譜 (±2)
    if z <= -1.5:
        score += 2; reasons.append({"score": +2, "name": "五線譜", "detail": f"極度低估 (z={z:+.2f}σ)"})
    elif z <= -0.5:
        score += 1; reasons.append({"score": +1, "name": "五線譜", "detail": f"相對便宜 (z={z:+.2f}σ)"})
    elif z >= 1.5:
        score -= 2; reasons.append({"score": -2, "name": "五線譜", "detail": f"極度高估 (z={z:+.2f}σ)"})
    elif z >= 0.5:
        score -= 1; reasons.append({"score": -1, "name": "五線譜", "detail": f"相對偏貴 (z={z:+.2f}σ)"})
    else:
        reasons.append({"score": 0, "name": "五線譜", "detail": f"合理區間 (z={z:+.2f}σ)"})

    # 2. 震盪指標 = RSI + KD 合併 (±2)
    rsi_now = _safe(ind["rsi"])
    k_now = _safe(ind["k"]); k_prev = _safe(ind["k"], -2)
    d_now = _safe(ind["d"]); d_prev = _safe(ind["d"], -2)
    golden = k_prev <= d_prev and k_now > d_now
    death = k_prev >= d_prev and k_now < d_now

    osc = 0
    parts = []
    if rsi_now < 30:
        osc += 1; parts.append(f"RSI {rsi_now:.0f} 超賣")
    elif rsi_now > 70:
        osc -= 1; parts.append(f"RSI {rsi_now:.0f} 超買")
    else:
        parts.append(f"RSI {rsi_now:.0f}")
    if k_now < 20:
        osc += 1; parts.append(f"K {k_now:.0f} 超賣")
    elif k_now > 80:
        osc -= 1; parts.append(f"K {k_now:.0f} 超買")
    else:
        parts.append(f"K {k_now:.0f}")
    if golden and k_now < 30:
        osc = min(2, osc + 1); parts.append("KD 低檔黃金交叉")
    elif death and k_now > 70:
        osc = max(-2, osc - 1); parts.append("KD 高檔死亡交叉")
    elif golden:
        parts.append("KD 黃金交叉")
    elif death:
        parts.append("KD 死亡交叉")
    osc = max(-2, min(2, osc))
    score += osc
    reasons.append({"score": osc, "name": "震盪指標", "detail": "；".join(parts)})

    # ADX 趨勢強度（用於門檻）
    adx_now = _safe(ind["adx"])
    plus_di_now = _safe(ind["plus_di"])
    minus_di_now = _safe(ind["minus_di"])
    in_trend = adx_now >= 20

    # 3. MACD (±1)，ADX < 20 視為盤整，不加減分
    h_now = _safe(ind["macd_hist"])
    h_prev = _safe(ind["macd_hist"], -2)
    if not in_trend:
        reasons.append({"score": 0, "name": "MACD", "detail": f"盤整中 (ADX={adx_now:.0f}<20)，動能訊號低權重"})
    elif h_now > 0 and h_now > h_prev:
        score += 1; reasons.append({"score": +1, "name": "MACD", "detail": f"柱狀體擴張向上 ({h_now:+.2f})"})
    elif h_now < 0 and h_now < h_prev:
        score -= 1; reasons.append({"score": -1, "name": "MACD", "detail": f"柱狀體擴張向下 ({h_now:+.2f})"})
    else:
        reasons.append({"score": 0, "name": "MACD", "detail": f"動能轉弱 ({h_now:+.2f})"})

    # 4. 均線 (±1)，同樣受 ADX 門檻過濾
    p = _safe(close); m20 = _safe(ind["ma20"]); m60 = _safe(ind["ma60"])
    if not in_trend:
        reasons.append({"score": 0, "name": "均線", "detail": f"盤整中 (ADX={adx_now:.0f}<20)，均線訊號低權重"})
    elif p > m20 > m60:
        score += 1; reasons.append({"score": +1, "name": "均線", "detail": "多頭排列（價>MA20>MA60）"})
    elif p < m20 < m60:
        score -= 1; reasons.append({"score": -1, "name": "均線", "detail": "空頭排列（價<MA20<MA60）"})
    else:
        reasons.append({"score": 0, "name": "均線", "detail": "盤整（均線糾結）"})

    # 5. 布林帶 (±1)
    pct_b_val = ind["pct_b"].iloc[-1]
    pct_b = float(pct_b_val) if not pd.isna(pct_b_val) else 0.5
    if pct_b < 0.05:
        score += 1; reasons.append({"score": +1, "name": "布林帶", "detail": f"%B={pct_b:.2f}：跌破下軌"})
    elif pct_b > 0.95:
        score -= 1; reasons.append({"score": -1, "name": "布林帶", "detail": f"%B={pct_b:.2f}：突破上軌"})
    elif pct_b < 0.2:
        reasons.append({"score": 0, "name": "布林帶", "detail": f"%B={pct_b:.2f}：靠近下軌"})
    elif pct_b > 0.8:
        reasons.append({"score": 0, "name": "布林帶", "detail": f"%B={pct_b:.2f}：靠近上軌"})
    else:
        reasons.append({"score": 0, "name": "布林帶", "detail": f"%B={pct_b:.2f}：中性"})

    # 6. 量能/OBV (±1)
    obv = ind["obv"]
    lookback = min(60, len(obv) - 1)
    obv_now = _safe(obv); obv_then = _safe(obv, -lookback)
    obv_change = obv_now - obv_then
    price_change_pct = (p / _safe(close, -lookback) - 1) * 100 if _safe(close, -lookback) > 0 else 0
    vol_now = _safe(ind["volume"]); vol_ma = _safe(ind["vol_ma20"], default=vol_now)
    vol_ratio = vol_now / vol_ma if vol_ma > 0 else 1.0

    obv_dir = 1 if obv_change > 0 else -1 if obv_change < 0 else 0
    price_dir = 1 if price_change_pct > 1 else -1 if price_change_pct < -1 else 0

    if obv_dir == 1 and price_dir == 1:
        score += 1; reasons.append({"score": +1, "name": "量能/OBV", "detail": f"60 日量價同步向上，今量/均量 ×{vol_ratio:.1f}"})
    elif obv_dir == -1 and price_dir == -1:
        score -= 1; reasons.append({"score": -1, "name": "量能/OBV", "detail": f"60 日量價同步向下，今量/均量 ×{vol_ratio:.1f}"})
    elif obv_dir == 1 and price_dir == -1:
        score += 1; reasons.append({"score": +1, "name": "量能/OBV", "detail": "底背離：價跌但 OBV 上揚（買盤累積）"})
    elif obv_dir == -1 and price_dir == 1:
        score -= 1; reasons.append({"score": -1, "name": "量能/OBV", "detail": "頂背離：價漲但 OBV 下滑（量能不支持）"})
    else:
        reasons.append({"score": 0, "name": "量能/OBV", "detail": f"中性，今量/均量 ×{vol_ratio:.1f}"})

    # 7. 相對大盤 (±2)
    if relative_return is not None:
        bn = benchmark_name or "大盤"
        if relative_return >= 15:
            score += 2; reasons.append({"score": +2, "name": "相對大盤", "detail": f"60 日大幅強於 {bn} ({relative_return:+.1f}%)"})
        elif relative_return >= 5:
            score += 1; reasons.append({"score": +1, "name": "相對大盤", "detail": f"60 日強於 {bn} ({relative_return:+.1f}%)"})
        elif relative_return <= -15:
            score -= 2; reasons.append({"score": -2, "name": "相對大盤", "detail": f"60 日大幅弱於 {bn} ({relative_return:+.1f}%)"})
        elif relative_return <= -5:
            score -= 1; reasons.append({"score": -1, "name": "相對大盤", "detail": f"60 日弱於 {bn} ({relative_return:+.1f}%)"})
        else:
            reasons.append({"score": 0, "name": "相對大盤", "detail": f"與 {bn} 同步 ({relative_return:+.1f}%)"})
    else:
        reasons.append({"score": 0, "name": "相對大盤", "detail": "無基準資料"})

    # 8. 分析師共識 (±2)
    if target and target.get("mean") is not None:
        rec = target.get("recommendation") or "none"
        up = target.get("upside_mean")
        up_str = f"，平均目標價漲幅 {up:+.1f}%" if up is not None else ""
        rec_map = {
            "strong_buy":  (+2, "強力買進"),
            "buy":         (+1, "買進"),
            "hold":        (0,  "持有"),
            "underperform": (-1, "劣於大盤"),
            "sell":        (-1, "賣出"),
            "strong_sell": (-2, "強力賣出"),
        }
        s, lbl = rec_map.get(rec, (0, "無"))
        score += s
        reasons.append({"score": s, "name": "分析師共識", "detail": f"{lbl}{up_str}"})
    else:
        reasons.append({"score": 0, "name": "分析師共識", "detail": "無資料"})

    # 綜合（範圍 −12 ~ +12，因 ADX 過濾，常見 −10 ~ +10）
    if score >= 5:
        label, cls = "STRONG BUY", "strong-buy"
    elif score >= 2:
        label, cls = "BUY", "buy"
    elif score <= -5:
        label, cls = "STRONG SELL", "strong-sell"
    elif score <= -2:
        label, cls = "SELL", "sell"
    else:
        label, cls = "HOLD", "hold"

    return {
        "score": score, "label": label, "class": cls,
        "reasons": reasons,
        "adx": adx_now, "plus_di": plus_di_now, "minus_di": minus_di_now,
    }


# ===== Routes =====
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def _get_stock_payload(raw: str, years: float):
    """共用：抓單檔資料 + 計算指標 + 訊號 → 完整 payload。
    回傳 (payload, error_msg)；payload 為 None 表示失敗。
    """
    symbol = normalize_symbol(raw)
    cache_key = (symbol, round(years, 2))
    cached = cache_get(cache_key)
    if cached is not None:
        return cached, None
    return _fetch_payload(raw, years, symbol, cache_key)


def _fetch_payload(raw, years, symbol, cache_key):
    end = datetime.now()
    start = end - timedelta(days=int(years * 365.25))
    try:
        ticker, hist = fetch_history(symbol, start, end)
        if hist.empty and symbol.endswith(".TW"):
            symbol_two = symbol.replace(".TW", ".TWO")
            ticker, hist = fetch_history(symbol_two, start, end)
            if not hist.empty:
                symbol = symbol_two

        hist = hist.dropna(subset=["Close"])
        if hist.empty or len(hist) < 60:
            return None, f"找不到 {raw} 的足量資料（需至少 60 個交易日）"

        close = hist["Close"]
        dates = hist.index.strftime("%Y-%m-%d").tolist()
        prices = close.to_numpy()

        x = np.arange(len(prices))
        slope, intercept = np.polyfit(x, prices, 1)
        trend = slope * x + intercept
        sigma = float(np.std(prices - trend, ddof=1))

        ind = compute_indicators(hist)

        # ticker.info 容易被 Yahoo 擋；失敗也不影響主流程
        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            pass
        name = info.get("longName") or info.get("shortName") or symbol
        currency = info.get("currency", "")
        # 沒拿到 currency 時用啟發式
        if not currency:
            currency = "TWD" if is_tw(symbol) else "USD"

        current = float(prices[-1])
        trend_now = float(trend[-1])
        z = (current - trend_now) / sigma if sigma > 0 else 0.0

        target = extract_target(info, current)

        # Finnhub 補強推薦評等（覆蓋 yfinance 的 recommendation 欄位，目標價維持 yfinance）
        fh_rec = finnhub_recommendation(symbol) if FINNHUB_KEY else None
        if fh_rec:
            rec = fh_rec["recommendation"]
            label, cls = REC_LABEL.get(rec, REC_LABEL["none"])
            target["recommendation"] = rec
            target["recommendation_label"] = label
            target["recommendation_class"] = cls
            target["count"] = fh_rec["count"]
            target["finnhub_counts"] = {
                "strong_buy": fh_rec["strong_buy"],
                "buy": fh_rec["buy"],
                "hold": fh_rec["hold"],
                "sell": fh_rec["sell"],
                "strong_sell": fh_rec["strong_sell"],
                "period": fh_rec.get("period"),
            }

        bench_sym = benchmark_for(symbol)
        rel_ret = None
        if symbol != bench_sym and len(close) >= 61:
            bench_ret = fetch_benchmark_return(bench_sym)
            if bench_ret is not None:
                stock_ret = (current / float(close.iloc[-60]) - 1) * 100
                rel_ret = stock_ret - bench_ret

        signal = build_signal(close, ind, z, target, rel_ret,
                              benchmark_label(bench_sym) if rel_ret is not None else None)

        # 籌碼分析（僅台股）
        chip = None
        if is_tw(symbol):
            stock_no = symbol.replace(".TW", "").replace(".TWO", "")
            chip = fetch_chip_data(stock_no)
            if chip:
                chip_reasons = compute_chip_reasons(chip)
                # 加入訊號維度
                signal["reasons"].extend(chip_reasons)
                signal["score"] += sum(r["score"] for r in chip_reasons)
                # 重新分類 label（加入籌碼後範圍 ±17）
                sc = signal["score"]
                if sc >= 7:
                    signal["label"], signal["class"] = "STRONG BUY", "strong-buy"
                elif sc >= 3:
                    signal["label"], signal["class"] = "BUY", "buy"
                elif sc <= -7:
                    signal["label"], signal["class"] = "STRONG SELL", "strong-sell"
                elif sc <= -3:
                    signal["label"], signal["class"] = "SELL", "sell"
                else:
                    signal["label"], signal["class"] = "HOLD", "hold"

        # 新聞：Finnhub 優先（美股豐富），yfinance fallback（台股）
        news = []
        if FINNHUB_KEY:
            fh_news = finnhub_news(symbol)
            if fh_news:
                news = fh_news
        if not news:
            news = extract_news(ticker)

        payload = {
            "symbol": symbol, "name": name, "currency": currency,
            "dates": dates, "prices": prices.tolist(),
            "trend": trend.tolist(),
            "upper2": (trend + 2 * sigma).tolist(),
            "upper1": (trend + 1 * sigma).tolist(),
            "lower1": (trend - 1 * sigma).tolist(),
            "lower2": (trend - 2 * sigma).tolist(),
            "ma5":  to_jsonable(ind["ma5"]),
            "ma20": to_jsonable(ind["ma20"]),
            "ma60": to_jsonable(ind["ma60"]),
            "rsi":  to_jsonable(ind["rsi"]),
            "macd": to_jsonable(ind["macd"]),
            "macd_signal": to_jsonable(ind["signal"]),
            "macd_hist":   to_jsonable(ind["macd_hist"]),
            "k": to_jsonable(ind["k"]),
            "d": to_jsonable(ind["d"]),
            "bb_upper": to_jsonable(ind["bb_upper"]),
            "bb_mid":   to_jsonable(ind["bb_mid"]),
            "bb_lower": to_jsonable(ind["bb_lower"]),
            "adx":      to_jsonable(ind["adx"]),
            "plus_di":  to_jsonable(ind["plus_di"]),
            "minus_di": to_jsonable(ind["minus_di"]),
            "obv":      to_jsonable(ind["obv"]),
            "volume":   to_jsonable(ind["volume"]),
            "current_price": current,
            "trend_now": trend_now,
            "sigma": sigma,
            "z_score": z,
            "levels": {
                "樂觀價":   trend_now + 2 * sigma,
                "相對高價": trend_now + 1 * sigma,
                "趨勢價":   trend_now,
                "相對低價": trend_now - 1 * sigma,
                "悲觀價":   trend_now - 2 * sigma,
            },
            "benchmark": benchmark_label(bench_sym),
            "relative_return": rel_ret,
            "signal": signal,
            "target": target,
            "news": news,
            "chip": chip,
            "_stale": False,
        }
        cache_set(cache_key, payload)
        return payload, None
    except Exception as e:
        stale, age = cache_get_stale(cache_key)
        if stale is not None:
            stale = {**stale, "_stale": True, "_age_sec": int(age), "_error": str(e)}
            return stale, None
        return None, str(e)


def _get_multi_payload(raw: str):
    """抓一次 10 年資料，本地切出 1/3.5/5/10 年回歸 + 訊號。
    省 4 倍 yfinance 載荷。回傳 (payload, error)。
    為了快不 fetch ticker.info（target 設 None），不打 Finnhub、不算 relative strength。
    訊號分數範圍：±8（少了 analyst 與 relative）"""
    symbol = normalize_symbol(raw)
    cache_key = ("multi", symbol)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached, None

    end = datetime.now()
    start = end - timedelta(days=int(10 * 365.25))

    try:
        ticker, hist = fetch_history(symbol, start, end)
        if hist.empty and symbol.endswith(".TW"):
            symbol_two = symbol.replace(".TW", ".TWO")
            ticker, hist = fetch_history(symbol_two, start, end)
            if not hist.empty:
                symbol = symbol_two

        hist = hist.dropna(subset=["Close"])
        if hist.empty or len(hist) < 60:
            return None, f"找不到 {raw} 的足量資料"

        current = float(hist["Close"].iloc[-1])
        windows = {}
        for years in [1.0, 3.5, 5.0, 10.0]:
            ndays = int(years * 252)  # 252 個交易日 ≒ 1 年
            # 容忍稍短：3.5 年至少要有 2 年資料才計算
            min_needed = max(60, int(ndays * 0.6))
            if len(hist) < min_needed:
                windows[str(years)] = {"error": "資料不足"}
                continue
            window_hist = hist.tail(min(len(hist), ndays))
            close = window_hist["Close"]
            prices = close.to_numpy()
            x = np.arange(len(prices))
            slope, intercept = np.polyfit(x, prices, 1)
            trend = slope * x + intercept
            sigma = float(np.std(prices - trend, ddof=1))
            trend_now = float(trend[-1])
            current_p = float(prices[-1])
            z = (current_p - trend_now) / sigma if sigma > 0 else 0.0

            ind = compute_indicators(window_hist)
            signal = build_signal(close, ind, z,
                                  target=None, relative_return=None, benchmark_name=None)
            # 年化趨勢漲幅 = 斜率 × 252 (一年交易日) / 平均價，% — 對負區間 robust
            mean_p = float(prices.mean())
            slope_pa_pct = (slope * 252) / mean_p * 100 if mean_p > 0 else 0
            windows[str(years)] = {
                "z_score": z,
                "trend_up": bool(slope > 0),
                "trend_slope_pa_pct": slope_pa_pct,
                "signal": {
                    "label": signal["label"],
                    "score": signal["score"],
                    "class": signal["class"],
                },
                "days": len(prices),
            }

        payload = {
            "symbol": symbol,
            "current_price": current,
            "data_days": len(hist),
            "windows": windows,
        }
        cache_set(cache_key, payload)
        return payload, None

    except Exception as e:
        stale, age = cache_get_stale(cache_key)
        if stale is not None:
            return {**stale, "_stale": True, "_age_sec": int(age)}, None
        return None, str(e)


def _get_chip_only(raw):
    """單獨抓籌碼資料 + 計分（台股專用）。"""
    symbol = normalize_symbol(raw)
    if not is_tw(symbol):
        return None, "not a TW stock"
    stock_no = symbol.replace(".TW", "").replace(".TWO", "")
    cache_key = ("chip_only", stock_no)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached, None
    try:
        chip = fetch_chip_data(stock_no)
        if not chip:
            return None, "no chip data"
        reasons = compute_chip_reasons(chip)
        payload = {
            "symbol": symbol,
            "chip": chip,
            "reasons": reasons,
            "score": sum(r["score"] for r in reasons),
        }
        cache_set(cache_key, payload)
        return payload, None
    except Exception as e:
        return None, str(e)


@app.route("/api/chip")
def chip_only():
    raw = request.args.get("symbol", "")
    payload, err = _get_chip_only(raw)
    if payload is None:
        return jsonify({"error": err}), 404
    return jsonify(payload)


@app.route("/api/chip_scan")
def chip_scan():
    """批量抓籌碼，只回計分結果，供掃描使用。"""
    raw = request.args.get("symbols", "").strip()
    if not raw:
        return jsonify({"results": []})
    syms = [s.strip() for s in raw.split(",") if s.strip()][:50]
    results = []
    for s in syms:
        payload, err = _get_chip_only(s)
        if payload is None:
            results.append({"requested": s, "error": err})
        else:
            results.append({"requested": s, **payload})
    return jsonify({"results": results})


@app.route("/api/multi")
def multi_window():
    raw = request.args.get("symbol", "AAPL")
    payload, err = _get_multi_payload(raw)
    if payload is None:
        return jsonify({"error": err}), 500
    return jsonify(payload)


@app.route("/api/multi_scan")
def multi_scan():
    """批量 multi 查詢，供整盤掃描使用。"""
    raw = request.args.get("symbols", "").strip()
    if not raw:
        return jsonify({"results": []})
    syms = [s.strip() for s in raw.split(",") if s.strip()][:50]
    results = []
    for s in syms:
        payload, err = _get_multi_payload(s)
        if payload is None:
            results.append({"requested": s, "error": err})
        else:
            results.append({"requested": s, **payload})
    return jsonify({"results": results})


@app.route("/api/scan")
def scan():
    """批量查詢，供觀察清單/持有清單使用，只回傳輕量摘要。"""
    raw = request.args.get("symbols", "").strip()
    try:
        years = float(request.args.get("years", 3.5))
    except ValueError:
        years = 3.5
    if not raw:
        return jsonify({"results": []})
    syms = [s.strip() for s in raw.split(",") if s.strip()][:50]

    results = []
    for s in syms:
        payload, err = _get_stock_payload(s, years)
        if payload is None:
            results.append({"requested": s, "error": err})
            continue
        trend = payload.get("trend") or []
        trend_slope_pct = None
        if len(trend) > 1 and trend[0]:
            trend_slope_pct = (trend[-1] / trend[0] - 1) * 100  # 3.5 年趨勢累積漲幅 %
        results.append({
            "requested": s,
            "symbol": payload["symbol"],
            "name": payload["name"],
            "currency": payload["currency"],
            "price": payload["current_price"],
            "z_score": payload["z_score"],
            "trend_slope_pct": trend_slope_pct,
            "signal": {
                "label": payload["signal"]["label"],
                "score": payload["signal"]["score"],
                "class": payload["signal"]["class"],
            },
            "_stale": payload.get("_stale", False),
        })
    return jsonify({"results": results})


@app.route("/api/stock")
def get_stock():
    raw = request.args.get("symbol", "AAPL")
    years = float(request.args.get("years", 3.5))
    payload, err = _get_stock_payload(raw, years)
    if payload is None:
        status = 404 if err and "找不到" in err else 500
        return jsonify({"error": err}), status
    return jsonify(payload)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, host="0.0.0.0", port=port)
