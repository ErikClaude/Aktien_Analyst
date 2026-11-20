"""Ideen-Tagebuch und Auswertung.

Jede genannte Idee wird mit Stichtag, Kurs, Indexstand und Score in
``journal/ideen.csv`` eingetragen. ``aktualisiere_kurse`` ergänzt später die
Kurse nach 5, 20 und 60 Handelstagen; ``auswertung`` vergleicht die Ideen
mit ihrem Heimatindex.

Grundsätze:
  - Bestehende Einträge werden nie überschrieben (Datum + Ticker eindeutig).
  - Renditen in Handelswährung gegen den Heimatindex, ohne Währungseffekt.
  - Taucht eine Aktie an mehreren Tagen auf, zählt sie in der Auswertung
    je Horizont nur einmal (sonst blähen Dauerbrenner die Statistik auf).
  - Unter ``min_anzahl`` Ideen gibt es keine Aussage, nur den Hinweis darauf.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from analyst.universe import PROJEKT

STANDARD_PFAD = PROJEKT / "journal" / "ideen.csv"
HORIZONTE = (5, 20, 60)  # Handelstage: ca. 1 Woche, 1 Monat, 3 Monate

ERFASSUNG = [
    "datum", "ticker", "name", "markt", "index", "waehrung", "kurs", "kurs_eur", "index_stand",
    "score", "max_punkte", "klasse", "T", "M", "B", "K", "E", "R",
    "trend", "muster", "muster_status", "stopp", "quelle", "eingetragen_am",
]


def spalten_spaeter(horizonte=HORIZONTE) -> list[str]:
    """Spalten, die ``aktualisiere_kurse`` nachträglich füllt."""
    sp = []
    for h in horizonte:
        sp += [f"datum_{h}t", f"kurs_{h}t", f"index_{h}t",
               f"rendite_{h}t_pct", f"index_rendite_{h}t_pct", f"ueberrendite_{h}t_pp"]
    return sp + ["stopp_gerissen_am"]


SPALTEN = ERFASSUNG + spalten_spaeter()
DATUMSSPALTEN = ["datum", "eingetragen_am", "stopp_gerissen_am"] + [f"datum_{h}t" for h in HORIZONTE]


# --------------------------------------------------------------------------
# Lesen, Schreiben, Eintragen
# --------------------------------------------------------------------------

def lade_journal(pfad: Path | str = STANDARD_PFAD) -> pd.DataFrame:
    """Journal lesen; fehlt die Datei, ein leeres Journal mit allen Spalten."""
    pfad = Path(pfad)
    if not pfad.exists():
        return pd.DataFrame(columns=SPALTEN)
    df = pd.read_csv(pfad)
    for s in SPALTEN:
        if s not in df:
            df[s] = np.nan
    for s in DATUMSSPALTEN:
        df[s] = pd.to_datetime(df[s])
    return df[SPALTEN]


def speichere_journal(df: pd.DataFrame, pfad: Path | str = STANDARD_PFAD) -> None:
    """Journal als CSV speichern (Datumsspalten als JJJJ-MM-TT)."""
    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    aus = df.copy()
    for s in DATUMSSPALTEN:
        aus[s] = pd.to_datetime(aus[s]).dt.strftime("%Y-%m-%d")
    aus.to_csv(pfad, index=False)


def ideen_aus_bewertung(
    tabelle: pd.DataFrame,
    kurse: dict[str, pd.DataFrame],
    top: int,
    quelle: str,
    eingetragen_am: date | None = None,
) -> pd.DataFrame:
    """Die ``top`` besten Ideen aus ``scoring.bewerte_universum`` als Journalzeilen."""
    eingetragen_am = eingetragen_am or date.today()
    zeilen = []
    for _, z in tabelle.head(top).iterrows():
        idx = kurse.get(z["index"])
        stand = float(idx["Close"].asof(z["datum"])) if idx is not None and len(idx) else math.nan
        m = z["haupt_muster"]
        zeilen.append({
            "datum": pd.Timestamp(z["datum"]), "ticker": z["ticker"], "name": z["name"],
            "markt": z["markt"], "index": z["index"], "waehrung": z["waehrung"],
            "kurs": z["kurs"], "kurs_eur": z["kurs_eur"], "index_stand": stand,
            "score": z["gesamt"], "max_punkte": z["max_punkte"], "klasse": z["klasse"],
            "T": z["T"], "M": z["M"], "R": z["R"],
            **{b: z.get(b, np.nan) for b in ("B", "K", "E")},
            "trend": z["trend"],
            "muster": m.name if m is not None else "", "muster_status": m.status if m is not None else "",
            "stopp": z["stopp"], "quelle": quelle, "eingetragen_am": pd.Timestamp(eingetragen_am),
        })
    return pd.DataFrame(zeilen, columns=ERFASSUNG).reindex(columns=SPALTEN)


def trage_ein(journal: pd.DataFrame, neue: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Neue Ideen anhängen. Gleiche Kombination Datum + Ticker wird nicht doppelt erfasst.

    Gibt (Journal, Anzahl neu eingetragener Zeilen) zurück.
    """
    if journal.empty:
        gesamt = neue.copy()
    else:
        schluessel = set(zip(pd.to_datetime(journal["datum"]), journal["ticker"]))
        neu = neue[[(d, t) not in schluessel for d, t in zip(pd.to_datetime(neue["datum"]), neue["ticker"])]]
        gesamt = pd.concat([journal, neu], ignore_index=True) if len(neu) else journal.copy()
    return gesamt.reset_index(drop=True), len(gesamt) - len(journal)


# --------------------------------------------------------------------------
# Spätere Kurse
# --------------------------------------------------------------------------

def aktualisiere_kurse(
    journal: pd.DataFrame,
    kurse: dict[str, pd.DataFrame],
    horizonte=HORIZONTE,
) -> pd.DataFrame:
    """Ergänzt Kurse nach h Handelstagen und prüft, ob die Invalidierung gerissen ist.

    Formeln:
      Rendite_h       = Kurs_h / Kurs − 1                (in %)
      Index-Rendite_h = Index_h / Index-Stand − 1        (Index am selben Datum wie Kurs_h)
      Überrendite_h   = Rendite_h − Index-Rendite_h      (in Prozentpunkten)
    Ein Horizont wird erst gefüllt, wenn h Handelstage der Aktie vorliegen.
    Invalidierung gerissen = erster Schlusskurs unter ``stopp`` innerhalb
    des längsten Horizonts.
    """
    j = journal.copy()
    for s in spalten_spaeter(horizonte):
        if s not in j:
            j[s] = np.nan
    for i, z in j.iterrows():
        df = kurse.get(z["ticker"])
        if df is None or df.empty:
            continue
        close = df["Close"]
        start = int(close.index.searchsorted(pd.Timestamp(z["datum"]), side="right")) - 1
        if start < 0:
            continue
        idx = kurse.get(z["index"])
        for h in horizonte:
            ziel = start + h
            if ziel >= len(close) or not pd.isna(z[f"kurs_{h}t"]):
                continue
            d, k = close.index[ziel], float(close.iloc[ziel])
            i_k = float(idx["Close"].asof(d)) if idx is not None and len(idx) else math.nan
            r = (k / z["kurs"] - 1) * 100
            ir = (i_k / z["index_stand"] - 1) * 100
            j.loc[i, [f"datum_{h}t", f"kurs_{h}t", f"index_{h}t", f"rendite_{h}t_pct",
                      f"index_rendite_{h}t_pct", f"ueberrendite_{h}t_pp"]] = [d, k, i_k, r, ir, r - ir]
        if pd.isna(z["stopp_gerissen_am"]) and not pd.isna(z["stopp"]):
            fenster = close.iloc[start + 1:start + 1 + max(horizonte)]
            unter = fenster[fenster < z["stopp"]]
            if len(unter):
                j.loc[i, "stopp_gerissen_am"] = unter.index[0]
    for s in DATUMSSPALTEN:
        if s in j:
            j[s] = pd.to_datetime(j[s])
    return j


# --------------------------------------------------------------------------
# Auswertung
# --------------------------------------------------------------------------

def unabhaengige_ideen(journal: pd.DataFrame, h: int) -> pd.DataFrame:
    """Nur Einträge, deren Ticker nicht schon innerhalb der letzten h Handelstage erfasst war.

    h Handelstage werden als ceil(h · 7/5) Kalendertage angenähert.
    """
    abstand = pd.Timedelta(days=math.ceil(h * 7 / 5))
    behalten, letzte = [], {}
    for i, z in journal.sort_values("datum").iterrows():
        vorher = letzte.get(z["ticker"])
        if vorher is None or z["datum"] - vorher >= abstand:
            behalten.append(i)
            letzte[z["ticker"]] = z["datum"]
    return journal.loc[behalten]


def auswertung(journal: pd.DataFrame, horizonte=HORIZONTE) -> pd.DataFrame:
    """Kennzahlen je Gruppe (alle, je Klasse) und Horizont.

    Nur abgeschlossene Horizonte und unabhängige Ideen zählen.
    """
    zeilen = []
    for h in horizonte:
        basis = unabhaengige_ideen(journal, h)
        basis = basis[basis[f"ueberrendite_{h}t_pp"].notna()]
        gruppen = [("alle", basis)] + [(f"Klasse {k}", g) for k, g in basis.groupby("klasse")]
        for name, g in gruppen:
            ue = g[f"ueberrendite_{h}t_pp"]
            gerissen = g["stopp_gerissen_am"].notna() & (g["stopp_gerissen_am"] <= g[f"datum_{h}t"])
            zeilen.append({
                "gruppe": name, "horizont_t": h, "anzahl": len(g),
                "rendite_pct": g[f"rendite_{h}t_pct"].mean(),
                "index_rendite_pct": g[f"index_rendite_{h}t_pct"].mean(),
                "ueberrendite_pp": ue.mean(), "ueberrendite_median_pp": ue.median(),
                "besser_als_index_pct": (ue > 0).mean() * 100 if len(g) else math.nan,
                "invalidierung_gerissen_pct": gerissen.mean() * 100 if len(g) else math.nan,
            })
    return pd.DataFrame(zeilen)


def _z(x: float, einheit: str = "%") -> str:
    """Zahl mit Vorzeichen im deutschen Format."""
    if pd.isna(x):
        return "nicht gefunden"
    return f"{'+' if x > 0 else ''}{x:.1f}".replace(".", ",") + f" {einheit}"


def auswertung_markdown(aus: pd.DataFrame, journal: pd.DataFrame, min_anzahl: int = 20,
                        stichtag: date | None = None) -> str:
    """Auswertung als Markdown (Listen, keine Tabellen)."""
    stichtag = stichtag or date.today()
    t = [
        f"# Journal-Auswertung – {stichtag:%d.%m.%Y}",
        "",
        f"- Einträge gesamt: {len(journal)}, verschiedene Aktien: {journal['ticker'].nunique()}",
        "- Renditen in Handelswährung gegen den Heimatindex (ohne Währungseffekt).",
        "- Je Horizont zählt jede Aktie nur einmal (unabhängige Ideen).",
        f"- Unter {min_anzahl} Ideen gibt es keine Aussage.",
        "",
    ]
    for h in sorted(aus["horizont_t"].unique()) if len(aus) else []:
        t += [f"## Nach {h} Handelstagen", ""]
        for _, z in aus[aus["horizont_t"] == h].iterrows():
            if z["anzahl"] < min_anzahl:
                t.append(f"- **{z['gruppe']}** ({z['anzahl']} Ideen): zu wenige Daten für eine "
                         "belastbare Aussage")
                continue
            t.append(
                f"- **{z['gruppe']}** ({z['anzahl']} Ideen): Ø {_z(z['rendite_pct'])} vs. Index "
                f"{_z(z['index_rendite_pct'])} → Überrendite Ø {_z(z['ueberrendite_pp'], 'Pp.')}, "
                f"Median {_z(z['ueberrendite_median_pp'], 'Pp.')}; besser als Index: "
                f"{z['besser_als_index_pct']:.0f} %; Invalidierung gerissen: "
                f"{z['invalidierung_gerissen_pct']:.0f} %")
        t.append("")
    if not len(aus) or aus["anzahl"].max() == 0:
        t += ["Noch kein Horizont abgeschlossen – erste Werte nach 5 Handelstagen.", ""]
    t += ["Ein schlechtes Ergebnis wird genauso berichtet wie ein gutes.", ""]
    return "\n".join(t)


# --------------------------------------------------------------------------
# Kommandozeile
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """python -m analyst.journal aktualisieren | auswerten [--nur-cache]"""
    import argparse
    import logging

    from analyst.data import lade_markt
    from analyst.universe import Aktie, lade_config

    p = argparse.ArgumentParser(description="Ideen-Journal")
    p.add_argument("aktion", choices=["aktualisieren", "auswerten"])
    p.add_argument("--nur-cache", action="store_true")
    p.add_argument("--min-anzahl", type=int, default=20)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    journal = lade_journal()
    if journal.empty:
        print("Journal ist leer.")
        return
    if args.aktion == "aktualisieren":
        cfg = lade_config()
        aktien = [Aktie(t, n, m, w, i) for t, n, m, w, i in
                  journal[["ticker", "name", "markt", "waehrung", "index"]].drop_duplicates("ticker")
                  .itertuples(index=False)]
        kurse = lade_markt(cfg, aktien, nur_cache=args.nur_cache)
        journal = aktualisiere_kurse(journal, kurse)
        speichere_journal(journal)
        print(f"Journal aktualisiert: {len(journal)} Einträge")
    else:
        text = auswertung_markdown(auswertung(journal), journal, args.min_anzahl)
        ziel = PROJEKT / "reports" / f"journal_{date.today().isoformat()}.md"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(text, encoding="utf-8")
        print(text)
        print(f"gespeichert: {ziel}")


if __name__ == "__main__":
    main()
