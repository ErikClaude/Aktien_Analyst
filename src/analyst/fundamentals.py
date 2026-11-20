"""Bewertung und Erwartungen: KGV, EV/EBITDA, Kursziele, Schätzungstrend, Zahlentermin.

Quelle: Yahoo Finance über yfinance. Yahoo begrenzt diese Abfragen streng
(HTTP 401/429). Deshalb:
  - Abruf je Ticker mit Pause, Ergebnis 7 Tage im Cache (data/meta/fundamentals/)
  - fehlende Werte bleiben leer und erscheinen als "nicht gefunden"
  - nie werden Werte geschätzt oder aufgefüllt

Punkte (je 0–10, Regeln in config/scoring.yaml):
  B (Bewertung):   KGV vs. Branchenmedian, KGV vs. eigener Median der letzten
                   Jahre, EV/EBITDA vs. Branchenmedian
  E (Erwartungen): Abstand zum Ø-Kursziel, Analystenempfehlung,
                   Trend der Gewinnschätzung (nächstes Jahr), Revisionen 30 Tage
Kurstreiber (K) vergibt Claude nach der Websuche (Befehl /top-aktien).
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from analyst.universe import PROJEKT

log = logging.getLogger(__name__)

CACHE = PROJEKT / "data" / "meta" / "fundamentals"

INFO_FELDER = [
    "trailingPE", "forwardPE", "priceToBook", "priceToSalesTrailing12Months", "enterpriseToEbitda",
    "targetMeanPrice", "targetLowPrice", "targetHighPrice", "numberOfAnalystOpinions",
    "recommendationMean", "debtToEquity", "sector", "industry", "currency", "financialCurrency",
]

FundFetcher = Callable[[str, bool], dict]


# --------------------------------------------------------------------------
# Abruf und Cache
# --------------------------------------------------------------------------

def _zahl(x) -> float:
    """Zahl oder NaN (Yahoo liefert manchmal Text wie "Infinity" oder None)."""
    try:
        x = float(x)
        return x if math.isfinite(x) else math.nan
    except (TypeError, ValueError):
        return math.nan


def yfinance_fundamentals(ticker: str, details: bool = True) -> dict:
    """Ein Ticker von Yahoo. ``details=False``: nur Basiskennzahlen (eine Abfrage).

    Rückgabe ist ein flaches dict; Fehler einzelner Teile führen zu fehlenden
    Feldern, nicht zum Abbruch. ``gesperrt=True``, wenn Yahoo gar nichts liefert.
    """
    import yfinance as yf

    tk = yf.Ticker(ticker)
    d: dict = {"ticker": ticker}
    try:
        info = tk.info or {}
    except Exception as e:  # Yahoo wirft bei Sperre unterschiedliche Fehler
        log.warning("%s: info nicht abrufbar (%s)", ticker, e)
        info = {}
    for f in INFO_FELDER:
        d[f] = info.get(f)
    d["gesperrt"] = not any(info.get(f) is not None for f in ("trailingPE", "forwardPE", "sector",
                                                                "targetMeanPrice"))
    if not details:
        return d
    try:
        trend = tk.eps_trend
        if trend is not None and "+1y" in trend.index:
            z = trend.loc["+1y"]
            d["eps_naechstes_jahr"] = _zahl(z.get("current"))
            d["eps_naechstes_jahr_vor_90t"] = _zahl(z.get("90daysAgo"))
    except Exception as e:
        log.info("%s: eps_trend fehlt (%s)", ticker, e)
    try:
        rev = tk.eps_revisions
        if rev is not None and "+1y" in rev.index:
            d["revisionen_hoch_30t"] = _zahl(rev.loc["+1y"].get("upLast30days"))
            d["revisionen_runter_30t"] = _zahl(rev.loc["+1y"].get("downLast30days"))
    except Exception as e:
        log.info("%s: eps_revisions fehlt (%s)", ticker, e)
    try:
        kal = tk.calendar or {}
        termine = kal.get("Earnings Date") or []
        if termine:
            # Achtung: Yahoo liefert hier auch den LETZTEN Termin, wenn der nächste fehlt
            d["zahlentermin"] = pd.Timestamp(min(termine)).strftime("%Y-%m-%d")
    except Exception as e:
        log.info("%s: calendar fehlt (%s)", ticker, e)
    try:
        inc = tk.income_stmt
        if inc is not None and "Diluted EPS" in inc.index:
            eps = inc.loc["Diluted EPS"].dropna()
            d["eps_historie"] = {pd.Timestamp(k).strftime("%Y-%m-%d"): float(v) for k, v in eps.items()}
    except Exception as e:
        log.info("%s: income_stmt fehlt (%s)", ticker, e)
    return d


def _cache_pfad(ordner: Path, ticker: str) -> Path:
    return ordner / f"{ticker.replace('^', 'IDX_').replace('=', '_')}.json"


def lade_fundamentals(
    tickers: list[str],
    *,
    details: bool = True,
    ordner: Path = CACHE,
    fetcher: FundFetcher = yfinance_fundamentals,
    max_alter_tage: int = 7,
    pause: float = 1.0,
    jetzt: datetime | None = None,
    nur_cache: bool = False,
) -> pd.DataFrame:
    """Fundamentaldaten je Ticker als Tabelle, aus dem Cache oder frisch.

    Gecacht wird nur, was Yahoo tatsächlich geliefert hat (nicht bei Sperre).
    Bricht nach 3 gesperrten Abfragen in Folge ab, statt Yahoo weiter zu belasten.
    Ein Basisdatensatz (``details=False``) gilt für ``details=True`` als veraltet.
    """
    ordner.mkdir(parents=True, exist_ok=True)
    jetzt = jetzt or datetime.now(timezone.utc)
    zeilen, gesperrt_in_folge = [], 0
    for t in tickers:
        pfad = _cache_pfad(ordner, t)
        alt = json.loads(pfad.read_text()) if pfad.exists() else None
        frisch = (alt is not None
                  and datetime.fromisoformat(alt["stand"]) >= jetzt - timedelta(days=max_alter_tage)
                  and (alt.get("details") or not details))
        if frisch or nur_cache or gesperrt_in_folge >= 3:
            if alt is not None:
                zeilen.append(alt)
            continue
        d = fetcher(t, details)
        if d.get("gesperrt"):
            gesperrt_in_folge += 1
            log.warning("%s: Yahoo liefert keine Fundamentaldaten (gesperrt?)", t)
            if alt is not None:
                zeilen.append(alt)
            if gesperrt_in_folge >= 3:
                log.warning("3 Sperren in Folge – Abruf abgebrochen, Rest aus dem Cache")
            continue
        gesperrt_in_folge = 0
        d.update({"stand": jetzt.isoformat(), "details": details})
        pfad.write_text(json.dumps(d, ensure_ascii=False, default=str, indent=1))
        zeilen.append(d)
        time.sleep(pause)
    return pd.DataFrame(zeilen)


# --------------------------------------------------------------------------
# Kennzahlen
# --------------------------------------------------------------------------

def kgv(z: dict) -> tuple[float, str]:
    """Aktuelles KGV (Kurs / Gewinn der letzten 12 Monate), nur positive Werte.

    Für die Punkte zählt das aktuelle KGV, weil Branchenmediane und eigene
    Historie ebenfalls auf tatsächlichen Gewinnen beruhen (gleich mit gleich).
    """
    w = _zahl(z.get("trailingPE"))
    return (w, "aktuell") if w > 0 else (math.nan, "nicht gefunden")


def kgv_erwartet(z: dict, kurs: float) -> float:
    """Erwartetes KGV = Kurs / Gewinnschätzung nächstes Jahr (selbst gerechnet).

    Yahoos Feld "forwardPE" ist bei manchen Werten unstimmig (z. B. 8306.T am
    20.11.2026: 19,9 statt 3.197 / 280,4 = 11,4) und wird daher nicht genutzt.
    """
    eps = _zahl(z.get("eps_naechstes_jahr"))
    return kurs / eps if eps > 0 and kurs > 0 else math.nan


def kgv_historie(eps_historie: dict | None, close: pd.Series | None,
                 waehrung_ok: bool = True) -> tuple[float, int]:
    """KGV-Median der letzten Geschäftsjahre: Schlusskurs am Jahresende / Gewinn je Aktie.

    Median statt Mittelwert: Ein Jahr mit sehr kleinem Gewinn (z. B. Sonder-
    abschreibung) würde den Mittelwert stark nach oben ziehen.

    Nur Jahre mit positivem Gewinn und vorhandenem Kurs. Bei abweichender
    Berichtswährung (z. B. ADRs) nicht berechenbar. Rückgabe (Schnitt, Anzahl Jahre).
    """
    if not eps_historie or close is None or close.empty or not waehrung_ok:
        return math.nan, 0
    werte = []
    for datum, eps in eps_historie.items():
        d = pd.Timestamp(datum)
        if eps is None or eps <= 0 or d < close.index[0]:
            continue
        kurs = close.asof(d)
        if not pd.isna(kurs):
            werte.append(kurs / eps)
    return (float(pd.Series(werte).median()), len(werte)) if werte else (math.nan, 0)


def markt_aus_ticker(ticker: str) -> str:
    """Markt aus dem Yahoo-Suffix: .T -> jp, .DE/.PA/.AS -> de, ohne Suffix -> us."""
    if ticker.endswith(".T"):
        return "jp"
    if ticker.endswith((".DE", ".PA", ".AS")):
        return "de"
    return "us"


def branchen_mediane(fund: pd.DataFrame, maerkte: dict[str, str], min_anzahl: int = 5) -> pd.DataFrame:
    """Median von KGV und EV/EBITDA je Branche (Sektor), nur innerhalb desselben Marktes.

    Kein Rückfall auf "alle Märkte": Bewertungsniveaus unterscheiden sich je Land
    (z. B. Japan meist niedriger als USA) – ein gemischter Median würde ganze
    Märkte systematisch günstig oder teuer erscheinen lassen.
    Unter ``min_anzahl`` Werten: kein Median. ``maerkte``: ticker -> markt.
    Rückgabe: je Ticker die Spalten kgv_branche, ev_ebitda_branche, branche_basis.
    """
    df = fund.copy()
    df["markt"] = df["ticker"].map(maerkte)
    df["_kgv"] = [kgv(z)[0] for z in df.to_dict("records")]
    ev = df["enterpriseToEbitda"] if "enterpriseToEbitda" in df else pd.Series(math.nan, index=df.index)
    df["_ev"] = pd.to_numeric(ev, errors="coerce").where(lambda s: s > 0)
    ergebnis = []
    for _, z in df.iterrows():
        if not isinstance(z.get("sector"), str):
            ergebnis.append((z["ticker"], math.nan, math.nan, "nicht gefunden"))
            continue
        gleich = df[(df["sector"] == z["sector"]) & (df["markt"] == z["markt"])]
        basis = f"{z['sector']}, Markt {z['markt']}"
        n_kgv, n_ev = gleich["_kgv"].notna().sum(), gleich["_ev"].notna().sum()
        ergebnis.append((
            z["ticker"],
            float(gleich["_kgv"].median()) if n_kgv >= min_anzahl else math.nan,
            float(gleich["_ev"].median()) if n_ev >= min_anzahl else math.nan,
            f"{basis} ({n_kgv} Werte)" if n_kgv >= min_anzahl else "zu wenige Vergleichswerte",
        ))
    return pd.DataFrame(ergebnis, columns=["ticker", "kgv_branche", "ev_ebitda_branche", "branche_basis"])


# --------------------------------------------------------------------------
# Punkte
# --------------------------------------------------------------------------

def _stufe_kleiner(wert: float, tabelle: list) -> int:
    """Erste Zeile [Grenze, Punkte] mit wert <= Grenze; NaN -> 0."""
    if pd.isna(wert):
        return 0
    for grenze, p in tabelle:
        if wert <= grenze:
            return int(p)
    return 0


def _stufe_groesser(wert: float, tabelle: list) -> int:
    """Erste Zeile [Grenze, Punkte] mit wert >= Grenze; NaN -> 0."""
    if pd.isna(wert):
        return 0
    for grenze, p in tabelle:
        if wert >= grenze:
            return int(p)
    return 0


def _f(x: float, stellen: int = 1) -> str:
    return "nicht gefunden" if pd.isna(x) else f"{x:.{stellen}f}".replace(".", ",")


def punkte_bewertung(z: dict, cfg: dict, trend: str = "") -> dict:
    """B-Punkte, Fazit (günstig/fair/teuer), Kennzahlen-Text und Warnungen.

    ``z`` enthält die Fundamentaldaten plus kgv_branche, ev_ebitda_branche,
    kgv_hist, kgv_hist_jahre (aus ``ergaenze_fundamentals``).
    """
    c = cfg["bewertung"]
    k, k_art = kgv(z)
    ev = _zahl(z.get("enterpriseToEbitda"))
    ev = ev if ev > 0 else math.nan
    r_branche = k / z["kgv_branche"] if z.get("kgv_branche", 0) > 0 else math.nan
    # Eigene Historie zählt erst ab c["min_jahre_historie"] Geschäftsjahren
    genug_jahre = z.get("kgv_hist_jahre", 0) >= c.get("min_jahre_historie", 3)
    r_hist = k / z["kgv_hist"] if z.get("kgv_hist", 0) > 0 and genug_jahre else math.nan
    r_ev = ev / z["ev_ebitda_branche"] if z.get("ev_ebitda_branche", 0) > 0 else math.nan

    teile = [
        (_stufe_kleiner(r_branche, c["kgv_vs_branche"]),
         f"KGV {k_art} {_f(k)} vs. Branche {_f(z.get('kgv_branche', math.nan))}"),
        (_stufe_kleiner(r_hist, c["kgv_vs_historie"]),
         f"KGV vs. eigener Median {_f(z.get('kgv_hist', math.nan))} "
         f"({z.get('kgv_hist_jahre', 0)} Jahre"
         + ("" if genug_jahre else f", zu kurz – zählt erst ab {c.get('min_jahre_historie', 3)}") + ")"),
        (_stufe_kleiner(r_ev, c["ev_ebitda_vs_branche"]),
         f"EV/EBITDA {_f(ev)} vs. Branche {_f(z.get('ev_ebitda_branche', math.nan))}"),
    ]
    punkte = max(0, min(10, sum(p for p, _ in teile)))
    warn = []
    if pd.isna(k):
        warn.append("kein positives KGV (Verlust oder nicht gefunden)")

    verhaeltnisse = [x for x in (r_branche, r_hist) if not pd.isna(x)]
    if not verhaeltnisse:
        fazit = "nicht bewertbar"
    elif all(x <= c["guenstig_bis"] for x in verhaeltnisse):
        fazit = "günstig"
    elif any(x >= c["teuer_ab"] for x in verhaeltnisse):
        fazit = "teuer"
    else:
        fazit = "fair"
    eps_trend = _eps_trend_pct(z)
    if fazit == "günstig" and ((not pd.isna(eps_trend) and eps_trend < 0) or trend == "Abwärtstrend"):
        warn.append("Vorsicht Value-Falle: günstig, aber "
                    + ("fallende Gewinnschätzungen" if not pd.isna(eps_trend) and eps_trend < 0
                       else "Abwärtstrend"))
    return {"B": punkte, "fazit": fazit, "gruende_B": [f"{t} (+{p})" for p, t in teile],
            "warnungen_B": warn, "kgv": k, "kgv_art": k_art, "ev_ebitda": ev}


def _eps_trend_pct(z: dict) -> float:
    """Veränderung der Gewinnschätzung fürs nächste Jahr gegenüber vor 90 Tagen in %."""
    jetzt, vorher = _zahl(z.get("eps_naechstes_jahr")), _zahl(z.get("eps_naechstes_jahr_vor_90t"))
    if pd.isna(jetzt) or pd.isna(vorher) or vorher == 0:
        return math.nan
    return (jetzt / vorher - 1) * 100 * (1 if vorher > 0 else -1)


def punkte_erwartungen(z: dict, kurs: float, cfg: dict) -> dict:
    """E-Punkte mit Begründung: Kursziel, Empfehlung, Schätzungstrend, Revisionen."""
    c = cfg["erwartungen"]
    ziel = _zahl(z.get("targetMeanPrice"))
    abstand = (ziel / kurs - 1) * 100 if ziel > 0 and kurs > 0 else math.nan
    empf = _zahl(z.get("recommendationMean"))
    trend = _eps_trend_pct(z)
    hoch, runter = _zahl(z.get("revisionen_hoch_30t")), _zahl(z.get("revisionen_runter_30t"))
    rev_p = (0 if pd.isna(hoch) or pd.isna(runter) or hoch + runter == 0
             else c["revisionen_mehr_hoch"] if hoch > runter
             else c["revisionen_ausgeglichen"] if hoch == runter else 0)
    teile = [
        (_stufe_groesser(abstand, c["kursziel_abstand_pct"]),
         f"Ø-Kursziel {_f(ziel, 2)} ({_f(abstand)} % Abstand)"),
        (_stufe_kleiner(empf, c["empfehlung"]), f"Empfehlung Ø {_f(empf)} (1 = Kaufen, 5 = Verkaufen)"),
        (_stufe_groesser(trend, c["eps_trend_pct"]), f"Gewinnschätzung nächstes Jahr {_f(trend)} % in 90 Tagen"),
        (rev_p, f"Revisionen 30 Tage: {_f(hoch, 0)} hoch / {_f(runter, 0)} runter"),
    ]
    punkte = sum(p for p, _ in teile)
    warn = []
    analysten = _zahl(z.get("numberOfAnalystOpinions"))
    if pd.isna(analysten) or analysten < c["min_analysten"]:
        punkte = min(punkte, c["max_punkte_wenige_analysten"])
        warn.append(f"wenige Analysten ({_f(analysten, 0)}) – Erwartungen wenig aussagekräftig")
    if not pd.isna(trend) and trend < -0.5:
        warn.append("fallende Gewinnschätzungen")
    return {"E": max(0, min(10, punkte)), "gruende_E": [f"{t} (+{p})" for p, t in teile],
            "warnungen_E": warn, "kursziel": ziel, "kursziel_tief": _zahl(z.get("targetLowPrice")),
            "kursziel_hoch": _zahl(z.get("targetHighPrice")), "kursziel_abstand_pct": abstand,
            "analysten": analysten, "eps_trend_pct": trend}


# --------------------------------------------------------------------------
# Zusammenführen mit der Score-Tabelle
# --------------------------------------------------------------------------

def ergaenze_fundamentals(
    tabelle: pd.DataFrame,
    fund: pd.DataFrame,
    basis: pd.DataFrame,
    kurse: dict[str, pd.DataFrame],
    cfg: dict,
    kurse_historie: dict[str, pd.DataFrame] | None = None,
    maerkte: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Fügt B und E (und Kennzahlen) zur Score-Tabelle hinzu und rechnet den Gesamtscore neu.

    ``fund``  = Details der Kandidaten, ``basis`` = Basisdaten möglichst vieler
    Aktien (für die Branchenmediane). Aktien ohne Fundamentaldaten bekommen
    B = E = 0 und den Hinweis "nicht gefunden". ``kurse_historie`` (z. B. der
    10-Jahres-Cache) liefert die Jahresendkurse für den eigenen KGV-Median.
    ``maerkte`` (ticker -> markt aus dem Universum) ordnet die Basisdaten zu;
    fehlt ein Ticker, wird der Markt aus dem Suffix abgeleitet.
    """
    from analyst.scoring import gesamtscore, klasse

    alle = pd.concat([basis, fund], ignore_index=True).drop_duplicates("ticker", keep="last")
    min_vergleich = cfg["bewertung"].get("min_vergleichswerte", 8)
    zuordnung = {t: markt_aus_ticker(t) for t in alle["ticker"]}
    zuordnung.update(maerkte or {})
    zuordnung.update(dict(zip(tabelle["ticker"], tabelle["markt"])))
    mediane = branchen_mediane(alle, zuordnung, min_vergleich)
    med = mediane.set_index("ticker")
    f = fund.set_index("ticker") if len(fund) else pd.DataFrame()
    neu = []
    for _, z in tabelle.iterrows():
        z = z.to_dict()
        hat_daten = z["ticker"] in f.index
        d = f.loc[z["ticker"]].to_dict() if hat_daten else {}
        if hat_daten and z["ticker"] in med.index:
            d.update(med.loc[z["ticker"]].to_dict())
        lang = (kurse_historie or {}).get(z["ticker"])
        close = (lang if lang is not None and len(lang) else kurse.get(z["ticker"], pd.DataFrame())).get("Close")
        gleiche_waehrung = d.get("financialCurrency") in (None, z["waehrung"]) or pd.isna(d.get("financialCurrency"))
        d["kgv_hist"], d["kgv_hist_jahre"] = kgv_historie(d.get("eps_historie"), close, gleiche_waehrung)
        if hat_daten:
            b = punkte_bewertung(d, cfg, z.get("trend", ""))
            e = punkte_erwartungen(d, z["kurs"], cfg)
        else:
            b = {"B": 0, "fazit": "nicht gefunden", "gruende_B": ["Fundamentaldaten nicht gefunden"],
                 "warnungen_B": [], "kgv": math.nan, "kgv_art": "nicht gefunden", "ev_ebitda": math.nan}
            e = {"E": 0, "gruende_E": ["Analystendaten nicht gefunden"], "warnungen_E": [],
                 "kursziel": math.nan, "kursziel_tief": math.nan, "kursziel_hoch": math.nan,
                 "kursziel_abstand_pct": math.nan, "analysten": math.nan, "eps_trend_pct": math.nan}
        z.update(b)
        z.update(e)
        z["kgv_erwartet"] = kgv_erwartet(d, z["kurs"]) if hat_daten else math.nan
        z.update({
            "K": z.get("K", 0) if not pd.isna(z.get("K", 0)) else 0,
            "kgv_branche": d.get("kgv_branche", math.nan), "ev_ebitda_branche": d.get("ev_ebitda_branche", math.nan),
            "branche_basis": d.get("branche_basis", "nicht gefunden"), "sektor_yahoo": d.get("sector") or "",
            "kgv_hist": d["kgv_hist"], "kgv_hist_jahre": d["kgv_hist_jahre"],
            "zahlentermin_yahoo": d.get("zahlentermin") or "", "fund_stand": d.get("stand") or "",
        })
        z["warnungen"] = list(z["warnungen"]) + b["warnungen_B"] + e["warnungen_E"]
        z["gesamt"], z["max_punkte"] = gesamtscore({k: z[k] for k in ("T", "M", "R", "B", "K", "E")}, cfg)
        z["klasse"] = klasse(z["gesamt"], z["max_punkte"], cfg)
        z["gewertet"] = [b_ for b_, w in cfg["gewichte"].items() if w > 0]
        neu.append(z)
    return pd.DataFrame(neu).sort_values(cfg["sortierung"], ascending=False).reset_index(drop=True)
