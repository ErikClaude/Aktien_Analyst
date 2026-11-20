"""Tests für analyst.patterns – konstruierte Kursreihen.

Die Reihen werden aus Eckpunkten (Tag, Kurs) linear verbunden. In jeder
Reihe ist das Muster sicher vorhanden bzw. sicher nicht vorhanden.
"""

import numpy as np
import pandas as pd
import pytest

from analyst.patterns import (
    MusterRegeln,
    finde_ausbruch,
    finde_doppel,
    finde_kopf_schulter,
    finde_muster,
    muster_tabelle,
)
from analyst.universe import Aktie


def reihe(close, volumen=None) -> pd.DataFrame:
    """Kurs-DataFrame an Werktagen, High = Low = Close."""
    close = np.asarray(close, dtype=float)
    vol = np.full(len(close), 1000.0) if volumen is None else np.asarray(volumen, float)
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": vol},
                        index=pd.bdate_range("2026-01-05", periods=len(close)))


def zickzack(anker: list[tuple[int, float]]) -> pd.DataFrame:
    """Verbindet Eckpunkte (Tag, Kurs) linear zu einer Tagesreihe."""
    tage, kurse = zip(*anker)
    return reihe(np.interp(np.arange(tage[-1] + 1), tage, kurse))


def gespiegelt(df: pd.DataFrame, achse: float = 200.0) -> pd.DataFrame:
    """Spiegelt eine Reihe (aus Top wird Boden), High/Low werden getauscht."""
    neu = df.copy()
    neu["Close"] = achse - df["Close"]
    neu["Open"] = achse - df["Open"]
    neu["High"], neu["Low"] = achse - df["Low"], achse - df["High"]
    return neu


# --- Doppeltop / Doppelboden -------------------------------------------------

DT_BASIS = [(0, 80), (30, 120), (45, 105), (65, 120.5)]


def test_doppeltop_bestaetigt():
    df = zickzack(DT_BASIS + [(85, 95), (95, 96)])
    m = finde_doppel(df, boden=False)
    assert (m.name, m.richtung, m.status) == ("Doppeltop", "bärisch", "bestätigt")
    assert m.ausbruchsmarke == pytest.approx(105)
    assert m.invalidierung == pytest.approx(120.5)
    assert m.messziel == pytest.approx(105 - (120.25 - 105))
    assert m.sicherheit == "mittel"        # 3 von 4: Volumen konstant
    assert m.bestaetigt_am is not None and m.gescheitert_am is None
    # mit geringerem Volumen am zweiten Top: alle 4 Kriterien -> hoch
    df.loc[df.index[60:71], "Volume"] = 500.0
    assert finde_doppel(df, boden=False).sicherheit == "hoch"


def test_kein_doppeltop_ohne_vortrend():
    """Vorher nie unter der Nackenlinie (105), kein höheres Hoch: nur der Vortrend fehlt."""
    df = zickzack([(0, 118), (20, 108), (30, 120), (45, 105), (65, 120.5), (85, 95)])
    assert finde_doppel(df, boden=False) is None


def test_kein_doppeltop_bei_hoeherem_hoch_davor():
    """Vor dem ersten Top lag ein höheres Hoch (125): die Spitzen dominieren nicht."""
    df = zickzack([(0, 80), (10, 125), (20, 95), (30, 120), (45, 105), (65, 120.5), (85, 95)])
    assert finde_doppel(df, boden=False) is None


def test_doppeltop_in_bildung():
    m = finde_doppel(zickzack(DT_BASIS + [(80, 110), (90, 112)]), boden=False)
    assert m.status == "in Bildung" and m.bestaetigt_am is None


def test_doppeltop_gescheitert():
    m = finde_doppel(zickzack(DT_BASIS + [(75, 110), (95, 130)]), boden=False)
    assert m.status == "gescheitert" and m.gescheitert_am is not None


def test_doppeltop_gescheitert_nach_bestaetigung():
    m = finde_doppel(zickzack(DT_BASIS + [(80, 100), (100, 125)]), boden=False)
    assert m.status == "gescheitert"
    assert m.bestaetigt_am is not None and m.gescheitert_am > m.bestaetigt_am


def test_kein_doppeltop_bei_ungleichen_spitzen():
    """Spitzen 120 und 130 (7,7 % Abstand > 3 %)."""
    assert finde_doppel(zickzack([(0, 80), (30, 120), (45, 105), (65, 130), (85, 95)]), boden=False) is None


def test_kein_doppeltop_bei_flachem_ruecksetzer():
    """Rücksetzer nur auf 117 (2,5 % < 5 %)."""
    assert finde_doppel(zickzack([(0, 80), (30, 120), (45, 117), (65, 120), (85, 95)]), boden=False) is None


def test_doppelboden_bestaetigt_gespiegelt():
    m = finde_doppel(gespiegelt(zickzack(DT_BASIS + [(85, 95), (95, 96)])), boden=True)
    assert (m.name, m.richtung, m.status) == ("Doppelboden", "bullisch", "bestätigt")
    assert m.ausbruchsmarke == pytest.approx(95)     # 200 − 105
    assert m.invalidierung == pytest.approx(79.5)    # 200 − 120,5


def test_kein_doppelboden_im_doppeltop():
    assert finde_doppel(zickzack(DT_BASIS + [(80, 110), (90, 112)]), boden=True) is None


# --- Kopf-Schulter -------------------------------------------------------------

SKS_BASIS = [(0, 80), (20, 110), (30, 100), (45, 120), (60, 100), (75, 110)]


def test_kopf_schulter_bestaetigt():
    m = finde_kopf_schulter(zickzack(SKS_BASIS + [(90, 90), (100, 92)]), umgekehrt=False)
    assert (m.name, m.status) == ("Kopf-Schulter", "bestätigt")
    assert m.ausbruchsmarke == pytest.approx(100)   # flache Nackenlinie
    assert m.invalidierung == pytest.approx(110)    # rechte Schulter
    assert m.messziel == pytest.approx(80)          # 100 − (120 − 100)
    assert m.sicherheit == "hoch"          # 4 von 5 (Volumen konstant)
    assert [p[2] for p in m.punkte] == ["linke Schulter", "Kopf", "rechte Schulter"]


def test_kopf_schulter_schraege_nackenlinie():
    """Nackenpunkte 100 und 104: Linie steigt, Marke am letzten Tag liegt darüber."""
    df = zickzack([(0, 80), (20, 110), (30, 100), (45, 120), (60, 104), (75, 111), (90, 108), (96, 109)])
    m = finde_kopf_schulter(df, umgekehrt=False)
    assert m.status == "in Bildung"
    erwartet = 100 + (104 - 100) / 30 * (96 - 30)
    assert m.ausbruchsmarke == pytest.approx(erwartet)
    assert "flache Nackenlinie" not in m.kriterien


def test_keine_kopf_schulter_bei_ungleichen_schultern():
    """Schultern 104 und 111: 5,8 % des Kopfes auseinander (> 5 %)."""
    df = zickzack([(0, 80), (20, 104), (30, 100), (45, 120), (60, 100), (75, 111), (90, 90)])
    assert finde_kopf_schulter(df, umgekehrt=False) is None


def test_keine_kopf_schulter_bei_zeitlicher_schieflage():
    """Linke Seite 10 Tage, rechte 40 Tage (Faktor 4 > 2)."""
    df = zickzack([(0, 80), (25, 110), (30, 100), (35, 120), (55, 100), (75, 110), (90, 90)])
    assert finde_kopf_schulter(df, umgekehrt=False) is None


def test_keine_kopf_schulter_ohne_vortrend():
    """Vorher nie unter der Nackenlinie (100), kein Hoch über dem Kopf: nur der Vortrend fehlt."""
    df = zickzack([(0, 115), (12, 104), (20, 110), (30, 100), (45, 120), (60, 100), (75, 110), (90, 90)])
    assert finde_kopf_schulter(df, umgekehrt=False) is None


def test_umgekehrte_kopf_schulter_in_bildung():
    df = zickzack([(0, 120), (20, 90), (30, 100), (45, 80), (60, 100), (75, 90), (90, 97), (100, 96)])
    m = finde_kopf_schulter(df, umgekehrt=True)
    assert (m.name, m.richtung, m.status) == ("Umgekehrte Kopf-Schulter", "bullisch", "in Bildung")
    assert m.ausbruchsmarke == pytest.approx(100)
    assert m.invalidierung == pytest.approx(90)


def test_umgekehrte_kopf_schulter_bestaetigt_gespiegelt():
    m = finde_kopf_schulter(gespiegelt(zickzack(SKS_BASIS + [(90, 90), (100, 92)])), umgekehrt=True)
    assert m.status == "bestätigt" and m.messziel == pytest.approx(120)


# --- Seitwärtsphase / Ausbruch -------------------------------------------------

def band(tage: int = 50) -> np.ndarray:
    """Seitwärtsband um 100 (± 3), mehrere volle Schwingungen."""
    return 100 + 3 * np.sin(np.arange(tage) / 3)


def test_ausbruch_nach_oben_bestaetigt():
    kurse = np.r_[band(), [108, 109, 109.5, 110]]
    vol = np.r_[np.full(50, 1000.0), [3000, 1500, 1200, 1100]]
    m = finde_ausbruch(reihe(kurse, vol))
    assert (m.name, m.status, m.richtung) == ("Ausbruch nach oben", "bestätigt", "bullisch")
    assert m.ausbruchsmarke == pytest.approx(band().max())
    assert m.invalidierung == pytest.approx((band().max() + band().min()) / 2)
    assert "hohes Volumen beim Ausbruch" in m.kriterien
    assert m.bestaetigt_am == reihe(kurse).index[50]


def test_fehlausbruch_gescheitert():
    kurse = np.r_[band(), [108, 104, 99]]
    m = finde_ausbruch(reihe(kurse))
    assert m.status == "gescheitert"


def test_ausbruch_nach_unten():
    m = finde_ausbruch(reihe(np.r_[band(), [92, 91]]))
    assert (m.name, m.richtung) == ("Ausbruch nach unten", "bärisch")


def test_seitwaertsphase_in_bildung():
    m = finde_ausbruch(reihe(band(60)))
    assert (m.name, m.status, m.richtung) == ("Seitwärtsphase", "in Bildung", "offen")
    assert m.ausbruchsmarke == pytest.approx(band(60).max())
    assert m.invalidierung == pytest.approx(band(60).min())


def test_kein_band_im_langsamen_trend():
    """Gleichmäßiger Anstieg passt in ein 10-%-Band, ist aber keine Seitwärtsphase."""
    assert finde_ausbruch(reihe(np.linspace(50, 150, 260))) is None


def test_kein_band_zu_kurz():
    """15 Tage schnelle Schwingung (genug Randwechsel), aber kürzer als 20 Tage."""
    kurse = np.r_[np.linspace(50, 90, 100), 100 + 3 * np.sin(np.arange(15) * 1.0)]
    assert finde_ausbruch(reihe(kurse)) is None
    # Gegenprobe: mit 10 Tagen Mindestlänge wird das Band erkannt
    assert finde_ausbruch(reihe(kurse), MusterRegeln(band_min_tage=10)) is not None


# --- Gesamt --------------------------------------------------------------------

def test_kein_muster_im_trend():
    assert finde_muster(reihe(np.linspace(50, 150, 260))) == []


def test_alte_muster_werden_ignoriert():
    """Doppeltop, danach 100 Tage Abwärtstrend: zweites Top > 60 Tage alt."""
    df = zickzack(DT_BASIS + [(85, 95), (185, 40)])
    assert finde_doppel(df, boden=False) is None
    locker = MusterRegeln(max_alter_tage=200, max_alter_ereignis_tage=200)
    assert finde_doppel(df, boden=False, regeln=locker) is not None


def test_ausgespielte_muster_werden_ignoriert():
    """Zweites Top jung genug (40 Tage), Bestätigung aber 26 Tage her (> 20)."""
    df = zickzack(DT_BASIS + [(80, 100), (105, 98)])
    assert finde_doppel(df, boden=False) is None
    assert finde_doppel(df, boden=False, regeln=MusterRegeln(max_alter_ereignis_tage=30)).status == "bestätigt"


def test_muster_tabelle():
    aktien = [Aktie("SKS", "SKS AG", "de", "EUR", "^X"), Aktie("TREND", "Trend AG", "de", "EUR", "^X")]
    kurse = {"SKS": zickzack(SKS_BASIS + [(90, 90), (100, 92)]), "TREND": reihe(np.linspace(50, 150, 260))}
    tab = muster_tabelle(aktien, kurse)
    assert set(tab["ticker"]) == {"SKS"}
    assert "Kopf-Schulter" in set(tab["muster"])
    assert {"status", "ausbruchsmarke", "invalidierung", "sicherheit", "datum"} <= set(tab.columns)


def test_kein_band_bei_v_form():
    """Runter und wieder hoch innerhalb von 10 %: nur 3 Randbesuche, keine Seitwärtsphase."""
    kurse = np.r_[np.linspace(80, 105, 40), np.linspace(105, 96, 15), np.linspace(96, 105, 15)]
    assert finde_ausbruch(reihe(kurse)) is None
    assert finde_ausbruch(reihe(kurse), MusterRegeln(band_min_wechsel=3)) is not None
