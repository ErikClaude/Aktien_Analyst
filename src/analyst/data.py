"""Laden und Cachen der Kursdaten, Währungsumrechnung.

Datenquelle: yfinance (Yahoo Finance, inoffiziell, Tagesdaten).

Cache-Layout:
  data/prices/<ticker>.csv      Tageskurse (Date, Open, High, Low, Close, Adj Close, Volume)
  data/prices/_stand.json       Zeitpunkt des letzten Abrufs je Ticker (UTC)
  data/meta/marktkap.csv        Marktkapitalisierung (ticker, marktkap, waehrung, stand)

Zwei Fallen von Yahoo, die hier abgefangen werden:
  1. Während der Handelszeit liefert Yahoo eine unfertige Tageszeile.
     Sie wird verworfen, bis Handelsschluss + Puffer erreicht ist.
  2. Manchmal fehlt der Schlusskurs (NaN). Solche Zeilen werden verworfen.

Der Abruf läuft über eine austauschbare Funktion (``fetcher``), damit
Tests ohne Netz laufen und später auch eine GitHub Action einspringen kann.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

from analyst.universe import PROJEKT, Aktie

log = logging.getLogger(__name__)

SPALTEN = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
DATENORDNER = PROJEKT / "data"

# Signaturen der austauschbaren Abruffunktionen
KursFetcher = Callable[[list[str], str], dict[str, pd.DataFrame]]
MarktkapFetcher = Callable[[list[str]], dict[str, tuple[float, str]]]


@dataclass(frozen=True)
class Boerse:
    """Handelszeit eines Marktes – nötig, um unfertige Tagesbalken zu erkennen."""

    zeitzone: str
    handelsschluss: time
    puffer_min: int = 30

    @classmethod
    def aus_config(cls, markt_cfg: dict, puffer_min: int = 30) -> "Boerse":
        h, m = (int(x) for x in str(markt_cfg["handelsschluss"]).split(":"))
        return cls(markt_cfg["zeitzone"], time(h, m), puffer_min)

    def schluss_mit_puffer(self, tag: date) -> datetime:
        """Zeitpunkt (mit Zeitzone), ab dem der Balken von ``tag`` als fertig gilt."""
        tz = ZoneInfo(self.zeitzone)
        return datetime.combine(tag, self.handelsschluss, tz) + timedelta(minutes=self.puffer_min)

    def letzter_fertiger_schluss(self, jetzt: datetime) -> datetime:
        """Letzter Werktag-Schluss (+Puffer), der vor ``jetzt`` liegt.

        Feiertage werden nicht berücksichtigt – an Feiertagen wird höchstens
        einmal unnötig neu geladen, das ist harmlos.
        """
        tag = jetzt.astimezone(ZoneInfo(self.zeitzone)).date()
        while True:
            if tag.weekday() < 5 and self.schluss_mit_puffer(tag) <= jetzt:
                return self.schluss_mit_puffer(tag)
            tag -= timedelta(days=1)


# --------------------------------------------------------------------------
# Bereinigung
# --------------------------------------------------------------------------

def bereinige_kurse(
    df: pd.DataFrame,
    boerse: Boerse | None,
    jetzt: datetime,
) -> pd.DataFrame:
    """Bringt Rohdaten in eine einheitliche Form und entfernt Problemzeilen.

    - Index: Datum (ohne Uhrzeit), aufsteigend, ohne Dubletten
    - Spalten: ``SPALTEN`` (fehlende Spalten werden mit NaN ergänzt)
    - Zeilen ohne Schlusskurs werden entfernt
    - Ist ``boerse`` gesetzt, wird ein unfertiger Balken von heute entfernt
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=SPALTEN, index=pd.DatetimeIndex([], name="Date"))

    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance liefert je nach Aufruf (Feld, Ticker) oder (Ticker, Feld)
        ebene = 0 if set(SPALTEN) & set(df.columns.get_level_values(0)) else 1
        df.columns = df.columns.get_level_values(ebene)
    df = df.reindex(columns=SPALTEN)

    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = pd.DatetimeIndex(idx.normalize(), name="Date")
    df = df[~df.index.duplicated(keep="last")].sort_index()

    df = df.dropna(subset=["Close"])

    if boerse is not None and len(df):
        heute = jetzt.astimezone(ZoneInfo(boerse.zeitzone)).date()
        letzter = df.index[-1].date()
        if letzter >= heute and jetzt < boerse.schluss_mit_puffer(heute):
            df = df.iloc[:-1]

    return df


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def dateiname(ticker: str) -> str:
    """Dateisicherer Name: "^GDAXI" -> "IDX_GDAXI", "EURUSD=X" -> "EURUSD_X"."""
    return ticker.replace("^", "IDX_").replace("=", "_").replace("/", "_")


class KursCache:
    """Einfacher CSV-Cache für Tageskurse mit Abrufzeitpunkt je Ticker."""

    def __init__(self, ordner: Path | str = DATENORDNER / "prices"):
        self.ordner = Path(ordner)
        self._stand_datei = self.ordner / "_stand.json"

    def lies(self, ticker: str) -> pd.DataFrame | None:
        """Gecachte Kurse oder ``None``, wenn nicht vorhanden."""
        pfad = self.ordner / f"{dateiname(ticker)}.csv"
        if not pfad.exists():
            return None
        return pd.read_csv(pfad, index_col="Date", parse_dates=["Date"])

    def schreibe(self, ticker: str, df: pd.DataFrame, abruf: datetime) -> None:
        """Speichert Kurse und merkt sich den Abrufzeitpunkt."""
        self.ordner.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.ordner / f"{dateiname(ticker)}.csv", index_label="Date")
        stand = self._lies_stand()
        stand[ticker] = abruf.astimezone(timezone.utc).isoformat()
        self._stand_datei.write_text(json.dumps(stand, indent=1, sort_keys=True))

    def abrufzeit(self, ticker: str) -> datetime | None:
        """Zeitpunkt des letzten Abrufs (UTC) oder ``None``."""
        wert = self._lies_stand().get(ticker)
        return datetime.fromisoformat(wert) if wert else None

    def _lies_stand(self) -> dict:
        if self._stand_datei.exists():
            return json.loads(self._stand_datei.read_text())
        return {}


def ist_aktuell(abruf: datetime | None, boerse: Boerse, jetzt: datetime) -> bool:
    """Cache gilt als aktuell, wenn nach dem letzten fertigen Handelsschluss abgerufen wurde."""
    return abruf is not None and abruf >= boerse.letzter_fertiger_schluss(jetzt)


# --------------------------------------------------------------------------
# Abruf (yfinance)
# --------------------------------------------------------------------------

def yfinance_kurse(tickers: list[str], zeitraum: str = "2y", block: int = 100) -> dict[str, pd.DataFrame]:
    """Lädt Tageskurse per yfinance, in Blöcken, um Yahoo zu schonen."""
    import yfinance as yf

    ergebnis: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), block):
        teil = tickers[i : i + block]
        roh = yf.download(
            teil, period=zeitraum, interval="1d", auto_adjust=False,
            group_by="ticker", threads=True, progress=False,
        )
        for t in teil:
            try:
                df = roh[t] if isinstance(roh.columns, pd.MultiIndex) else roh
            except KeyError:
                continue
            if not df.dropna(how="all").empty:
                ergebnis[t] = df.dropna(how="all")
    return ergebnis


def yfinance_marktkap(tickers: list[str]) -> dict[str, tuple[float, str]]:
    """Marktkapitalisierung in Handelswährung je Ticker (fehlende werden ausgelassen)."""
    import yfinance as yf

    ergebnis: dict[str, tuple[float, str]] = {}
    for t in tickers:
        try:
            fi = yf.Ticker(t).fast_info
            kap, wae = fi.get("marketCap"), fi.get("currency")
        except Exception as e:  # Yahoo wirft bei Einzelfehlern verschiedenste Fehler
            log.warning("Marktkap. für %s nicht gefunden: %s", t, e)
            continue
        if kap and wae:
            ergebnis[t] = (float(kap), str(wae))
    return ergebnis


# --------------------------------------------------------------------------
# Laden mit Cache
# --------------------------------------------------------------------------

def lade_kurse(
    tickers: list[str],
    boerse: Boerse | None,
    *,
    cache: KursCache | None = None,
    fetcher: KursFetcher = yfinance_kurse,
    zeitraum: str = "2y",
    jetzt: datetime | None = None,
    nur_cache: bool = False,
    erzwingen: bool = False,
) -> dict[str, pd.DataFrame]:
    """Liefert bereinigte Tageskurse je Ticker, aus dem Cache oder frisch geladen.

    ``boerse`` = Handelszeit der Ticker (``None`` z. B. für Devisen, die
    praktisch rund um die Uhr gehandelt werden: dann zählt jeder Abruf ab
    dem letzten Kalendertag als aktuell und kein Balken wird gekürzt).
    ``nur_cache=True`` lädt nichts nach (offline / Daten aus GitHub Action).
    Ticker ohne Daten fehlen im Ergebnis.
    """
    cache = cache or KursCache()
    jetzt = jetzt or datetime.now(timezone.utc)

    def aktuell(t: str) -> bool:
        abruf = cache.abrufzeit(t)
        if boerse is None:
            return abruf is not None and abruf.date() >= jetzt.date()
        return ist_aktuell(abruf, boerse, jetzt)

    zu_laden = [] if nur_cache else [t for t in tickers if erzwingen or not aktuell(t)]
    if zu_laden:
        log.info("Lade %d von %d Tickern neu", len(zu_laden), len(tickers))
        geladen = set()
        for t, roh in fetcher(zu_laden, zeitraum).items():
            df = bereinige_kurse(roh, boerse, jetzt)
            if len(df):
                cache.schreibe(t, df, jetzt)
                geladen.add(t)
        fehlend = set(zu_laden) - geladen
        if fehlend:
            log.warning("Keine neuen Kursdaten für: %s", ", ".join(sorted(fehlend)))

    ergebnis = {}
    for t in tickers:
        df = cache.lies(t)
        if df is not None and len(df):
            # auch Cache-Daten erneut prüfen (z. B. aus einer GitHub Action)
            ergebnis[t] = bereinige_kurse(df, boerse, jetzt)
    return ergebnis


def lade_markt(
    config: dict,
    aktien: list[Aktie],
    **kwargs,
) -> dict[str, pd.DataFrame]:
    """Lädt Kurse für Aktien und die Vergleichsindizes, gruppiert nach Markt."""
    puffer = config.get("puffer_nach_schluss_min", 30)
    kwargs.setdefault("zeitraum", config.get("zeitraum", "2y"))
    ergebnis: dict[str, pd.DataFrame] = {}
    for markt in sorted({a.markt for a in aktien}):
        mcfg = config["maerkte"][markt]
        tickers = [a.ticker for a in aktien if a.markt == markt] + [mcfg["index"]]
        ergebnis.update(lade_kurse(tickers, Boerse.aus_config(mcfg, puffer), **kwargs))
    return ergebnis


# --------------------------------------------------------------------------
# Währung
# --------------------------------------------------------------------------

def lade_wechselkurse(config: dict, **kwargs) -> dict[str, pd.Series]:
    """Wechselkurse als Serie "Fremdwährung je 1 EUR" (Schlusskurs), je Währung."""
    kwargs.setdefault("zeitraum", config.get("zeitraum", "2y"))
    paare = config["wechselkurse"]
    daten = lade_kurse(list(paare.values()), None, **kwargs)
    return {wae: daten[t]["Close"] for wae, t in paare.items() if t in daten}


def in_euro(kurse: pd.Series, waehrung: str, fx: dict[str, pd.Series]) -> pd.Series:
    """Rechnet eine Kursreihe in Euro um.

    Formel: Kurs_EUR = Kurs_Fremd / (Fremdwährung je 1 EUR).
    Für jeden Handelstag gilt der letzte bekannte Wechselkurs an oder vor
    diesem Datum (Feiertage der Märkte unterscheiden sich).
    """
    if waehrung == "EUR":
        return kurse.copy()
    if waehrung not in fx:
        raise KeyError(f"Kein Wechselkurs für {waehrung} geladen")
    rate = fx[waehrung].sort_index()
    rate = rate.reindex(rate.index.union(kurse.index)).ffill().reindex(kurse.index)
    return kurse / rate


# --------------------------------------------------------------------------
# Marktkapitalisierung (langsam veränderlich, daher wöchentlich)
# --------------------------------------------------------------------------

def lade_marktkap(
    tickers: list[str],
    *,
    datei: Path | str = DATENORDNER / "meta" / "marktkap.csv",
    fetcher: MarktkapFetcher = yfinance_marktkap,
    max_alter_tage: int = 7,
    jetzt: datetime | None = None,
    nur_cache: bool = False,
) -> pd.DataFrame:
    """Marktkapitalisierung je Ticker (Spalten: ticker, marktkap, waehrung, stand).

    Werte, die jünger als ``max_alter_tage`` sind, kommen aus dem Cache.
    """
    datei = Path(datei)
    jetzt = jetzt or datetime.now(timezone.utc)
    spalten = ["ticker", "marktkap", "waehrung", "stand"]
    alt = pd.read_csv(datei, parse_dates=["stand"]) if datei.exists() else pd.DataFrame(columns=spalten)
    if len(alt):
        alt["stand"] = pd.to_datetime(alt["stand"], utc=True)

    grenze = pd.Timestamp(jetzt - timedelta(days=max_alter_tage))
    frisch = set(alt.loc[alt["stand"] >= grenze, "ticker"]) if len(alt) else set()
    zu_laden = [] if nur_cache else [t for t in tickers if t not in frisch]

    if zu_laden:
        neu = pd.DataFrame(
            [(t, k, w, pd.Timestamp(jetzt)) for t, (k, w) in fetcher(zu_laden).items()],
            columns=spalten,
        )
        teile = [x for x in (alt[~alt["ticker"].isin(neu["ticker"])], neu) if len(x)]
        alt = pd.concat(teile, ignore_index=True) if teile else pd.DataFrame(columns=spalten)
        datei.parent.mkdir(parents=True, exist_ok=True)
        alt.to_csv(datei, index=False)

    return alt[alt["ticker"].isin(tickers)].reset_index(drop=True)


# --------------------------------------------------------------------------
# Tabelle für die Filter
# --------------------------------------------------------------------------

def kennzahlen_fuer_filter(
    aktien: list[Aktie],
    kurse: dict[str, pd.DataFrame],
    fx: dict[str, pd.Series],
    marktkap: pd.DataFrame,
) -> pd.DataFrame:
    """Letzter Kurs und Marktkap. in Euro je Aktie, mit Datum (Stand).

    Fehlende Werte bleiben NaN – der Filter schließt sie dann aus.
    """
    kap = marktkap.set_index("ticker") if len(marktkap) else pd.DataFrame()
    zeilen = []
    for a in aktien:
        df = kurse.get(a.ticker)
        z = {"ticker": a.ticker, "name": a.name, "markt": a.markt, "waehrung": a.waehrung,
             "datum": pd.NaT, "kurs": float("nan"), "kurs_eur": float("nan"),
             "marktkap_eur": float("nan")}
        if df is not None and len(df):
            letzter = df["Close"].iloc[[-1]]
            z["datum"] = letzter.index[0]
            z["kurs"] = float(letzter.iloc[0])
            try:
                z["kurs_eur"] = float(in_euro(letzter, a.waehrung, fx).iloc[0])
            except KeyError:
                pass
        if a.ticker in kap.index:
            wert, wae = kap.at[a.ticker, "marktkap"], kap.at[a.ticker, "waehrung"]
            if pd.notna(wert) and (wae == "EUR" or wae in fx):
                z["marktkap_eur"] = float(wert) if wae == "EUR" else float(wert) / float(fx[wae].iloc[-1])
        zeilen.append(z)
    return pd.DataFrame(zeilen)


# --------------------------------------------------------------------------
# Kommandozeile: python -m analyst.data [--markt de us jp] [--nur-cache]
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """Aktualisiert den Cache und zeigt, was nach den Filtern übrig bleibt."""
    import argparse

    from analyst.universe import lade_config, lade_universum, wende_filter_an

    p = argparse.ArgumentParser(description="Kursdaten laden und Universum filtern")
    p.add_argument("--markt", nargs="*", help="de, us, jp (Standard: alle)")
    p.add_argument("--nur-cache", action="store_true", help="nichts aus dem Netz laden")
    p.add_argument("--max-kurs", type=float, help="Preisgrenze in Euro")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = lade_config()
    aktien = lade_universum(config, args.markt)
    kurse = lade_markt(config, aktien, nur_cache=args.nur_cache)
    fx = lade_wechselkurse(config, nur_cache=args.nur_cache)
    kap = lade_marktkap([a.ticker for a in aktien], nur_cache=args.nur_cache,
                        max_alter_tage=config.get("marktkap_max_alter_tage", 7))
    tabelle = kennzahlen_fuer_filter(aktien, kurse, fx, kap)
    behalten, raus = wende_filter_an(tabelle, config["filter"], args.max_kurs)

    print(f"Universum: {len(aktien)} Aktien, Kursdaten für {sum(a.ticker in kurse for a in aktien)}")
    print(f"Nach Filtern: {len(behalten)} | ausgeschlossen: {len(raus)}")
    if len(raus):
        print(raus["grund"].value_counts().to_string())
    for markt, gruppe in behalten.groupby("markt"):
        print(f"  {markt}: {len(gruppe)} Aktien, Kursstand {gruppe['datum'].max().date()}")


if __name__ == "__main__":
    main()
