"""Tests für analyst.indicators – Referenzwerte und konstruierte Kursreihen."""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analyst.indicators import (
    band_52w,
    berechne_indikatoren,
    indikator_tabelle,
    letzte_kreuzung,
    lokale_extrema,
    performance,
    preiszonen,
    relative_staerke,
    rsi,
    rsi_zone,
    sma,
    trend,
    unterstuetzung_widerstand,
    volumen_ausschlag,
)
from analyst.universe import Aktie

FIX = Path(__file__).parent / "fixtures"


def kursreihe(close, start="2025-01-01", volumen=None, spanne=0.0) -> pd.DataFrame:
    """Baut einen Kurs-DataFrame an Werktagen. High/Low = Close ± ``spanne``."""
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range(start, periods=len(close))
    vol = np.full(len(close), 1000.0) if volumen is None else np.asarray(volumen, float)
    return pd.DataFrame({"Open": close, "High": close + spanne, "Low": close - spanne,
                         "Close": close, "Volume": vol}, index=idx)


# --- RSI -------------------------------------------------------------------

def test_rsi_referenz_stockcharts():
    """Rechenbeispiel von StockCharts (Wilder-RSI, 14 Tage), Referenz auf 2 Stellen gerundet."""
    ref = pd.read_csv(FIX / "rsi_referenz.csv")
    berechnet = rsi(ref["close"])
    soll = ref["rsi14_referenz"]
    assert berechnet[soll.isna()].isna().all()           # die ersten 14 Tage: kein Wert
    assert np.allclose(berechnet[soll.notna()], soll.dropna(), atol=0.01)


def test_rsi_extremfaelle():
    assert rsi(pd.Series(np.arange(1, 31, dtype=float))).iloc[-1] == 100.0   # nur Gewinne
    assert rsi(pd.Series(np.arange(30, 0, -1, dtype=float))).iloc[-1] == 0.0  # nur Verluste
    assert rsi(pd.Series([5.0] * 20)).iloc[-1] == 50.0                       # keine Bewegung
    assert rsi(pd.Series([1.0, 2.0, 3.0])).isna().all()                      # zu kurz


def test_rsi_zone():
    assert rsi_zone(75) == "überkauft"
    assert rsi_zone(25) == "überverkauft"
    assert rsi_zone(70) == "neutral" and rsi_zone(30) == "neutral"
    assert rsi_zone(float("nan")) == "nicht gefunden"


# --- Linien und Trend -------------------------------------------------------

def test_sma():
    s = sma(pd.Series([1.0, 2, 3, 4, 5]), 3)
    assert s.isna().sum() == 2 and s.iloc[-1] == 4.0


def test_golden_und_death_cross():
    # 250 Tage fallend, dann steil steigend: SMA50 kreuzt SMA200 von unten
    up = np.r_[np.linspace(200, 100, 250), np.linspace(100, 260, 120)]
    s = pd.Series(up, index=pd.bdate_range("2024-01-01", periods=len(up)))
    k = letzte_kreuzung(sma(s, 50), sma(s, 200), max_tage=400)
    assert k["art"] == "Golden Cross"
    # Spiegelbild
    k2 = letzte_kreuzung(sma(-s + 500, 50), sma(-s + 500, 200), max_tage=400)
    assert k2["art"] == "Death Cross"
    # nicht frisch genug
    assert letzte_kreuzung(sma(s, 50), sma(s, 200), max_tage=k["tage_her"] - 1) is None


def test_keine_kreuzung():
    s = pd.Series(np.linspace(100, 200, 300))
    assert letzte_kreuzung(sma(s, 50), sma(s, 200), max_tage=500) is None


def test_trend():
    assert trend(110, 105, 100) == "Aufwärtstrend"
    assert trend(90, 95, 100) == "Abwärtstrend"
    assert trend(100, 95, 105) == "gemischt"
    assert trend(100, float("nan"), 90) == "nicht gefunden"


# --- 52-Wochen-Band ----------------------------------------------------------

def test_band_52w():
    # Tief 50, Hoch 150, Kurs am Ende 100 -> genau Mitte, 33,3 % unter dem Hoch
    werte = [100] * 100 + [50] + [100] * 100 + [150] + [100] * 50
    b = band_52w(kursreihe(werte))
    assert (b["tief_52w"], b["hoch_52w"]) == (50, 150)
    assert b["lage_52w_pct"] == pytest.approx(50.0)
    assert b["abstand_hoch_pct"] == pytest.approx(-33.333, abs=0.001)
    assert b["band_vollstaendig"]


def test_band_52w_altes_tief_faellt_raus():
    werte = [10] + [100] * 260 + [120]
    assert band_52w(kursreihe(werte))["tief_52w"] == 100


def test_band_52w_kurze_historie():
    assert not band_52w(kursreihe([1, 2, 3]))["band_vollstaendig"]


# --- Performance und relative Stärke ----------------------------------------

def test_performance_kalendermonate():
    idx = pd.to_datetime(["2026-01-15", "2026-01-30", "2026-02-27", "2026-03-02"])
    s = pd.Series([100.0, 110.0, 120.0, 132.0], index=idx)
    # Stichtag 02.02. ist kein Kurstag -> letzter Kurs davor (30.01.: 110)
    assert performance(s, 1) == pytest.approx(20.0)
    assert math.isnan(performance(s, 3))  # Historie reicht nicht


def test_relative_staerke():
    idx = pd.bdate_range("2025-01-01", "2026-01-01")
    aktie = pd.Series(np.linspace(100, 200, len(idx)), index=idx)   # +100 %
    index = pd.Series(np.linspace(100, 120, len(idx)), index=idx)   # +20 %
    rs = relative_staerke(aktie, index)
    assert rs["perf_12m_pct"] == pytest.approx(100.0, abs=0.5)
    assert rs["index_12m_pct"] == pytest.approx(20.0, abs=0.5)
    assert rs["rs_12m_pp"] == pytest.approx(80.0, abs=1.0)
    assert rs["rs_1m_pp"] > 0


def test_relative_staerke_ohne_index():
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-01-01", "2026-02-02"]))
    assert math.isnan(relative_staerke(s, None)["rs_1m_pp"])


# --- Volumen ---------------------------------------------------------------

def test_volumen_ausschlag():
    vol = [1000] * 40 + [3000]
    v = volumen_ausschlag(pd.Series(vol, dtype=float))
    assert v["volumen_faktor"] == pytest.approx(3.0)
    assert v["volumen_auffaellig"] and v["volumen_spitzen_5t"] == 1
    ruhig = volumen_ausschlag(pd.Series([1000.0] * 41))
    assert ruhig["volumen_faktor"] == 1.0 and not ruhig["volumen_auffaellig"]
    assert math.isnan(volumen_ausschlag(pd.Series([1.0] * 10))["volumen_faktor"])


# --- Extrema, Unterstützung, Widerstand --------------------------------------

def test_lokale_extrema():
    # Zickzack: Hochs bei 110 (Tag 10 und 30), Tief bei 90 (Tag 20)
    werte = np.r_[np.linspace(100, 110, 11), np.linspace(109, 90, 10),
                  np.linspace(91, 110, 10), np.linspace(109, 100, 10)]
    df = kursreihe(werte)
    hochs, tiefs = lokale_extrema(df, fenster=5)
    assert list(hochs.round(2)) == [110.0, 110.0]
    assert list(tiefs.round(2)) == [90.0]


def test_lokale_extrema_plateau_zaehlt_einmal():
    df = kursreihe([1, 2, 3, 4, 5, 9, 9, 9, 5, 4, 3, 2, 1])
    hochs, _ = lokale_extrema(df, fenster=3)
    assert len(hochs) == 1


def test_preiszonen():
    preise = pd.Series([100.0, 101.5, 120.0, 99.8],
                       index=pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"]))
    zonen = preiszonen(preise, toleranz=0.02)
    assert [z["beruehrungen"] for z in zonen] == [3, 1]
    assert zonen[0]["zuletzt"] == pd.Timestamp("2026-04-01")


def test_unterstuetzung_widerstand():
    # Seitwärtsband zwischen 90 und 110, Kurs am Ende 100
    welle = np.r_[np.linspace(100, 110, 6)[1:], np.linspace(110, 90, 11)[1:], np.linspace(90, 100, 6)[1:]]
    werte = np.r_[[100.0], np.tile(welle, 8), [100.0] * 6]
    sw = unterstuetzung_widerstand(kursreihe(werte), fenster=3)
    assert sw["unterstuetzung"] == pytest.approx(90.0)
    assert sw["widerstand"] == pytest.approx(110.0)
    assert sw["unterstuetzung_beruehrungen"] >= 7
    assert sw["abstand_widerstand_pct"] == pytest.approx(10.0)


def test_kein_widerstand_am_hoch():
    sw = unterstuetzung_widerstand(kursreihe(np.linspace(50, 150, 260)))
    assert math.isnan(sw["widerstand"])


# --- Gesamt ------------------------------------------------------------------

def test_berechne_indikatoren_und_tabelle():
    werte = np.linspace(100, 140, 300) + np.sin(np.arange(300) / 5) * 3
    df = kursreihe(werte, spanne=1.0)
    idx = kursreihe(np.linspace(1000, 1100, 300))
    ind = berechne_indikatoren(df, idx)
    assert ind["datum"] == df.index[-1]
    assert ind["trend"] == "Aufwärtstrend"
    assert ind["rs_12m_pp"] > 0
    for schluessel in ("rsi14", "sma50", "sma200", "lage_52w_pct", "volumen_faktor"):
        assert not math.isnan(ind[schluessel]), schluessel

    aktien = [Aktie("A", "A AG", "de", "EUR", "^IDX"), Aktie("KURZ", "Kurz", "de", "EUR", "^IDX"),
              Aktie("FEHLT", "Fehlt", "de", "EUR", "^IDX")]
    tab = indikator_tabelle(aktien, {"A": df, "^IDX": idx, "KURZ": kursreihe([1.0] * 10)})
    assert list(tab["ticker"]) == ["A"]


def test_kurze_historie_ohne_sma200():
    ind = berechne_indikatoren(kursreihe(np.linspace(10, 20, 100)))
    assert math.isnan(ind["sma200"]) and ind["trend"] == "nicht gefunden"
    assert math.isnan(ind["rs_12m_pp"])
