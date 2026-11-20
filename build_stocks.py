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

DAX_FALLBACKS: list[dict] = [
    {"ticker": "ADS.DE", "name": "adidas AG", "wkn": "A1EWWW", "isin": "DE000A1EWWW0"},
    {"ticker": "AIR.DE", "name": "Airbus SE", "wkn": "938914", "isin": "NL0000235190"},
    {"ticker": "ALV.DE", "name": "Allianz SE", "wkn": "840400", "isin": "DE0008404005"},
    {"ticker": "BAS.DE", "name": "BASF SE", "wkn": "BASF11", "isin": "DE000BASF111"},
    {"ticker": "BAYN.DE", "name": "Bayer AG", "wkn": "BAY001", "isin": "DE000BAY0017"},
    {"ticker": "BEI.DE", "name": "Beiersdorf AG", "wkn": "520000", "isin": "DE0005200000"},
    {"ticker": "BMW.DE", "name": "BMW AG", "wkn": "519000", "isin": "DE0005190003"},
    {"ticker": "BNR.DE", "name": "Brenntag SE", "wkn": "A1DAHH", "isin": "DE000A1DAHH0"},
    {"ticker": "CBK.DE", "name": "Commerzbank AG", "wkn": "CBK100", "isin": "DE000CBK1001"},
    {"ticker": "CON.DE", "name": "Continental AG", "wkn": "543900", "isin": "DE0005439004"},
    {"ticker": "DTG.DE", "name": "Daimler Truck Holding AG", "wkn": "DTR0CK", "isin": "DE000DTR0CK8"},
    {"ticker": "DBK.DE", "name": "Deutsche Bank AG", "wkn": "514000", "isin": "DE0005140008"},
    {"ticker": "DB1.DE", "name": "Deutsche Börse AG", "wkn": "581005", "isin": "DE0005810055"},
    {"ticker": "DTE.DE", "name": "Deutsche Telekom AG", "wkn": "555750", "isin": "DE0005557508"},
    {"ticker": "DHL.DE", "name": "DHL Group", "wkn": "555200", "isin": "DE0005552004"},
    {"ticker": "EOAN.DE", "name": "E.ON SE", "wkn": "ENAG99", "isin": "DE000ENAG999"},
    {"ticker": "FRE.DE", "name": "Fresenius SE & Co. KGaA", "wkn": "578560", "isin": "DE0005785604"},
    {"ticker": "FME.DE", "name": "Fresenius Medical Care AG", "wkn": "578580", "isin": "DE0005785802"},
    {"ticker": "G1A.DE", "name": "GEA Group AG", "wkn": "660200", "isin": "DE0006602006"},
    {"ticker": "HNR1.DE", "name": "Hannover Rück SE", "wkn": "840221", "isin": "DE0008402215"},
    {"ticker": "HEI.DE", "name": "Heidelberg Materials AG", "wkn": "604700", "isin": "DE0006047004"},
    {"ticker": "HEN3.DE", "name": "Henkel AG & Co. KGaA Vz.", "wkn": "604843", "isin": "DE0006048432"},
    {"ticker": "IFX.DE", "name": "Infineon Technologies AG", "wkn": "623100", "isin": "DE0006231004"},
    {"ticker": "MBG.DE", "name": "Mercedes-Benz Group AG", "wkn": "710000", "isin": "DE0007100000"},
    {"ticker": "MRK.DE", "name": "Merck KGaA", "wkn": "659990", "isin": "DE0006599905"},
    {"ticker": "MTX.DE", "name": "MTU Aero Engines AG", "wkn": "A0D9PT", "isin": "DE000A0D9PT0"},
    {"ticker": "MUV2.DE", "name": "Münchener Rückversicherungs-Gesellschaft AG", "wkn": "843002", "isin": "DE0008430026"},
    {"ticker": "PAH3.DE", "name": "Porsche Automobil Holding SE", "wkn": "PAH003", "isin": "DE000PAH0038"},
    {"ticker": "QIA.DE", "name": "QIAGEN N.V.", "wkn": "A400D5", "isin": "NL0015001WM6"},
    {"ticker": "RHM.DE", "name": "Rheinmetall AG", "wkn": "703000", "isin": "DE0007030009"},
    {"ticker": "RWE.DE", "name": "RWE AG", "wkn": "703712", "isin": "DE0007037129"},
    {"ticker": "SAP.DE", "name": "SAP SE", "wkn": "716460", "isin": "DE0007164600"},
    {"ticker": "G24.DE", "name": "Scout24 SE", "wkn": "A12DM8", "isin": "DE000A12DM80"},
    {"ticker": "SIE.DE", "name": "Siemens AG", "wkn": "723610", "isin": "DE0007236101"},
    {"ticker": "ENR.DE", "name": "Siemens Energy AG", "wkn": "ENER6Y", "isin": "DE000ENER6Y0"},
    {"ticker": "SHL.DE", "name": "Siemens Healthineers AG", "wkn": "SHL100", "isin": "DE000SHL1006"},
    {"ticker": "SY1.DE", "name": "Symrise AG", "wkn": "SYM999", "isin": "DE000SYM9999"},
    {"ticker": "VOW3.DE", "name": "Volkswagen AG Vz.", "wkn": "766403", "isin": "DE0007664039"},
    {"ticker": "VNA.DE", "name": "Vonovia SE", "wkn": "A1ML7J", "isin": "DE000A1ML7J1"},
    {"ticker": "ZAL.DE", "name": "Zalando SE", "wkn": "ZAL111", "isin": "DE000ZAL1111"},
]

DAX_ISIN_MAP = {item["isin"].upper(): item for item in DAX_FALLBACKS}


def make_entry(
    ticker: str,
    name: str,
    wkn: str,
    isin: str,
    asset_type: str,
) -> dict:
    return {
        "ticker": ticker,
        "name": name or ticker,
        "wkn": wkn,
        "isin": isin,
        "asset_type": asset_type,
        "price_usd": None,
        "quality_value_score": 5.0,
        "factor_score": 5.0,
        "dividend_cashflow_score": 5.0,
    }


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


def fetch_and_parse(
    repo_path: str,
    asset_type: str,
    max_items: int,
    priority_isins: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
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
    priority_rows: list[dict] = []

    for row in reader:
        ticker = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        name = str(row.get("name") or row.get("company_name") or "").strip()
        isin = str(row.get("isin") or "").strip().upper()
        wkn = str(row.get("wkn") or "").strip().upper()

        if not name and not ticker:
            continue

        entry = make_entry(ticker=ticker, name=name, wkn=wkn, isin=isin, asset_type=asset_type)

        if priority_isins and isin in priority_isins:
            fallback = DAX_ISIN_MAP.get(isin, {})
            if not entry["wkn"] and fallback.get("wkn"):
                entry["wkn"] = fallback["wkn"]
            priority_rows.append(entry)

        if len(rows) < max_items:
            rows.append(entry)

    return rows, priority_rows


def main():
    equity_paths, etf_paths = find_repo_files()
    print(f"Gefunden: {len(equity_paths)} Aktien-Dateien, {len(etf_paths)} ETF-Dateien.")

    items: list[dict] = []
    dax_from_db: list[dict] = []
    dax_isin_set = set(DAX_ISIN_MAP.keys())

    for path in equity_paths:
        remaining = max(0, 3500 - len(items))
        fetched, prio = fetch_and_parse(
            path,
            "equity",
            max_items=remaining,
            priority_isins=dax_isin_set,
        )
        items.extend(fetched)
        dax_from_db.extend(prio)

    etf_count = 0
    for path in etf_paths:
        if etf_count >= 1500:
            break
        remaining = 1500 - etf_count
        fetched, _ = fetch_and_parse(path, "etf", max_items=remaining)
        items.extend(fetched)
        etf_count += len(fetched)

    deduped: dict[str, dict] = {}
    for entry in items:
        key = entry["isin"] or entry["wkn"] or f"{entry['ticker']}:{entry['name']}"
        if key not in deduped:
            deduped[key] = entry

    # DAX-Treffer aus FinanceDatabase einpflegen / WKN ergänzen
    for entry in dax_from_db:
        key = entry["isin"] or entry["wkn"] or f"{entry['ticker']}:{entry['name']}"
        if key in deduped:
            if not deduped[key]["wkn"] and entry["wkn"]:
                deduped[key]["wkn"] = entry["wkn"]
        else:
            deduped[key] = entry

    # Fehlende DAX-40-Titel per Fallback ergänzen
    existing_isins = {v["isin"].upper() for v in deduped.values() if v.get("isin")}
    added_dax = 0
    for dax in DAX_FALLBACKS:
        if dax["isin"].upper() not in existing_isins:
            entry = make_entry(
                ticker=dax["ticker"],
                name=dax["name"],
                wkn=dax["wkn"],
                isin=dax["isin"],
                asset_type="equity",
            )
            deduped[entry["isin"]] = entry
            existing_isins.add(entry["isin"].upper())
            added_dax += 1

    result = sorted(deduped.values(), key=lambda x: x["name"].lower())
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(
        f"Fertig! {len(result)} Einträge in '{OUTPUT_FILE.name}' gespeichert "
        f"({added_dax} DAX-Werte per Fallback ergänzt)."
    )


if __name__ == "__main__":
    main()