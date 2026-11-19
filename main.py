from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Investment Score API")

DATA_FILE = Path(__file__).parent / "stocks_tr.json"


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


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Investment Score Backend läuft",
        "loaded_stocks": len(STOCKS),
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