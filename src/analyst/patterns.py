"""Chartmuster: Kopf-Schulter, Doppeltop/-boden, Seitwärtsphase und Ausbruch.

Alle Muster werden aus lokalen Extrempunkten (``indicators.lokale_extrema``)
mit festen Toleranzregeln erkannt. Die Regeln stehen in ``MusterRegeln``.

Status eines Musters:
  - "bestätigt":   ein Schlusskurs liegt um ``bestaetigung_pct`` jenseits der
                   Nackenlinie bzw. Ausbruchsmarke
  - "gescheitert": ein Schlusskurs liegt jenseits der Invalidierungsmarke
                   (auch wenn das Muster vorher bestätigt war)
  - "in Bildung":  weder das eine noch das andere

Wichtig: Muster sind unscharf, ihr Nutzen ist umstritten. Das Messziel ist
die klassische Faustregel (Musterhöhe ab Nackenlinie abgetragen), keine
Prognose. Ob die Muster bei uns etwas taugen, klärt das Backtest-Modul.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

from analyst.indicators import lokale_extrema


@dataclass(frozen=True)
class MusterRegeln:
    """Alle Toleranzen der Mustererkennung an einer Stelle."""

    pivot_fenster: int = 5          # Tage links/rechts für lokale Hochs/Tiefs
    rueckblick_tage: int = 250      # nur die letzten ~12 Monate durchsuchen
    max_alter_tage: int = 60        # letzter Eckpunkt höchstens so alt
    bestaetigung_pct: float = 1.0   # Schlusskurs so weit jenseits der Linie = Durchbruch
    max_alter_ereignis_tage: int = 20  # Bestätigung/Scheitern höchstens so alt, sonst ausgespielt

    # Doppeltop / Doppelboden
    doppel_toleranz_pct: float = 3.0   # max. Abstand der beiden Spitzen
    doppel_min_abstand: int = 10       # Handelstage zwischen den Spitzen
    doppel_max_abstand: int = 120
    doppel_min_tiefe_pct: float = 5.0  # Rücksetzer zwischen den Spitzen

    # Kopf-Schulter
    sks_kopf_min_pct: float = 3.0         # Kopf überragt beide Schultern mindestens um
    sks_schulter_toleranz_pct: float = 5.0  # Schultern max. so weit auseinander (bez. auf Kopf)
    sks_zeit_faktor: float = 2.0           # Zeitabstände Schulter–Kopf max. Faktor 2
    sks_min_dauer: int = 15
    sks_max_dauer: int = 150

    # Seitwärtsphase / Ausbruch
    band_min_tage: int = 20
    band_max_breite_pct: float = 10.0   # (Hoch − Tief) / Tief
    band_min_wechsel: int = 4           # abwechselnde Randberührungen (je 2x oben und unten)
    band_rand_anteil: float = 0.2       # äußeres Fünftel des Bands zählt als Rand
    ausbruch_max_alter: int = 20        # Ausbruch höchstens so viele Tage her
    ausbruch_volumen_faktor: float = 1.5


@dataclass
class Muster:
    """Ein erkanntes Muster mit allen Marken für Bericht und Chart."""

    name: str                   # z. B. "Doppelboden"
    richtung: str               # "bullisch", "bärisch" oder "offen"
    status: str                 # "in Bildung", "bestätigt", "gescheitert"
    ausbruchsmarke: float       # Nackenlinie (am letzten Tag) bzw. Bandgrenze
    invalidierung: float
    sicherheit: str             # "niedrig", "mittel", "hoch"
    messziel: float             # Faustregel, keine Prognose
    datum: pd.Timestamp         # letzter Kurstag der Auswertung
    punkte: list[tuple[pd.Timestamp, float, str]] = field(default_factory=list)
    linie: tuple[tuple[pd.Timestamp, float], tuple[pd.Timestamp, float]] | None = None
    bestaetigt_am: pd.Timestamp | None = None
    gescheitert_am: pd.Timestamp | None = None
    kriterien: list[str] = field(default_factory=list)  # erfüllte Qualitätskriterien

    def ruecklauf(self, kurs: float) -> bool:
        """Bestätigt, aber der Kurs steht wieder auf der falschen Seite der Marke.

        Bullisch: Kurs < Ausbruchsmarke, bärisch: Kurs > Ausbruchsmarke.
        Ein solcher Rücklauf ist häufig (Test der Linie), schwächt das Signal aber.
        """
        if self.status != "bestätigt":
            return False
        if self.richtung == "bullisch":
            return kurs < self.ausbruchsmarke
        if self.richtung == "bärisch":
            return kurs > self.ausbruchsmarke
        return False

    def als_dict(self) -> dict:
        """Flache Darstellung für Tabellen."""
        return {
            "muster": self.name, "richtung": self.richtung, "status": self.status,
            "ausbruchsmarke": self.ausbruchsmarke, "invalidierung": self.invalidierung,
            "sicherheit": self.sicherheit, "messziel": self.messziel, "datum": self.datum,
            "beginn": self.punkte[0][0] if self.punkte else pd.NaT,
            "bestaetigt_am": self.bestaetigt_am, "gescheitert_am": self.gescheitert_am,
            "kriterien": ", ".join(self.kriterien),
        }


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

def _sicherheit(punkte: int, mittel_ab: int, hoch_ab: int) -> str:
    """Punktzahl der Qualitätskriterien -> niedrig / mittel / hoch."""
    if punkte >= hoch_ab:
        return "hoch"
    if punkte >= mittel_ab:
        return "mittel"
    return "niedrig"


def _status(
    close: np.ndarray,
    ab: int,
    linie: np.ndarray,
    invalidierung: float,
    bullisch: bool,
    puffer: float,
) -> tuple[str, int | None, int | None]:
    """Status ab Position ``ab`` (erster Tag nach dem letzten Eckpunkt).

    ``linie`` = Nackenlinie je Tag (gleiche Länge wie ``close``).
    Bullisch: Durchbruch = Close > Linie·(1+puffer), gescheitert = Close < Invalidierung.
    Bärisch spiegelbildlich. Gibt (Status, Position Bestätigung, Position Scheitern) zurück.
    """
    bestaetigt = gescheitert = None
    for t in range(ab, len(close)):
        if bullisch:
            if close[t] < invalidierung:
                gescheitert = t
                break
            if bestaetigt is None and close[t] > linie[t] * (1 + puffer):
                bestaetigt = t
        else:
            if close[t] > invalidierung:
                gescheitert = t
                break
            if bestaetigt is None and close[t] < linie[t] * (1 - puffer):
                bestaetigt = t
    if gescheitert is not None:
        return "gescheitert", bestaetigt, gescheitert
    if bestaetigt is not None:
        return "bestätigt", bestaetigt, None
    return "in Bildung", None, None


def _pivots(df: pd.DataFrame, regeln: MusterRegeln) -> tuple[list[int], list[int]]:
    """Positionen der lokalen Hochs und Tiefs im Rückblickfenster."""
    hochs, tiefs = lokale_extrema(df, regeln.pivot_fenster)
    start = max(0, len(df) - regeln.rueckblick_tage)
    pos_h = [p for p in df.index.get_indexer(hochs.index) if p >= start]
    pos_t = [p for p in df.index.get_indexer(tiefs.index) if p >= start]
    return pos_h, pos_t


def _datum(df: pd.DataFrame, pos: int | None) -> pd.Timestamp | None:
    return df.index[pos] if pos is not None else None


def _ausgespielt(n: int, pos_b: int | None, pos_g: int | None, regeln: MusterRegeln) -> bool:
    """True, wenn Bestätigung bzw. Scheitern länger als ``max_alter_ereignis_tage`` her ist."""
    ereignis = pos_g if pos_g is not None else pos_b
    return ereignis is not None and n - 1 - ereignis > regeln.max_alter_ereignis_tage


def _volumen_um(vol: np.ndarray, pos: int, breite: int = 2) -> float:
    """Ø-Volumen in einem kleinen Fenster (± ``breite`` Tage) um einen Eckpunkt."""
    return float(np.nanmean(vol[max(0, pos - breite):pos + breite + 1]))


def _volumen(df: pd.DataFrame) -> np.ndarray:
    return df["Volume"].to_numpy(float) if "Volume" in df else np.full(len(df), np.nan)


# --------------------------------------------------------------------------
# Doppeltop / Doppelboden
# --------------------------------------------------------------------------

def finde_doppel(df: pd.DataFrame, boden: bool, regeln: MusterRegeln = MusterRegeln()) -> Muster | None:
    """Jüngstes Doppeltop (``boden=False``) bzw. jüngsten Doppelboden.

    Regeln (Doppeltop; Doppelboden spiegelbildlich):
      1. Zwei lokale Hochs H1, H2 mit 10–120 Handelstagen Abstand,
         H2 höchstens ``max_alter_tage`` alt.
      2. |H1 − H2| ≤ 3 % des höheren Hochs.
      3. Kein Tageshoch zwischen H1 und H2 über dem höheren Hoch.
      4. Nackenlinie N = tiefstes Tief zwischen H1 und H2;
         Rücksetzer (niedrigeres Hoch − N) ≥ 5 % des niedrigeren Hochs.
      5. Vortrend: In einer Musterbreite (H2 − H1 Tage) vor H1 lag der Kurs
         unter N – es gab also einen Anstieg, der umkehren kann.
      6. Dominanz: In diesem Vorfenster kein Hoch über dem höheren Hoch.
      7. Bestätigung bzw. Scheitern höchstens 20 Handelstage her.
      Invalidierung = höheres Hoch. Messziel = N − (Ø-Hoch − N).
    Qualitätspunkte: Spitzen ≤ 1 % auseinander, Rücksetzer ≥ 10 %,
      Abstand ≥ 20 Tage, Volumen am zweiten Top geringer als am ersten.
      0–1 niedrig, 2–3 mittel, 4 hoch.
    """
    hoch, tief, close = (df[c].to_numpy(float) for c in ("High", "Low", "Close"))
    vol = _volumen(df)
    pos_h, pos_t = _pivots(df, regeln)
    spitzen, extrem = (pos_t, tief) if boden else (pos_h, hoch)
    gegen = hoch if boden else tief
    n = len(df)

    kandidaten = []
    for a, b in combinations(spitzen, 2):
        if not regeln.doppel_min_abstand <= b - a <= regeln.doppel_max_abstand:
            continue
        if n - 1 - b > regeln.max_alter_tage:
            continue
        p1, p2 = extrem[a], extrem[b]
        aeusser = min(p1, p2) if boden else max(p1, p2)
        innen = max(p1, p2) if boden else min(p1, p2)
        if abs(p1 - p2) / max(p1, p2) * 100 > regeln.doppel_toleranz_pct:
            continue
        zwischen = slice(a + 1, b)
        if boden and tief[zwischen].min() < aeusser:
            continue
        if not boden and hoch[zwischen].max() > aeusser:
            continue
        nacken = gegen[zwischen].max() if boden else gegen[zwischen].min()
        tiefe = abs(nacken - innen) / innen * 100
        if tiefe < regeln.doppel_min_tiefe_pct:
            continue
        vor = slice(max(0, a - (b - a)), a)
        if a == 0:
            continue
        if boden and not (hoch[vor].max() > nacken and tief[vor].min() >= aeusser):
            continue  # kein Vortrend oder tieferes Tief davor
        if not boden and not (tief[vor].min() < nacken and hoch[vor].max() <= aeusser):
            continue  # kein Vortrend oder höheres Hoch davor

        kriterien = []
        if abs(p1 - p2) / max(p1, p2) * 100 <= 1.0:
            kriterien.append("Spitzen fast gleich hoch")
        if tiefe >= 10:
            kriterien.append("deutlicher Rücksetzer")
        if b - a >= 20:
            kriterien.append("ausreichender Abstand")
        if _volumen_um(vol, b) < _volumen_um(vol, a):
            kriterien.append("Volumen an der zweiten Spitze geringer")
        kandidaten.append((b, len(kriterien), a, nacken, aeusser, kriterien))

    if not kandidaten:
        return None
    b, _, a, nacken, aeusser, kriterien = max(kandidaten, key=lambda k: (k[0], k[1]))
    linie = np.full(n, nacken)
    status, pos_b, pos_g = _status(close, b + 1, linie, aeusser, boden, regeln.bestaetigung_pct / 100)
    if _ausgespielt(n, pos_b, pos_g, regeln):
        return None
    mittel = (extrem[a] + extrem[b]) / 2
    return Muster(
        name="Doppelboden" if boden else "Doppeltop",
        richtung="bullisch" if boden else "bärisch",
        status=status,
        ausbruchsmarke=float(nacken),
        invalidierung=float(aeusser),
        sicherheit=_sicherheit(len(kriterien), 2, 4),
        messziel=float(nacken + (nacken - mittel)),
        datum=df.index[-1],
        punkte=[(df.index[a], float(extrem[a]), "Boden 1" if boden else "Top 1"),
                (df.index[b], float(extrem[b]), "Boden 2" if boden else "Top 2")],
        linie=((df.index[a], float(nacken)), (df.index[-1], float(nacken))),
        bestaetigt_am=_datum(df, pos_b),
        gescheitert_am=_datum(df, pos_g),
        kriterien=kriterien,
    )


# --------------------------------------------------------------------------
# Kopf-Schulter
# --------------------------------------------------------------------------

def finde_kopf_schulter(df: pd.DataFrame, umgekehrt: bool, regeln: MusterRegeln = MusterRegeln()) -> Muster | None:
    """Jüngste Kopf-Schulter-Formation (``umgekehrt=True``: umgekehrte SKS).

    Regeln (klassische SKS; umgekehrte spiegelbildlich):
      1. Drei lokale Hochs LS < K > RS in zeitlicher Folge, Dauer 15–150 Tage,
         RS höchstens ``max_alter_tage`` alt.
      2. Kopf überragt beide Schultern um ≥ 3 %.
      3. Schultern liegen ≤ 5 % (bezogen auf den Kopf) auseinander.
      4. Zeitabstände LS→K und K→RS unterscheiden sich höchstens um Faktor 2.
      5. Kein Tageshoch zwischen LS und RS über dem Kopf.
      6. Nackenlinie: Gerade durch das tiefste Tief zwischen LS und K (T1)
         und zwischen K und RS (T2). Beide Schultern liegen über der Linie.
      7. Vortrend: Im Vorfenster (so lang wie LS→K) lag der Kurs unter der
         Nackenlinie. Dominanz: dort kein Hoch über dem Kopf.
      8. Bestätigung bzw. Scheitern höchstens 20 Handelstage her.
      Invalidierung = rechte Schulter.
      Messziel = Nackenlinie heute − (Kopf − Nackenlinie am Kopf).
    Qualitätspunkte: Schultern ≤ 2 % auseinander, Zeitsymmetrie (Faktor ≤ 1,5),
      flache Nackenlinie (T1/T2 ≤ 3 % auseinander), Kopf ≥ 5 % über den
      Schultern, Volumen an der rechten Schulter geringer als am Kopf.
      0–1 niedrig, 2–3 mittel, 4–5 hoch.
    """
    hoch, tief, close = (df[c].to_numpy(float) for c in ("High", "Low", "Close"))
    vol = _volumen(df)
    pos_h, pos_t = _pivots(df, regeln)
    spitzen, extrem = (pos_t, tief) if umgekehrt else (pos_h, hoch)
    gegen = hoch if umgekehrt else tief
    n = len(df)
    # Vorzeichen: so lassen sich beide Varianten mit "größer" formulieren
    v = -1.0 if umgekehrt else 1.0

    kandidaten = []
    for ls, k, rs in combinations(spitzen, 3):
        dauer = rs - ls
        if not regeln.sks_min_dauer <= dauer <= regeln.sks_max_dauer:
            continue
        if n - 1 - rs > regeln.max_alter_tage:
            continue
        pls, pk, prs = extrem[ls], extrem[k], extrem[rs]
        kopf_vorsprung = min(v * (pk - pls), v * (pk - prs)) / pk * 100
        if kopf_vorsprung < regeln.sks_kopf_min_pct:
            continue
        schulter_diff = abs(pls - prs) / pk * 100
        if schulter_diff > regeln.sks_schulter_toleranz_pct:
            continue
        links, rechts = k - ls, rs - k
        zeit_faktor = max(links, rechts) / max(1, min(links, rechts))
        if zeit_faktor > regeln.sks_zeit_faktor:
            continue
        if (v * extrem[ls + 1:rs] > v * pk).any():
            continue
        # Nackenpunkte: Gegenextrem zwischen Schulter und Kopf
        seg1, seg2 = gegen[ls + 1:k], gegen[k + 1:rs]
        if len(seg1) == 0 or len(seg2) == 0:
            continue
        t1 = ls + 1 + int(np.argmax(seg1) if umgekehrt else np.argmin(seg1))
        t2 = k + 1 + int(np.argmax(seg2) if umgekehrt else np.argmin(seg2))
        steigung = (gegen[t2] - gegen[t1]) / (t2 - t1)
        linie = gegen[t1] + steigung * (np.arange(n) - t1)
        if v * (pls - linie[ls]) <= 0 or v * (prs - linie[rs]) <= 0:
            continue
        vor = slice(max(0, ls - (k - ls)), ls)
        if ls == 0 or not (v * gegen[vor] < v * linie[vor]).any():
            continue  # kein Vortrend
        if (v * extrem[vor] > v * pk).any():
            continue  # Kopf ist nicht das Extrem im Umfeld

        kriterien = []
        if schulter_diff <= 2:
            kriterien.append("Schultern fast gleich hoch")
        if zeit_faktor <= 1.5:
            kriterien.append("zeitlich symmetrisch")
        if abs(gegen[t2] - gegen[t1]) / min(gegen[t1], gegen[t2]) * 100 <= 3:
            kriterien.append("flache Nackenlinie")
        if kopf_vorsprung >= 5:
            kriterien.append("Kopf deutlich ausgeprägt")
        if _volumen_um(vol, rs) < _volumen_um(vol, k):
            kriterien.append("Volumen an der rechten Schulter geringer")
        kandidaten.append((rs, len(kriterien), ls, k, t1, t2, linie, kriterien))

    if not kandidaten:
        return None
    rs, _, ls, k, t1, t2, linie, kriterien = max(kandidaten, key=lambda x: (x[0], x[1]))
    invalidierung = extrem[rs]
    status, pos_b, pos_g = _status(close, rs + 1, linie, invalidierung, umgekehrt,
                                   regeln.bestaetigung_pct / 100)
    if _ausgespielt(n, pos_b, pos_g, regeln):
        return None
    hoehe = extrem[k] - linie[k]
    return Muster(
        name="Umgekehrte Kopf-Schulter" if umgekehrt else "Kopf-Schulter",
        richtung="bullisch" if umgekehrt else "bärisch",
        status=status,
        ausbruchsmarke=float(linie[-1]),
        invalidierung=float(invalidierung),
        sicherheit=_sicherheit(len(kriterien), 2, 4),
        messziel=float(linie[-1] - hoehe),
        datum=df.index[-1],
        punkte=[(df.index[ls], float(extrem[ls]), "linke Schulter"),
                (df.index[k], float(extrem[k]), "Kopf"),
                (df.index[rs], float(extrem[rs]), "rechte Schulter")],
        linie=((df.index[t1], float(linie[t1])), (df.index[-1], float(linie[-1]))),
        bestaetigt_am=_datum(df, pos_b),
        gescheitert_am=_datum(df, pos_g),
        kriterien=kriterien,
    )


# --------------------------------------------------------------------------
# Seitwärtsphase und Ausbruch
# --------------------------------------------------------------------------

def _band_rueckwaerts(hoch: np.ndarray, tief: np.ndarray, ende: int, max_breite: float) -> tuple[int, float, float]:
    """Längstes Band, das an Position ``ende`` endet.

    Geht Tag für Tag zurück, solange (Hoch − Tief) / Tief ≤ ``max_breite``.
    Gibt (Startposition, Band-Hoch, Band-Tief) zurück.
    """
    h, t = hoch[ende], tief[ende]
    start = ende
    for i in range(ende - 1, -1, -1):
        nh, nt = max(h, hoch[i]), min(t, tief[i])
        if (nh - nt) / nt > max_breite:
            break
        h, t, start = nh, nt, i
    return start, h, t


def _randwechsel(hoch: np.ndarray, tief: np.ndarray, start: int, ende: int,
                 bh: float, bt: float, anteil: float) -> int:
    """Zählt abwechselnde Besuche am oberen und unteren Bandrand.

    Oberer Rand: Tageshoch ≥ Band-Hoch − anteil·Bandhöhe, unterer analog.
    Mehrere Tage am selben Rand hintereinander zählen als ein Besuch.
    Ein Besuch, der schon am ersten Bandtag läuft, zählt nicht: Er gehört
    zur Bewegung, die in das Band hineinführt.
    Ein gleichmäßiger Trend kommt so auf 1 Besuch, eine V-Form auf 2–3.
    """
    rand = (bh - bt) * anteil
    besuche, letzter, erster_besuch_am_start = 0, None, False
    for i in range(start, ende + 1):
        oben, unten = hoch[i] >= bh - rand, tief[i] <= bt + rand
        for seite, ja in (("oben", oben), ("unten", unten)):
            if ja and seite != letzter:
                besuche, letzter = besuche + 1, seite
                if besuche == 1 and i == start:
                    erster_besuch_am_start = True
    return besuche - 1 if erster_besuch_am_start else besuche


def finde_ausbruch(df: pd.DataFrame, regeln: MusterRegeln = MusterRegeln()) -> Muster | None:
    """Ausbruch aus einer Seitwärtsphase oder laufende Seitwärtsphase.

    Regeln:
      1. Seitwärtsphase: mindestens 20 Handelstage, in denen
         (höchstes Hoch − tiefstes Tief) / tiefstes Tief ≤ 10 %, und der Kurs
         mindestens 4-mal abwechselnd den oberen und unteren Rand (äußeres
         Fünftel) berührt – sonst wäre es nur ein Trend oder eine V-Form.
      2. Ausbruch an Tag b (höchstens 20 Tage her): Schlusskurs ≥ 1 % über
         dem Band-Hoch (bullisch) bzw. unter dem Band-Tief (bärisch), wobei
         das Band an Tag b−1 endet. Gewertet wird der früheste solche Tag.
      3. Gescheitert: danach Schlusskurs zurück jenseits der Bandmitte
         (Invalidierung). Messziel = Ausbruchsmarke ± Bandhöhe.
      4. Ohne Ausbruch, aber mit laufendem Band bis heute:
         "Seitwärtsphase", Status "in Bildung", Richtung "offen";
         Ausbruchsmarke = Band-Hoch, Invalidierung = Band-Tief.
    Qualitätspunkte: Volumen am Ausbruchstag ≥ 1,5 × Ø-Volumen im Band,
      Band ≥ 40 Tage, Band ≤ 6 % breit. 0 niedrig, 1–2 mittel, 3 hoch.
      (Für die laufende Seitwärtsphase entfällt das Volumenkriterium.)
    """
    hoch, tief, close = (df[c].to_numpy(float) for c in ("High", "Low", "Close"))
    vol = _volumen(df)
    n, breite = len(df), regeln.band_max_breite_pct / 100
    puffer = regeln.bestaetigung_pct / 100

    for b in range(max(1, n - 1 - regeln.ausbruch_max_alter), n):
        start, bh, bt = _band_rueckwaerts(hoch, tief, b - 1, breite)
        laenge = b - start
        if laenge < regeln.band_min_tage:
            continue
        if _randwechsel(hoch, tief, start, b - 1, bh, bt, regeln.band_rand_anteil) < regeln.band_min_wechsel:
            continue
        hoch_raus, tief_raus = close[b] > bh * (1 + puffer), close[b] < bt * (1 - puffer)
        if not (hoch_raus or tief_raus):
            continue
        bullisch = bool(hoch_raus)
        mitte = (bh + bt) / 2
        marke = bh if bullisch else bt
        kriterien = []
        vol_schnitt = np.nanmean(vol[start:b]) if laenge else np.nan
        if vol_schnitt and vol[b] >= regeln.ausbruch_volumen_faktor * vol_schnitt:
            kriterien.append("hohes Volumen beim Ausbruch")
        if laenge >= 40:
            kriterien.append("lange Seitwärtsphase")
        if (bh - bt) / bt * 100 <= 6:
            kriterien.append("enges Band")
        status = "bestätigt"
        gescheitert = None
        for t in range(b + 1, n):
            if (bullisch and close[t] < mitte) or (not bullisch and close[t] > mitte):
                status, gescheitert = "gescheitert", t
                break
        if _ausgespielt(n, b, gescheitert, regeln):
            return None
        return Muster(
            name="Ausbruch nach oben" if bullisch else "Ausbruch nach unten",
            richtung="bullisch" if bullisch else "bärisch",
            status=status,
            ausbruchsmarke=float(marke),
            invalidierung=float(mitte),
            sicherheit=_sicherheit(len(kriterien), 1, 3),
            messziel=float(marke + (bh - bt) if bullisch else marke - (bh - bt)),
            datum=df.index[-1],
            punkte=[(df.index[start], float(bt), "Bandbeginn"), (df.index[b], float(close[b]), "Ausbruch")],
            linie=((df.index[start], float(marke)), (df.index[-1], float(marke))),
            bestaetigt_am=df.index[b],
            gescheitert_am=_datum(df, gescheitert),
            kriterien=kriterien,
        )

    # kein Ausbruch: läuft gerade eine Seitwärtsphase?
    start, bh, bt = _band_rueckwaerts(hoch, tief, n - 1, breite)
    if n - start < regeln.band_min_tage:
        return None
    if _randwechsel(hoch, tief, start, n - 1, bh, bt, regeln.band_rand_anteil) < regeln.band_min_wechsel:
        return None
    kriterien = []
    if n - start >= 40:
        kriterien.append("lange Seitwärtsphase")
    if (bh - bt) / bt * 100 <= 6:
        kriterien.append("enges Band")
    return Muster(
        name="Seitwärtsphase",
        richtung="offen",
        status="in Bildung",
        ausbruchsmarke=float(bh),
        invalidierung=float(bt),
        sicherheit=_sicherheit(len(kriterien), 1, 3),
        messziel=float(bh + (bh - bt)),
        datum=df.index[-1],
        punkte=[(df.index[start], float(bt), "Bandbeginn")],
        linie=((df.index[start], float(bh)), (df.index[-1], float(bh))),
        kriterien=kriterien,
    )


# --------------------------------------------------------------------------
# Gesamt
# --------------------------------------------------------------------------

def finde_muster(df: pd.DataFrame, regeln: MusterRegeln = MusterRegeln()) -> list[Muster]:
    """Alle Mustertypen für eine Aktie (je Typ höchstens das jüngste Muster)."""
    if len(df) < 2 * regeln.pivot_fenster + regeln.band_min_tage:
        return []
    gefunden = [
        finde_kopf_schulter(df, umgekehrt=False, regeln=regeln),
        finde_kopf_schulter(df, umgekehrt=True, regeln=regeln),
        finde_doppel(df, boden=False, regeln=regeln),
        finde_doppel(df, boden=True, regeln=regeln),
        finde_ausbruch(df, regeln),
    ]
    return [m for m in gefunden if m is not None]


def muster_tabelle(aktien: list, kurse: dict[str, pd.DataFrame],
                   regeln: MusterRegeln = MusterRegeln()) -> pd.DataFrame:
    """Eine Zeile pro gefundenem Muster und Aktie."""
    zeilen = []
    for a in aktien:
        df = kurse.get(a.ticker)
        if df is None:
            continue
        for m in finde_muster(df, regeln):
            zeilen.append({"ticker": a.ticker, "name": a.name, "markt": a.markt, **m.als_dict()})
    return pd.DataFrame(zeilen)
