"""Tests für analyst.report – Formatierung, Berichtsaufbau, Chart-Datei."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from analyst.report import (
    ABSCHLUSS,
    bericht_markdown,
    boerse,
    gebuehr_anteil,
    preis,
    prozent,
    schreibe_bericht,
    zahl,
)
from analyst.scoring import bewerte_universum, lade_scoring_config
from analyst.universe import Aktie


def reihe(close) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"Open": close, "High": close * 1.005, "Low": close * 0.995,
                         "Close": close, "Volume": np.full(len(close), 1000.0)},
                        index=pd.bdate_range("2025-06-02", periods=len(close)))


@pytest.fixture(scope="module")
def daten():
    wellen = np.linspace(100, 150, 300) + np.sin(np.arange(300) / 4) * 2
    aktien = [Aktie("AUF", "Auf AG", "us", "USD", "^GSPC"), Aktie("SAP.DE", "SAP", "de", "EUR", "^GDAXI")]
    kurse = {"AUF": reihe(wellen), "SAP.DE": reihe(wellen[::-1] + 20),
             "^GSPC": reihe(np.linspace(5000, 5200, 300)), "^GDAXI": reihe(np.linspace(20000, 21000, 300))}
    filt = pd.DataFrame({"ticker": ["AUF", "SAP.DE"], "kurs_eur": [135.0, 120.0], "marktkap_eur": [5e9, 5e9]})
    tab = bewerte_universum(aktien, kurse, filt, lade_scoring_config())
    meta = {"universum": 2, "gefiltert": 2, "max_kurs": 150,
            "kursstand": {"S&P 500": tab["datum"].max()},
            "wechselkurse": {"USD": (pd.Timestamp("2026-11-20"), 1.0971)},
            "gewichte": {"T": 1, "M": 0, "R": 0}, "gewichte_grund": "Backtest-Test"}
    return tab, kurse, meta


def test_formatierung():
    assert zahl(12345.678) == "12.345,68"
    assert preis(3197.0, "JPY") == "3.197 ¥"
    assert preis(54.5, "USD") == "54,50 $"
    assert preis(float("nan"), "EUR") == "nicht gefunden"
    assert prozent(12.34) == "+12,3 %" and prozent(-5.0) == "-5,0 %"
    assert boerse("SAP.DE") == "Xetra" and boerse("8306.T") == "Tokio" and boerse("INTC") == "USA"


def test_gebuehr():
    assert gebuehr_anteil(19.80) == pytest.approx(5.0)
    assert gebuehr_anteil(99.0) == pytest.approx(1.0)


def test_bericht_aufbau(daten):
    tab, _, meta = daten
    md = bericht_markdown(tab, 8, date(2026, 11, 20), meta)
    assert md.startswith("# Aktien-Screener – 20.11.2026")
    assert md.rstrip().endswith(ABSCHLUSS)
    for abschnitt in ("**Setup:**", "**Kurs & Trend**", "**Muster**", "**Bewertung:** nicht bewertet",
                      "**Kurstreiber:** nicht geprüft", "**Bull-Fall (technisch):**",
                      "**Bear-Fall (technisch):**", "**Eingepreist?** nicht bewertet", "**Gebühr:**",
                      "**Quellen:**", "## Termine der nächsten Wochen", "**Begriffe**"):
        assert abschnitt in md, abschnitt
    assert "Score " in md and "/10" in md
    assert "Gewichte: T 1, M 0, R 0" in md and "Grund der Gewichtung: Backtest-Test" in md
    assert "gewertet: T (übrige nur angezeigt)" in md
    assert "|---" not in md and "| " not in md        # keine Tabellen (Tablet)
    assert "1 € = 1,0971 USD" in md
    assert "Wechselkurs EUR/USD" in md                 # Quelle nur bei Fremdwährung


def test_keine_versprechen(daten):
    tab, _, meta = daten
    md = bericht_markdown(tab, 8, date(2026, 11, 20), meta).lower()
    for verboten in ("sicher steigen", "sehr wahrscheinlich", "kaufen sie", "garantiert"):
        assert verboten not in md


def test_schreibe_bericht_mit_charts(daten, tmp_path):
    tab, kurse, meta = daten
    pfad = schreibe_bericht(tab, kurse, meta, top=2, ordner=tmp_path, stichtag=date(2026, 11, 20))
    assert pfad == tmp_path / "2026-11-20.md"
    pngs = sorted(p.name for p in (tmp_path / "charts").glob("*.png"))
    assert pngs == ["2026-11-20_AUF.png", "2026-11-20_SAP.DE.png"]
    assert "](charts/2026-11-20_AUF.png)" in pfad.read_text(encoding="utf-8")
