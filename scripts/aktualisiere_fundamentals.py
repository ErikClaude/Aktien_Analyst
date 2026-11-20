"""Lädt Basis-Kennzahlen (KGV, EV/EBITDA, Sektor, Kursziele) für alle gefilterten Aktien.

Grundlage für die Branchenmediane der Bewertung. Eine Abfrage je Aktie,
mit Pause; was jünger als 7 Tage ist, kommt aus dem Cache. Bricht nach 3
gesperrten Abfragen in Folge ab (Yahoo-Limit) – einfach später erneut starten,
es geht dort weiter, wo es aufgehört hat.

Aufruf:  python scripts/aktualisiere_fundamentals.py [--pause 1.5]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analyst.data import kennzahlen_fuer_filter, lade_markt, lade_marktkap, lade_wechselkurse  # noqa: E402
from analyst.fundamentals import lade_fundamentals  # noqa: E402
from analyst.universe import lade_config, lade_universum, wende_filter_an  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pause", type=float, default=1.5)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = lade_config()
    aktien = lade_universum(cfg)
    kurse = lade_markt(cfg, aktien, nur_cache=True)
    fx = lade_wechselkurse(cfg, nur_cache=True)
    kap = lade_marktkap([a.ticker for a in aktien], nur_cache=True)
    behalten, _ = wende_filter_an(kennzahlen_fuer_filter(aktien, kurse, fx, kap), cfg["filter"])
    df = lade_fundamentals(list(behalten["ticker"]), details=False, pause=args.pause)
    mit_kgv = df["trailingPE"].notna().sum() if "trailingPE" in df else 0
    print(f"Basisdaten: {len(df)} von {len(behalten)} Aktien im Cache, davon {mit_kgv} mit KGV")


if __name__ == "__main__":
    main()
