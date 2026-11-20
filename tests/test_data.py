"""Tests für analyst.data – nur feste Testdaten, Abruf über Fake-Fetcher."""

from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from analyst.data import (
    Boerse,
    KursCache,
    bereinige_kurse,
    dateiname,
    in_euro,
    ist_aktuell,
    kennzahlen_fuer_filter,
    lade_kurse,
    lade_marktkap,
)
from analyst.universe import Aktie

FIX = Path(__file__).parent / "fixtures"
XETRA = Boerse("Europe/Berlin", time(17, 30), 30)
BERLIN = ZoneInfo("Europe/Berlin")


def roh() -> pd.DataFrame:
    return pd.read_csv(FIX / "kurse_roh.csv", index_col="Date", parse_dates=["Date"])


def fx_usd() -> dict:
    return {"USD": pd.read_csv(FIX / "eurusd.csv", index_col="Date", parse_dates=["Date"])["Close"]}


# --- Bereinigung -----------------------------------------------------------

def test_bereinigung_waehrend_handelszeit():
    """Fr 20.11.2026, 15:00 Berlin: heutiger Balken unfertig -> weg; NaN-Zeile weg; Dublette zusammengefasst."""
    jetzt = datetime(2026, 11, 20, 15, 0, tzinfo=BERLIN)
    df = bereinige_kurse(roh(), XETRA, jetzt)
    assert [d.day for d in df.index] == [16, 17, 19]
    assert df.loc["2026-11-17", "Close"] == 102.5  # letzte Version der Dublette


def test_bereinigung_nach_schluss_und_puffer():
    jetzt = datetime(2026, 11, 20, 18, 5, tzinfo=BERLIN)
    df = bereinige_kurse(roh(), XETRA, jetzt)
    assert df.index[-1].day == 20


def test_bereinigung_im_puffer():
    """17:45 – Börse zu, aber Puffer (30 min) noch nicht vorbei."""
    jetzt = datetime(2026, 11, 20, 17, 45, tzinfo=BERLIN)
    assert bereinige_kurse(roh(), XETRA, jetzt).index[-1].day == 19


def test_bereinigung_ohne_boerse_behaelt_letzten_balken():
    jetzt = datetime(2026, 11, 20, 12, 0, tzinfo=BERLIN)
    assert bereinige_kurse(roh(), None, jetzt).index[-1].day == 20


def test_bereinigung_multiindex_und_leer():
    df = roh()
    df.columns = pd.MultiIndex.from_product([["SAP.DE"], df.columns])
    jetzt = datetime(2026, 11, 21, 12, 0, tzinfo=BERLIN)
    sauber = bereinige_kurse(df, XETRA, jetzt)
    assert list(sauber.columns) == ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    assert bereinige_kurse(pd.DataFrame(), XETRA, jetzt).empty


def test_tokio_zeitzone():
    """20:00 Uhr Berlin am 20.11. ist in Tokio schon der 21.11. – Balken vom 20.11. ist fertig."""
    tse = Boerse("Asia/Tokyo", time(15, 30), 30)
    jetzt = datetime(2026, 11, 20, 20, 0, tzinfo=BERLIN)
    assert bereinige_kurse(roh(), tse, jetzt).index[-1].day == 20


# --- Aktualität ------------------------------------------------------------

def test_letzter_fertiger_schluss_wochenende():
    """Sonntag -> letzter fertiger Schluss ist Freitag 18:00."""
    sonntag = datetime(2026, 11, 22, 12, 0, tzinfo=BERLIN)
    assert XETRA.letzter_fertiger_schluss(sonntag) == datetime(2026, 11, 20, 18, 0, tzinfo=BERLIN)


def test_ist_aktuell():
    jetzt = datetime(2026, 11, 20, 19, 0, tzinfo=BERLIN)
    assert ist_aktuell(datetime(2026, 11, 20, 18, 30, tzinfo=BERLIN), XETRA, jetzt)
    assert not ist_aktuell(datetime(2026, 11, 20, 16, 0, tzinfo=BERLIN), XETRA, jetzt)
    assert not ist_aktuell(None, XETRA, jetzt)


# --- Cache und Laden -------------------------------------------------------

class FakeFetcher:
    """Liefert feste Testdaten und zählt Aufrufe."""

    def __init__(self, daten: dict):
        self.daten, self.aufrufe = daten, []

    def __call__(self, tickers, zeitraum):
        self.aufrufe.append(list(tickers))
        return {t: self.daten[t] for t in tickers if t in self.daten}


def test_lade_kurse_cached(tmp_path):
    cache = KursCache(tmp_path)
    fetcher = FakeFetcher({"SAP.DE": roh()})
    jetzt = datetime(2026, 11, 20, 19, 0, tzinfo=BERLIN)

    erst = lade_kurse(["SAP.DE", "FEHLT"], XETRA, cache=cache, fetcher=fetcher, jetzt=jetzt)
    assert list(erst) == ["SAP.DE"] and len(erst["SAP.DE"]) == 4
    assert (tmp_path / "SAP.DE.csv").exists()

    # zweiter Aufruf kurz danach: SAP.DE kommt aus dem Cache, nur FEHLT wird erneut versucht
    lade_kurse(["SAP.DE", "FEHLT"], XETRA, cache=cache, fetcher=fetcher,
               jetzt=datetime(2026, 11, 20, 20, 0, tzinfo=BERLIN))
    assert fetcher.aufrufe == [["SAP.DE", "FEHLT"], ["FEHLT"]]

    # nächster Handelstag nach Schluss: neu laden
    lade_kurse(["SAP.DE"], XETRA, cache=cache, fetcher=fetcher,
               jetzt=datetime(2026, 11, 23, 18, 30, tzinfo=BERLIN))
    assert fetcher.aufrufe[-1] == ["SAP.DE"]


def test_lade_kurse_nur_cache(tmp_path):
    fetcher = FakeFetcher({"SAP.DE": roh()})
    ergebnis = lade_kurse(["SAP.DE"], XETRA, cache=KursCache(tmp_path), fetcher=fetcher, nur_cache=True)
    assert ergebnis == {} and fetcher.aufrufe == []


def test_dateiname():
    assert dateiname("^GDAXI") == "IDX_GDAXI"
    assert dateiname("EURUSD=X") == "EURUSD_X"


# --- Währung ---------------------------------------------------------------

def test_in_euro_mit_luecken():
    """19.11. hat keinen FX-Kurs -> letzter bekannter (18.11.: 1,20) gilt; 16.11. liegt vor allen FX-Daten."""
    kurse = pd.Series([110.0, 120.0, 125.0],
                      index=pd.to_datetime(["2026-11-17", "2026-11-19", "2026-11-20"]))
    eur = in_euro(kurse, "USD", fx_usd())
    assert eur.round(4).tolist() == [100.0, 100.0, 100.0]

    frueh = in_euro(pd.Series([10.0], index=pd.to_datetime(["2026-11-16"])), "USD", fx_usd())
    assert frueh.isna().all()


def test_in_euro_eur_und_unbekannt():
    s = pd.Series([1.0], index=pd.to_datetime(["2026-11-20"]))
    assert in_euro(s, "EUR", {}).iloc[0] == 1.0
    with pytest.raises(KeyError):
        in_euro(s, "GBP", {})


# --- Marktkap und Filtertabelle -------------------------------------------

def test_lade_marktkap_cache(tmp_path):
    datei = tmp_path / "kap.csv"
    aufrufe = []

    def fetcher(tickers):
        aufrufe.append(list(tickers))
        return {t: (2e9, "USD") for t in tickers if t != "OHNE"}

    t0 = datetime(2026, 11, 20, tzinfo=timezone.utc)
    kap = lade_marktkap(["INTC", "OHNE"], datei=datei, fetcher=fetcher, jetzt=t0)
    assert list(kap["ticker"]) == ["INTC"]
    lade_marktkap(["INTC"], datei=datei, fetcher=fetcher, jetzt=datetime(2026, 11, 25, tzinfo=timezone.utc))
    lade_marktkap(["INTC"], datei=datei, fetcher=fetcher, jetzt=datetime(2026, 11, 28, tzinfo=timezone.utc))
    assert aufrufe == [["INTC", "OHNE"], ["INTC"]]  # nach 8 Tagen neu


def test_kennzahlen_fuer_filter():
    aktien = [Aktie("INTC", "Intel", "us", "USD", "^GSPC"), Aktie("SAP.DE", "SAP", "de", "EUR", "^GDAXI"),
              Aktie("LEER", "Leer", "us", "USD", "^GSPC")]
    kurse = {"INTC": pd.DataFrame({"Close": [62.5]}, index=pd.to_datetime(["2026-11-20"])),
             "SAP.DE": pd.DataFrame({"Close": [180.0]}, index=pd.to_datetime(["2026-11-20"]))}
    kap = pd.DataFrame({"ticker": ["INTC", "SAP.DE"], "marktkap": [250e9, 200e9],
                        "waehrung": ["USD", "EUR"], "stand": [pd.Timestamp("2026-11-20")] * 2})
    t = kennzahlen_fuer_filter(aktien, kurse, fx_usd(), kap).set_index("ticker")
    assert t.at["INTC", "kurs_eur"] == pytest.approx(50.0)       # 62,5 / 1,25
    assert t.at["INTC", "marktkap_eur"] == pytest.approx(200e9)  # 250 Mrd / 1,25
    assert t.at["SAP.DE", "kurs_eur"] == 180.0
    assert pd.isna(t.at["LEER", "kurs_eur"]) and pd.isna(t.at["LEER", "datum"])
