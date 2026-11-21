from difflib import SequenceMatcher
import html
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Investment Score API")

DATA_FILE = Path(__file__).parent / "stocks_tr.json"
QUOTE_CACHE_TTL_SECONDS = 60
HISTORY_CACHE_TTL_SECONDS = 120
ANALYSIS_CACHE_TTL_SECONDS = 60 * 60 * 12  # 12 Stunden

_quote_cache: dict[str, tuple[float, dict]] = {}
_quote_cache_lock = threading.Lock()

_history_cache: dict[str, tuple[float, dict]] = {}
_history_cache_lock = threading.Lock()

_analysis_cache: dict[str, tuple[float, dict]] = {}
_analysis_cache_lock = threading.Lock()

PEER_GROUPS: dict[str, list[str]] = {
    "AAPL": ["Microsoft (MSFT)", "Alphabet (GOOGL)", "Samsung Electronics", "Sony (SONY)"],
    "MSFT": ["Alphabet (GOOGL)", "Apple (AAPL)", "Oracle (ORCL)", "SAP (SAP.DE)"],
    "GOOGL": ["Microsoft (MSFT)", "Meta Platforms (META)", "Amazon (AMZN)", "Apple (AAPL)"],
    "GOOG": ["Microsoft (MSFT)", "Meta Platforms (META)", "Amazon (AMZN)", "Apple (AAPL)"],
    "AMZN": ["Microsoft (MSFT)", "Alphabet (GOOGL)", "Walmart (WMT)", "Shopify (SHOP)"],
    "META": ["Alphabet (GOOGL)", "Snap (SNAP)", "Pinterest (PINS)", "TikTok / ByteDance"],
    "NVDA": ["AMD (AMD)", "Broadcom (AVGO)", "Intel (INTC)", "TSMC (TSM)"],
    "AMD": ["NVIDIA (NVDA)", "Intel (INTC)", "Broadcom (AVGO)", "Qualcomm (QCOM)"],
    "INTC": ["TSMC (TSM)", "Samsung Foundry", "NVIDIA (NVDA)", "AMD (AMD)"],
    "TSM": ["Samsung Foundry", "Intel (INTC)", "ASML (ASML)", "Broadcom (AVGO)"],
    "ASML": ["Applied Materials (AMAT)", "Lam Research (LRCX)", "KLA (KLAC)", "Tokyo Electron"],
    "TSLA": ["BYD", "Volkswagen (VOW3.DE)", "Rivian (RIVN)", "Hyundai"],
    "VOW3.DE": ["BMW (BMW.DE)", "Mercedes-Benz (MBG.DE)", "Tesla (TSLA)", "Stellantis"],
    "BMW.DE": ["Mercedes-Benz (MBG.DE)", "Volkswagen (VOW3.DE)", "Tesla (TSLA)"],
    "MBG.DE": ["BMW (BMW.DE)", "Volkswagen (VOW3.DE)", "Tesla (TSLA)"],
    "SAP": ["Microsoft (MSFT)", "Oracle (ORCL)", "Salesforce (CRM)", "ServiceNow (NOW)"],
    "SAP.DE": ["Microsoft (MSFT)", "Oracle (ORCL)", "Salesforce (CRM)", "ServiceNow (NOW)"],
    "SIE.DE": ["ABB", "Schneider Electric", "Honeywell (HON)", "General Electric (GE)"],
    "ALV.DE": ["Munich Re (MUV2.DE)", "AXA", "Zurich Insurance", "Generali"],
    "MUV2.DE": ["Hannover Rück (HNR1.DE)", "Allianz (ALV.DE)", "Swiss Re"],
    "DTE.DE": ["Vodafone (VOD)", "Orange", "Telefónica", "Deutsche Telekom"],
    "BAS.DE": ["Dow", "LyondellBasell", "Evonik (EVK.DE)", "Covestro"],
    "AIR.PA": ["Boeing (BA)", "Embraer", "GE Aerospace"],
    "NOVO-B.CO": ["Eli Lilly (LLY)", "Sanofi (SAN.PA)", "AstraZeneca (AZN)"],
    "LLY": ["Novo Nordisk", "Johnson & Johnson (JNJ)", "Merck & Co. (MRK)"],
    "JPM": ["Bank of America (BAC)", "Citigroup (C)", "Goldman Sachs (GS)", "Morgan Stanley (MS)"],
    "V": ["Mastercard (MA)", "American Express (AXP)", "PayPal (PYPL)"],
    "MA": ["Visa (V)", "American Express (AXP)", "PayPal (PYPL)"],
    "KO": ["PepsiCo (PEP)", "Monster Beverage (MNST)", "Keurig Dr Pepper (KDP)"],
    "PEP": ["Coca-Cola (KO)", "Mondelez (MDLZ)", "Nestlé"],
}

HISTORY_RANGE_CONFIG: dict[str, tuple[str, str, str]] = {
    "1d": ("1d", "5m", "1 Tag"),
    "1w": ("5d", "1h", "Letzte Woche"),
    "1m": ("1mo", "1d", "Letzter Monat"),
    "1y": ("1y", "1d", "52 Wochen"),
}


def load_stocks() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    with DATA_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


STOCKS: list[dict] = load_stocks()


class StockScoreRequest(BaseModel):
    ticker: str
    company_name: str
    wkn: str = ""
    isin: str = ""
    price_usd: float | None = None
    quality_value_score: float
    factor_score: float
    dividend_cashflow_score: float


class DeepAnalysisRequest(BaseModel):
    ticker: str
    company_name: str = ""
    wkn: str = ""
    isin: str = ""
    force_refresh: bool = False


def clamp_score(value: float) -> float:
    return max(0.0, min(10.0, value))


def safe_float(value: object, default: float = 5.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def safe_optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        val = float(value)
        return val if val == val and abs(val) != float("inf") else None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        val = float(match.group(0))
        return val if val == val and abs(val) != float("inf") else None
    except ValueError:
        return None


def safe_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return max(0, int(float(str(value).strip())))
    except (ValueError, TypeError):
        return default


def safe_str_list(value: object, limit: int) -> list[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def normalize_text(value: str) -> str:
    value = value.lower().strip()
    value = (
        value.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def similarity_score(query_norm: str, stock: dict) -> float:
    fields = [
        normalize_text(stock.get("wkn", "")),
        normalize_text(stock.get("isin", "")),
        normalize_text(stock.get("ticker", "")),
        normalize_text(stock.get("name", "")),
        normalize_text(stock.get("market", "")),
    ]
    best = 0.0
    for field in fields:
        if not field:
            continue
        if query_norm == field:
            best = max(best, 1.0)
        elif query_norm in field:
            best = max(best, 0.85)
        else:
            ratio = SequenceMatcher(None, query_norm, field).ratio()
            for token in field.split():
                ratio = max(ratio, SequenceMatcher(None, query_norm, token).ratio())
            best = max(best, ratio * 0.8)
    return best


def _fetch_yahoo_chart_payload(ticker: str, range_param: str, interval_param: str) -> dict:
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        raise ValueError("Leerer Ticker")

    encoded = urllib.parse.quote(clean_ticker)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
        f"?interval={urllib.parse.quote(interval_param)}&range={urllib.parse.quote(range_param)}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ValueError(chart["error"].get("description") or "Ticker nicht gefunden")

    results = chart.get("result") or []
    if not results:
        raise ValueError(f"Keine Kursdaten für '{clean_ticker}' gefunden")

    return results[0]


def _fetch_yahoo_quote(ticker: str) -> dict:
    clean_ticker = ticker.strip().upper()
    chart_res = _fetch_yahoo_chart_payload(clean_ticker, range_param="1d", interval_param="1m")

    meta = chart_res.get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    currency = meta.get("currency") or "USD"
    exchange = meta.get("exchangeName") or meta.get("fullExchangeName") or ""
    market_time = meta.get("regularMarketTime")
    day_high = meta.get("regularMarketDayHigh")
    day_low = meta.get("regularMarketDayLow")
    week_52_high = meta.get("fiftyTwoWeekHigh")
    week_52_low = meta.get("fiftyTwoWeekLow")
    volume = meta.get("regularMarketVolume")

    if price is None:
        raise ValueError(f"Kein aktueller Kurs für '{clean_ticker}' verfügbar")

    price_val = float(price)
    prev_val = float(prev_close) if prev_close is not None else None
    change = round(price_val - prev_val, 4) if prev_val is not None else None
    change_percent = (
        round(((price_val - prev_val) / prev_val) * 100.0, 2)
        if prev_val not in (None, 0.0)
        else None
    )

    day_high_val = float(day_high) if day_high is not None else None
    day_low_val = float(day_low) if day_low is not None else None
    day_range_pct = (
        round(((day_high_val - day_low_val) / price_val) * 100.0, 2)
        if day_high_val is not None and day_low_val is not None and price_val > 0.0
        else None
    )

    w52_high_val = float(week_52_high) if week_52_high is not None else None
    w52_low_val = float(week_52_low) if week_52_low is not None else None
    distance_52w_high_pct = (
        round(((price_val - w52_high_val) / w52_high_val) * 100.0, 2)
        if w52_high_val not in (None, 0.0)
        else None
    )
    distance_52w_low_pct = (
        round(((price_val - w52_low_val) / w52_low_val) * 100.0, 2)
        if w52_low_val not in (None, 0.0)
        else None
    )
    range_52w_pos_pct = (
        round(
            max(0.0, min(100.0, ((price_val - w52_low_val) / (w52_high_val - w52_low_val)) * 100.0)),
            1,
        )
        if w52_high_val is not None
        and w52_low_val is not None
        and (w52_high_val - w52_low_val) > 0.0
        else None
    )

    return {
        "ticker": clean_ticker,
        "price": round(price_val, 4),
        "previous_close": round(prev_val, 4) if prev_val is not None else None,
        "change": change,
        "change_percent": change_percent,
        "day_high": round(day_high_val, 4) if day_high_val is not None else None,
        "day_low": round(day_low_val, 4) if day_low_val is not None else None,
        "day_range_percent": day_range_pct,
        "fifty_two_week_high": round(w52_high_val, 4) if w52_high_val is not None else None,
        "fifty_two_week_low": round(w52_low_val, 4) if w52_low_val is not None else None,
        "distance_from_52w_high_percent": distance_52w_high_pct,
        "distance_from_52w_low_percent": distance_52w_low_pct,
        "range_52w_position_percent": range_52w_pos_pct,
        "volume": int(volume) if volume is not None else None,
        "currency": currency,
        "exchange": exchange,
        "market_timestamp": market_time,
        "delayed_note": "Kursdaten können je nach Börse/Quelle leicht verzögert sein",
        "cached": False,
    }


def _fetch_yahoo_history(ticker: str, requested_range: str) -> dict:
    clean_ticker = ticker.strip().upper()
    norm_range = requested_range.strip().lower()
    if norm_range not in HISTORY_RANGE_CONFIG:
        raise ValueError("Ungültiger Zeitraum. Erlaubt sind: 1d, 1w, 1m, 1y")

    yf_range, yf_interval, range_label = HISTORY_RANGE_CONFIG[norm_range]
    chart_res = _fetch_yahoo_chart_payload(clean_ticker, range_param=yf_range, interval_param=yf_interval)

    meta = chart_res.get("meta") or {}
    currency = str(meta.get("currency") or "USD")
    timestamps = chart_res.get("timestamp") or []
    quote_block = ((chart_res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    closes = quote_block.get("close") or []

    points: list[dict] = []
    for ts, close_val in zip(timestamps, closes):
        price_opt = safe_optional_float(close_val)
        if ts is None or price_opt is None:
            continue
        points.append({
            "timestamp": int(ts),
            "price": round(price_opt, 4),
        })

    if not points:
        raise ValueError(f"Keine Zeitreihendaten für '{clean_ticker}' ({norm_range}) verfügbar")

    prices = [p["price"] for p in points]
    start_price = prices[0]
    end_price = prices[-1]
    period_high = max(prices)
    period_low = min(prices)
    change = round(end_price - start_price, 4)
    change_percent = round(((end_price - start_price) / start_price) * 100.0, 2) if start_price != 0.0 else None

    return {
        "ticker": clean_ticker,
        "range": norm_range,
        "range_label": range_label,
        "interval": yf_interval,
        "currency": currency,
        "start_price": start_price,
        "end_price": end_price,
        "change": change,
        "change_percent": change_percent,
        "period_high": period_high,
        "period_low": period_low,
        "points": points,
        "cached": False,
    }


def _pick_metric(metrics: dict, keys: list[str]) -> float | None:
    for key in keys:
        if key in metrics:
            val = safe_optional_float(metrics.get(key))
            if val is not None:
                return val
    return None


def _rate_single_metric(key: str, val: float | None) -> dict:
    if val is None:
        return {
            "value": None,
            "score": None,
            "signal": "none",
            "label": "Keine Daten",
            "rule": "Keine Kennzahl im Datenfeed vorhanden",
        }

    rounded_val = round(val, 2)

    if key == "pe_ttm":
        rule = "KGV <= 12: 9,0 | <= 18: 7,5 | <= 25: 6,0 | <= 35: 4,5 | > 35: 3,0 | <= 0: 2,5"
        if val <= 0.0:
            return {"value": rounded_val, "score": 2.5, "signal": "red", "label": "Negativ / kein Gewinn", "rule": rule}
        if val <= 12.0:
            return {"value": rounded_val, "score": 9.0, "signal": "green", "label": "Günstig bewertet", "rule": rule}
        if val <= 18.0:
            return {"value": rounded_val, "score": 7.5, "signal": "green", "label": "Attraktiv", "rule": rule}
        if val <= 25.0:
            return {"value": rounded_val, "score": 6.0, "signal": "yellow", "label": "Moderat", "rule": rule}
        if val <= 35.0:
            return {"value": rounded_val, "score": 4.5, "signal": "yellow", "label": "Erhöht", "rule": rule}
        return {"value": rounded_val, "score": 3.0, "signal": "red", "label": "Sportlich bewertet", "rule": rule}

    if key == "pb":
        rule = "KBV <= 1,5: 8,5 | <= 3,5: 7,0 | <= 7,0: 5,5 | <= 12,0: 4,0 | > 12,0: 3,0 | <= 0: 3,0"
        if val <= 0.0:
            return {"value": rounded_val, "score": 3.0, "signal": "red", "label": "Negatives Eigenkapital", "rule": rule}
        if val <= 1.5:
            return {"value": rounded_val, "score": 8.5, "signal": "green", "label": "Günstig", "rule": rule}
        if val <= 3.5:
            return {"value": rounded_val, "score": 7.0, "signal": "green", "label": "Solide", "rule": rule}
        if val <= 7.0:
            return {"value": rounded_val, "score": 5.5, "signal": "yellow", "label": "Moderat", "rule": rule}
        if val <= 12.0:
            return {"value": rounded_val, "score": 4.0, "signal": "yellow", "label": "Erhöht", "rule": rule}
        return {"value": rounded_val, "score": 3.0, "signal": "red", "label": "Hoch", "rule": rule}

    if key == "ps_ttm":
        rule = "KUV <= 1,5: 8,5 | <= 4,0: 7,0 | <= 8,0: 5,5 | > 8,0: 3,5 | <= 0: 3,5"
        if val <= 0.0:
            return {"value": rounded_val, "score": 3.5, "signal": "red", "label": "Kein Umsatz", "rule": rule}
        if val <= 1.5:
            return {"value": rounded_val, "score": 8.5, "signal": "green", "label": "Günstig", "rule": rule}
        if val <= 4.0:
            return {"value": rounded_val, "score": 7.0, "signal": "green", "label": "Solide", "rule": rule}
        if val <= 8.0:
            return {"value": rounded_val, "score": 5.5, "signal": "yellow", "label": "Moderat", "rule": rule}
        return {"value": rounded_val, "score": 3.5, "signal": "red", "label": "Hoch", "rule": rule}

    if key == "roe_ttm":
        rule = "ROE >= 25%: 9,0 | >= 15%: 7,5 | >= 8%: 6,0 | >= 0%: 4,5 | < 0%: 2,5"
        if val >= 25.0:
            return {"value": rounded_val, "score": 9.0, "signal": "green", "label": "Sehr stark", "rule": rule}
        if val >= 15.0:
            return {"value": rounded_val, "score": 7.5, "signal": "green", "label": "Gut", "rule": rule}
        if val >= 8.0:
            return {"value": rounded_val, "score": 6.0, "signal": "yellow", "label": "Durchschnittlich", "rule": rule}
        if val >= 0.0:
            return {"value": rounded_val, "score": 4.5, "signal": "yellow", "label": "Schwach", "rule": rule}
        return {"value": rounded_val, "score": 2.5, "signal": "red", "label": "Negativ", "rule": rule}

    if key == "net_margin_ttm":
        rule = "Nettomarge >= 20%: 8,5 | >= 12%: 7,5 | >= 5%: 5,5 | >= 0%: 4,0 | < 0%: 2,5"
        if val >= 20.0:
            return {"value": rounded_val, "score": 8.5, "signal": "green", "label": "Sehr hoch", "rule": rule}
        if val >= 12.0:
            return {"value": rounded_val, "score": 7.5, "signal": "green", "label": "Solide", "rule": rule}
        if val >= 5.0:
            return {"value": rounded_val, "score": 5.5, "signal": "yellow", "label": "Moderat", "rule": rule}
        if val >= 0.0:
            return {"value": rounded_val, "score": 4.0, "signal": "yellow", "label": "Knapp", "rule": rule}
        return {"value": rounded_val, "score": 2.5, "signal": "red", "label": "Defizitär", "rule": rule}

    if key == "debt_to_equity":
        rule = "Verschuldung/EK <= 0,4: 8,5 | <= 1,0: 7,0 | <= 2,0: 5,0 | > 2,0: 3,0"
        if val < 0.0:
            return {"value": rounded_val, "score": 3.0, "signal": "red", "label": "Kritisch", "rule": rule}
        norm_de = val / 100.0 if val > 10.0 else val
        if norm_de <= 0.4:
            return {"value": rounded_val, "score": 8.5, "signal": "green", "label": "Sehr niedrig", "rule": rule}
        if norm_de <= 1.0:
            return {"value": rounded_val, "score": 7.0, "signal": "green", "label": "Kontrolliert", "rule": rule}
        if norm_de <= 2.0:
            return {"value": rounded_val, "score": 5.0, "signal": "yellow", "label": "Erhöht", "rule": rule}
        return {"value": rounded_val, "score": 3.0, "signal": "red", "label": "Hoch verschuldet", "rule": rule}

    if key == "current_ratio":
        rule = "Current Ratio >= 1,5: 8,0 | >= 1,0: 6,5 | < 1,0: 4,0 | <= 0: 3,0"
        if val <= 0.0:
            return {"value": rounded_val, "score": 3.0, "signal": "red", "label": "Kritisch", "rule": rule}
        if val >= 1.5:
            return {"value": rounded_val, "score": 8.0, "signal": "green", "label": "Komfortabel", "rule": rule}
        if val >= 1.0:
            return {"value": rounded_val, "score": 6.5, "signal": "yellow", "label": "Ausreichend", "rule": rule}
        return {"value": rounded_val, "score": 4.0, "signal": "red", "label": "Knapp", "rule": rule}

    if key == "dividend_yield":
        rule = "Dividende 1,5–6,0%: 7,5 | < 1,5%: 6,0 | <= 8,0%: 5,5 | > 8,0%: 4,0 | 0%: 5,0"
        if val <= 0.0:
            return {"value": rounded_val, "score": 5.0, "signal": "yellow", "label": "Keine Dividende", "rule": rule}
        if 1.5 <= val <= 6.0:
            return {"value": rounded_val, "score": 7.5, "signal": "green", "label": "Attraktiv", "rule": rule}
        if val < 1.5:
            return {"value": rounded_val, "score": 6.0, "signal": "yellow", "label": "Niedrig", "rule": rule}
        if val <= 8.0:
            return {"value": rounded_val, "score": 5.5, "signal": "yellow", "label": "Hoch", "rule": rule}
        return {"value": rounded_val, "score": 4.0, "signal": "red", "label": "Auffällig hoch", "rule": rule}

    return {"value": rounded_val, "score": 5.0, "signal": "yellow", "label": "Neutral", "rule": "Standardbewertung 5,0"}


def _evaluate_fundamentals(metrics_map: dict[str, float | None]) -> tuple[float | None, str, dict[str, dict], dict[str, int]]:
    ratings: dict[str, dict] = {}
    scores: list[float] = []
    counts = {"green": 0, "yellow": 0, "red": 0}

    for key in [
        "pe_ttm",
        "ps_ttm",
        "pb",
        "roe_ttm",
        "net_margin_ttm",
        "debt_to_equity",
        "current_ratio",
        "dividend_yield",
    ]:
        item = _rate_single_metric(key, metrics_map.get(key))
        ratings[key] = item
        sig = item["signal"]
        if sig in counts:
            counts[sig] += 1
        if item["score"] is not None:
            scores.append(float(item["score"]))

    if not scores:
        return None, "Keine ausreichenden Fundamentalkennzahlen verfügbar", ratings, counts

    score = round(clamp_score(sum(scores) / len(scores)), 2)
    if score >= 7.5:
        label = "Solide Fundamentaldaten"
    elif score >= 5.0:
        label = "Gemischte Fundamentaldaten"
    else:
        label = "Schwache / anspruchsvolle Fundamentaldaten"
    return score, label, ratings, counts


def _fetch_fundamentals(ticker: str) -> dict | None:
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return None

    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        return None

    candidates: list[str] = [clean_ticker]
    root_ticker = clean_ticker.split(".")[0]
    if root_ticker and root_ticker not in candidates:
        candidates.append(root_ticker)

    for sym in candidates:
        encoded_sym = urllib.parse.quote(sym)
        encoded_key = urllib.parse.quote(api_key)
        url = f"https://finnhub.io/api/v1/stock/metric?symbol={encoded_sym}&metric=all&token={encoded_key}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue

        if not isinstance(data, dict):
            continue

        raw_metric = data.get("metric")
        if not isinstance(raw_metric, dict) or not raw_metric:
            continue

        extracted: dict[str, float | None] = {
            "pe_ttm": _pick_metric(raw_metric, ["peBasicExclExtraTTM", "peTTM"]),
            "ps_ttm": _pick_metric(raw_metric, ["psTTM"]),
            "pb": _pick_metric(raw_metric, ["pbQuarterly", "pbAnnual"]),
            "roe_ttm": _pick_metric(raw_metric, ["roeTTM", "roeRfy"]),
            "net_margin_ttm": _pick_metric(raw_metric, ["netProfitMarginTTM", "netProfitMarginAnnual"]),
            "debt_to_equity": _pick_metric(raw_metric, ["totalDebt/totalEquityQuarterly", "totalDebt/totalEquityAnnual"]),
            "current_ratio": _pick_metric(raw_metric, ["currentRatioQuarterly", "currentRatioAnnual"]),
            "dividend_yield": _pick_metric(raw_metric, ["dividendYieldIndicatedAnnual", "currentDividendYieldTTM"]),
        }

        available_count = sum(1 for v in extracted.values() if v is not None)
        if available_count == 0:
            continue

        score, assessment, ratings, counts = _evaluate_fundamentals(extracted)
        rounded_metrics = {
            k: (round(v, 2) if v is not None else None)
            for k, v in extracted.items()
        }

        return {
            "source": "finnhub",
            "symbol_queried": sym,
            "fundamental_score": score,
            "score_method": "Durchschnitt aller verfügbaren Kennzahlen-Scores (0,0–10,0)",
            "assessment": assessment,
            "metrics_count": available_count,
            "signal_counts": counts,
            "metrics": rounded_metrics,
            "metric_ratings": ratings,
        }

    return None


def _fetch_analyst_ratings(ticker: str) -> dict | None:
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return None

    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        return None

    candidates: list[str] = [clean_ticker]
    root_ticker = clean_ticker.split(".")[0]
    if root_ticker and root_ticker not in candidates:
        candidates.append(root_ticker)

    for sym in candidates:
        encoded_sym = urllib.parse.quote(sym)
        encoded_key = urllib.parse.quote(api_key)
        url = f"https://finnhub.io/api/v1/stock/recommendation?symbol={encoded_sym}&token={encoded_key}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue

        if not isinstance(data, list) or not data:
            continue

        latest = data[0] if isinstance(data[0], dict) else None
        if not latest:
            continue

        strong_buy = safe_int(latest.get("strongBuy"))
        buy = safe_int(latest.get("buy"))
        hold = safe_int(latest.get("hold"))
        sell = safe_int(latest.get("sell"))
        strong_sell = safe_int(latest.get("strongSell"))

        total_buy = strong_buy + buy
        total_sell = sell + strong_sell
        total_count = total_buy + hold + total_sell

        if total_count == 0:
            consensus = "Keine Daten"
        elif strong_buy > total_buy and strong_buy >= hold and strong_buy >= total_sell:
            consensus = "Strong Buy"
        elif total_buy >= hold and total_buy >= total_sell:
            consensus = "Buy"
        elif total_sell > total_buy and total_sell >= hold:
            consensus = "Sell"
        else:
            consensus = "Hold"

        return {
            "source": "finnhub",
            "symbol_queried": sym,
            "period": str(latest.get("period") or ""),
            "strong_buy": strong_buy,
            "buy": buy,
            "hold": hold,
            "sell": sell,
            "strong_sell": strong_sell,
            "buy_total": total_buy,
            "sell_total": total_sell,
            "total_analysts": total_count,
            "consensus": consensus,
        }

    return None


def _fetch_recent_news(ticker: str, limit: int = 5) -> list[dict]:
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        return []

    encoded = urllib.parse.quote(clean_ticker)
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={encoded}&region=US&lang=en-US"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/xml, text/xml",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            xml_data = resp.read()
        root_elem = ET.fromstring(xml_data)
    except Exception:
        return []

    headlines: list[dict] = []
    seen_titles: set[str] = set()
    for item in root_elem.findall(".//channel/item"):
        title_elem = item.find("title")
        if title_elem is None or not title_elem.text:
            continue
        title = html.unescape(title_elem.text).strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        link_elem = item.find("link")
        link_url = link_elem.text.strip() if link_elem is not None and link_elem.text else None
        headlines.append({
            "title": title,
            "url": link_url,
            "source": "Yahoo Finance",
        })
        if len(headlines) >= limit:
            break

    return headlines


def _find_competitor_candidates(target_ticker: str, target_name: str, limit: int = 5) -> list[str]:
    target_t = target_ticker.strip().upper()
    target_root = target_t.split(".")[0] if target_t else ""
    target_name_norm = normalize_text(target_name)
    candidates: list[str] = []

    curated = PEER_GROUPS.get(target_t) or PEER_GROUPS.get(target_root) or []
    for peer in curated:
        peer_norm = normalize_text(peer)
        if target_root and target_root == peer_norm.upper():
            continue
        if target_name_norm and len(target_name_norm) >= 4 and target_name_norm in peer_norm:
            continue
        if peer not in candidates:
            candidates.append(peer)
        if len(candidates) >= limit:
            return candidates

    target_prefix = target_name_norm.split(" ")[0] if target_name_norm else ""
    for s in STOCKS:
        t = str(s.get("ticker", "")).strip().upper()
        n = str(s.get("name", "")).strip()
        if not t or t == target_t:
            continue
        if target_prefix and len(target_prefix) >= 3 and target_prefix in normalize_text(n):
            entry = f"{n} ({t})"
            if entry not in candidates:
                candidates.append(entry)
        if len(candidates) >= limit:
            break

    return candidates


def _build_llm_prompt(
    ticker: str,
    company_name: str,
    wkn: str,
    isin: str,
    quote_info: dict | None,
    competitors: list[str],
    headlines: list[dict],
    ratings_info: dict | None = None,
    fundamentals_info: dict | None = None,
) -> str:
    if quote_info:
        currency = str(quote_info.get("currency") or "USD").upper()
        currency_hint = (
            "Notiert in EUR (kein direktes Währungsrisiko für Euro-Anleger)."
            if currency == "EUR"
            else f"Notiert in {currency} (Wechselkursrisiko gegenüber EUR für Euro-Anleger berücksichtigen)."
        )
        quote_str = json.dumps(quote_info, ensure_ascii=False)
    else:
        currency_hint = "Währung unbekannt, da keine Live-Kursdaten abgerufen werden konnten."
        quote_str = (
            "Keine aktuellen Kursdaten verfügbar. WICHTIG: Erfinde oder schätze keinen aktuellen Aktienkurs, "
            "sondern weise sachlich darauf hin, dass keine Live-Kursdaten vorlagen."
        )

    peers_str = ", ".join(competitors) if competitors else "Keine direkten Kandidaten aus lokalem Katalog abgeleitet"
    headline_titles = [str(h.get("title") or "").strip() for h in headlines if str(h.get("title") or "").strip()]
    news_str = (
        " | ".join(headline_titles)
        if headline_titles
        else "Keine aktuellen Schlagzeilen abgerufen; stütze dich nur auf bekannte Fakten und Kursdaten."
    )
    ratings_str = (
        json.dumps(ratings_info, ensure_ascii=False)
        if ratings_info and ratings_info.get("total_analysts", 0) > 0
        else "Keine Analysten-Zähler verfügbar"
    )
    fundamentals_str = (
        json.dumps(fundamentals_info, ensure_ascii=False)
        if fundamentals_info and fundamentals_info.get("metrics_count", 0) > 0
        else "Keine Fundamentalkennzahlen verfügbar"
    )

    return f"""
Du bist ein strukturierter Finanz- und Marktanalyst. Analysiere den Wert sachlich, knapp und ohne Anlageberatung.
Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Objekt (ohne Markdown, ohne Zusatztext).

Wert:
- Ticker: {ticker}
- Name: {company_name or "Unbekannt"}
- WKN: {wkn or "-"}
- ISIN: {isin or "-"}
- Währungskontext: {currency_hint}
- Kurs- & Spannenkennzahlen (inkl. 52-Wochen-Spanne, Tagesrange & Volumen falls vorhanden): {quote_str}
- Fundamentalkennzahlen, Ampel-Vorabbewertung & Fundamental-Score: {fundamentals_str}
- Analysten-Zähler (Buy/Hold/Sell): {ratings_str}
- Wettbewerber-Hinweise: {peers_str}
- Aktuelle Schlagzeilen: {news_str}

Bewerte jede Dimension von 0.0 bis 10.0 (Dezimalzahl mit Punkt, z. B. 7.5) mit jeweils 1-2 kurzen Sätzen auf Deutsch:
1. history_and_price: Kursverlauf, Einordnung in die 52-Wochen-Spanne (Abstand zu Hoch/Tief sowie Position in %), Tagesschwankung/Volumen, Trend und aktueller Kurs.
2. economics_and_macro: Makroumfeld, Zinsen, Konjunktur, Margen, Fundamentalkennzahlen (z. B. KGV/ROE/Verschuldung) und ggf. Währungseffekte.
3. politics_and_regulation: Politik, Regularien, Zölle und Gesetze.
4. competition_and_moat: Wettbewerb, Burggraben und relevante Konkurrenten.

Wichtige Regeln:
- Der overall_score soll dem Durchschnitt deiner vier Dimensions-Scores entsprechen.
- Erfinde keine konkreten aktuellen Kurse, Kennzahlen oder News, die nicht in den bereitgestellten Daten stehen.

JSON-Schema:
{{
  "overall_score": 0.0,
  "status": "Starkes Profil | Solide / Beobachten | Schwaches Profil",
  "summary": "Kurzfassung in 1-2 Sätzen",
  "dimensions": {{
    "history_and_price": {{ "score": 0.0, "analysis": "..." }},
    "economics_and_macro": {{ "score": 0.0, "analysis": "..." }},
    "politics_and_regulation": {{ "score": 0.0, "analysis": "..." }},
    "competition_and_moat": {{ "score": 0.0, "analysis": "..." }}
  }},
  "competitors": ["Konkurrent 1", "Konkurrent 2"],
  "key_catalysts": ["Treiber 1", "Treiber 2"],
  "key_risks": ["Risiko 1", "Risiko 2"],
  "disclaimer": "KI-gestützte Analyse auf Basis bereitgestellter Marktdaten; keine Anlageberatung."
}}
""".strip()


def _call_gemini_json(prompt: str) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY ist auf dem Server nicht gesetzt")

    primary_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash"
    fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"
    lite_fallback_model = os.getenv("GEMINI_LITE_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"

    models_to_try: list[str] = []
    for candidate_model in [primary_model, fallback_model, lite_fallback_model]:
        if candidate_model and candidate_model not in models_to_try:
            models_to_try.append(candidate_model)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    raw_body: dict | None = None
    model_errors: list[str] = []
    backoff_seconds = [2.0, 4.0]

    for model_name in models_to_try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{urllib.parse.quote(model_name)}:generateContent?key={urllib.parse.quote(api_key)}"
        )

        for attempt, wait_sec in enumerate(backoff_seconds):
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
                    raw_body = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as err:
                detail_body = err.read().decode("utf-8", errors="replace")
                short_detail = detail_body.replace("\n", " ")[:180]
                model_errors.append(f"{model_name}: HTTP {err.code} ({short_detail})")
                if err.code in (400, 404):
                    break
                if err.code in (429, 503) and attempt < len(backoff_seconds) - 1:
                    time.sleep(wait_sec)
                    continue
                break
            except Exception as exc:
                model_errors.append(f"{model_name}: {exc}")
                if attempt < len(backoff_seconds) - 1:
                    time.sleep(wait_sec)
                    continue
                break

        if raw_body is not None:
            break

    if not raw_body:
        summary_err = " | ".join(model_errors[-3:]) if model_errors else "Unbekannter LLM-Fehler"
        raise RuntimeError(f"Alle Gemini-Modelle fehlgeschlagen: {summary_err}")

    candidates = raw_body.get("candidates") or []
    if not candidates:
        raise RuntimeError("Keine Antwort-Kandidaten vom LLM erhalten")

    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    text_out = "".join(str(p.get("text", "")) for p in parts).strip()
    if not text_out:
        raise RuntimeError("Leere Textantwort vom LLM erhalten")

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out, flags=re.IGNORECASE | re.DOTALL).strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM hat kein gültiges JSON-Objekt zurückgegeben")
    return parsed


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Investment Score Backend läuft",
        "loaded_stocks": len(STOCKS),
        "llm_configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        "llm_model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash",
        "ratings_configured": bool(os.getenv("FINNHUB_API_KEY", "").strip()),
    }


@app.get("/search-stocks")
def search_stocks(
    q: str = Query(..., min_length=1, description="Suche nach WKN, ISIN, Name, Markt oder Ticker"),
    limit: int = Query(15, ge=1, le=30),
):
    q_clean = q.strip()
    q_norm = normalize_text(q_clean)
    q_upper = q_clean.upper()

    exact_or_prefix: list[tuple[float, dict]] = []
    suggestions: list[tuple[float, dict]] = []

    for stock in STOCKS:
        wkn = str(stock.get("wkn", "")).upper()
        isin = str(stock.get("isin", "")).upper()
        ticker = str(stock.get("ticker", "")).upper()
        name = str(stock.get("name", ""))
        market = str(stock.get("market", ""))
        name_norm = normalize_text(name)
        market_norm = normalize_text(market)

        if q_upper == wkn or q_upper == isin or q_upper == ticker:
            exact_or_prefix.append((1.0, stock))
        elif (
            wkn.startswith(q_upper)
            or isin.startswith(q_upper)
            or ticker.startswith(q_upper)
            or (q_norm and name_norm.startswith(q_norm))
        ):
            exact_or_prefix.append((0.9, stock))
        elif q_norm and (
            q_norm in normalize_text(wkn)
            or q_norm in normalize_text(isin)
            or q_norm in normalize_text(ticker)
            or q_norm in name_norm
            or q_norm in market_norm
        ):
            exact_or_prefix.append((0.78, stock))
        else:
            sim = similarity_score(q_norm, stock)
            if sim >= 0.52:
                suggestions.append((sim, stock))

    exact_or_prefix.sort(key=lambda item: item[0], reverse=True)
    suggestions.sort(key=lambda item: item[0], reverse=True)

    seen_keys = set()
    matches = []
    for _, s in exact_or_prefix:
        key = s.get("isin") or s.get("wkn") or f"{s.get('ticker')}:{s.get('name')}"
        if key not in seen_keys:
            seen_keys.add(key)
            matches.append(s)

    alt_suggestions = []
    for _, s in suggestions:
        key = s.get("isin") or s.get("wkn") or f"{s.get('ticker')}:{s.get('name')}"
        if key not in seen_keys:
            seen_keys.add(key)
            alt_suggestions.append(s)

    combined = (matches + alt_suggestions)[:limit]

    return {
        "query": q_clean,
        "exact_or_matching_count": len(matches),
        "results": combined,
        "suggestions": alt_suggestions[:limit] if not matches else alt_suggestions[: max(0, limit - len(matches))],
    }


@app.get("/quote")
def get_quote(ticker: str = Query(..., min_length=1, description="Ticker-Symbol, z. B. AAPL oder SAP.DE")):
    clean_ticker = ticker.strip().upper()
    now = time.time()

    with _quote_cache_lock:
        cached = _quote_cache.get(clean_ticker)
        if cached and (now - cached[0]) < QUOTE_CACHE_TTL_SECONDS:
            data = dict(cached[1])
            data["cached"] = True
            return data

    try:
        quote_data = _fetch_yahoo_quote(clean_ticker)
    except Exception as exc:
        with _quote_cache_lock:
            stale = _quote_cache.get(clean_ticker)
            if stale:
                fallback = dict(stale[1])
                fallback["cached"] = True
                fallback["stale"] = True
                return fallback
        raise HTTPException(status_code=502, detail=f"Kursabruf fehlgeschlagen: {exc}") from exc

    with _quote_cache_lock:
        _quote_cache[clean_ticker] = (now, quote_data)

    return quote_data


@app.get("/history")
def get_history(
    ticker: str = Query(..., min_length=1, description="Ticker-Symbol, z. B. AAPL oder SAP.DE"),
    range_period: str = Query("1y", alias="range", description="Zeitraum: 1d, 1w, 1m oder 1y (52 Wochen)"),
):
    clean_ticker = ticker.strip().upper()
    norm_range = range_period.strip().lower()
    if norm_range not in HISTORY_RANGE_CONFIG:
        raise HTTPException(status_code=400, detail="Ungültiger Zeitraum. Erlaubt sind: 1d, 1w, 1m, 1y")

    cache_key = f"{clean_ticker}:{norm_range}"
    now = time.time()

    with _history_cache_lock:
        cached = _history_cache.get(cache_key)
        if cached and (now - cached[0]) < HISTORY_CACHE_TTL_SECONDS:
            data = dict(cached[1])
            data["cached"] = True
            return data

    try:
        history_data = _fetch_yahoo_history(clean_ticker, norm_range)
    except Exception as exc:
        with _history_cache_lock:
            stale = _history_cache.get(cache_key)
            if stale:
                fallback = dict(stale[1])
                fallback["cached"] = True
                fallback["stale"] = True
                return fallback
        raise HTTPException(status_code=502, detail=f"Historienabruf fehlgeschlagen: {exc}") from exc

    with _history_cache_lock:
        _history_cache[cache_key] = (now, history_data)

    return history_data


@app.post("/analyze-stock")
def analyze_stock(req_data: DeepAnalysisRequest):
    clean_ticker = req_data.ticker.strip().upper()
    if not clean_ticker:
        raise HTTPException(status_code=400, detail="Ticker darf nicht leer sein")

    cache_key = f"{clean_ticker}|{req_data.isin.strip().upper()}|{req_data.wkn.strip().upper()}"
    now = time.time()

    if not req_data.force_refresh:
        with _analysis_cache_lock:
            cached = _analysis_cache.get(cache_key)
            if cached and (now - cached[0]) < ANALYSIS_CACHE_TTL_SECONDS:
                out = dict(cached[1])
                out["cached"] = True
                return out

    resolved_name = req_data.company_name.strip()
    resolved_wkn = req_data.wkn.strip().upper()
    resolved_isin = req_data.isin.strip().upper()

    if not resolved_name or not resolved_wkn or not resolved_isin:
        for s in STOCKS:
            if str(s.get("ticker", "")).strip().upper() == clean_ticker:
                resolved_name = resolved_name or str(s.get("name", ""))
                resolved_wkn = resolved_wkn or str(s.get("wkn", ""))
                resolved_isin = resolved_isin or str(s.get("isin", ""))
                break

    quote_info = None
    try:
        quote_info = _fetch_yahoo_quote(clean_ticker)
    except Exception:
        quote_info = None

    competitors = _find_competitor_candidates(clean_ticker, resolved_name)
    headlines = _fetch_recent_news(clean_ticker, limit=5)
    ratings_info = _fetch_analyst_ratings(clean_ticker)
    fundamentals_info = _fetch_fundamentals(clean_ticker)
    prompt = _build_llm_prompt(
        ticker=clean_ticker,
        company_name=resolved_name,
        wkn=resolved_wkn,
        isin=resolved_isin,
        quote_info=quote_info,
        competitors=competitors,
        headlines=headlines,
        ratings_info=ratings_info,
        fundamentals_info=fundamentals_info,
    )

    try:
        llm_result = _call_gemini_json(prompt)
        llm_available = True
        llm_note = ""
    except Exception as exc:
        llm_result = {}
        llm_available = False
        llm_note = f"KI-Analyse vorübergehend nicht verfügbar: {exc}"

    raw_dims = llm_result.get("dimensions")
    dims = raw_dims if isinstance(raw_dims, dict) else {}

    def norm_dim(key: str) -> dict:
        raw_d = dims.get(key)
        d = raw_d if isinstance(raw_d, dict) else {}
        return {
            "score": round(clamp_score(safe_float(d.get("score"), 5.0)), 1) if llm_available else 0.0,
            "analysis": str(d.get("analysis") or "").strip(),
        }

    norm_dims = {
        "history_and_price": norm_dim("history_and_price"),
        "economics_and_macro": norm_dim("economics_and_macro"),
        "politics_and_regulation": norm_dim("politics_and_regulation"),
        "competition_and_moat": norm_dim("competition_and_moat"),
    }

    if llm_available:
        overall = round(sum(v["score"] for v in norm_dims.values()) / 4.0, 2)
        if overall >= 7.5:
            status = "Starkes Profil (Grün)"
        elif overall >= 5.0:
            status = "Solide / Beobachten (Gelb)"
        else:
            status = "Schwaches Profil (Rot)"
        summary_text = str(llm_result.get("summary") or "").strip()
    else:
        overall = 0.0
        status = "KI vorübergehend nicht verfügbar"
        summary_text = llm_note

    final_payload = {
        "ticker": clean_ticker,
        "company_name": resolved_name or clean_ticker,
        "wkn": resolved_wkn,
        "isin": resolved_isin,
        "quote": quote_info,
        "quote_available": quote_info is not None,
        "fundamentals": fundamentals_info,
        "analyst_ratings": ratings_info,
        "recent_headlines": headlines,
        "overall_score": overall,
        "status": status,
        "llm_available": llm_available,
        "llm_note": llm_note,
        "summary": summary_text,
        "dimensions": norm_dims,
        "competitors": safe_str_list(llm_result.get("competitors"), 6) if llm_available else competitors,
        "key_catalysts": safe_str_list(llm_result.get("key_catalysts"), 5),
        "key_risks": safe_str_list(llm_result.get("key_risks"), 5),
        "cached": False,
        "disclaimer": str(
            llm_result.get("disclaimer")
            or "KI-gestützte Analyse auf Basis bereitgestellter Marktdaten; keine Anlageberatung."
        ).strip(),
    }

    if llm_available:
        with _analysis_cache_lock:
            _analysis_cache[cache_key] = (now, final_payload)

    return final_payload


@app.get("/stocks/{identifier}")
def get_stock(identifier: str):
    ident = identifier.strip().upper()
    for stock in STOCKS:
        if (
            str(stock.get("ticker", "")).upper() == ident
            or str(stock.get("wkn", "")).upper() == ident
            or str(stock.get("isin", "")).upper() == ident
        ):
            return stock
    raise HTTPException(status_code=404, detail="Aktie nicht gefunden")


@app.post("/calculate-score")
def calculate_score(stock: StockScoreRequest):
    qv = clamp_score(stock.quality_value_score)
    fac = clamp_score(stock.factor_score)
    div = clamp_score(stock.dividend_cashflow_score)

    total = round((qv * 0.40) + (fac * 0.30) + (div * 0.30), 2)
    total = clamp_score(total)

    if total >= 7.5:
        status = "Starkes Profil (Grün)"
    elif total >= 5.0:
        status = "Solide / Beobachten (Gelb)"
    else:
        status = "Schwaches Profil (Rot)"

    return {
        "ticker": stock.ticker,
        "company_name": stock.company_name,
        "wkn": stock.wkn,
        "isin": stock.isin,
        "price_usd": stock.price_usd,
        "quality_value_score": qv,
        "factor_score": fac,
        "dividend_cashflow_score": div,
        "total_score": total,
        "status": status,
    }