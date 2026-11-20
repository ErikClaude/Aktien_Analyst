"""Tests für analyst.backtest – konstruierte Kursreihen, kein Netz.

Wichtigster Test: kein Blick in die Zukunft. Zwei Reihen, die bis zum
Stichtag identisch sind und danach auseinanderlaufen, müssen am Stichtag
denselben Score bekommen.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from analyst.backtest import (
    Einstellungen,
    auswerten,
    backtest_markdown,
    beobachtungen,
    beobachtungen_aktie,
    gruppen_statistik,
    muster_masken,
    rang_korrelation,
    stichtage,
    urteil,
)
from analyst.scoring import bewerte_aktie, lade_scoring_config
from analyst.universe import Aktie

EINST = Einstellungen(schritt_tage=20, vorlauf_tage=260, fenster_tage=300, horizonte=(5, 20))
TAGE = 400


@pytest.fixture(scope="module")
def cfg():
    return lade_scoring_config()


def reihe(close) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
                         "Volume": np.full(len(close), 1000.0)},
                        index=pd.bdate_range("2024-01-01", periods=len(close)))


def wellen(start=50.0, ende=80.0, n=TAGE) -> np.ndarray:
    return np.linspace(start, ende, n) + np.sin(np.arange(n) / 4) * 1.5


def fx_flach(n=TAGE) -> dict:
    return {"USD": pd.Series(1.25, index=pd.bdate_range("2024-01-01", periods=n))}


def test_stichtage():
    kal = pd.bdate_range("2024-01-01", periods=TAGE)
    tage = stichtage(kal, EINST)
    assert tage[0] == kal[260] and tage[1] == kal[280] and tage[-1] == kal[380]


def test_kein_blick_in_die_zukunft(cfg):
    basis = wellen()
    zukunft_a = basis.copy()
    zukunft_b = basis.copy()
    zukunft_b[300:] *= 3                      # nach Stichtag 300 völlig anderer Verlauf
    idx = reihe(np.linspace(1000, 1100, TAGE))
    tag = [reihe(basis).index[299]]
    a = Aktie("A", "A", "us", "USD", "^IDX")
    za = beobachtungen_aktie(a, reihe(zukunft_a), idx, fx_flach(), tag, cfg, EINST)[0]
    zb = beobachtungen_aktie(a, reihe(zukunft_b), idx, fx_flach(), tag, cfg, EINST)[0]
    for s in ("T", "M", "R", "gesamt", "rsi14", "stopp", "muster_liste"):
        assert za[s] == zb[s], s
    assert za["r_20"] != zb["r_20"]           # nur die Zukunft unterscheidet sich
    # und der Score entspricht einer Bewertung auf den abgeschnittenen Daten
    direkt = bewerte_aktie(reihe(basis).iloc[:300].iloc[-300:], idx.iloc[:300], cfg)
    assert za["gesamt"] == direkt["gesamt"]


def test_renditen_und_ueberrendite(cfg):
    kurs = np.r_[wellen(n=300), 100 * 1.01 ** np.arange(1, 101)]   # ab Tag 300: +1 % je Tag
    kurs[299] = 100.0
    idx = reihe(np.full(TAGE, 1000.0))
    tag = [reihe(kurs).index[299]]
    z = beobachtungen_aktie(Aktie("A", "A", "us", "USD", "^IDX"), reihe(kurs), idx, fx_flach(), tag,
                            cfg, EINST)[0]
    assert z["r_5"] == pytest.approx((1.01 ** 5 - 1) * 100)
    assert z["idx_5"] == pytest.approx(0.0)
    assert z["ue_20"] == pytest.approx((1.01 ** 20 - 1) * 100)


def test_preisfilter_mit_umrechnung(cfg):
    """200 USD / 1,25 = 160 € -> raus; 150 USD = 120 € -> drin."""
    idx = reihe(np.full(TAGE, 1000.0))
    tag = [reihe(np.ones(TAGE)).index[299]]
    a = Aktie("A", "A", "us", "USD", "^IDX")
    assert beobachtungen_aktie(a, reihe(wellen(190, 200)), idx, fx_flach(), tag, cfg, EINST) == []
    assert len(beobachtungen_aktie(a, reihe(wellen(140, 150)), idx, fx_flach(), tag, cfg, EINST)) == 1


def test_zu_wenig_historie(cfg):
    kurz = reihe(wellen(n=200))
    tag = [kurz.index[-1]]
    assert beobachtungen_aktie(Aktie("A", "A", "us", "USD", "^IDX"), kurz, None, fx_flach(), tag,
                               cfg, EINST) == []


def test_beobachtungen_top_n(cfg):
    aktien = [Aktie(f"A{i}", f"A{i}", "us", "USD", "^GSPC") for i in range(3)]
    kurse = {"A0": reihe(wellen(50, 80)), "A1": reihe(wellen(80, 50)), "A2": reihe(wellen(60, 61)),
             "^GSPC": reihe(np.linspace(1000, 1100, TAGE))}
    einst = Einstellungen(schritt_tage=20, vorlauf_tage=260, horizonte=(5, 20), top_n=1)
    beob = beobachtungen(aktien, kurse, fx_flach(), cfg, einst)
    assert len(beob) == 3 * 7                 # 3 Aktien × 7 Stichtage
    assert beob.groupby("periode")["top"].sum().eq(1).all()
    assert (beob[beob["top"]]["ticker"] == "A0").mean() > 0.5   # Aufwärtstrend meist vorn


# --- Statistik ------------------------------------------------------------------

def handgemacht() -> pd.DataFrame:
    """4 Stichtage × 2 Aktien mit bekannten Überrenditen."""
    return pd.DataFrame({
        "periode": [0, 0, 1, 1, 2, 2, 3, 3],
        "stichtag": pd.to_datetime(["2025-01-02"] * 2 + ["2025-02-03"] * 2 + ["2025-03-03"] * 2
                                   + ["2025-04-01"] * 2),
        "ticker": ["X", "Y"] * 4, "gesamt": [20, 5] * 4, "T": [8, 2] * 4,
        "r_5": [3.0, -1.0, 1.0, 0.0, 2.0, -2.0, 4.0, 1.0],
        "ue_5": [2.0, -2.0, 1.0, -1.0, 3.0, -3.0, 2.0, 0.0],
        "stopp_5": [False, True, False, False, False, True, False, False],
    })


def test_gruppen_statistik():
    b = handgemacht()
    s = gruppen_statistik(b, b["ticker"] == "X", 5)
    # Stichtagsmittel 2, 1, 3, 2 -> Ø 2; Std 0,8165 -> t = 2 / (0,8165 / 2) = 4,90
    assert s["ue_mittel_pp"] == pytest.approx(2.0)
    assert s["t"] == pytest.approx(4.899, abs=0.001)
    assert s["trefferquote_pct"] == 100.0 and s["stichtage"] == 4
    alle = gruppen_statistik(b, pd.Series(True, index=b.index), 5)
    assert alle["ue_mittel_pp"] == pytest.approx(0.25) and alle["stopp_quote_pct"] == 25.0


def test_rang_korrelation():
    zeilen = []
    for p in range(5):
        for i in range(10):
            zeilen.append({"periode": p, "gesamt": i, "ue_5": i * 0.5 + p})
    rk = rang_korrelation(pd.DataFrame(zeilen), "gesamt", 5)
    assert rk["ic"] == pytest.approx(1.0) and rk["stichtage"] == 5


def test_muster_masken():
    b = pd.DataFrame({"muster_liste": ["Doppelboden|bestätigt|hoch;Seitwärtsphase|in Bildung|niedrig",
                                       "", "Doppelboden|in Bildung|mittel"]})
    m = muster_masken(b)
    assert list(m["Doppelboden – bestätigt"]) == [True, False, False]
    assert list(m["Doppelboden – in Bildung"]) == [False, False, True]
    assert set(m) == {"Doppelboden – bestätigt", "Doppelboden – in Bildung", "Seitwärtsphase – in Bildung"}


def test_urteil():
    def z(t_diff, stichtage=20, beob=100, gruppe="G"):
        return pd.Series({"gruppe": gruppe, "beobachtungen": beob, "stichtage": stichtage, "t_diff": t_diff})
    assert urteil(z(2.5), EINST) == "Vorsprung belastbar"
    assert urteil(z(1.0), EINST) == "kein belastbarer Unterschied"
    assert urteil(z(-3.0), EINST) == "Nachteil belastbar"
    assert urteil(z(9.0, stichtage=5), EINST) == "zu wenige Stichtage"
    assert urteil(z(float("nan"), beob=0), EINST) == "keine Fälle"
    assert urteil(z(0.0, gruppe="Alle Aktien im Filter (Messlatte)"), EINST) == "Messlatte"


def test_differenz_zur_messlatte():
    """Gruppe X liegt an jedem Stichtag genau 2 Pp. über dem Durchschnitt beider Aktien."""
    b = handgemacht()
    basis = b.groupby("periode")["ue_5"].mean()
    s = gruppen_statistik(b, b["ticker"] == "X", 5, basis)
    # X − Mittel je Stichtag: 2, 1, 3, 1 -> Ø 1,75
    assert s["diff_pp"] == pytest.approx(1.75)
    assert s["t_diff"] == pytest.approx(1.75 / (np.std([2, 1, 3, 1], ddof=1) / 2))


def test_bericht(cfg):
    aktien = [Aktie(f"A{i}", f"A{i}", "us", "USD", "^GSPC") for i in range(3)]
    kurse = {"A0": reihe(wellen(50, 80)), "A1": reihe(wellen(80, 50)), "A2": reihe(wellen(60, 61)),
             "^GSPC": reihe(np.linspace(1000, 1100, TAGE))}
    beob = beobachtungen(aktien, kurse, fx_flach(), cfg, EINST)
    md = backtest_markdown(beob, auswerten(beob, EINST), EINST, date(2026, 11, 20))
    for teil in ("# Backtest – 20.11.2026", "**Bekannte Verzerrungen**", "Survivorship Bias",
                 "Alle Aktien im Filter (Messlatte)", "## Nach Gebühren", "## Ordnet der Score"):
        assert teil in md, teil
    assert "|" not in md
