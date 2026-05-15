"""
樂活五線譜 + 技術指標 風格的股票走勢查詢網站
支援美股 (AAPL, TSLA...) 與台股 (2330.TW, 0050.TW...)
回傳：五線譜 / MA5/20/60 / RSI(14) / MACD(12,26,9) / KD(9,3,3)
並依各指標分數綜合出 BUY / HOLD / SELL 建議
"""
from flask import Flask, render_template, request, jsonify
import yfinance as yf
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

app = Flask(__name__)


REC_LABEL = {
    "strong_buy": ("強力買進", "strong-buy"),
    "buy":        ("買進",     "buy"),
    "hold":       ("持有",     "hold"),
    "underperform": ("劣於大盤", "sell"),
    "sell":       ("賣出",     "sell"),
    "strong_sell": ("強力賣出", "strong-sell"),
    "none":       ("—",       "hold"),
}


def extract_target(info: dict, current: float) -> dict:
    """從 yfinance .info 抓分析師目標價；缺值回傳 None 欄位以方便前端判斷。"""
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
        "mean": mean,
        "median": median,
        "high": high,
        "low": low,
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
    """整理 ticker.news 為前端使用的精簡格式；支援新舊兩種 schema。"""
    try:
        raw = ticker.news or []
    except Exception:
        return []

    out = []
    for item in raw[:limit]:
        title = url = publisher = pub = thumb = None

        if "content" in item:  # 新版 schema
            c = item.get("content") or {}
            title = c.get("title")
            publisher = (c.get("provider") or {}).get("displayName")
            pub = c.get("pubDate")  # ISO 字串
            url = ((c.get("canonicalUrl") or {}).get("url")
                   or (c.get("clickThroughUrl") or {}).get("url"))
            thumb_list = ((c.get("thumbnail") or {}).get("resolutions") or [])
            if thumb_list:
                thumb = thumb_list[-1].get("url")
        else:  # 舊版
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


def normalize_symbol(raw: str) -> str:
    """純數字 → 視為台股，自動加上 .TW；其餘保留原樣（大寫）。"""
    s = raw.strip().upper()
    if s.isdigit():
        return f"{s}.TW"
    return s


def to_jsonable(series: pd.Series) -> list:
    """pandas Series → list；NaN 轉為 None。"""
    return [None if pd.isna(v) else float(v) for v in series]


def compute_indicators(hist: pd.DataFrame) -> dict:
    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]

    # 均線
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    # RSI(14) - Wilder smoothing
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)

    # MACD(12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line

    # KD (9-day Stochastic, 台股慣用 1/3 平滑)
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()

    return {
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "rsi": rsi,
        "macd": macd_line, "signal": signal_line, "macd_hist": macd_hist,
        "k": k, "d": d,
    }


def build_signal(close: pd.Series, ind: dict, z: float, target: dict | None = None) -> dict:
    """彙整各指標 → 分數 + 建議。"""
    reasons = []
    score = 0

    # 1. 五線譜 z-score
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

    # 2. RSI
    rsi_now = float(ind["rsi"].iloc[-1])
    if rsi_now < 30:
        score += 1; reasons.append({"score": +1, "name": "RSI(14)", "detail": f"超賣 ({rsi_now:.1f})"})
    elif rsi_now > 70:
        score -= 1; reasons.append({"score": -1, "name": "RSI(14)", "detail": f"超買 ({rsi_now:.1f})"})
    else:
        reasons.append({"score": 0, "name": "RSI(14)", "detail": f"中性 ({rsi_now:.1f})"})

    # 3. MACD（柱狀體方向）
    h_now = float(ind["macd_hist"].iloc[-1])
    h_prev = float(ind["macd_hist"].iloc[-2])
    if h_now > 0 and h_now > h_prev:
        score += 1; reasons.append({"score": +1, "name": "MACD", "detail": f"柱狀體擴張向上 ({h_now:+.2f})"})
    elif h_now < 0 and h_now < h_prev:
        score -= 1; reasons.append({"score": -1, "name": "MACD", "detail": f"柱狀體擴張向下 ({h_now:+.2f})"})
    elif h_now > 0:
        reasons.append({"score": 0, "name": "MACD", "detail": f"多頭但動能轉弱 ({h_now:+.2f})"})
    else:
        reasons.append({"score": 0, "name": "MACD", "detail": f"空頭但動能轉弱 ({h_now:+.2f})"})

    # 4. 均線排列
    p = float(close.iloc[-1])
    m20 = float(ind["ma20"].iloc[-1])
    m60 = float(ind["ma60"].iloc[-1])
    if p > m20 > m60:
        score += 1; reasons.append({"score": +1, "name": "均線", "detail": "多頭排列（價>MA20>MA60）"})
    elif p < m20 < m60:
        score -= 1; reasons.append({"score": -1, "name": "均線", "detail": "空頭排列（價<MA20<MA60）"})
    else:
        reasons.append({"score": 0, "name": "均線", "detail": "盤整（均線糾結）"})

    # 5. KD (含黃金/死亡交叉)
    k_now = float(ind["k"].iloc[-1]); k_prev = float(ind["k"].iloc[-2])
    d_now = float(ind["d"].iloc[-1]); d_prev = float(ind["d"].iloc[-2])
    golden = k_prev <= d_prev and k_now > d_now
    death = k_prev >= d_prev and k_now < d_now
    if golden and k_now < 30:
        score += 2; reasons.append({"score": +2, "name": "KD", "detail": f"低檔黃金交叉 (K={k_now:.1f})"})
    elif death and k_now > 70:
        score -= 2; reasons.append({"score": -2, "name": "KD", "detail": f"高檔死亡交叉 (K={k_now:.1f})"})
    elif golden:
        score += 1; reasons.append({"score": +1, "name": "KD", "detail": f"黃金交叉 (K={k_now:.1f})"})
    elif death:
        score -= 1; reasons.append({"score": -1, "name": "KD", "detail": f"死亡交叉 (K={k_now:.1f})"})
    elif k_now < 20:
        score += 1; reasons.append({"score": +1, "name": "KD", "detail": f"超賣 (K={k_now:.1f})"})
    elif k_now > 80:
        score -= 1; reasons.append({"score": -1, "name": "KD", "detail": f"超買 (K={k_now:.1f})"})
    else:
        reasons.append({"score": 0, "name": "KD", "detail": f"中性 (K={k_now:.1f}, D={d_now:.1f})"})

    # 6. 分析師共識（目標價 + 推薦）
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

    # 綜合建議（範圍 −9 ~ +9）
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

    return {"score": score, "label": label, "class": cls, "reasons": reasons}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stock")
def get_stock():
    raw = request.args.get("symbol", "AAPL")
    years = float(request.args.get("years", 3.5))
    symbol = normalize_symbol(raw)

    end = datetime.now()
    start = end - timedelta(days=int(years * 365.25))

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(start=start, end=end, auto_adjust=True)

        # 純數字找不到 .TW，再試上櫃 .TWO
        if hist.empty and symbol.endswith(".TW"):
            symbol_two = symbol.replace(".TW", ".TWO")
            ticker = yf.Ticker(symbol_two)
            hist = ticker.history(start=start, end=end, auto_adjust=True)
            if not hist.empty:
                symbol = symbol_two

        # 剔除 Close 為 NaN 的列（盤前/未開盤等）
        hist = hist.dropna(subset=["Close"])

        if hist.empty or len(hist) < 60:
            return jsonify({"error": f"找不到 {raw} 的足量資料（需至少 60 個交易日）"}), 404

        close = hist["Close"]
        dates = hist.index.strftime("%Y-%m-%d").tolist()
        prices = close.to_numpy()

        # 線性回歸（五線譜）
        x = np.arange(len(prices))
        slope, intercept = np.polyfit(x, prices, 1)
        trend = slope * x + intercept
        sigma = float(np.std(prices - trend, ddof=1))

        # 技術指標
        ind = compute_indicators(hist)

        try:
            info = ticker.info or {}
        except Exception:
            info = {}
        name = info.get("longName") or info.get("shortName") or symbol
        currency = info.get("currency", "")

        current = float(prices[-1])
        trend_now = float(trend[-1])
        z = (current - trend_now) / sigma if sigma > 0 else 0.0

        target = extract_target(info, current)
        signal = build_signal(close, ind, z, target)
        news = extract_news(ticker)

        return jsonify({
            "symbol": symbol,
            "name": name,
            "currency": currency,
            "dates": dates,
            "prices": prices.tolist(),

            # 五線譜
            "trend": trend.tolist(),
            "upper2": (trend + 2 * sigma).tolist(),
            "upper1": (trend + 1 * sigma).tolist(),
            "lower1": (trend - 1 * sigma).tolist(),
            "lower2": (trend - 2 * sigma).tolist(),

            # 均線
            "ma5": to_jsonable(ind["ma5"]),
            "ma20": to_jsonable(ind["ma20"]),
            "ma60": to_jsonable(ind["ma60"]),

            # 技術指標
            "rsi": to_jsonable(ind["rsi"]),
            "macd": to_jsonable(ind["macd"]),
            "macd_signal": to_jsonable(ind["signal"]),
            "macd_hist": to_jsonable(ind["macd_hist"]),
            "k": to_jsonable(ind["k"]),
            "d": to_jsonable(ind["d"]),

            "current_price": current,
            "trend_now": trend_now,
            "sigma": sigma,
            "z_score": z,
            "levels": {
                "樂觀價": trend_now + 2 * sigma,
                "相對高價": trend_now + 1 * sigma,
                "趨勢價": trend_now,
                "相對低價": trend_now - 1 * sigma,
                "悲觀價": trend_now - 2 * sigma,
            },
            "signal": signal,
            "target": target,
            "news": news,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5001)
