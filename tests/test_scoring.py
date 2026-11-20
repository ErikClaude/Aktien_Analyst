"""Tests für analyst.scoring – feste Konfiguration, konstruierte Daten."""

import math

import numpy as np
import pandas as pd
import pytest

from analyst.patterns import Muster
from analyst.scoring import (
    bewerte_aktie,
    gesamtscore,
    bewerte_universum,
    klasse,
    lade_scoring_config,
    punkte_muster,
    punkte_risiko,
    punkte_technik,
    risiko_kennzahlen,
    stopp_marke,
    stufe,
)
from analyst.universe import Aktie


@pytest.fixture(scope="module")
def cfg():
    return lade_scoring_config()


def reihe(close) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"Open": close, "High": close * 1.005, "Low": close * 0.995,
                         "Close": close, "Volume": np.full(len(close), 1000.0)},
                        index=pd.bdate_range("2025-06-02", periods=len(close)))


def ind_basis(**aenderung) -> dict:
    """Indikator-Momentaufnahme eines sauberen Aufwärtstrends."""
    ind = {"kurs": 110.0, "sma50": 105.0, "sma200": 100.0, "rsi14": 55.0,
           "trend": "Aufwärtstrend", "sma200_steigung_20t_pct": 1.0, "kreuzung": "",
           "rs_3m_pp": 2.0, "rs_12m_pp": 5.0, "abstand_hoch_pct": -10.0,
           "unterstuetzung": 104.0, "widerstand": 120.0}
    ind.update(aenderung)
    return ind


def muster(name="Doppelboden", richtung="bullisch", status="bestätigt", sicherheit="hoch",
           invalidierung=100.0) -> Muster:
    return Muster(name=name, richtung=richtung, status=status, ausbruchsmarke=105.0,
                  invalidierung=invalidierung, sicherheit=sicherheit, messziel=115.0,
                  datum=pd.Timestamp("2026-11-20"))


# --- Hilfen ------------------------------------------------------------------

def test_stufe():
    tab = [[22, 4], [28, 3], [35, 2]]
    assert stufe(20, tab) == 4 and stufe(22, tab) == 4 and stufe(30, tab) == 2
    assert stufe(50, tab) == 0 and stufe(float("nan"), tab) == 0


def test_klasse(cfg):
    assert klasse(21, 30, cfg) == "stark"
    assert klasse(20, 30, cfg) == "gemischt" and klasse(12, 30, cfg) == "gemischt"
    assert klasse(11, 30, cfg) == "schwach"
    assert klasse(42, 60, cfg) == "stark" and klasse(23, 60, cfg) == "schwach"


# --- Risiko-Kennzahlen ---------------------------------------------------------

def test_risiko_kennzahlen_ruhig():
    r = risiko_kennzahlen(reihe(np.linspace(100, 110, 260)))
    assert r["vola_60t_pct"] < 1 and r["max_rueckgang_12m_pct"] == 0 and r["tagesspruenge_12m"] == 0


def test_risiko_kennzahlen_sprung_und_rueckgang():
    kurse = np.r_[np.full(100, 100.0), [70.0], np.full(50, 70.0), [77.5], np.full(50, 77.5)]
    r = risiko_kennzahlen(reihe(kurse))
    assert r["max_rueckgang_12m_pct"] == pytest.approx(30.0)
    assert r["tagesspruenge_12m"] == 2  # −30 % und +10,7 %


# --- Technik -------------------------------------------------------------------

def test_technik_aufwaertstrend(cfg):
    t, gruende, warn = punkte_technik(ind_basis(), cfg)
    # 2 + 1 + 1 + 1 (Linien) + 1 + 1 (rel. Stärke) = 7
    assert t == 7 and len(gruende) == 6 and warn == []


def test_technik_ruecksetzer_im_trend_und_deckel(cfg):
    t, gruende, _ = punkte_technik(ind_basis(rsi14=28, kreuzung="Golden Cross", abstand_hoch_pct=-3), cfg)
    # 7 + 2 (Rücksetzer) + 1 (Golden Cross) + 1 (nahe Hoch) = 11 -> auf 10 begrenzt
    assert t == 10
    assert any("Rücksetzer" in g for g in gruende)


def test_technik_ueberverkauft_im_abwaertstrend_ist_warnung(cfg):
    ind = ind_basis(kurs=80, sma50=90, sma200=100, rsi14=25, trend="Abwärtstrend",
                    sma200_steigung_20t_pct=-1, rs_3m_pp=-5, rs_12m_pp=-10, kreuzung="Death Cross")
    t, gruende, warn = punkte_technik(ind, cfg)
    assert t == 0
    assert not any("Rücksetzer" in g for g in gruende)
    assert any("kein Kaufsignal" in w for w in warn)
    assert "frisches Death Cross" in warn


def test_technik_ueberkauft_abzug(cfg):
    assert punkte_technik(ind_basis(rsi14=75), cfg)[0] == 6


# --- Muster --------------------------------------------------------------------

def test_muster_punkte(cfg):
    assert punkte_muster([], cfg)[0] == 0
    assert punkte_muster([muster()], cfg)[0] == 9
    assert punkte_muster([muster(status="in Bildung", sicherheit="mittel")], cfg)[0] == 4
    assert punkte_muster([muster(status="gescheitert")], cfg)[0] == 0
    seit = muster(name="Seitwärtsphase", richtung="offen", status="in Bildung", sicherheit="mittel")
    assert punkte_muster([seit], cfg)[0] == 2


def test_muster_baerisch_abzug_und_hauptmuster(cfg):
    bull, baer = muster(), muster(name="Doppeltop", richtung="bärisch", status="bestätigt")
    p, gruende, warn, haupt = punkte_muster([bull, baer], cfg)
    assert p == 5 and haupt is bull
    assert any("Doppeltop" in w for w in warn)
    p2, _, _, haupt2 = punkte_muster([baer], cfg)
    assert p2 == 0 and haupt2 is baer   # bärisches Muster wird im Bericht gezeigt


def test_bestes_bullisches_muster_zaehlt(cfg):
    schwach, stark = muster(status="in Bildung", sicherheit="niedrig"), muster(name="Ausbruch nach oben")
    assert punkte_muster([schwach, stark], cfg)[3] is stark


# --- Stopp und Risiko ----------------------------------------------------------

def test_stopp_marke():
    assert stopp_marke(ind_basis(), muster(invalidierung=101.0)) == (101.0, "Invalidierung Doppelboden")
    assert stopp_marke(ind_basis(), None) == (104.0, "nächste Unterstützung")
    assert math.isnan(stopp_marke(ind_basis(unterstuetzung=float("nan")), None)[0])
    # gescheitertes Muster liefert keine Invalidierung mehr
    assert stopp_marke(ind_basis(), muster(status="gescheitert"))[1] == "nächste Unterstützung"


def test_risiko_punkte(cfg):
    r = {"vola_60t_pct": 20.0, "max_rueckgang_12m_pct": 15.0, "tagesspruenge_12m": 0}
    p, gruende, warn = punkte_risiko(ind_basis(), r, stopp=104.5, cfg=cfg)  # Abstand 5 %
    assert p == 10 and warn == []
    r2 = {"vola_60t_pct": 70.0, "max_rueckgang_12m_pct": 50.0, "tagesspruenge_12m": 9}
    p2, _, warn2 = punkte_risiko(ind_basis(), r2, stopp=float("nan"), cfg=cfg)
    assert p2 == 0
    assert "hohe Volatilität" in warn2 and any("Tagessprünge" in w for w in warn2)


# --- Gesamt --------------------------------------------------------------------

def test_bewerte_aktie_aufwaertstrend(cfg):
    wellen = np.linspace(100, 150, 300) + np.sin(np.arange(300) / 4) * 2
    z = bewerte_aktie(reihe(wellen), reihe(np.linspace(1000, 1050, 300)), cfg)
    # Gewichte laut config: T 1, M 0, R 0 -> Gesamt = T, Maximum 10
    assert z["gesamt"] == z["T"] and z["max_punkte"] == 10
    assert z["gewertet"] == ["T"]
    assert z["T"] >= 6
    assert z["klasse"] in ("stark", "gemischt", "schwach")


def test_bewerte_universum_filter_und_sortierung(cfg):
    auf = reihe(np.linspace(100, 150, 300) + np.sin(np.arange(300) / 4) * 2)
    ab = reihe(np.linspace(150, 100, 300) + np.sin(np.arange(300) / 4) * 2)
    aktien = [Aktie("AB", "Ab AG", "de", "EUR", "^X"), Aktie("AUF", "Auf AG", "de", "EUR", "^X"),
              Aktie("RAUS", "Raus AG", "de", "EUR", "^X")]
    kurse = {"AB": ab, "AUF": auf, "RAUS": auf, "^X": reihe(np.linspace(1000, 1050, 300))}
    filt = pd.DataFrame({"ticker": ["AB", "AUF"], "kurs_eur": [100.0, 150.0], "marktkap_eur": [5e9, 5e9]})
    tab = bewerte_universum(aktien, kurse, filt, cfg)
    assert list(tab["ticker"]) == ["AUF", "AB"]
    assert tab["gesamt"].is_monotonic_decreasing


def test_ruecklauf_nach_bestaetigung_zaehlt_wie_in_bildung(cfg):
    m = muster()                                  # bestätigt, hoch, Ausbruchsmarke 105
    assert punkte_muster([m], cfg, kurs=108.0)[0] == 9
    p, gruende, warn, _ = punkte_muster([m], cfg, kurs=103.0)
    assert p == 5                                  # Wert "in Bildung, hoch"
    assert any("Rücklauf" in w for w in warn)
    assert m.ruecklauf(103.0) and not m.ruecklauf(108.0)
    assert not muster(status="in Bildung").ruecklauf(103.0)


def test_gesamtscore_gewichte(cfg):
    assert gesamtscore({"T": 8, "M": 9, "R": 10}, cfg) == (8, 10)
    alt = {**cfg, "gewichte": {"T": 1, "M": 1, "R": 1}}
    assert gesamtscore({"T": 8, "M": 9, "R": 10}, alt) == (27, 30)


def test_klasse_als_anteil(cfg):
    assert klasse(7, 10, cfg) == "stark" and klasse(6, 10, cfg) == "gemischt"
    assert klasse(4, 10, cfg) == "gemischt" and klasse(3, 10, cfg) == "schwach"


def test_neue_ruecksetzer_regel(cfg):
    """RSI 38 über der 200-Tage-Linie, Trend nur gemischt (unter der 50-Tage-Linie): +2."""
    ind = ind_basis(kurs=102, sma50=105, sma200=100, rsi14=38, trend="gemischt")
    t, gruende, _ = punkte_technik(ind, cfg)
    assert any("Rücksetzer im langfristigen Trend" in g for g in gruende)
    unter = ind_basis(kurs=95, sma50=105, sma200=100, rsi14=38, trend="gemischt")
    assert not any("Rücksetzer" in g for g in punkte_technik(unter, cfg)[1])


def test_sortierung_ohne_risiko(cfg, monkeypatch):
    """Gleicher Score und gleiche Technik: höhere relative Stärke (3 M) vorn, R egal."""
    feste = {"RUHIG": {"gesamt": 8, "T": 8, "R": 10, "rs_3m_pp": 1.0},
             "STARK": {"gesamt": 8, "T": 8, "R": 2, "rs_3m_pp": 6.0},
             "HOCH": {"gesamt": 9, "T": 9, "R": 0, "rs_3m_pp": -3.0}}
    monkeypatch.setattr("analyst.scoring.bewerte_aktie", lambda df, idx, c, r: dict(feste[df.attrs["t"]]))
    aktien, kurse = [], {}
    for t in feste:
        aktien.append(Aktie(t, t, "de", "EUR", "^X"))
        kurse[t] = reihe(np.linspace(100, 110, 100))
        kurse[t].attrs["t"] = t
    filt = pd.DataFrame({"ticker": list(feste), "kurs_eur": 100.0, "marktkap_eur": 5e9})
    assert list(bewerte_universum(aktien, kurse, filt, cfg)["ticker"]) == ["HOCH", "STARK", "RUHIG"]
