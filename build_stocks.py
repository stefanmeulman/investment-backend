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

# Kern-Märkte: DAX, MDAX, TecDAX, Dow Jones, S&P 500 Schwergewichte, Europa/STOXX & Nikkei
CORE_EQUITIES: list[dict] = [
    # --- DAX ---
    {"ticker": "ADS.DE", "name": "adidas AG", "wkn": "A1EWWW", "isin": "DE000A1EWWW0", "market": "DAX"},
    {"ticker": "AIR.DE", "name": "Airbus SE", "wkn": "938914", "isin": "NL0000235190", "market": "DAX"},
    {"ticker": "ALV.DE", "name": "Allianz SE", "wkn": "840400", "isin": "DE0008404005", "market": "DAX"},
    {"ticker": "BAS.DE", "name": "BASF SE", "wkn": "BASF11", "isin": "DE000BASF111", "market": "DAX"},
    {"ticker": "BAYN.DE", "name": "Bayer AG", "wkn": "BAY001", "isin": "DE000BAY0017", "market": "DAX"},
    {"ticker": "BEI.DE", "name": "Beiersdorf AG", "wkn": "520000", "isin": "DE0005200000", "market": "DAX"},
    {"ticker": "BMW.DE", "name": "BMW AG", "wkn": "519000", "isin": "DE0005190003", "market": "DAX"},
    {"ticker": "BNR.DE", "name": "Brenntag SE", "wkn": "A1DAHH", "isin": "DE000A1DAHH0", "market": "DAX"},
    {"ticker": "CBK.DE", "name": "Commerzbank AG", "wkn": "CBK100", "isin": "DE000CBK1001", "market": "DAX"},
    {"ticker": "CON.DE", "name": "Continental AG", "wkn": "543900", "isin": "DE0005439004", "market": "DAX"},
    {"ticker": "DTG.DE", "name": "Daimler Truck Holding AG", "wkn": "DTR0CK", "isin": "DE000DTR0CK8", "market": "DAX"},
    {"ticker": "DBK.DE", "name": "Deutsche Bank AG", "wkn": "514000", "isin": "DE0005140008", "market": "DAX"},
    {"ticker": "DB1.DE", "name": "Deutsche Börse AG", "wkn": "581005", "isin": "DE0005810055", "market": "DAX"},
    {"ticker": "DTE.DE", "name": "Deutsche Telekom AG", "wkn": "555750", "isin": "DE0005557508", "market": "DAX"},
    {"ticker": "DHL.DE", "name": "DHL Group", "wkn": "555200", "isin": "DE0005552004", "market": "DAX"},
    {"ticker": "EOAN.DE", "name": "E.ON SE", "wkn": "ENAG99", "isin": "DE000ENAG999", "market": "DAX"},
    {"ticker": "FRE.DE", "name": "Fresenius SE & Co. KGaA", "wkn": "578560", "isin": "DE0005785604", "market": "DAX"},
    {"ticker": "FME.DE", "name": "Fresenius Medical Care AG", "wkn": "578580", "isin": "DE0005785802", "market": "DAX"},
    {"ticker": "G1A.DE", "name": "GEA Group AG", "wkn": "660200", "isin": "DE0006602006", "market": "DAX"},
    {"ticker": "HNR1.DE", "name": "Hannover Rück SE", "wkn": "840221", "isin": "DE0008402215", "market": "DAX"},
    {"ticker": "HEI.DE", "name": "Heidelberg Materials AG", "wkn": "604700", "isin": "DE0006047004", "market": "DAX"},
    {"ticker": "HEN3.DE", "name": "Henkel AG & Co. KGaA Vz.", "wkn": "604843", "isin": "DE0006048432", "market": "DAX"},
    {"ticker": "IFX.DE", "name": "Infineon Technologies AG", "wkn": "623100", "isin": "DE0006231004", "market": "DAX"},
    {"ticker": "MBG.DE", "name": "Mercedes-Benz Group AG", "wkn": "710000", "isin": "DE0007100000", "market": "DAX"},
    {"ticker": "MRK.DE", "name": "Merck KGaA", "wkn": "659990", "isin": "DE0006599905", "market": "DAX"},
    {"ticker": "MTX.DE", "name": "MTU Aero Engines AG", "wkn": "A0D9PT", "isin": "DE000A0D9PT0", "market": "DAX"},
    {"ticker": "MUV2.DE", "name": "Münchener Rückversicherungs-Gesellschaft AG", "wkn": "843002", "isin": "DE0008430026", "market": "DAX"},
    {"ticker": "PAH3.DE", "name": "Porsche Automobil Holding SE", "wkn": "PAH003", "isin": "DE000PAH0038", "market": "DAX"},
    {"ticker": "QIA.DE", "name": "QIAGEN N.V.", "wkn": "A400D5", "isin": "NL0015001WM6", "market": "DAX"},
    {"ticker": "RHM.DE", "name": "Rheinmetall AG", "wkn": "703000", "isin": "DE0007030009", "market": "DAX"},
    {"ticker": "RWE.DE", "name": "RWE AG", "wkn": "703712", "isin": "DE0007037129", "market": "DAX"},
    {"ticker": "SAP.DE", "name": "SAP SE", "wkn": "716460", "isin": "DE0007164600", "market": "DAX"},
    {"ticker": "G24.DE", "name": "Scout24 SE", "wkn": "A12DM8", "isin": "DE000A12DM80", "market": "DAX"},
    {"ticker": "SIE.DE", "name": "Siemens AG", "wkn": "723610", "isin": "DE0007236101", "market": "DAX"},
    {"ticker": "ENR.DE", "name": "Siemens Energy AG", "wkn": "ENER6Y", "isin": "DE000ENER6Y0", "market": "DAX"},
    {"ticker": "SHL.DE", "name": "Siemens Healthineers AG", "wkn": "SHL100", "isin": "DE000SHL1006", "market": "DAX"},
    {"ticker": "SY1.DE", "name": "Symrise AG", "wkn": "SYM999", "isin": "DE000SYM9999", "market": "DAX"},
    {"ticker": "VOW3.DE", "name": "Volkswagen AG Vz.", "wkn": "766403", "isin": "DE0007664039", "market": "DAX"},
    {"ticker": "VNA.DE", "name": "Vonovia SE", "wkn": "A1ML7J", "isin": "DE000A1ML7J1", "market": "DAX"},
    {"ticker": "ZAL.DE", "name": "Zalando SE", "wkn": "ZAL111", "isin": "DE000ZAL1111", "market": "DAX"},

    # --- MDAX & TecDAX Auswahl ---
    {"ticker": "AFX.DE", "name": "Carl Zeiss Meditec AG", "wkn": "531370", "isin": "DE0005313704", "market": "MDAX / TecDAX"},
    {"ticker": "AIXA.DE", "name": "AIXTRON SE", "wkn": "A0WMPJ", "isin": "DE000A0WMPJ6", "market": "MDAX / TecDAX"},
    {"ticker": "BC8.DE", "name": "Bechtle AG", "wkn": "515870", "isin": "DE0005158703", "market": "MDAX / TecDAX"},
    {"ticker": "EVK.DE", "name": "Evonik Industries AG", "wkn": "EVNK01", "isin": "DE000EVNK013", "market": "MDAX"},
    {"ticker": "FPE3.DE", "name": "Fuchs SE Vz.", "wkn": "579043", "isin": "DE0005790430", "market": "MDAX"},
    {"ticker": "GYC.DE", "name": "Grand City Properties S.A.", "wkn": "A1MLV7", "isin": "LU0775917882", "market": "MDAX"},
    {"ticker": "HAG.DE", "name": "HENSOLDT AG", "wkn": "HAG000", "isin": "DE000HAG0005", "market": "MDAX"},
    {"ticker": "HOT.DE", "name": "HOCHTIEF AG", "wkn": "607000", "isin": "DE0006070006", "market": "MDAX"},
    {"ticker": "JUN3.DE", "name": "Jungheinrich AG Vz.", "wkn": "621993", "isin": "DE0006219934", "market": "MDAX"},
    {"ticker": "KRN.DE", "name": "KRONES AG", "wkn": "633500", "isin": "DE0006335003", "market": "MDAX"},
    {"ticker": "LEG.DE", "name": "LEG Immobilien SE", "wkn": "LEG111", "isin": "DE000LEG1110", "market": "MDAX"},
    {"ticker": "LXS.DE", "name": "LANXESS AG", "wkn": "547040", "isin": "DE0005470405", "market": "MDAX"},
    {"ticker": "NDA.DE", "name": "Aurubis AG", "wkn": "676650", "isin": "DE0006766504", "market": "MDAX"},
    {"ticker": "NEM.DE", "name": "Nemetschek SE", "wkn": "645290", "isin": "DE0006452907", "market": "MDAX / TecDAX"},
    {"ticker": "P911.DE", "name": "Dr. Ing. h.c. F. Porsche AG", "wkn": "PAG911", "isin": "DE000PAG9113", "market": "MDAX"},
    {"ticker": "SDF.DE", "name": "K+S AG", "wkn": "KSAG88", "isin": "DE000KSAG888", "market": "MDAX"},
    {"ticker": "TEG.DE", "name": "TAG Immobilien AG", "wkn": "830350", "isin": "DE0008303504", "market": "MDAX"},
    {"ticker": "TKA.DE", "name": "thyssenkrupp AG", "wkn": "750000", "isin": "DE0007500001", "market": "MDAX"},
    {"ticker": "TMV.DE", "name": "TeamViewer SE", "wkn": "A2YN90", "isin": "DE000A2YN900", "market": "MDAX / TecDAX"},
    {"ticker": "UTDI.DE", "name": "United Internet AG", "wkn": "508903", "isin": "DE0005089031", "market": "MDAX / TecDAX"},

    # --- Dow Jones & S&P 500 Kernwerte ---
    {"ticker": "AAPL", "name": "Apple Inc.", "wkn": "865985", "isin": "US0378331005", "market": "Dow Jones / S&P 500"},
    {"ticker": "MSFT", "name": "Microsoft Corporation", "wkn": "870747", "isin": "US5949181045", "market": "Dow Jones / S&P 500"},
    {"ticker": "NVDA", "name": "NVIDIA Corporation", "wkn": "918422", "isin": "US67066G1040", "market": "Dow Jones / S&P 500"},
    {"ticker": "AMZN", "name": "Amazon.com Inc.", "wkn": "906866", "isin": "US0231351067", "market": "S&P 500"},
    {"ticker": "GOOGL", "name": "Alphabet Inc. (Class A)", "wkn": "A14Y6F", "isin": "US02079K3059", "market": "S&P 500"},
    {"ticker": "META", "name": "Meta Platforms Inc.", "wkn": "A1JWVX", "isin": "US30303M1027", "market": "S&P 500"},
    {"ticker": "TSLA", "name": "Tesla Inc.", "wkn": "A1CX3T", "isin": "US88160R1014", "market": "S&P 500"},
    {"ticker": "BRK-B", "name": "Berkshire Hathaway Inc. (B)", "wkn": "A0YJQ2", "isin": "US0846707026", "market": "S&P 500"},
    {"ticker": "JPM", "name": "JPMorgan Chase & Co.", "wkn": "850628", "isin": "US46625H1005", "market": "Dow Jones / S&P 500"},
    {"ticker": "V", "name": "Visa Inc.", "wkn": "A0NC7B", "isin": "US92826C8394", "market": "Dow Jones / S&P 500"},
    {"ticker": "MA", "name": "Mastercard Incorporated", "wkn": "A0F602", "isin": "US57636Q1040", "market": "S&P 500"},
    {"ticker": "UNH", "name": "UnitedHealth Group Incorporated", "wkn": "869561", "isin": "US91324P1021", "market": "Dow Jones / S&P 500"},
    {"ticker": "JNJ", "name": "Johnson & Johnson", "wkn": "853260", "isin": "US4781601046", "market": "Dow Jones / S&P 500"},
    {"ticker": "PG", "name": "Procter & Gamble Company", "wkn": "852062", "isin": "US7427181091", "market": "Dow Jones / S&P 500"},
    {"ticker": "KO", "name": "The Coca-Cola Company", "wkn": "850663", "isin": "US1912161007", "market": "Dow Jones / S&P 500"},
    {"ticker": "PEP", "name": "PepsiCo Inc.", "wkn": "851995", "isin": "US7134481081", "market": "S&P 500"},
    {"ticker": "HD", "name": "The Home Depot Inc.", "wkn": "866953", "isin": "US4370761029", "market": "Dow Jones / S&P 500"},
    {"ticker": "MCD", "name": "McDonald's Corporation", "wkn": "856958", "isin": "US5801351017", "market": "Dow Jones / S&P 500"},
    {"ticker": "DIS", "name": "The Walt Disney Company", "wkn": "855686", "isin": "US2546871060", "market": "Dow Jones / S&P 500"},
    {"ticker": "GS", "name": "The Goldman Sachs Group Inc.", "wkn": "920332", "isin": "US38141G1040", "market": "Dow Jones / S&P 500"},
    {"ticker": "IBM", "name": "International Business Machines", "wkn": "851399", "isin": "US4592001014", "market": "Dow Jones / S&P 500"},
    {"ticker": "CAT", "name": "Caterpillar Inc.", "wkn": "850521", "isin": "US1491231015", "market": "Dow Jones / S&P 500"},
    {"ticker": "HON", "name": "Honeywell International Inc.", "wkn": "870153", "isin": "US4385161066", "market": "Dow Jones / S&P 500"},
    {"ticker": "AMGN", "name": "Amgen Inc.", "wkn": "867900", "isin": "US0311621009", "market": "Dow Jones / S&P 500"},
    {"ticker": "AXP", "name": "American Express Company", "wkn": "850226", "isin": "US0258161092", "market": "Dow Jones / S&P 500"},
    {"ticker": "CRM", "name": "Salesforce Inc.", "wkn": "A0B87V", "isin": "US79466L3024", "market": "Dow Jones / S&P 500"},
    {"ticker": "LLY", "name": "Eli Lilly and Company", "wkn": "858560", "isin": "US5324571083", "market": "S&P 500"},
    {"ticker": "AVGO", "name": "Broadcom Inc.", "wkn": "A2JG9Z", "isin": "US11135F1012", "market": "S&P 500"},
    {"ticker": "COST", "name": "Costco Wholesale Corporation", "wkn": "888351", "isin": "US22160K1051", "market": "S&P 500"},
    {"ticker": "NFLX", "name": "Netflix Inc.", "wkn": "552484", "isin": "US64110L1061", "market": "S&P 500"},
    {"ticker": "AMD", "name": "Advanced Micro Devices Inc.", "wkn": "863186", "isin": "US0079031078", "market": "S&P 500"},
    {"ticker": "ADBE", "name": "Adobe Inc.", "wkn": "871981", "isin": "US00724F1012", "market": "S&P 500"},
    {"ticker": "ORCL", "name": "Oracle Corporation", "wkn": "871460", "isin": "US68389X1054", "market": "S&P 500"},

    # --- Wichtige Europäische Aktien / STOXX ---
    {"ticker": "ASML.AS", "name": "ASML Holding N.V.", "wkn": "A1J83C", "isin": "NL0010273215", "market": "STOXX / Europa"},
    {"ticker": "MC.PA", "name": "LVMH Moët Hennessy Louis Vuitton", "wkn": "853292", "isin": "FR0000121014", "market": "STOXX / Europa"},
    {"ticker": "OR.PA", "name": "L'Oréal S.A.", "wkn": "853888", "isin": "FR0000120321", "market": "STOXX / Europa"},
    {"ticker": "TTE.PA", "name": "TotalEnergies SE", "wkn": "850727", "isin": "FR0000120271", "market": "STOXX / Europa"},
    {"ticker": "SAN.PA", "name": "Sanofi S.A.", "wkn": "920657", "isin": "FR0000120578", "market": "STOXX / Europa"},
    {"ticker": "SU.PA", "name": "Schneider Electric SE", "wkn": "860180", "isin": "FR0000121972", "market": "STOXX / Europa"},
    {"ticker": "RMS.PA", "name": "Hermès International S.A.", "wkn": "886670", "isin": "FR0000052292", "market": "STOXX / Europa"},
    {"ticker": "NESN.SW", "name": "Nestlé S.A.", "wkn": "A0Q4DC", "isin": "CH0038863350", "market": "STOXX / Europa"},
    {"ticker": "NOVN.SW", "name": "Novartis AG", "wkn": "904278", "isin": "CH0012005267", "market": "STOXX / Europa"},
    {"ticker": "ROG.SW", "name": "Roche Holding AG", "wkn": "855167", "isin": "CH0012032048", "market": "STOXX / Europa"},
    {"ticker": "NOVO-B.CO", "name": "Novo Nordisk A/S", "wkn": "A1XA8R", "isin": "DK0062498333", "market": "STOXX / Europa"},
    {"ticker": "AZN.L", "name": "AstraZeneca plc", "wkn": "886455", "isin": "GB0009895292", "market": "STOXX / Europa"},
    {"ticker": "SHEL.L", "name": "Shell plc", "wkn": "A3C99G", "isin": "GB00BP6MXD84", "market": "STOXX / Europa"},
    {"ticker": "ULVR.L", "name": "Unilever plc", "wkn": "A2PHG5", "isin": "GB00B10RZP78", "market": "STOXX / Europa"},
    {"ticker": "HSBA.L", "name": "HSBC Holdings plc", "wkn": "923893", "isin": "GB0005405286", "market": "STOXX / Europa"},
    {"ticker": "IBE.MC", "name": "Iberdrola S.A.", "wkn": "A0M46B", "isin": "ES0144580Y14", "market": "STOXX / Europa"},
    {"ticker": "ITX.MC", "name": "Industria de Diseño Textil (Inditex)", "wkn": "A11873", "isin": "ES0148396007", "market": "STOXX / Europa"},
    {"ticker": "ENEL.MI", "name": "Enel S.p.A.", "wkn": "928624", "isin": "IT0003128367", "market": "STOXX / Europa"},
    {"ticker": "ISP.MI", "name": "Intesa Sanpaolo S.p.A.", "wkn": "850605", "isin": "IT0000072618", "market": "STOXX / Europa"},

    # --- Nikkei (Japan) ---
    {"ticker": "7203.T", "name": "Toyota Motor Corporation", "wkn": "853510", "isin": "JP3633400001", "market": "Nikkei"},
    {"ticker": "6758.T", "name": "Sony Group Corporation", "wkn": "853687", "isin": "JP3435000009", "market": "Nikkei"},
    {"ticker": "8035.T", "name": "Tokyo Electron Limited", "wkn": "876911", "isin": "JP3571400005", "market": "Nikkei"},
    {"ticker": "9984.T", "name": "SoftBank Group Corp.", "wkn": "891624", "isin": "JP3436100006", "market": "Nikkei"},
    {"ticker": "6861.T", "name": "Keyence Corporation", "wkn": "880143", "isin": "JP3236200006", "market": "Nikkei"},
    {"ticker": "8306.T", "name": "Mitsubishi UFJ Financial Group", "wkn": "A0B9N3", "isin": "JP3902900004", "market": "Nikkei"},
    {"ticker": "9432.T", "name": "Nippon Telegraph and Telephone (NTT)", "wkn": "872508", "isin": "JP3735400008", "market": "Nikkei"},
    {"ticker": "4502.T", "name": "Takeda Pharmaceutical Company", "wkn": "895516", "isin": "JP3463000004", "market": "Nikkei"},
    {"ticker": "6501.T", "name": "Hitachi Ltd.", "wkn": "853495", "isin": "JP3788600009", "market": "Nikkei"},
    {"ticker": "7974.T", "name": "Nintendo Co. Ltd.", "wkn": "864009", "isin": "JP3756600007", "market": "Nikkei"},
]


def make_entry(
    ticker: str,
    name: str,
    wkn: str,
    isin: str,
    asset_type: str,
    market: str = "",
) -> dict:
    return {
        "ticker": ticker,
        "name": name or ticker,
        "wkn": wkn,
        "isin": isin,
        "asset_type": asset_type,
        "market": market,
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
    allowed_tickers: set[str] | None = None,
    allowed_isins: set[str] | None = None,
) -> list[dict]:
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
        ticker = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        name = str(row.get("name") or row.get("company_name") or "").strip()
        isin = str(row.get("isin") or "").strip().upper()
        wkn = str(row.get("wkn") or "").strip().upper()

        if not name and not ticker:
            continue

        if asset_type == "equity":
            root_ticker = ticker.split(".")[0] if ticker else ""
            in_filter = (
                (allowed_isins and isin and isin in allowed_isins)
                or (allowed_tickers and (ticker in allowed_tickers or root_ticker in allowed_tickers))
            )
            if not in_filter:
                continue

        entry = make_entry(ticker=ticker, name=name, wkn=wkn, isin=isin, asset_type=asset_type)
        rows.append(entry)
        if len(rows) >= max_items:
            break

    return rows


def main():
    equity_paths, etf_paths = find_repo_files()
    print(f"Gefunden: {len(equity_paths)} Aktien-Dateien, {len(etf_paths)} ETF-Dateien.")

    allowed_isins = {item["isin"].upper() for item in CORE_EQUITIES if item.get("isin")}
    allowed_tickers = {item["ticker"].upper() for item in CORE_EQUITIES if item.get("ticker")} | {
        item["ticker"].split(".")[0].upper() for item in CORE_EQUITIES if item.get("ticker")
    }

    items: list[dict] = []

    # 1. Katalog-Aktien (DAX, MDAX, TecDAX, Dow Jones, S&P 500, STOXX/Europa, Nikkei) anlegen
    for eq in CORE_EQUITIES:
        items.append(
            make_entry(
                ticker=eq["ticker"].upper(),
                name=eq["name"],
                wkn=eq.get("wkn", "").upper(),
                isin=eq.get("isin", "").upper(),
                asset_type="equity",
                market=eq.get("market", ""),
            )
        )

    # 2. Ergänzende Treffer aus FinanceDatabase für diese Märkte einlesen
    for path in equity_paths:
        fetched = fetch_and_parse(
            path,
            asset_type="equity",
            max_items=2500,
            allowed_tickers=allowed_tickers,
            allowed_isins=allowed_isins,
        )
        items.extend(fetched)

    # 3. ETF-Liste wie gehabt (bis zu 1.500 Einträge) einlesen
    etf_count = 0
    for path in etf_paths:
        if etf_count >= 1500:
            break
        remaining = 1500 - etf_count
        fetched = fetch_and_parse(path, asset_type="etf", max_items=remaining)
        items.extend(fetched)
        etf_count += len(fetched)

    deduped: dict[str, dict] = {}
    for entry in items:
        key = entry["isin"] or entry["wkn"] or f"{entry['ticker']}:{entry['name']}"
        if key not in deduped:
            deduped[key] = entry
        else:
            existing = deduped[key]
            if not existing.get("wkn") and entry.get("wkn"):
                existing["wkn"] = entry["wkn"]
            if not existing.get("isin") and entry.get("isin"):
                existing["isin"] = entry["isin"]
            if not existing.get("market") and entry.get("market"):
                existing["market"] = entry["market"]
            if existing.get("asset_type") == "equity" and entry.get("ticker", "").endswith(".DE"):
                existing["ticker"] = entry["ticker"]

    result = sorted(deduped.values(), key=lambda x: x["name"].lower())
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(
        f"Fertig! {len(result)} Einträge in '{OUTPUT_FILE.name}' gespeichert "
        f"(davon {etf_count} ETFs)."
    )


if __name__ == "__main__":
    main()
