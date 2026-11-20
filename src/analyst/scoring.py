"""Signal-Score je Aktie.

Ausbaustufe 1: Technik (T), Muster (M) und Risiko (R), je 0–10 -> 0–30.
Bewertung, Kurstreiber und Erwartungen folgen mit ``fundamentals`` und der
Websuche. Alle Punkte und Schwellen stehen in ``config/scoring.yaml``.

Der Score fasst Indizien für eine Long-Idee (Kauf) zusammen. Er ist keine
Prognose. Jeder Punkt wird mit einem Grund protokolliert, damit der Bericht
ihn nachvollziehbar machen kann.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from analyst.indicators import berechne_indikatoren
from analyst.patterns import Muster, MusterRegeln, finde_muster
from analyst.universe import PROJEKT

STANDARD_CONFIG = PROJEKT / "config" / "scoring.yaml"


def lade_scoring_config(pfad: Path | str = STANDARD_CONFIG) -> dict:
    """Liest Gewichte und Schwellen (YAML)."""
    with open(pfad, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _fmt(x: float, stellen: int = 1) -> str:
    """Zahl im deutschen Format, z. B. 12,3."""
    return f"{x:.{stellen}f}".replace(".", ",")


def stufe(wert: float, tabelle: list) -> int:
    """Punkte aus einer Stufentabelle [[Grenze, Punkte], ...].

    Die erste Zeile mit ``wert <= Grenze`` zählt; sonst 0. NaN -> 0
    (fehlender Wert bringt keine Punkte).
    """
    if wert is None or (isinstance(wert, float) and math.isnan(wert)):
        return 0
    for grenze, punkte in tabelle:
        if wert <= grenze:
            return int(punkte)
    return 0


def _begrenzt(x: float) -> int:
    """Auf 0–10 begrenzen."""
    return int(max(0, min(10, round(x))))


# --------------------------------------------------------------------------
# Risiko-Kennzahlen aus Kursdaten
# --------------------------------------------------------------------------

def risiko_kennzahlen(df: pd.DataFrame, sprung_pct: float = 8.0) -> dict:
    """Volatilität, größter Rückgang und Tagessprünge.

    vola_60t_pct          = Standardabw. der log. Tagesrenditen (60 Tage) · √252 · 100
    max_rueckgang_12m_pct = größter Rückgang vom laufenden Hoch (252 Tage), positiv in %
    tagesspruenge_12m     = Tage mit |Tagesbewegung| > ``sprung_pct`` (Hinweis auf
                            Zahlen, Studien, Urteile – binäre Ereignisse)
    """
    close = df["Close"].iloc[-252:]
    log_ret = np.log(close).diff().dropna()
    vola = float(log_ret.iloc[-60:].std() * math.sqrt(252) * 100) if len(log_ret) >= 20 else math.nan
    rueckgang = float(((close / close.cummax()) - 1).min() * -100) if len(close) else math.nan
    spruenge = int((log_ret.abs() > math.log(1 + sprung_pct / 100)).sum())
    return {"vola_60t_pct": vola, "max_rueckgang_12m_pct": rueckgang, "tagesspruenge_12m": spruenge}


# --------------------------------------------------------------------------
# Teil-Scores
# --------------------------------------------------------------------------

def punkte_technik(ind: dict, cfg: dict) -> tuple[int, list[str], list[str]]:
    """Technik-Punkte (0–10) mit Begründungen und Warnungen."""
    c = cfg["technik"]
    summe, gruende, warn = 0, [], []

    def plus(bedingung: bool, schluessel: str, text: str) -> None:
        nonlocal summe
        if bedingung:
            summe += c[schluessel]
            gruende.append(f"{text} ({c[schluessel]:+d})")

    kurs, s50, s200 = ind["kurs"], ind["sma50"], ind["sma200"]
    rsi = ind["rsi14"]
    aufwaerts = ind["trend"] == "Aufwärtstrend"
    plus(kurs > s200, "kurs_ueber_sma200", "Kurs über 200-Tage-Linie")
    plus(kurs > s50, "kurs_ueber_sma50", "Kurs über 50-Tage-Linie")
    plus(s50 > s200, "sma50_ueber_sma200", "50- über 200-Tage-Linie")
    plus(ind["sma200_steigung_20t_pct"] > 0, "sma200_steigend", "200-Tage-Linie steigt")
    plus(ind["kreuzung"] == "Golden Cross", "golden_cross_frisch", "frisches Golden Cross")
    plus(ind["kreuzung"] == "Death Cross", "death_cross_frisch", "frisches Death Cross")
    plus(ind["rs_3m_pp"] > 0, "rs_3m_positiv", "stärker als Index (3 Monate)")
    plus(ind["rs_12m_pp"] > 0, "rs_12m_positiv", "stärker als Index (12 Monate)")
    plus(rsi < c["rsi_ueberverkauft"] and aufwaerts, "ruecksetzer_im_aufwaertstrend",
         "überverkaufter Rücksetzer im Aufwärtstrend")
    plus(rsi > c["rsi_ueberkauft"], "ueberkauft", "RSI überkauft")
    plus(aufwaerts and ind["abstand_hoch_pct"] >= -c["nahe_hoch_pct"], "nahe_52w_hoch_im_trend",
         "nahe 52-Wochen-Hoch im Aufwärtstrend")

    if rsi < c["rsi_ueberverkauft"] and ind["trend"] == "Abwärtstrend":
        warn.append("überverkaufter RSI im Abwärtstrend: Warnzeichen, kein Kaufsignal")
    if ind["kreuzung"] == "Death Cross":
        warn.append("frisches Death Cross")
    if ind["trend"] == "nicht gefunden":
        warn.append("Trend nicht bestimmbar (Historie zu kurz)")
    return _begrenzt(summe), gruende, warn


def punkte_muster(muster: list[Muster], cfg: dict, kurs: float | None = None,
                  ) -> tuple[int, list[str], list[str], Muster | None]:
    """Muster-Punkte (0–10): bestes bullisches Muster minus Abzug für bärische.

    Rücklauf: Ist ein bullisches Muster bestätigt, der Kurs aber wieder unter
    der Ausbruchsmarke, zählt es nur wie "in Bildung" (mit Warnung).

    Gibt (Punkte, Gründe, Warnungen, maßgebliches Muster) zurück. Maßgeblich
    ist das bullische Muster mit den meisten Punkten, sonst eine
    Seitwärtsphase, sonst das erste bärische Muster (für den Bericht).
    """
    c = cfg["muster"]
    bestes, bester_wert, gruende, warn = None, 0, [], []
    for m in muster:
        if m.richtung == "bullisch":
            status = "in Bildung" if kurs is not None and m.ruecklauf(kurs) else m.status
            wert = c["bullisch"].get(status, {}).get(m.sicherheit, 0)
        elif m.richtung == "offen":
            wert = c["seitwaertsphase"].get(m.status, {}).get(m.sicherheit, 0)
        else:
            continue
        if bestes is None or wert > bester_wert:
            bestes, bester_wert = m, wert
    if bestes is not None and bester_wert > 0:
        zusatz = ", Kurs wieder unter der Marke" if kurs is not None and bestes.ruecklauf(kurs) else ""
        gruende.append(f"{bestes.name}, {bestes.status}{zusatz}, Sicherheit {bestes.sicherheit} "
                       f"(+{bester_wert})")
        if zusatz:
            warn.append(f"Rücklauf: Kurs nach Bestätigung wieder unter der Marke ({bestes.name})")

    abzug = 0
    for m in muster:
        if m.richtung == "bärisch" and m.status in c["abzug_baerisch"]:
            a = c["abzug_baerisch"][m.status]
            abzug = max(abzug, a)
            warn.append(f"bärisches Muster: {m.name} ({m.status})")
    if abzug:
        gruende.append(f"Abzug für bärisches Muster (−{abzug})")
    if bestes is None:
        bestes = next((m for m in muster if m.richtung == "bärisch" and m.status != "gescheitert"), None)
    return _begrenzt(bester_wert - abzug), gruende, warn, bestes


def stopp_marke(ind: dict, muster: Muster | None) -> tuple[float, str]:
    """Marke, an der die Idee falsch wäre (für Risiko und Bericht).

    Bullisches Muster: dessen Invalidierung. Sonst die nächste Unterstützung.
    """
    if muster is not None and muster.richtung in ("bullisch", "offen") and muster.status != "gescheitert":
        return muster.invalidierung, f"Invalidierung {muster.name}"
    if not math.isnan(ind.get("unterstuetzung", math.nan)):
        return ind["unterstuetzung"], "nächste Unterstützung"
    return math.nan, "nicht gefunden"


def punkte_risiko(ind: dict, risiko: dict, stopp: float, cfg: dict) -> tuple[int, list[str], list[str]]:
    """Risiko-Punkte (0–10, 10 = geringes Risiko) aus Kursdaten."""
    c = cfg["risiko"]
    kurs = ind["kurs"]
    abstand = (kurs - stopp) / kurs * 100 if not math.isnan(stopp) and stopp < kurs else math.nan
    teile = [
        (stufe(risiko["vola_60t_pct"], c["volatilitaet_60t"]),
         f"Volatilität {_fmt(risiko['vola_60t_pct'], 0)} % p. a."),
        (stufe(risiko["max_rueckgang_12m_pct"], c["max_rueckgang_12m"]),
         f"größter Rückgang 12 Monate {_fmt(risiko['max_rueckgang_12m_pct'], 0)} %"),
        (stufe(risiko["tagesspruenge_12m"], c["tagesspruenge_12m"]),
         f"{risiko['tagesspruenge_12m']} Tagessprünge > {c['sprung_pct']} % in 12 Monaten"),
        (stufe(abstand, c["stopp_abstand"]),
         f"Abstand zur Invalidierung {_fmt(abstand)} %" if not math.isnan(abstand)
         else "Abstand zur Invalidierung nicht gefunden"),
    ]
    gruende = [f"{text} (+{p})" for p, text in teile]
    warn = []
    if risiko["vola_60t_pct"] > c["volatilitaet_60t"][-1][0]:
        warn.append("hohe Volatilität")
    if risiko["tagesspruenge_12m"] > c["tagesspruenge_12m"][-1][0]:
        warn.append("häufige große Tagessprünge (binäre Ereignisse?)")
    return _begrenzt(sum(p for p, _ in teile)), gruende, warn


def klasse(gesamt: int, max_punkte: int, cfg: dict) -> str:
    """Einstufung: "stark", "gemischt" oder "schwach"."""
    k = cfg["klassen"][max_punkte]
    if gesamt >= k["stark_ab"]:
        return "stark"
    if gesamt >= k["gemischt_ab"]:
        return "gemischt"
    return "schwach"


# --------------------------------------------------------------------------
# Gesamt
# --------------------------------------------------------------------------

def bewerte_aktie(
    df: pd.DataFrame,
    index_df: pd.DataFrame | None,
    cfg: dict,
    regeln: MusterRegeln = MusterRegeln(),
) -> dict:
    """Indikatoren, Muster und Score für eine Aktie."""
    ind = berechne_indikatoren(df, index_df)
    muster = finde_muster(df, regeln)
    risiko = risiko_kennzahlen(df, cfg["risiko"]["sprung_pct"])
    t, t_gr, t_w = punkte_technik(ind, cfg)
    m, m_gr, m_w, haupt = punkte_muster(muster, cfg, ind["kurs"])
    stopp, stopp_art = stopp_marke(ind, haupt)
    r, r_gr, r_w = punkte_risiko(ind, risiko, stopp, cfg)
    max_punkte = 10 * len(cfg["bereiche"])
    gesamt = t + m + r
    return {
        **ind, **risiko,
        "T": t, "M": m, "R": r, "gesamt": gesamt, "max_punkte": max_punkte,
        "klasse": klasse(gesamt, max_punkte, cfg),
        "gruende_T": t_gr, "gruende_M": m_gr, "gruende_R": r_gr,
        "warnungen": t_w + m_w + r_w,
        "stopp": stopp, "stopp_art": stopp_art,
        "haupt_muster": haupt, "alle_muster": muster,
    }


def bewerte_universum(
    aktien: list,
    kurse: dict[str, pd.DataFrame],
    filtertabelle: pd.DataFrame,
    cfg: dict,
    regeln: MusterRegeln = MusterRegeln(),
    min_tage: int = 60,
) -> pd.DataFrame:
    """Score für alle Aktien, die den Filter passiert haben, absteigend sortiert.

    ``filtertabelle`` = ``behalten`` aus ``universe.wende_filter_an``
    (liefert kurs_eur, waehrung, marktkap_eur).
    """
    erlaubt = filtertabelle.set_index("ticker")
    zeilen = []
    for a in aktien:
        df = kurse.get(a.ticker)
        if a.ticker not in erlaubt.index or df is None or len(df) < min_tage:
            continue
        z = erlaubt.loc[a.ticker]
        zeilen.append({
            "ticker": a.ticker, "name": a.name, "markt": a.markt, "waehrung": a.waehrung,
            "index": a.index, "kurs_eur": z["kurs_eur"], "marktkap_eur": z["marktkap_eur"],
            **bewerte_aktie(df, kurse.get(a.index), cfg, regeln),
        })
    if not zeilen:
        return pd.DataFrame()
    return (pd.DataFrame(zeilen)
            .sort_values(["gesamt", "T", "R"], ascending=False)
            .reset_index(drop=True))
