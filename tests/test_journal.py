"""Tests für analyst.journal – feste Journal-CSV und konstruierte Kursreihen."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analyst.journal import (
    SPALTEN,
    aktualisiere_kurse,
    auswertung,
    auswertung_markdown,
    ideen_aus_bewertung,
    lade_journal,
    speichere_journal,
    trage_ein,
    unabhaengige_ideen,
)
from analyst.scoring import bewerte_universum, lade_scoring_config
from analyst.universe import Aktie

FIX = Path(__file__).parent / "fixtures"


def reihe(close, start="2026-01-05") -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                         "Volume": np.full(len(close), 1000.0)},
                        index=pd.bdate_range(start, periods=len(close)))


def eintrag(datum="2026-01-05", ticker="A", kurs=100.0, index_stand=1000.0, stopp=95.0) -> pd.DataFrame:
    """Eine Journalzeile mit den nötigen Erfassungswerten."""
    z = {s: np.nan for s in SPALTEN}
    z.update({"datum": pd.Timestamp(datum), "ticker": ticker, "name": ticker, "markt": "us",
              "index": "^IDX", "waehrung": "USD", "kurs": kurs, "index_stand": index_stand,
              "score": 20, "max_punkte": 30, "klasse": "gemischt", "stopp": stopp,
              "stopp_gerissen_am": pd.NaT, "datum_5t": pd.NaT, "datum_20t": pd.NaT, "datum_60t": pd.NaT})
    return pd.DataFrame([z])[SPALTEN]


# --- Eintragen ---------------------------------------------------------------

def test_leeres_journal(tmp_path):
    j = lade_journal(tmp_path / "fehlt.csv")
    assert j.empty and list(j.columns) == SPALTEN


def test_trage_ein_ohne_dubletten():
    j, n = trage_ein(lade_journal(Path("/gibt/es/nicht.csv")), eintrag())
    assert n == 1
    neu = pd.concat([eintrag(kurs=999.0), eintrag(ticker="B")])
    j2, n2 = trage_ein(j, neu)
    assert n2 == 1 and len(j2) == 2
    assert j2.loc[j2["ticker"] == "A", "kurs"].iloc[0] == 100.0  # alter Eintrag bleibt


def test_speichern_und_laden(tmp_path):
    pfad = tmp_path / "ideen.csv"
    speichere_journal(eintrag(), pfad)
    j = lade_journal(pfad)
    assert j.loc[0, "datum"] == pd.Timestamp("2026-01-05")
    assert pd.isna(j.loc[0, "datum_5t"])


# --- Spätere Kurse -------------------------------------------------------------

def test_aktualisiere_kurse():
    # Aktie +1 je Tag ab 100, Index +2 je Tag ab 1000
    kurse = {"A": reihe(100 + np.arange(30)), "^IDX": reihe(1000 + 2 * np.arange(30))}
    j = aktualisiere_kurse(eintrag(), kurse)
    z = j.iloc[0]
    assert z["kurs_5t"] == 105 and z["index_5t"] == 1010
    assert z["rendite_5t_pct"] == pytest.approx(5.0)
    assert z["index_rendite_5t_pct"] == pytest.approx(1.0)
    assert z["ueberrendite_5t_pp"] == pytest.approx(4.0)
    assert z["kurs_20t"] == 120
    assert pd.isna(z["kurs_60t"])          # 60 Tage liegen noch nicht vor
    assert pd.isna(z["stopp_gerissen_am"])


def test_stopp_gerissen_und_datum_ohne_kurstag():
    # Idee an einem Samstag erfasst -> Startkurs ist der Freitag davor
    kurs = np.r_[np.full(5, 100.0), [96.0, 94.0, 97.0], np.full(20, 98.0)]
    kurse = {"A": reihe(kurs), "^IDX": reihe(np.full(28, 1000.0))}
    z = aktualisiere_kurse(eintrag(datum="2026-01-10", stopp=95.0), kurse).iloc[0]
    assert z["stopp_gerissen_am"] == pd.Timestamp("2026-01-13")   # Schluss 94 < 95
    assert z["datum_5t"] == pd.Timestamp("2026-01-16")


def test_aktualisieren_ueberschreibt_nicht():
    kurse = {"A": reihe(100 + np.arange(30)), "^IDX": reihe(np.full(30, 1000.0))}
    j = aktualisiere_kurse(eintrag(), kurse)
    kurse["A"] = reihe(np.full(30, 1.0))   # z. B. korrigierte Daten
    assert aktualisiere_kurse(j, kurse).iloc[0]["kurs_5t"] == 105


# --- Auswertung ------------------------------------------------------------------

def test_unabhaengige_ideen():
    j = lade_journal(FIX / "journal_beispiel.csv")
    assert len(j) == 26
    assert len(unabhaengige_ideen(j, 5)) == 25      # T00 zwei Tage später zählt nicht doppelt


def test_auswertung_mit_fixture():
    j = lade_journal(FIX / "journal_beispiel.csv")
    aus = auswertung(j).set_index(["gruppe", "horizont_t"])
    alle = aus.loc[("alle", 5)]
    assert alle["anzahl"] == 25
    # 15 × (+2 %) und 10 × (−1 %) -> Ø +0,8 %; Index +0,5 % -> Überrendite +0,3 Pp.
    assert alle["rendite_pct"] == pytest.approx(0.8)
    assert alle["ueberrendite_pp"] == pytest.approx(0.3)
    assert alle["besser_als_index_pct"] == pytest.approx(60.0)
    assert alle["invalidierung_gerissen_pct"] == pytest.approx(20.0)
    assert aus.loc[("Klasse stark", 5), "anzahl"] == 10
    assert aus.loc[("alle", 20), "anzahl"] == 0     # 20-Tage-Horizont noch offen


def test_auswertung_markdown_ehrlich_bei_wenig_daten():
    j = lade_journal(FIX / "journal_beispiel.csv")
    md = auswertung_markdown(auswertung(j), j, min_anzahl=20, stichtag=date(2026, 11, 20))
    assert ("**alle** (25 Ideen): Ø +0,8 % vs. Index +0,5 % → Überrendite Ø +0,3 Pp., "
            "Median +1,5 Pp.; besser als Index: 60 %; Invalidierung gerissen: 20 %") in md
    assert "**Klasse stark** (10 Ideen): zu wenige Daten" in md
    assert "|" not in md
    leer = auswertung_markdown(auswertung(j.iloc[:0]), j.iloc[:0])
    assert "Noch kein Horizont abgeschlossen" in leer


# --- Übernahme aus dem Score ---------------------------------------------------

def test_ideen_aus_bewertung():
    wellen = np.linspace(100, 150, 300) + np.sin(np.arange(300) / 4) * 2
    aktien = [Aktie("AUF", "Auf AG", "us", "USD", "^IDX")]
    kurse = {"AUF": reihe(wellen, "2025-06-02"), "^IDX": reihe(np.linspace(1000, 1100, 300), "2025-06-02")}
    filt = pd.DataFrame({"ticker": ["AUF"], "kurs_eur": [130.0], "marktkap_eur": [5e9]})
    tab = bewerte_universum(aktien, kurse, filt, lade_scoring_config())
    neu = ideen_aus_bewertung(tab, kurse, top=5, quelle="reports/x.md", eingetragen_am=date(2026, 11, 20))
    z = neu.iloc[0]
    assert list(neu.columns) == SPALTEN and len(neu) == 1
    assert z["datum"] == kurse["AUF"].index[-1] and z["index_stand"] == pytest.approx(1100)
    assert z["score"] == tab.iloc[0]["gesamt"] and pd.isna(z["B"])
