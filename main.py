from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Investment Score API")

DATA_FILE = Path(__file__).parent / "stocks_tr.json"
QUOTE_CACHE_TTL_SECONDS = 60
ANALYSIS_CACHE_TTL_SECONDS = 60 * 60 * 12  # 12 Stunden

_quote_cache: dict[str, tuple[float, dict]] = {}
_quote_cache_lock = threading.Lock()

_analysis_cache: dict[str, tuple[float, dict]] = {}
_analysis_cache_lock = threading.Lock()


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


def _fetch_yahoo_quote(ticker: str) -> dict:
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        raise ValueError("Leerer Ticker")

    encoded = urllib.parse.quote(clean_ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1m&range=1d"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ValueError(chart["error"].get("description") or "Ticker nicht gefunden")

    results = chart.get("result") or []
    if not results:
        raise ValueError(f"Keine Kursdaten für '{clean_ticker}' gefunden")

    meta = results[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    currency = meta.get("currency") or "USD"
    market_time = meta.get("regularMarketTime")

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

    return {
        "ticker": clean_ticker,
        "price": round(price_val, 4),
        "previous_close": round(prev_val, 4) if prev_val is not None else None,
        "change": change,
        "change_percent": change_percent,
        "currency": currency,
        "market_timestamp": market_time,
        "delayed_note": "Kursdaten können je nach Börse/Quelle leicht verzögert sein",
        "cached": False,
    }


def _find_competitor_candidates(target_ticker: str, target_name: str, limit: int = 5) -> list[str]:
    target_t = target_ticker.strip().upper()
    target_prefix = normalize_text(target_name).split(" ")[0] if target_name else ""
    candidates: list[str] = []

    for s in STOCKS:
        t = str(s.get("ticker", "")).strip().upper()
        n = str(s.get("name", "")).strip()
        if not t or t == target_t:
            continue
        if target_prefix and len(target_prefix) >= 3 and target_prefix in normalize_text(n):
            candidates.append(f"{n} ({t})")
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
) -> str:
    quote_str = json.dumps(quote_info, ensure_ascii=False) if quote_info else "Keine Live-Kursdaten verfügbar"
    peers_str = ", ".join(competitors) if competitors else "Keine direkten Kandidaten aus lokalem Katalog abgeleitet"

    return f"""
Du bist ein strukturierter Finanz- und Marktanalyst. Analysiere den folgenden Wert sachlich, nüchtern und ohne Anlageberatung.
Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Objekt (ohne Markdown-Codeblöcke, ohne zusätzlichen Text davor oder danach).

Zielwert:
- Ticker: {ticker}
- Name: {company_name or "Unbekannt"}
- WKN: {wkn or "-"}
- ISIN: {isin or "-"}
- Aktuelle Kursdaten: {quote_str}
- Mögliche Wettbewerber-Kandidaten: {peers_str}

Bewerte jede Dimension von 0.0 bis 10.0 und liefere kompakte, präzise Begründungen auf Deutsch:
1. history_and_price: Historische Kursentwicklung, Trends, Volatilität und Einordnung aktueller Kursdaten.
2. economics_and_macro: Makroökonomisches Umfeld (Zinsen, Konjunktur, Währung, Margen/Geschäftsmodell).
3. politics_and_regulation: Politische Ereignisse, Regularien, Zölle, Gesetze und deren wahrscheinliche Kurswirkung.
4. competition_and_moat: Wettbewerbsposition, Burggraben (Moat) und Vergleich mit Konkurrenten.

Verwende exakt dieses JSON-Schema:
{{
  "overall_score": 0.0,
  "status": "Starkes Profil | Solide / Beobachten | Schwaches Profil",
  "summary": "Kurzfassung in 2-3 Sätzen",
  "dimensions": {{
    "history_and_price": {{ "score": 0.0, "analysis": "..." }},
    "economics_and_macro": {{ "score": 0.0, "analysis": "..." }},
    "politics_and_regulation": {{ "score": 0.0, "analysis": "..." }},
    "competition_and_moat": {{ "score": 0.0, "analysis": "..." }}
  }},
  "competitors": ["Konkurrent 1", "Konkurrent 2"],
  "key_catalysts": ["Chance/Treiber 1", "Chance/Treiber 2"],
  "key_risks": ["Risiko 1", "Risiko 2"],
  "disclaimer": "KI-gestützte Analyse auf Basis bereitgestellter Marktdaten; keine Anlageberatung."
}}
""".strip()


def _call_gemini_json(prompt: str) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY ist auf dem Server nicht gesetzt")

    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(api_key)}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw_body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail_body = err.read().decode("utf-8", errors="replace")
        if err.code == 429:
            raise RuntimeError("Gemini Free-Tier Rate-Limit erreicht (HTTP 429). Bitte kurz warten.") from err
        raise RuntimeError(f"Gemini HTTP {err.code}: {detail_body[:300]}") from err

    candidates = raw_body.get("candidates") or []
    if not candidates:
        raise RuntimeError("Keine Antwort-Kandidaten vom LLM erhalten")

    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    text_out = "".join(str(p.get("text", "")) for p in parts).strip()
    if not text_out:
        raise RuntimeError("Leere Textantwort vom LLM erhalten")

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out, flags=re.IGNORECASE | re.DOTALL).strip()
    return json.loads(cleaned)


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Investment Score Backend läuft",
        "loaded_stocks": len(STOCKS),
        "llm_configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        "llm_model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash",
    }


@app.get("/search-stocks")
def search_stocks(
    q: str = Query(..., min_length=1, description="Suche nach WKN, ISIN, Name oder Ticker"),
    limit: int = Query(8, ge=1, le=25),
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
        name_norm = normalize_text(name)

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
        key = s.get("isin") or s.get("wkn") or s.get("ticker")
        if key not in seen_keys:
            seen_keys.add(key)
            matches.append(s)

    alt_suggestions = []
    for _, s in suggestions:
        key = s.get("isin") or s.get("wkn") or s.get("ticker")
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
    prompt = _build_llm_prompt(
        ticker=clean_ticker,
        company_name=resolved_name,
        wkn=resolved_wkn,
        isin=resolved_isin,
        quote_info=quote_info,
        competitors=competitors,
    )

    try:
        llm_result = _call_gemini_json(prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM-Analyse fehlgeschlagen: {exc}") from exc

    dims = llm_result.get("dimensions") or {}

    def norm_dim(key: str) -> dict:
        d = dims.get(key) or {}
        return {
            "score": round(clamp_score(float(d.get("score", 5.0))), 1),
            "analysis": str(d.get("analysis", "")).strip(),
        }

    norm_dims = {
        "history_and_price": norm_dim("history_and_price"),
        "economics_and_macro": norm_dim("economics_and_macro"),
        "politics_and_regulation": norm_dim("politics_and_regulation"),
        "competition_and_moat": norm_dim("competition_and_moat"),
    }

    overall_raw = llm_result.get("overall_score")
    if overall_raw is not None:
        overall = round(clamp_score(float(overall_raw)), 2)
    else:
        overall = round(sum(v["score"] for v in norm_dims.values()) / 4.0, 2)

    status = str(llm_result.get("status") or "").strip()
    if not status:
        if overall >= 7.5:
            status = "Starkes Profil (Grün)"
        elif overall >= 5.0:
            status = "Solide / Beobachten (Gelb)"
        else:
            status = "Schwaches Profil (Rot)"

    final_payload = {
        "ticker": clean_ticker,
        "company_name": resolved_name or clean_ticker,
        "wkn": resolved_wkn,
        "isin": resolved_isin,
        "quote": quote_info,
        "overall_score": overall,
        "status": status,
        "summary": str(llm_result.get("summary", "")).strip(),
        "dimensions": norm_dims,
        "competitors": [str(c) for c in (llm_result.get("competitors") or []) if str(c).strip()][:6],
        "key_catalysts": [str(c) for c in (llm_result.get("key_catalysts") or []) if str(c).strip()][:5],
        "key_risks": [str(r) for r in (llm_result.get("key_risks") or []) if str(r).strip()][:5],
        "cached": False,
        "disclaimer": str(
            llm_result.get("disclaimer")
            or "KI-gestützte Analyse auf Basis bereitgestellter Marktdaten; keine Anlageberatung."
        ).strip(),
    }

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