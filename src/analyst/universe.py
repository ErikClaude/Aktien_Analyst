"""Aktienlisten und Filter.

Das Universum besteht aus den Indexmitgliedern von DAX, S&P 500 und
Nikkei 225 (Listen in ``config/tickers/``) plus manuellen Zusätzen aus
``config/universe.yaml``. Die Filter (Preis < 150 €, kein Penny Stock,
Marktkapitalisierung ≥ 1 Mrd. €) werden auf eine Tabelle mit Euro-Kursen
angewendet, die ``data.kennzahlen_fuer_filter`` liefert.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

PROJEKT = Path(__file__).resolve().parents[2]
STANDARD_CONFIG = PROJEKT / "config" / "universe.yaml"


@dataclass(frozen=True)
class Aktie:
    """Eine Aktie im Universum."""

    ticker: str          # Yahoo-Ticker, z. B. "SAP.DE", "INTC", "8306.T"
    name: str
    markt: str           # "de", "us", "jp"
    waehrung: str        # Handelswährung, z. B. "EUR"
    index: str           # Vergleichsindex, z. B. "^GDAXI"
    sektor: str = ""


def lade_config(pfad: Path | str = STANDARD_CONFIG) -> dict:
    """Liest die Universums-Konfiguration (YAML)."""
    with open(pfad, encoding="utf-8") as f:
        return yaml.safe_load(f)


def lade_universum(
    config: dict,
    maerkte: list[str] | None = None,
    basis: Path = PROJEKT,
) -> list[Aktie]:
    """Baut die Aktienliste aus den Ticker-CSVs plus Zusätzen.

    ``maerkte`` begrenzt auf einzelne Märkte (z. B. ``["de"]``).
    Tickerdubletten werden entfernt, Ausschlüsse aus der Config beachtet.
    """
    gewaehlt = maerkte or list(config["maerkte"])
    unbekannt = set(gewaehlt) - set(config["maerkte"])
    if unbekannt:
        raise ValueError(f"Unbekannter Markt: {sorted(unbekannt)}")

    ausschluss = set(config.get("ausschluss") or [])
    aktien: dict[str, Aktie] = {}

    for markt in gewaehlt:
        info = config["maerkte"][markt]
        liste = pd.read_csv(basis / info["liste"], dtype=str).fillna("")
        for _, zeile in liste.iterrows():
            aktien.setdefault(zeile["ticker"], _aktie(zeile, markt, info))

    for z in config.get("zusatz") or []:
        if z["markt"] in gewaehlt:
            info = config["maerkte"][z["markt"]]
            aktien.setdefault(z["ticker"], _aktie(z, z["markt"], info))

    return [a for t, a in aktien.items() if t not in ausschluss]


def _aktie(zeile, markt: str, info: dict) -> Aktie:
    """Hilfsfunktion: eine Zeile (CSV oder Zusatz) in eine Aktie umwandeln."""
    return Aktie(
        ticker=zeile["ticker"],
        name=zeile.get("name", "") or zeile["ticker"],
        markt=markt,
        waehrung=zeile.get("waehrung", "") or info["waehrung"],
        index=info["index"],
        sektor=zeile.get("sektor", "") or "",
    )


def wende_filter_an(
    tabelle: pd.DataFrame,
    filter_cfg: dict,
    max_kurs_eur: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Wendet Preis- und Größenfilter an.

    Erwartet die Spalten ``ticker``, ``kurs_eur``, ``marktkap_eur``.
    Gibt ``(behalten, ausgeschlossen)`` zurück; ``ausgeschlossen`` hat eine
    Spalte ``grund``. Fehlt ein Wert, wird die Aktie ausgeschlossen
    („nicht gefunden“) – wir raten keine Werte.

    ``max_kurs_eur`` überschreibt die Preisgrenze aus der Config
    (optionales Argument von /top-aktien).
    """
    # float(): YAML liest z. B. "1.0e9" (ohne Vorzeichen im Exponenten) als Text
    grenze = float(max_kurs_eur if max_kurs_eur is not None else filter_cfg["max_kurs_eur"])
    min_kurs = float(filter_cfg["min_kurs_eur"])
    min_kap = float(filter_cfg["min_marktkap_eur"])
    # Große Firmen (z. B. NTT nach Aktiensplit) dürfen unter 1 € notieren
    ausnahme_ab = float(filter_cfg.get("penny_ausnahme_ab_marktkap_eur", min_kap))

    def grund(z) -> str:
        if pd.isna(z["kurs_eur"]):
            return "Kurs nicht gefunden"
        if z["kurs_eur"] >= grenze:
            return f"Kurs ab {grenze:g} €"
        if pd.isna(z["marktkap_eur"]):
            return "Marktkapitalisierung nicht gefunden"
        if z["marktkap_eur"] < min_kap:
            return f"Marktkapitalisierung unter {min_kap / 1e9:g} Mrd. €"
        if z["kurs_eur"] < min_kurs and z["marktkap_eur"] < ausnahme_ab:
            return f"Kurs unter {min_kurs:g} € (Penny Stock)"
        return ""

    gruende = tabelle.apply(grund, axis=1) if len(tabelle) else pd.Series(dtype=str)
    behalten = tabelle[gruende == ""].reset_index(drop=True)
    ausgeschlossen = tabelle[gruende != ""].assign(grund=gruende[gruende != ""])
    return behalten, ausgeschlossen.reset_index(drop=True)


# --- Normalisierung der Indexlisten (für scripts/aktualisiere_tickerlisten.py)

def us_symbol_zu_yahoo(symbol: str) -> str:
    """S&P-Symbol in Yahoo-Ticker umwandeln: "BRK.B" -> "BRK-B"."""
    return symbol.strip().replace(".", "-")


def jp_code_zu_yahoo(code: str) -> str:
    """Tokioter Wertpapiercode in Yahoo-Ticker: "8306" -> "8306.T".

    Seit 2024 gibt es auch alphanumerische Codes wie "285A".
    """
    code = str(code).strip().upper()
    if len(code) != 4 or not code.isalnum():
        raise ValueError(f"Ungültiger Tokioter Code: {code!r}")
    return f"{code}.T"
