import bz2
import csv
import io
import json
from pathlib import Path
import urllib.request

BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "stocks_tr.json"

TREE_API_URL = "https://api.github.com/repos/JerBouma/FinanceDatabase/git/trees/main?recursive=1"
RAW_BASE_URL = "https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/"


def find_repo_files() -> tuple[list[str], list[str]]:
    print("Suche Dateien in JerBouma/FinanceDatabase ...")
    req = urllib.request.Request(
        TREE_API_URL,
        headers={"User-Agent": "investment-backend-script"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        tree_data = json.loads(resp.read().decode("utf-8"))

    equity_paths: list[str] = []
    etf_paths: list[str] = []

    for item in tree_data.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        lower = path.lower()
        if not (lower.endswith(".csv.bz2") or lower.endswith(".csv")):
            continue

        if "equities" in lower:
            equity_paths.append(path)
        elif "etfs" in lower:
            etf_paths.append(path)

    return sorted(equity_paths), sorted(etf_paths)


def fetch_and_parse(repo_path: str, asset_type: str, max_items: int) -> list[dict]:
    url = RAW_BASE_URL + repo_path
    print(f"Lade {repo_path} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "investment-backend-script"})
    with urllib.request.urlopen(req, timeout=60) as response:
        raw_bytes = response.read()

    if repo_path.lower().endswith(".bz2"):
        text = bz2.decompress(raw_bytes).decode("utf-8-sig")
    else:
        text = raw_bytes.decode("utf-8-sig")

    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict] = []

    for row in reader:
        if len(rows) >= max_items:
            break
        ticker = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        name = str(row.get("name") or row.get("company_name") or "").strip()
        isin = str(row.get("isin") or "").strip().upper()
        wkn = str(row.get("wkn") or "").strip().upper()

        if not name and not ticker:
            continue

        rows.append({
            "ticker": ticker,
            "name": name or ticker,
            "wkn": wkn,
            "isin": isin,
            "asset_type": asset_type,
            "price_usd": None,
            "quality_value_score": 5.0,
            "factor_score": 5.0,
            "dividend_cashflow_score": 5.0,
        })
    return rows


def main():
    equity_paths, etf_paths = find_repo_files()
    print(f"Gefunden: {len(equity_paths)} Aktien-Dateien, {len(etf_paths)} ETF-Dateien.")

    items: list[dict] = []

    for path in equity_paths:
        if len(items) >= 3500:
            break
        remaining = 3500 - len(items)
        items.extend(fetch_and_parse(path, "equity", max_items=remaining))

    etf_count = 0
    for path in etf_paths:
        if etf_count >= 1500:
            break
        remaining = 1500 - etf_count
        fetched = fetch_and_parse(path, "etf", max_items=remaining)
        items.extend(fetched)
        etf_count += len(fetched)

    deduped: dict[str, dict] = {}
    for entry in items:
        key = entry["isin"] or entry["wkn"] or f"{entry['ticker']}:{entry['name']}"
        if key not in deduped:
            deduped[key] = entry

    result = sorted(deduped.values(), key=lambda x: x["name"].lower())
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Fertig! {len(result)} Einträge in '{OUTPUT_FILE.name}' gespeichert.")


if __name__ == "__main__":
    main()