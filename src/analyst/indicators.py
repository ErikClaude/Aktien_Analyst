"""Technische Indikatoren aus Tageskursen.

Alle Funktionen erwarten bereinigte Kurse aus ``analyst.data``
(DataFrame mit Datumsindex und den Spalten Open, High, Low, Close, Volume).

Grundsätze:
  - Gerechnet wird mit ``Close`` (splitbereinigt, nicht dividendenbereinigt),
    also mit dem Kurs, den man auch im Chart sieht.
  - Reicht die Historie nicht (z. B. < 200 Tage für die 200-Tage-Linie),
    ist der Wert NaN – es wird nichts geschätzt.
  - Jede Momentaufnahme trägt das Datum des letzten Kurses (``datum``).

Hinweis relative Stärke: Der DAX ist ein Performanceindex (Dividenden
reinvestiert), S&P 500 und Nikkei 225 sind Kursindizes. Gegen den DAX
schneiden deutsche Aktien dadurch über 12 Monate um grob die
Dividendenrendite schlechter ab.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

HANDELSTAGE_JAHR = 252  # ca. Handelstage in 52 Wochen


# --------------------------------------------------------------------------
# RSI
# --------------------------------------------------------------------------

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Relative Strength Index nach Wilder.

    Formel:
      Änderung_t  = Close_t − Close_(t−1)
      Gewinn_t    = max(Änderung_t, 0),  Verlust_t = max(−Änderung_t, 0)
      Startwert:  Ø-Gewinn und Ø-Verlust = einfacher Schnitt der ersten n Änderungen
      danach:     Ø_t = (Ø_(t−1) · (n − 1) + Wert_t) / n      (Wilder-Glättung)
      RS  = Ø-Gewinn / Ø-Verlust
      RSI = 100 − 100 / (1 + RS)      (Ø-Verlust = 0  ->  RSI = 100)

    Der erste RSI-Wert steht am (n+1)-ten Kurs, davor NaN.
    """
    werte = close.to_numpy(dtype=float)
    ergebnis = np.full(len(werte), np.nan)
    if len(werte) <= n:
        return pd.Series(ergebnis, index=close.index, name=f"RSI{n}")

    aenderung = np.diff(werte)
    gewinn = np.clip(aenderung, 0, None)
    verlust = np.clip(-aenderung, 0, None)

    avg_g = gewinn[:n].mean()
    avg_v = verlust[:n].mean()
    ergebnis[n] = _rsi_wert(avg_g, avg_v)
    for i in range(n, len(aenderung)):
        avg_g = (avg_g * (n - 1) + gewinn[i]) / n
        avg_v = (avg_v * (n - 1) + verlust[i]) / n
        ergebnis[i + 1] = _rsi_wert(avg_g, avg_v)
    return pd.Series(ergebnis, index=close.index, name=f"RSI{n}")


def _rsi_wert(avg_g: float, avg_v: float) -> float:
    """RSI aus durchschnittlichem Gewinn und Verlust."""
    if avg_v == 0:
        return 100.0 if avg_g > 0 else 50.0  # keine Bewegung: neutral
    return 100.0 - 100.0 / (1.0 + avg_g / avg_v)


def rsi_zone(wert: float, ueberkauft: float = 70, ueberverkauft: float = 30) -> str:
    """Einordnung: "überkauft" (> 70), "überverkauft" (< 30), sonst "neutral"."""
    if pd.isna(wert):
        return "nicht gefunden"
    if wert > ueberkauft:
        return "überkauft"
    if wert < ueberverkauft:
        return "überverkauft"
    return "neutral"


# --------------------------------------------------------------------------
# Gleitende Durchschnitte
# --------------------------------------------------------------------------

def sma(close: pd.Series, n: int) -> pd.Series:
    """Einfacher gleitender Durchschnitt: Schnitt der letzten n Schlusskurse."""
    return close.rolling(n, min_periods=n).mean()


def letzte_kreuzung(kurz: pd.Series, lang: pd.Series, max_tage: int = 10) -> dict | None:
    """Sucht eine frische Kreuzung zweier Linien in den letzten ``max_tage`` Handelstagen.

    Golden Cross: kurze Linie schneidet die lange von unten nach oben.
    Death Cross:  kurze Linie schneidet die lange von oben nach unten.
    Gibt ``{"art", "datum", "tage_her"}`` der jüngsten Kreuzung zurück oder ``None``.
    """
    diff = (kurz - lang).dropna()
    if len(diff) < 2:
        return None
    vorzeichen = np.sign(diff.to_numpy())
    # Tage mit Differenz exakt 0 übernehmen das vorherige Vorzeichen
    for i in range(1, len(vorzeichen)):
        if vorzeichen[i] == 0:
            vorzeichen[i] = vorzeichen[i - 1]
    wechsel = np.flatnonzero(vorzeichen[1:] != vorzeichen[:-1]) + 1
    wechsel = [i for i in wechsel if vorzeichen[i - 1] != 0]
    if not wechsel:
        return None
    i = wechsel[-1]
    tage_her = len(diff) - 1 - i
    if tage_her > max_tage:
        return None
    return {
        "art": "Golden Cross" if vorzeichen[i] > 0 else "Death Cross",
        "datum": diff.index[i],
        "tage_her": int(tage_her),
    }


def trend(close: float, sma50: float, sma200: float) -> str:
    """Grobe Trendeinordnung aus Kurs und Linien.

    Aufwärtstrend:  Kurs > 50-Tage-Linie > 200-Tage-Linie
    Abwärtstrend:   Kurs < 50-Tage-Linie < 200-Tage-Linie
    sonst gemischt.
    """
    if any(pd.isna(x) for x in (close, sma50, sma200)):
        return "nicht gefunden"
    if close > sma50 > sma200:
        return "Aufwärtstrend"
    if close < sma50 < sma200:
        return "Abwärtstrend"
    return "gemischt"


def steigung_pct(linie: pd.Series, tage: int = 20) -> float:
    """Veränderung einer Linie über ``tage`` Handelstage in %."""
    linie = linie.dropna()
    if len(linie) <= tage:
        return math.nan
    return (linie.iloc[-1] / linie.iloc[-1 - tage] - 1) * 100


# --------------------------------------------------------------------------
# 52-Wochen-Band
# --------------------------------------------------------------------------

def band_52w(df: pd.DataFrame, tage: int = HANDELSTAGE_JAHR) -> dict:
    """Lage des Kurses im 52-Wochen-Band (aus Tageshochs und -tiefs).

    lage_pct          = (Kurs − Tief) / (Hoch − Tief) · 100   (0 = am Tief, 100 = am Hoch)
    abstand_hoch_pct  = (Kurs / Hoch − 1) · 100               (negativ = unter dem Hoch)
    ``vollstaendig`` ist False, wenn weniger als ein Jahr Historie vorliegt.
    """
    fenster = df.iloc[-tage:]
    hoch, tief = fenster["High"].max(), fenster["Low"].min()
    kurs = df["Close"].iloc[-1]
    spanne = hoch - tief
    return {
        "hoch_52w": float(hoch),
        "tief_52w": float(tief),
        "lage_52w_pct": float((kurs - tief) / spanne * 100) if spanne > 0 else math.nan,
        "abstand_hoch_pct": float((kurs / hoch - 1) * 100),
        "band_vollstaendig": len(df) >= tage,
    }


# --------------------------------------------------------------------------
# Performance und relative Stärke
# --------------------------------------------------------------------------

def performance(close: pd.Series, monate: int, bis: pd.Timestamp | None = None) -> float:
    """Kursentwicklung in % über ``monate`` Kalendermonate bis ``bis`` (Standard: letzter Tag).

    Als Startkurs gilt der letzte Schlusskurs an oder vor dem Stichtag.
    Reicht die Historie nicht zurück, ist das Ergebnis NaN.
    """
    close = close.dropna()
    if close.empty:
        return math.nan
    bis = bis if bis is not None else close.index[-1]
    start_datum = bis - pd.DateOffset(months=monate)
    if start_datum < close.index[0]:
        return math.nan
    start, ende = close.asof(start_datum), close.asof(bis)
    return float((ende / start - 1) * 100)


def relative_staerke(
    close: pd.Series,
    index_close: pd.Series | None,
    monate: tuple[int, ...] = (1, 3, 12),
) -> dict:
    """Performance der Aktie und des Index sowie die Differenz in Prozentpunkten.

    rs_Xm = Performance Aktie − Performance Index   (positiv = stärker als der Index)
    Beide Zeiträume enden am letzten Kurstag der Aktie, damit
    unterschiedliche Feiertage die Rechnung nicht verschieben.
    """
    bis = close.dropna().index[-1]
    ergebnis = {}
    for m in monate:
        p = performance(close, m, bis)
        i = performance(index_close, m, bis) if index_close is not None else math.nan
        ergebnis[f"perf_{m}m_pct"] = p
        ergebnis[f"index_{m}m_pct"] = i
        ergebnis[f"rs_{m}m_pp"] = p - i
    return ergebnis


# --------------------------------------------------------------------------
# Volumen
# --------------------------------------------------------------------------

def volumen_ausschlag(volumen: pd.Series, fenster: int = 30, schwelle: float = 2.0) -> dict:
    """Volumen des letzten Tages im Verhältnis zum Schnitt der ``fenster`` Tage davor.

    faktor = Volumen_heute / Ø-Volumen(vorherige 30 Tage)
    ``auffaellig`` ab ``schwelle`` (Standard: doppeltes Normalvolumen).
    Zusätzlich: Zahl der auffälligen Tage in den letzten 5 Handelstagen.
    """
    v = volumen.dropna()
    if len(v) <= fenster:
        return {"volumen": math.nan, "volumen_schnitt_30": math.nan,
                "volumen_faktor": math.nan, "volumen_auffaellig": False, "volumen_spitzen_5t": 0}
    schnitt = v.shift(1).rolling(fenster, min_periods=fenster).mean()
    faktor = v / schnitt
    letzter = float(faktor.iloc[-1]) if schnitt.iloc[-1] > 0 else math.nan
    return {
        "volumen": float(v.iloc[-1]),
        "volumen_schnitt_30": float(schnitt.iloc[-1]),
        "volumen_faktor": letzter,
        "volumen_auffaellig": bool(letzter >= schwelle) if not math.isnan(letzter) else False,
        "volumen_spitzen_5t": int((faktor.iloc[-5:] >= schwelle).sum()),
    }


# --------------------------------------------------------------------------
# Lokale Extrema, Unterstützung und Widerstand
# --------------------------------------------------------------------------

def lokale_extrema(df: pd.DataFrame, fenster: int = 5) -> tuple[pd.Series, pd.Series]:
    """Lokale Hochs und Tiefs ("Pivots").

    Regel: Tag t ist ein lokales Hoch, wenn sein Tageshoch
      - größer ist als jedes Hoch der ``fenster`` Tage davor und
      - mindestens so groß wie jedes Hoch der ``fenster`` Tage danach.
    (Tief analog mit Tagestiefs.) Durch das "größer / mindestens so groß"
    zählt ein Plateau nur einmal.

    Achtung: Ein Pivot steht erst ``fenster`` Tage später fest. Die letzten
    ``fenster`` Tage können daher noch keine Pivots enthalten.
    Wird auch vom Mustermodul verwendet.
    """
    hoch, tief = df["High"].to_numpy(float), df["Low"].to_numpy(float)
    hochs, tiefs = [], []
    for t in range(fenster, len(df) - fenster):
        links_h, rechts_h = hoch[t - fenster:t], hoch[t + 1:t + 1 + fenster]
        if hoch[t] > links_h.max() and hoch[t] >= rechts_h.max():
            hochs.append(t)
        links_t, rechts_t = tief[t - fenster:t], tief[t + 1:t + 1 + fenster]
        if tief[t] < links_t.min() and tief[t] <= rechts_t.min():
            tiefs.append(t)
    return (pd.Series(hoch[hochs], index=df.index[hochs], name="hoch"),
            pd.Series(tief[tiefs], index=df.index[tiefs], name="tief"))


def preiszonen(preise: pd.Series, toleranz: float = 0.02) -> list[dict]:
    """Fasst nahe beieinanderliegende Pivot-Preise zu Zonen zusammen.

    Regel: Preise werden aufsteigend sortiert; ein Preis gehört zur aktuellen
    Zone, solange er höchstens ``toleranz`` (2 %) über dem niedrigsten Preis
    der Zone liegt. Zonenwert = Mittelwert, ``beruehrungen`` = Anzahl Pivots,
    ``zuletzt`` = jüngstes Datum eines Pivots der Zone.
    """
    if preise.empty:
        return []
    sortiert = preise.sort_values()
    zonen: list[list] = [[(sortiert.index[0], sortiert.iloc[0])]]
    for datum, p in zip(sortiert.index[1:], sortiert.iloc[1:]):
        if p <= zonen[-1][0][1] * (1 + toleranz):
            zonen[-1].append((datum, p))
        else:
            zonen.append([(datum, p)])
    return [{"preis": float(np.mean([p for _, p in z])),
             "beruehrungen": len(z),
             "zuletzt": max(d for d, _ in z)} for z in zonen]


def unterstuetzung_widerstand(
    df: pd.DataFrame,
    fenster: int = 5,
    toleranz: float = 0.02,
    tage: int = HANDELSTAGE_JAHR,
) -> dict:
    """Nächste Unterstützung unter und nächster Widerstand über dem Kurs.

    Grundlage sind alle lokalen Hochs UND Tiefs der letzten ``tage`` Handelstage
    (ein altes Hoch kann zur Unterstützung werden und umgekehrt), zusammengefasst
    zu Zonen (``preiszonen``). Unterstützung = höchste Zone unter dem Kurs,
    Widerstand = niedrigste Zone über dem Kurs. Fehlt eine Zone (z. B. Kurs auf
    Allzeithoch), ist der Wert NaN.
    """
    teil = df.iloc[-tage:]
    hochs, tiefs = lokale_extrema(teil, fenster)
    zonen = preiszonen(pd.concat([hochs, tiefs]), toleranz)
    kurs = float(df["Close"].iloc[-1])
    unter = [z for z in zonen if z["preis"] < kurs]
    ueber = [z for z in zonen if z["preis"] > kurs]
    u = max(unter, key=lambda z: z["preis"]) if unter else None
    w = min(ueber, key=lambda z: z["preis"]) if ueber else None
    return {
        "unterstuetzung": u["preis"] if u else math.nan,
        "unterstuetzung_beruehrungen": u["beruehrungen"] if u else 0,
        "abstand_unterstuetzung_pct": (u["preis"] / kurs - 1) * 100 if u else math.nan,
        "widerstand": w["preis"] if w else math.nan,
        "widerstand_beruehrungen": w["beruehrungen"] if w else 0,
        "abstand_widerstand_pct": (w["preis"] / kurs - 1) * 100 if w else math.nan,
    }


# --------------------------------------------------------------------------
# Momentaufnahme je Aktie und Tabelle fürs Universum
# --------------------------------------------------------------------------

def berechne_indikatoren(df: pd.DataFrame, index_df: pd.DataFrame | None = None) -> dict:
    """Alle Indikatoren für den letzten Kurstag einer Aktie (eine Zeile pro Aktie)."""
    close = df["Close"]
    s50, s200 = sma(close, 50), sma(close, 200)
    r = rsi(close)
    kurs = float(close.iloc[-1])
    werte = {
        "datum": close.index[-1],
        "kurs": kurs,
        "rsi14": float(r.iloc[-1]),
        "rsi_zone": rsi_zone(r.iloc[-1]),
        "sma50": float(s50.iloc[-1]),
        "sma200": float(s200.iloc[-1]),
        "abstand_sma50_pct": (kurs / s50.iloc[-1] - 1) * 100,
        "abstand_sma200_pct": (kurs / s200.iloc[-1] - 1) * 100,
        "sma200_steigung_20t_pct": steigung_pct(s200, 20),
        "trend": trend(kurs, s50.iloc[-1], s200.iloc[-1]),
    }
    kreuzung = letzte_kreuzung(s50, s200)
    werte["kreuzung"] = kreuzung["art"] if kreuzung else ""
    werte["kreuzung_datum"] = kreuzung["datum"] if kreuzung else pd.NaT
    werte.update(band_52w(df))
    idx_close = index_df["Close"] if index_df is not None and len(index_df) else None
    werte.update(relative_staerke(close, idx_close))
    werte.update(volumen_ausschlag(df["Volume"]))
    werte.update(unterstuetzung_widerstand(df))
    return werte


def indikator_tabelle(
    aktien: list,
    kurse: dict[str, pd.DataFrame],
    min_tage: int = 30,
) -> pd.DataFrame:
    """Indikatoren für alle Aktien mit Kursdaten (Aktien aus ``analyst.universe``).

    Aktien mit weniger als ``min_tage`` Kursen werden übersprungen.
    """
    zeilen = []
    for a in aktien:
        df = kurse.get(a.ticker)
        if df is None or len(df) < min_tage:
            continue
        zeilen.append({"ticker": a.ticker, "name": a.name, "markt": a.markt,
                       **berechne_indikatoren(df, kurse.get(a.index))})
    return pd.DataFrame(zeilen)
