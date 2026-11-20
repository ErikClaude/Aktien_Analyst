"""Erzeugt config/tickers/{de,us,jp}.csv aus Wikipedia.

Quellen:
  DAX:        https://en.wikipedia.org/wiki/DAX  (Tabelle "Ticker")
  S&P 500:    https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
  Nikkei 225: https://ja.wikipedia.org/wiki/日経平均株価  (Tabellen "証券コード")

Wikipedia ist nicht offiziell; die Listen können wenige Tage hinter
Indexänderungen liegen. Daher Stand in config/tickers/stand.yaml.

Aufruf:  python scripts/aktualisiere_tickerlisten.py
"""

from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analyst.universe import PROJEKT, jp_code_zu_yahoo, us_symbol_zu_yahoo  # noqa: E402

ZIEL = PROJEKT / "config" / "tickers"
KOPF = {"User-Agent": "aktien-analyst/0.1 (privates Projekt)"}

URL_DAX = "https://en.wikipedia.org/wiki/DAX"
URL_SP = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
URL_NK = "https://ja.wikipedia.org/wiki/%E6%97%A5%E7%B5%8C%E5%B9%B3%E5%9D%87%E6%A0%AA%E4%BE%A1"


def tabellen(url: str) -> list[pd.DataFrame]:
    """Alle HTML-Tabellen einer Seite."""
    antwort = requests.get(url, headers=KOPF, timeout=30)
    antwort.raise_for_status()
    return pd.read_html(io.StringIO(antwort.text))


def dax() -> pd.DataFrame:
    t = next(t for t in tabellen(URL_DAX) if "Ticker" in t.columns and "Company" in t.columns)
    return pd.DataFrame({"ticker": t["Ticker"].str.strip(), "name": t["Company"],
                         "sektor": t["Prime Standard Sector"]})


def sp500() -> pd.DataFrame:
    t = next(t for t in tabellen(URL_SP) if "Symbol" in t.columns and "GICS Sector" in t.columns)
    return pd.DataFrame({"ticker": t["Symbol"].map(us_symbol_zu_yahoo), "name": t["Security"],
                         "sektor": t["GICS Sector"]})


def nikkei225() -> pd.DataFrame:
    teile = [t for t in tabellen(URL_NK) if list(t.columns)[:2] == ["証券コード", "銘柄"]]
    t = pd.concat(teile, ignore_index=True)
    return pd.DataFrame({"ticker": t["証券コード"].astype(str).map(jp_code_zu_yahoo),
                         "name": t["銘柄"], "sektor": ""})


ERWARTET = {"de": (dax, 40), "us": (sp500, 500), "jp": (nikkei225, 225)}


def main() -> None:
    ZIEL.mkdir(parents=True, exist_ok=True)
    stand = {}
    for markt, (funktion, soll) in ERWARTET.items():
        df = funktion().drop_duplicates("ticker").sort_values("ticker")
        # Plausibilitätsprüfung: S&P 500 hat wegen Aktiengattungen ~503 Titel
        if not soll <= len(df) <= soll + 10:
            raise SystemExit(f"{markt}: {len(df)} Titel statt ~{soll} – Seite geändert? Abbruch.")
        df.to_csv(ZIEL / f"{markt}.csv", index=False)
        stand[markt] = {"anzahl": len(df), "abgerufen": date.today().isoformat()}
        print(f"{markt}: {len(df)} Titel")
    (ZIEL / "stand.yaml").write_text(yaml.safe_dump(stand, allow_unicode=True, sort_keys=True))


if __name__ == "__main__":
    main()
