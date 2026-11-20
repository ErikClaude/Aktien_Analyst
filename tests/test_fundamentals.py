"""Tests für analyst.fundamentals – feste Werte, Fake-Abruf, kein Netz."""

import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from analyst.fundamentals import (
    branchen_mediane,
    ergaenze_fundamentals,
    kgv,
    kgv_erwartet,
    kgv_historie,
    lade_fundamentals,
    punkte_bewertung,
    punkte_erwartungen,
)
from analyst.scoring import lade_scoring_config


@pytest.fixture(scope="module")
def cfg():
    return lade_scoring_config()


def fund(**werte) -> dict:
    """Fundamentaldatensatz mit sinnvollen Standardwerten."""
    d = {"ticker": "A", "trailingPE": 15.0, "enterpriseToEbitda": 8.0, "sector": "Utilities",
         "targetMeanPrice": 120.0, "targetLowPrice": 90.0, "targetHighPrice": 140.0,
         "numberOfAnalystOpinions": 12, "recommendationMean": 2.0,
         "eps_naechstes_jahr": 6.6, "eps_naechstes_jahr_vor_90t": 6.4,
         "revisionen_hoch_30t": 3, "revisionen_runter_30t": 1,
         "kgv_branche": 20.0, "ev_ebitda_branche": 10.0, "kgv_hist": 18.0, "kgv_hist_jahre": 4}
    d.update(werte)
    return d


# --- Kennzahlen --------------------------------------------------------------

def test_kgv_nur_positiv():
    assert kgv({"trailingPE": 15.0}) == (15.0, "aktuell")
    assert math.isnan(kgv({"trailingPE": -3.0})[0])
    assert math.isnan(kgv({"trailingPE": "Infinity"})[0])


def test_kgv_erwartet_selbst_gerechnet():
    """Wie 8306.T am 20.11.2026: Kurs 3.197, Schätzung 280,4 -> 11,4 (Yahoo meldete 19,9)."""
    assert kgv_erwartet({"eps_naechstes_jahr": 280.42517, "forwardPE": 19.9}, 3197.0) == pytest.approx(11.40, abs=0.01)
    assert math.isnan(kgv_erwartet({"eps_naechstes_jahr": -1.0}, 100.0))


def test_kgv_historie():
    close = pd.Series([100.0, 120.0, 90.0],
                      index=pd.to_datetime(["2023-12-29", "2024-12-31", "2025-12-31"]))
    eps = {"2023-12-31": 5.0, "2024-12-31": 6.0, "2025-12-31": -1.0, "2019-12-31": 4.0}
    schnitt, jahre = kgv_historie(eps, close)
    # 2023: 100/5 = 20 (Kurs vom 29.12.), 2024: 120/6 = 20; Verlustjahr und Jahr vor den Kursdaten fallen weg
    assert (schnitt, jahre) == (pytest.approx(20.0), 2)
    assert kgv_historie(eps, close, waehrung_ok=False) == (pytest.approx(math.nan, nan_ok=True), 0)


def test_branchen_mediane_mit_mindestanzahl():
    zeilen = [{"ticker": f"U{i}", "sector": "Utilities", "trailingPE": 10.0 + i, "enterpriseToEbitda": 8.0}
              for i in range(5)]
    zeilen += [{"ticker": "T1", "sector": "Technology", "trailingPE": 30.0, "enterpriseToEbitda": 20.0}]
    m = branchen_mediane(pd.DataFrame(zeilen), {z["ticker"]: "us" for z in zeilen}).set_index("ticker")
    assert m.at["U0", "kgv_branche"] == 12.0                  # Median aus 10..14
    assert math.isnan(m.at["T1", "kgv_branche"])              # nur 1 Wert < 5
    assert m.at["T1", "branche_basis"] == "zu wenige Vergleichswerte"


def test_branchen_mediane_kein_rueckfall_auf_andere_maerkte():
    """5 japanische Banken (KGV 10) und 3 deutsche (KGV 20): jeder Markt nur mit sich selbst."""
    zeilen = [{"ticker": f"J{i}", "sector": "Banks", "trailingPE": 10.0} for i in range(5)]
    zeilen += [{"ticker": f"D{i}", "sector": "Banks", "trailingPE": 20.0} for i in range(3)]
    maerkte = {**{f"J{i}": "jp" for i in range(5)}, **{f"D{i}": "de" for i in range(3)}}
    m = branchen_mediane(pd.DataFrame(zeilen), maerkte).set_index("ticker")
    assert m.at["J0", "kgv_branche"] == 10.0 and "Markt jp" in m.at["J0", "branche_basis"]
    assert math.isnan(m.at["D0", "kgv_branche"])        # nur 3 deutsche Werte, kein Mischen


# --- Punkte ------------------------------------------------------------------

def test_bewertung_guenstig(cfg):
    b = punkte_bewertung(fund(), cfg)
    # KGV/Branche 0,75 -> 3; KGV/Historie 0,83 -> 2; EV/EBITDA 0,8 -> 2
    assert b["B"] == 7 and b["fazit"] == "günstig" and b["warnungen_B"] == []
    # KGV/Historie 0,94 liegt über 0,9 -> nur "fair"
    assert punkte_bewertung(fund(kgv_hist=16.0), cfg)["fazit"] == "fair"


def test_bewertung_teuer_und_ohne_kgv(cfg):
    assert punkte_bewertung(fund(trailingPE=30.0), cfg)["fazit"] == "teuer"
    ohne = punkte_bewertung(fund(trailingPE=None), cfg)
    assert ohne["fazit"] == "nicht bewertbar" and "kein positives KGV" in ohne["warnungen_B"][0]


def test_value_falle(cfg):
    fallend = punkte_bewertung(fund(eps_naechstes_jahr=6.0, eps_naechstes_jahr_vor_90t=6.6), cfg)
    assert any("Value-Falle" in w and "fallende" in w for w in fallend["warnungen_B"])
    abwaerts = punkte_bewertung(fund(), cfg, trend="Abwärtstrend")
    assert any("Value-Falle" in w and "Abwärtstrend" in w for w in abwaerts["warnungen_B"])


def test_erwartungen(cfg):
    e = punkte_erwartungen(fund(), kurs=100.0, cfg=cfg)
    # Kursziel +20 % -> 2; Empfehlung 2,0 -> 1; Schätzung +3,1 % -> 3; Revisionen 3:1 -> 2
    assert e["E"] == 8 and e["kursziel_abstand_pct"] == pytest.approx(20.0)


def test_erwartungen_wenige_analysten(cfg):
    e = punkte_erwartungen(fund(numberOfAnalystOpinions=2), kurs=100.0, cfg=cfg)
    assert e["E"] == 3 and any("wenige Analysten" in w for w in e["warnungen_E"])


def test_erwartungen_fehlende_daten(cfg):
    e = punkte_erwartungen({"ticker": "X"}, kurs=100.0, cfg=cfg)
    assert e["E"] == 0 and math.isnan(e["kursziel"])


# --- Abruf und Cache -----------------------------------------------------------

def test_lade_fundamentals_cache_und_sperre(tmp_path):
    aufrufe = []

    def fetcher(t, details):
        aufrufe.append(t)
        if t.startswith("GESPERRT"):
            return {"ticker": t, "gesperrt": True}
        return {"ticker": t, "trailingPE": 10.0, "gesperrt": False}

    jetzt = datetime(2026, 11, 20, tzinfo=timezone.utc)
    df = lade_fundamentals(["A", "B"], ordner=tmp_path, fetcher=fetcher, pause=0, jetzt=jetzt)
    assert list(df["ticker"]) == ["A", "B"]
    lade_fundamentals(["A"], ordner=tmp_path, fetcher=fetcher, pause=0, jetzt=jetzt)
    assert aufrufe == ["A", "B"]                                   # A kam aus dem Cache
    # nach 3 Sperren in Folge wird nicht weiter abgefragt
    namen = [f"GESPERRT{i}" for i in range(5)]
    lade_fundamentals(namen, ordner=tmp_path, fetcher=fetcher, pause=0, jetzt=jetzt)
    assert aufrufe[2:] == namen[:3]
    assert not (tmp_path / "GESPERRT0.json").exists()              # Sperre wird nicht gecacht


def test_basis_cache_reicht_nicht_fuer_details(tmp_path):
    aufrufe = []

    def fetcher(t, details):
        aufrufe.append(details)
        return {"ticker": t, "trailingPE": 10.0, "gesperrt": False}

    jetzt = datetime(2026, 11, 20, tzinfo=timezone.utc)
    lade_fundamentals(["A"], details=False, ordner=tmp_path, fetcher=fetcher, pause=0, jetzt=jetzt)
    lade_fundamentals(["A"], details=True, ordner=tmp_path, fetcher=fetcher, pause=0, jetzt=jetzt)
    assert aufrufe == [False, True]
    assert json.loads((tmp_path / "A.json").read_text())["details"] is True


# --- Zusammenführen ------------------------------------------------------------

def test_ergaenze_fundamentals(cfg):
    tab = pd.DataFrame([
        {"ticker": "A", "markt": "us", "waehrung": "USD", "kurs": 100.0, "trend": "Aufwärtstrend",
         "warnungen": [], "T": 6, "M": 0, "R": 5, "rs_3m_pp": 1.0},
        {"ticker": "OHNE", "markt": "us", "waehrung": "USD", "kurs": 50.0, "trend": "gemischt",
         "warnungen": [], "T": 7, "M": 0, "R": 5, "rs_3m_pp": 2.0},
    ])
    peers = pd.DataFrame([{"ticker": f"P{i}", "sector": "Utilities", "trailingPE": 20.0,
                           "enterpriseToEbitda": 10.0} for i in range(8)])
    details = pd.DataFrame([{k: v for k, v in fund().items() if k not in ("kgv_branche", "ev_ebitda_branche",
                                                                          "kgv_hist", "kgv_hist_jahre")}])
    kurse = {"A": pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime(["2026-11-19"]))}
    erg = ergaenze_fundamentals(tab, details, peers, kurse, cfg).set_index("ticker")
    assert erg.at["A", "B"] > 0 and erg.at["A", "E"] > 0
    assert erg.at["A", "kgv_branche"] == pytest.approx(20.0)
    assert erg.at["OHNE", "B"] == 0 and erg.at["OHNE", "fazit"] == "nicht gefunden"
    assert erg.at["A", "max_punkte"] == 40                         # T, B, K, E je 10
    assert erg.at["A", "gesamt"] == erg.at["A", "T"] + erg.at["A", "B"] + erg.at["A", "E"]
    assert list(erg.index) == ["A", "OHNE"]                        # A hat jetzt mehr Punkte


def test_zu_kurze_historie_zaehlt_nicht(cfg):
    kurz = punkte_bewertung(fund(kgv_hist=30.0, kgv_hist_jahre=2), cfg)
    lang = punkte_bewertung(fund(kgv_hist=30.0, kgv_hist_jahre=3), cfg)
    assert lang["B"] - kurz["B"] == 3                 # KGV/Historie 0,5 -> 3 Punkte nur mit 3 Jahren
    assert any("zu kurz" in g for g in kurz["gruende_B"])


def test_ergaenze_braucht_genug_vergleichswerte(cfg):
    tab = pd.DataFrame([{"ticker": "A", "markt": "us", "waehrung": "USD", "kurs": 100.0, "trend": "gemischt",
                         "warnungen": [], "T": 5, "M": 0, "R": 5, "rs_3m_pp": 0.0}])
    peers = pd.DataFrame([{"ticker": f"P{i}", "sector": "Utilities", "trailingPE": 20.0} for i in range(6)])
    details = pd.DataFrame([{"ticker": "A", "sector": "Utilities", "trailingPE": 15.0}])
    erg = ergaenze_fundamentals(tab, details, peers, {}, cfg).iloc[0]
    assert math.isnan(erg["kgv_branche"]) and erg["branche_basis"] == "zu wenige Vergleichswerte"  # 7 < 8


def test_historie_aus_langem_cache(cfg):
    """Jahresendkurse kommen aus kurse_historie, wenn vorhanden (hier 4 statt 1 Jahr)."""
    tab = pd.DataFrame([{"ticker": "A", "markt": "us", "waehrung": "USD", "kurs": 100.0, "trend": "gemischt",
                         "warnungen": [], "T": 5, "M": 0, "R": 5, "rs_3m_pp": 0.0}])
    eps = {f"{j}-12-31": 5.0 for j in (2022, 2023, 2024, 2025)}
    details = pd.DataFrame([{"ticker": "A", "trailingPE": 20.0, "eps_historie": eps}])
    tage = pd.to_datetime([f"{j}-12-31" for j in (2022, 2023, 2024, 2025)])
    kurz = {"A": pd.DataFrame({"Close": [100.0]}, index=tage[-1:])}
    lang = {"A": pd.DataFrame({"Close": [100.0] * 4}, index=tage)}
    assert ergaenze_fundamentals(tab, details, pd.DataFrame(), kurz, cfg).iloc[0]["kgv_hist_jahre"] == 1
    erg = ergaenze_fundamentals(tab, details, pd.DataFrame(), kurz, cfg, kurse_historie=lang).iloc[0]
    assert erg["kgv_hist_jahre"] == 4 and erg["kgv_hist"] == pytest.approx(20.0)


def test_markt_aus_ticker():
    from analyst.fundamentals import markt_aus_ticker
    assert [markt_aus_ticker(t) for t in ("8306.T", "SAP.DE", "AIR.PA", "INTC")] == ["jp", "de", "de", "us"]


def test_kgv_historie_median_gegen_ausreisser():
    close = pd.Series([100.0] * 4, index=pd.to_datetime([f"{j}-12-31" for j in (2022, 2023, 2024, 2025)]))
    eps = {"2022-12-31": 5.0, "2023-12-31": 5.0, "2024-12-31": 0.5, "2025-12-31": 5.0}  # Ausreißerjahr KGV 200
    assert kgv_historie(eps, close) == (pytest.approx(20.0), 4)
