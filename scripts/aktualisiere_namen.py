"""Lädt englische Firmennamen für Märkte mit ``namen_en`` in config/universe.yaml.

Quelle: Yahoo Finance (yfinance, Feld "longName", ersatzweise "shortName").
Bereits vorhandene Namen werden nicht erneut abgefragt (Rate-Limit);
mit --alle werden alle neu geladen. Fehlende Namen bleiben leer – dann
zeigt der Bericht den Originalnamen.

Aufruf:  python scripts/aktualisiere_namen.py [--alle]
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analyst.universe import PROJEKT, lade_config  # noqa: E402


def englischer_name(ticker: str, versuche: int = 3) -> str:
    """longName (sonst shortName) von Yahoo; bei Fehlern mit Pause wiederholen."""
    import yfinance as yf

    for versuch in range(versuche):
        try:
            info = yf.Ticker(ticker).info
            return (info.get("longName") or info.get("shortName") or "").strip()
        except Exception as e:  # Yahoo wirft bei Rate-Limit unterschiedliche Fehler
            print(f"  {ticker}: {e} – Versuch {versuch + 1}/{versuche}")
            time.sleep(5 * (versuch + 1))
    return ""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--alle", action="store_true", help="auch vorhandene Namen neu laden")
    p.add_argument("--pause", type=float, default=0.5, help="Sekunden zwischen Abfragen")
    args = p.parse_args()

    for markt, info in lade_config()["maerkte"].items():
        if "namen_en" not in info:
            continue
        ziel = PROJEKT / info["namen_en"]
        tickers = pd.read_csv(PROJEKT / info["liste"], dtype=str)["ticker"].tolist()
        alt = (pd.read_csv(ziel, dtype=str).fillna("") if ziel.exists()
               else pd.DataFrame(columns=["ticker", "name_en", "stand"]))
        vorhanden = {} if args.alle else {t: (n, s) for t, n, s in alt.itertuples(index=False) if n}
        zeilen = []
        for i, t in enumerate(tickers, 1):
            if t in vorhanden:
                zeilen.append((t, *vorhanden[t]))
                continue
            name = englischer_name(t)
            zeilen.append((t, name, date.today().isoformat() if name else ""))
            print(f"{markt} {i}/{len(tickers)} {t}: {name or 'nicht gefunden'}")
            time.sleep(args.pause)
        df = pd.DataFrame(zeilen, columns=["ticker", "name_en", "stand"])
        df.to_csv(ziel, index=False)
        print(f"{markt}: {(df['name_en'] != '').sum()} von {len(df)} Namen -> {ziel.relative_to(PROJEKT)}")


if __name__ == "__main__":
    main()
