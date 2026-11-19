from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Investment Score API")


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


@app.get("/")
def root():
    return {"status": "ok", "message": "Investment Score Backend läuft"}


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