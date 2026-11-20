"""Tests für analyst.universe – nur feste Testdaten, kein Netz."""

from pathlib import Path

import pandas as pd
import pytest

from analyst.universe import (
    jp_code_zu_yahoo,
    lade_config,
    lade_universum,
    us_symbol_zu_yahoo,
    wende_filter_an,
)

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def config():
    return lade_config(FIX / "universe_test.yaml")


def test_universum_alle_maerkte(config):
    aktien = lade_universum(config, basis=FIX)
    tickers = [a.ticker for a in aktien]
    # AIR.PA ausgeschlossen, ASML.AS als Zusatz, SAP.DE nicht doppelt
    assert sorted(tickers) == ["8306.T", "ASML.AS", "BRK-B", "INTC", "SAP.DE"]
    sap = next(a for a in aktien if a.ticker == "SAP.DE")
    assert sap.name == "SAP"  # Indexliste hat Vorrang vor Zusatz
    assert (sap.waehrung, sap.index) == ("EUR", "^GDAXI")


def test_universum_ein_markt(config):
    aktien = lade_universum(config, ["jp"], basis=FIX)
    assert [(a.ticker, a.waehrung, a.index) for a in aktien] == [("8306.T", "JPY", "^N225")]


def test_universum_unbekannter_markt(config):
    with pytest.raises(ValueError):
        lade_universum(config, ["fr"], basis=FIX)


def test_filter(config):
    tabelle = pd.DataFrame({
        "ticker":       ["OK", "TEUER", "GRENZE", "GROSS_UNTER_1", "KLEIN", "OHNEKAP", "OHNEKURS"],
        "kurs_eur":     [50.0, 300.0,   150.0,    0.5,             20.0,    20.0,      float("nan")],
        "marktkap_eur": [5e9,  5e9,     5e9,      5e9,             5e8,     float("nan"), 5e9],
    })
    behalten, raus = wende_filter_an(tabelle, config["filter"])
    # Kurs unter 1 €, aber groß (>= 1 Mrd. €): nur die Marktkap. zählt -> bleibt drin
    assert list(behalten["ticker"]) == ["OK", "GROSS_UNTER_1"]
    gruende = dict(zip(raus["ticker"], raus["grund"]))
    assert gruende["TEUER"].startswith("Kurs ab 150")
    assert gruende["GRENZE"].startswith("Kurs ab 150")  # Regel: unter 150 €
    assert "unter 1 Mrd" in gruende["KLEIN"]
    assert "nicht gefunden" in gruende["OHNEKAP"]
    assert "nicht gefunden" in gruende["OHNEKURS"]


def test_filter_penny_mit_hoeherer_ausnahmeschwelle(config):
    """Schwelle 10 Mrd. €: unter 1 € mit 5 Mrd. € fliegt raus, mit 50 Mrd. € bleibt drin."""
    cfg = {**config["filter"], "penny_ausnahme_ab_marktkap_eur": 1e10}
    tabelle = pd.DataFrame({"ticker": ["MITTEL", "RIESE"], "kurs_eur": [0.5, 0.5],
                            "marktkap_eur": [5e9, 5e10]})
    behalten, raus = wende_filter_an(tabelle, cfg)
    assert list(behalten["ticker"]) == ["RIESE"]
    assert "Penny" in raus.at[0, "grund"]


def test_filter_preisgrenze_ueberschreiben(config):
    tabelle = pd.DataFrame({"ticker": ["A"], "kurs_eur": [80.0], "marktkap_eur": [5e9]})
    behalten, _ = wende_filter_an(tabelle, config["filter"], max_kurs_eur=50)
    assert behalten.empty


def test_filter_leere_tabelle(config):
    leer = pd.DataFrame(columns=["ticker", "kurs_eur", "marktkap_eur"])
    behalten, raus = wende_filter_an(leer, config["filter"])
    assert behalten.empty and raus.empty


def test_symbol_umwandlung():
    assert us_symbol_zu_yahoo("BRK.B") == "BRK-B"
    assert jp_code_zu_yahoo("8306") == "8306.T"
    assert jp_code_zu_yahoo("285a") == "285A.T"
    with pytest.raises(ValueError):
        jp_code_zu_yahoo("12345")


def test_englische_namen(config):
    mufg = lade_universum(config, ["jp"], basis=FIX)[0]
    assert mufg.name == "Mitsubishi UFJ Financial Group, Inc."
    assert mufg.name_lokal == "MUFG"
    sap = next(a for a in lade_universum(config, ["de"], basis=FIX) if a.ticker == "SAP.DE")
    assert sap.name_lokal == ""  # ohne Namensdatei bleibt alles wie gehabt
