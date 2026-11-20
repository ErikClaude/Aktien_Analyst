"""Markdown-Bericht und Charts.

Aufbau pro Idee nach CLAUDE.md Abschnitt 5. Felder, für die noch keine
Daten vorliegen (Bewertung, Kurstreiber, Eingepreist?), stehen ausdrücklich
als "nicht bewertet" bzw. "nicht geprüft" im Bericht – nichts wird geschätzt.
Bull- und Bear-Fall sind hier rein technisch; die inhaltliche Fassung
schreibt Claude beim Befehl /top-aktien nach der Websuche.

Aufruf:  PYTHONPATH=src python -m analyst.report [--markt de us jp] [--top 8] [--max-kurs 100]
"""

from __future__ import annotations

import logging
import math
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from analyst.scoring import _fmt
from analyst.universe import PROJEKT

log = logging.getLogger(__name__)

GEBUEHR_EUR = 0.99
ABSCHLUSS = "Recherche-Ideen, keine Anlageberatung – Kurse und Termine vor dem Kauf selbst prüfen."
WAEHRUNGSZEICHEN = {"EUR": "€", "USD": "$", "JPY": "¥"}
INDEXNAMEN = {"^GDAXI": "DAX", "^GSPC": "S&P 500", "^N225": "Nikkei 225"}
BOERSEN = {".DE": "Xetra", ".PA": "Euronext Paris", ".AS": "Euronext Amsterdam", ".T": "Tokio"}

# Farben (Referenzpalette des dataviz-Skills, helles Theme)
FARBE = {
    "flaeche": "#fcfcfb", "tinte": "#0b0b0b", "tinte2": "#52514e", "gitter": "#e6e5e1",
    "sma50": "#2a78d6", "sma200": "#eb6834", "muster": "#6250d6", "invalidierung": "#d03b3b",
}


# --------------------------------------------------------------------------
# Formatierung
# --------------------------------------------------------------------------

def zahl(x: float, stellen: int = 2) -> str:
    """Deutsches Zahlenformat mit Tausenderpunkt: 12345.6 -> "12.345,60"."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "nicht gefunden"
    s = f"{x:,.{stellen}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def preis(x: float, waehrung: str) -> str:
    """Preis mit Währungszeichen, Yen ohne Nachkommastellen."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "nicht gefunden"
    stellen = 0 if waehrung == "JPY" else 2
    return f"{zahl(x, stellen)} {WAEHRUNGSZEICHEN.get(waehrung, waehrung)}"


def prozent(x: float, vorzeichen: bool = True, stellen: int = 1) -> str:
    """Prozentwert, z. B. "+12,3 %"."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "nicht gefunden"
    return f"{'+' if vorzeichen and x > 0 else ''}{_fmt(x, stellen)} %"


def datum_de(d) -> str:
    """Datum als TT.MM.JJJJ."""
    return pd.Timestamp(d).strftime("%d.%m.%Y") if d is not None and not pd.isna(d) else "nicht gefunden"


def boerse(ticker: str) -> str:
    """Handelsplatz aus dem Ticker-Suffix (ohne Suffix: US-Börse)."""
    for suffix, name in BOERSEN.items():
        if ticker.endswith(suffix):
            return name
    return "USA"


def gebuehr_anteil(kurs_eur: float, gebuehr: float = GEBUEHR_EUR) -> float:
    """Gebührenanteil in % beim Kauf genau einer Aktie."""
    return gebuehr / kurs_eur * 100 if kurs_eur and not math.isnan(kurs_eur) else math.nan


# --------------------------------------------------------------------------
# Textbausteine je Idee
# --------------------------------------------------------------------------

def setup_text(z: dict) -> str:
    """2–3 Sätze zum technischen Bild."""
    m = z["haupt_muster"]
    saetze = [f"{z['trend']}, RSI {_fmt(z['rsi14'])} ({z['rsi_zone']})."]
    if m is not None:
        saetze.append(f"{m.name} ({m.status}, Sicherheit {m.sicherheit}).")
    else:
        saetze.append("Kein Chartmuster erkannt.")
    if isinstance(z.get("fazit"), str) and z["fazit"] not in ("nicht gefunden", "nicht bewertbar"):
        saetze.append(f"Bewertung {z['fazit']} (KGV {_fmt(z['kgv'])} vs. Branche "
                      f"{_fmt(z['kgv_branche']) if _hat(z, 'kgv_branche') else 'nicht gefunden'}).")
    else:
        saetze.append("Bewertung nicht gefunden.")
    return " ".join(saetze)


def muster_zeilen(z: dict) -> list[str]:
    """Muster-Abschnitt als Listenpunkte."""
    m = z["haupt_muster"]
    if m is None:
        return ["kein Muster erkannt"]
    w = z["waehrung"]
    marke = "Nackenlinie" if "Schulter" in m.name or "Doppel" in m.name else "Ausbruchsmarke"
    zeilen = [
        f"{m.name}, **{m.status}**, Erkennungs-Sicherheit {m.sicherheit}",
        f"{marke}: {preis(m.ausbruchsmarke, w)} · Invalidierung: {preis(m.invalidierung, w)}",
    ]
    if m.bestaetigt_am is not None:
        zeilen.append(f"bestätigt am {datum_de(m.bestaetigt_am)}"
                      + (" – Kurs inzwischen wieder jenseits der Marke (Rücklauf)"
                         if m.ruecklauf(z["kurs"]) else ""))
    if m.gescheitert_am is not None:
        zeilen.append(f"gescheitert am {datum_de(m.gescheitert_am)}")
    if m.kriterien:
        zeilen.append("erfüllt: " + ", ".join(m.kriterien))
    andere = [f"{x.name} ({x.status})" for x in z["alle_muster"] if x is not m]
    if andere:
        zeilen.append("weitere Muster: " + ", ".join(andere))
    return zeilen


def bull_bear(z: dict) -> tuple[str, str]:
    """Technischer Bull- und Bear-Fall."""
    w, m = z["waehrung"], z["haupt_muster"]
    if m is not None and m.richtung in ("bullisch", "offen") and m.status == "in Bildung":
        bull = (f"Schlusskurs über {preis(m.ausbruchsmarke, w)} würde das Muster bestätigen; "
                f"Faustregel-Messziel {preis(m.messziel, w)}.")
    elif m is not None and m.richtung == "bullisch" and m.ruecklauf(z["kurs"]):
        bull = (f"Muster bestätigt, Kurs aber wieder unter {preis(m.ausbruchsmarke, w)} (Rücklauf). "
                f"Ein erneuter Schlusskurs darüber würde das Setup stützen; "
                f"Faustregel-Messziel {preis(m.messziel, w)}.")
    elif m is not None and m.richtung == "bullisch" and m.status == "bestätigt":
        bull = (f"Muster bestätigt. Hält der Kurs über {preis(m.ausbruchsmarke, w)}, bleibt das Setup "
                f"intakt; Faustregel-Messziel {preis(m.messziel, w)}.")
    elif not math.isnan(z["widerstand"]):
        bull = f"Trend setzt sich fort; nächster Widerstand bei {preis(z['widerstand'], w)}."
    else:
        bull = "Trend setzt sich fort; kein Widerstand im letzten Jahr darüber."
    if math.isnan(z["stopp"]):
        bear = "Keine klare Invalidierungsmarke gefunden – Risiko schwer zu begrenzen."
    else:
        bear = f"Schlusskurs unter {preis(z['stopp'], w)} ({z['stopp_art']}): dann ist die Idee falsch."
    return bull, bear


def _hat(z: dict, feld: str) -> bool:
    """True, wenn ein Feld vorhanden und nicht leer/NaN ist."""
    v = z.get(feld)
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    return v != "" and v != []


def _recherche(z: dict) -> dict:
    """Recherche-Teil einer Idee (von Claude per Websuche), sonst leer."""
    r = z.get("recherche")
    return r if isinstance(r, dict) else {}


def score_zeile(z: dict) -> str:
    """Teilpunkte in drei Gruppen: gewertet, noch nicht bewertet, nur angezeigt.

    B und E gelten als bewertet, wenn Fundamentaldaten verarbeitet wurden,
    K erst nach der Websuche (Recherche-Datei).
    """
    berechnet = {"T": True, "M": True, "R": True,
                 "B": isinstance(z.get("gruende_B"), list),
                 "E": isinstance(z.get("gruende_E"), list),
                 "K": bool(_recherche(z))}
    gewertet = [b for b in ("T", "B", "K", "E") if berechnet[b] and b in z["gewertet"]]
    offen = [b for b in ("B", "K", "E") if not berechnet[b]]
    angezeigt = [b for b in ("M", "R") if b not in z["gewertet"]]
    teile = [" · ".join(f"{b} {z[b]}" for b in gewertet) + " (gewertet)"]
    if offen:
        teile.append(", ".join(offen) + ": noch nicht bewertet")
    if angezeigt:
        teile.append(" · ".join(f"{b} {z[b]}" for b in angezeigt) + " (nur angezeigt)")
    return " · ".join(teile) + f" · **{z['klasse']}**"


def bewertung_zeilen(z: dict) -> list[str]:
    """Bewertungsabschnitt mit Kennzahlen und Stand."""
    if not _hat(z, "fazit") or z["fazit"] == "nicht gefunden":
        return ["**Bewertung:** nicht gefunden (keine Fundamentaldaten von Yahoo)"]
    stand = datum_de(z["fund_stand"][:10]) if _hat(z, "fund_stand") else "nicht gefunden"
    zeilen = [
        f"**Bewertung: {z['fazit']}** (Kennzahlen Yahoo Finance, Stand {stand})",
        f"- KGV aktuell {_fmt(z['kgv']) if _hat(z, 'kgv') else 'nicht gefunden'}, "
        f"erwartet {_fmt(z['kgv_erwartet']) if _hat(z, 'kgv_erwartet') else 'nicht gefunden'}",
        f"- Branche ({z.get('branche_basis', 'nicht gefunden')}): KGV-Median "
        f"{_fmt(z['kgv_branche']) if _hat(z, 'kgv_branche') else 'nicht gefunden'}, EV/EBITDA-Median "
        f"{_fmt(z['ev_ebitda_branche']) if _hat(z, 'ev_ebitda_branche') else 'nicht gefunden'}",
        f"- eigener KGV-Median: "
        + (f"{_fmt(z['kgv_hist'])} ({z['kgv_hist_jahre']} Geschäftsjahre)" if _hat(z, "kgv_hist")
           else "nicht gefunden"),
        f"- EV/EBITDA {_fmt(z['ev_ebitda']) if _hat(z, 'ev_ebitda') else 'nicht gefunden'}",
    ]
    if _recherche(z).get("bewertung"):
        zeilen.append(f"- Einordnung: {_recherche(z)['bewertung']}")
    return zeilen


def erwartungs_zeilen(z: dict) -> list[str]:
    """Analysten und Schätzungen."""
    w = z["waehrung"]
    if not _hat(z, "kursziel") and not _hat(z, "analysten"):
        return ["**Analysten:** nicht gefunden"]
    return [
        "**Analysten** (Yahoo Finance)",
        f"- Ø-Kursziel {preis(z.get('kursziel', math.nan), w)} "
        f"({prozent(z.get('kursziel_abstand_pct', math.nan))} zum Kurs), Spanne "
        f"{preis(z.get('kursziel_tief', math.nan), w)} bis {preis(z.get('kursziel_hoch', math.nan), w)}, "
        f"{zahl(z.get('analysten', math.nan), 0)} Analysten",
        f"- Gewinnschätzung nächstes Jahr: {prozent(z.get('eps_trend_pct', math.nan))} in 90 Tagen",
    ]


def kurstreiber_zeilen(z: dict, stichtag: date) -> list[str]:
    """Kurstreiber aus der Recherche, sonst nur der ungeprüfte Yahoo-Hinweis."""
    r = _recherche(z)
    zeilen = []
    if r.get("kurstreiber"):
        zeilen.append(f"**Kurstreiber:** {r['kurstreiber']}")
    else:
        zeilen.append("**Kurstreiber:** nicht geprüft (Websuche mit /top-aktien)")
    for t in r.get("termine") or []:
        zeilen.append(f"- {datum_de(t['datum'])}: {t['ereignis']}"
                      + (f" ({t['quelle']})" if t.get("quelle") else ""))
    if not r.get("termine") and _hat(z, "zahlentermin_yahoo"):
        d = pd.Timestamp(z["zahlentermin_yahoo"])
        if d.date() >= stichtag:
            zeilen.append(f"- Zahlentermin laut Yahoo (ungeprüft): {datum_de(d)}")
        else:
            zeilen.append(f"- letzter Zahlentermin laut Yahoo: {datum_de(d)}; nächster nicht gefunden")
    if r.get("warum_bewegt"):
        zeilen.append(f"- Warum bewegt: {r['warum_bewegt']}")
    return zeilen


def idee_markdown(z: dict, rang: int, chart: str | None, stichtag: date | None = None) -> str:
    """Abschnitt für eine Idee (tablet-freundlich: Listen, keine Tabellen)."""
    stichtag = stichtag or date.today()
    w = z["waehrung"]
    idx = INDEXNAMEN.get(z["index"], z["index"])
    r = _recherche(z)
    bull_t, bear_t = bull_bear(z)
    gebuehr = gebuehr_anteil(z["kurs_eur"])
    abstand_stopp = ((z["kurs"] - z["stopp"]) / z["kurs"] * 100
                     if not math.isnan(z["stopp"]) else math.nan)
    kreuzung = (f" · {z['kreuzung']} am {datum_de(z['kreuzung_datum'])}" if z["kreuzung"] else "")
    eur_hinweis = "" if w == "EUR" else f" ≈ {preis(z['kurs_eur'], 'EUR')}"
    setup = r.get("setup") or setup_text(z)

    teile = [
        f"### {rang}. {z['name']} ({z['ticker']}, {boerse(z['ticker'])}) – Score {z['gesamt']}/{z['max_punkte']}",
        score_zeile(z),
        "",
        f"**Setup:** {setup}",
        "",
        "**Kurs & Trend**",
        f"- Kurs: {preis(z['kurs'], w)}{eur_hinweis} (Schluss {datum_de(z['datum'])})",
        f"- 52 Wochen: {prozent(z['lage_52w_pct'], False, 0)} der Spanne "
        f"({preis(z['tief_52w'], w)} bis {preis(z['hoch_52w'], w)}), "
        f"{prozent(z['abstand_hoch_pct'])} zum Hoch",
        f"- 50-Tage-Linie {preis(z['sma50'], w)} ({prozent(z['abstand_sma50_pct'])}), "
        f"200-Tage-Linie {preis(z['sma200'], w)} ({prozent(z['abstand_sma200_pct'])}){kreuzung}",
        f"- RSI (14): {_fmt(z['rsi14'])} – {z['rsi_zone']}",
        f"- vs. {idx}: 1 M {_pp(z['rs_1m_pp'])}, 3 M {_pp(z['rs_3m_pp'])}, 12 M {_pp(z['rs_12m_pp'])} "
        f"(Aktie 12 M {prozent(z['perf_12m_pct'])}, Index {prozent(z['index_12m_pct'])})",
        f"- Volumen: {_fmt(z['volumen_faktor'])}-fach des 30-Tage-Schnitts"
        + (" – auffällig" if z["volumen_auffaellig"] else ""),
        "",
        "**Muster** (zählt derzeit nicht im Score)" if "M" not in z["gewertet"] else "**Muster**",
        *[f"- {x}" for x in muster_zeilen(z)],
        "",
        f"**Invalidierung der Idee:** {preis(z['stopp'], w)} ({z['stopp_art']}), "
        f"{prozent(-abstand_stopp) if not math.isnan(abstand_stopp) else 'Abstand nicht gefunden'} vom Kurs",
        "",
        *bewertung_zeilen(z),
        "",
        *erwartungs_zeilen(z),
        "",
        *kurstreiber_zeilen(z, stichtag),
        "",
        f"**Bull-Fall:** {r['bull']}" if r.get("bull") else f"**Bull-Fall (technisch):** {bull_t}",
        "",
        f"**Bear-Fall:** {r['bear']}" if r.get("bear") else f"**Bear-Fall (technisch):** {bear_t}",
        "",
        f"**Eingepreist?** {r['eingepreist']}" if r.get("eingepreist") else "**Eingepreist?** nicht bewertet",
        "",
    ]
    if z["warnungen"]:
        teile += ["**Warnungen**", *[f"- {x}" for x in z["warnungen"]], ""]
    teile += [
        f"**Gebühr:** 1 Aktie ≈ {preis(z['kurs_eur'], 'EUR')} → 0,99 € entsprechen "
        f"{prozent(gebuehr, False, 2)} des Orderbetrags"
        + (" (hoch – mehrere Stück oder größere Order senken den Anteil)" if gebuehr > 1 else ""),
        "",
        "**Score-Begründung**",
        f"- T {z['T']}: " + ("; ".join(z["gruende_T"]) or "keine Punkte"),
    ]
    if "gruende_B" in z and isinstance(z["gruende_B"], list):
        teile.append(f"- B {z['B']}: " + "; ".join(z["gruende_B"]))
    if r:
        teile.append(f"- K {z['K']}: {r.get('k_begruendung', 'Recherche')}")
    if "gruende_E" in z and isinstance(z["gruende_E"], list):
        teile.append(f"- E {z['E']}: " + "; ".join(z["gruende_E"]))
    teile += [
        f"- M {z['M']} (nicht gewertet): " + ("; ".join(z["gruende_M"]) or "kein bullisches Muster"),
        f"- R {z['R']} (nicht gewertet): " + "; ".join(z["gruende_R"]),
        "",
    ]
    if chart:
        teile += [f"![Chart {z['ticker']}]({chart})", ""]
    fx = "" if w == "EUR" else f"; Wechselkurs EUR/{w} Yahoo Finance"
    quellen = (f"Kursdaten Yahoo Finance (yfinance), Schluss {datum_de(z['datum'])}; "
               f"Index {idx} Yahoo Finance{fx}")
    if _hat(z, "fund_stand"):
        quellen += f"; Kennzahlen und Analystendaten Yahoo Finance, Stand {datum_de(z['fund_stand'][:10])}"
    teile.append(f"**Quellen:** {quellen}")
    teile += [f"- {q}" for q in r.get("quellen") or []]
    teile.append("")
    return "\n".join(teile)


def _pp(x: float) -> str:
    """Prozentpunkte mit Vorzeichen."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "nicht gefunden"
    return f"{'+' if x > 0 else ''}{_fmt(x)} Pp."


BEGRIFFE = [
    "**RSI (14):** misst, wie stark die Gewinntage der letzten 14 Tage gegenüber den "
    "Verlusttagen überwiegen; über 70 überkauft, unter 30 überverkauft.",
    "**50-/200-Tage-Linie:** Durchschnitt der letzten 50 bzw. 200 Schlusskurse.",
    "**Golden Cross / Death Cross:** 50-Tage-Linie schneidet die 200-Tage-Linie von unten / von oben.",
    "**Pp. (Prozentpunkte):** Differenz zweier Prozentwerte, hier Aktie minus Index.",
    "**KGV:** Kurs-Gewinn-Verhältnis – Kurs geteilt durch den Gewinn je Aktie.",
    "**EV/EBITDA:** Unternehmenswert (inkl. Schulden) geteilt durch den operativen Gewinn vor Abschreibungen.",
    "**Nackenlinie / Ausbruchsmarke:** Linie, deren Durchbruch ein Muster bestätigt.",
    "**Invalidierung:** Marke, bei deren Unterschreiten die Idee falsch ist.",
    "**Messziel:** klassische Faustregel (Musterhöhe ab der Nackenlinie), keine Prognose.",
]


def termine_zeilen(ideen: pd.DataFrame, stichtag: date) -> list[str]:
    """Alle Termine der Ideen chronologisch; ungeprüfte Yahoo-Termine gekennzeichnet."""
    liste = []
    for _, z in ideen.iterrows():
        z = z.to_dict()
        r = _recherche(z)
        for t in r.get("termine") or []:
            liste.append((pd.Timestamp(t["datum"]), f"{z['name']} ({z['ticker']}): {t['ereignis']}"))
        if not r.get("termine") and _hat(z, "zahlentermin_yahoo"):
            d = pd.Timestamp(z["zahlentermin_yahoo"])
            if d.date() >= stichtag:
                liste.append((d, f"{z['name']} ({z['ticker']}): Zahlen laut Yahoo (ungeprüft)"))
    if not liste:
        return ["Keine Termine gefunden – vor jedem Kauf den nächsten Zahlentermin selbst prüfen."]
    return [f"- {datum_de(d)} – {text}" for d, text in sorted(liste, key=lambda x: x[0])]


def bericht_markdown(tabelle: pd.DataFrame, top: int, stichtag: date, meta: dict,
                     charts: dict[str, str] | None = None) -> str:
    """Gesamter Bericht als Markdown.

    ``meta``: Kontext für den Kopf (universum, gefiltert, kursstand je Markt,
    wechselkurse, max_kurs, gewichte, gewichte_grund, stil, kandidaten, recherchiert).
    """
    charts = charts or {}
    ideen = tabelle.head(top)
    max_p = int(ideen["max_punkte"].iloc[0]) if len(ideen) else 0
    recherchiert = bool(meta.get("recherchiert"))
    kopf = [
        f"# Aktien-Screener – {datum_de(stichtag)}",
        "",
        ("Endbericht mit Websuche (Kurstreiber, Termine, Bull-/Bear-Fall)." if recherchiert else
         "**Kandidatenbericht ohne Websuche** – Kurstreiber (K) noch nicht vergeben, "
         "Bull-/Bear-Fall nur technisch."),
        "",
        f"Score 0–{max_p}. Gewichte: "
        + ", ".join(f"{b} {w}" for b, w in meta.get("gewichte", {}).items())
        + ". Bereiche mit Gewicht 0 werden angezeigt, zählen aber nicht.",
        "",
        *([f"Grund der Gewichtung: {meta['gewichte_grund']}", ""] if meta.get("gewichte_grund") else []),
        "**Datenlage**",
        f"- Universum: {meta['universum']} Aktien, nach Filtern {meta['gefiltert']} "
        f"(Kurs unter {zahl(meta['max_kurs'], 0)} €, Marktkap. ab 1 Mrd. €)"
        + (f", Stil „{meta['stil']}“" if meta.get("stil") else ""),
        *([f"- Vorauswahl: die {meta['kandidaten']} technisch stärksten; nur sie haben Fundamentaldaten"]
          if meta.get("kandidaten") else []),
        *[f"- Kursstand {k}: {datum_de(v)}" for k, v in meta.get("kursstand", {}).items()],
        *[f"- Wechselkurs: 1 € = {zahl(v[1], 4)} {k} (Stand {datum_de(v[0])})"
          for k, v in meta.get("wechselkurse", {}).items()],
        "- Kurse und Kennzahlen aus Yahoo Finance können verzögert oder lückenhaft sein.",
        "",
        "**Begriffe**",
        *[f"- {b}" for b in BEGRIFFE],
        "",
        f"## Rangliste (Top {len(ideen)})",
        "",
    ]
    for i, (_, z) in enumerate(ideen.iterrows(), 1):
        m = z["haupt_muster"]
        kurz = f"{m.name} ({m.status})" if m is not None else "kein Muster"
        fazit = f", Bewertung {z['fazit']}" if "fazit" in z and isinstance(z["fazit"], str) else ""
        kopf.append(f"{i}. **{z['name']}** ({z['ticker']}) – {z['gesamt']}/{z['max_punkte']} "
                    f"{z['klasse']} – {z['trend']}{fazit}, {kurz}")
    kopf += ["", "Hohes Potenzial geht fast immer mit hohem Risiko einher. "
             "Der Score fasst Indizien zusammen, er ist keine Prognose.", "", "## Ideen", ""]

    rumpf = [idee_markdown(z.to_dict(), i, charts.get(z["ticker"]), stichtag)
             for i, (_, z) in enumerate(ideen.iterrows(), 1)]
    schluss = [
        "## Termine der nächsten Wochen",
        "",
        *termine_zeilen(ideen, stichtag),
        "",
        "---",
        "",
        ABSCHLUSS,
        "",
    ]
    return "\n".join(kopf + rumpf + schluss)


# --------------------------------------------------------------------------
# Chart
# --------------------------------------------------------------------------

def zeichne_chart(df: pd.DataFrame, z: dict, pfad: Path, tage: int = 250) -> Path:
    """Kurs mit 50/200-Tage-Linie, Muster, Invalidierung und RSI als PNG."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from analyst.indicators import rsi, sma

    voll = df["Close"]
    s50, s200, r = sma(voll, 50), sma(voll, 200), rsi(voll)
    teil = df.iloc[-tage:]
    x = teil.index
    w = z["waehrung"]

    fig, (ax, ax_rsi) = plt.subplots(
        2, 1, figsize=(10, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08},
        facecolor=FARBE["flaeche"])
    for a in (ax, ax_rsi):
        a.set_facecolor(FARBE["flaeche"])
        a.grid(True, color=FARBE["gitter"], lw=0.6)
        a.tick_params(colors=FARBE["tinte2"], labelsize=8)
        for seite in ("top", "right"):
            a.spines[seite].set_visible(False)
        for seite in ("left", "bottom"):
            a.spines[seite].set_color(FARBE["gitter"])

    ax.plot(x, teil["Close"], color=FARBE["tinte"], lw=1.4, label="Schlusskurs")
    ax.plot(x, s50.loc[x], color=FARBE["sma50"], lw=1.4, label="50-Tage-Linie")
    ax.plot(x, s200.loc[x], color=FARBE["sma200"], lw=1.4, label="200-Tage-Linie")

    def marke(wert: float, farbe: str, text: str, stil: str = "--") -> None:
        if wert is None or math.isnan(wert):
            return
        ax.axhline(wert, color=farbe, lw=1.0, ls=stil)
        ax.annotate(f"{text}\n{preis(wert, w)}", (1.0, wert), xycoords=("axes fraction", "data"),
                    xytext=(4, 0), textcoords="offset points", ha="left", va="center",
                    fontsize=7.5, color=FARBE["tinte2"])

    m = z.get("haupt_muster")
    if m is not None and m.linie is not None:
        (d1, p1), (d2, p2) = m.linie
        if d1 < x[0]:  # Linie beginnt vor dem Chart: am linken Rand interpolieren
            anteil = (x[0] - d1) / (d2 - d1)
            d1, p1 = x[0], p1 + (p2 - p1) * anteil
        ax.plot([d1, d2], [p1, p2], color=FARBE["muster"], lw=1.4, ls="--",
                label=f"{m.name} ({m.status})")
        for d, p, text in m.punkte:
            if d >= x[0]:
                ax.scatter([d], [p], s=40, color=FARBE["muster"], edgecolor=FARBE["flaeche"],
                           linewidth=1.5, zorder=4)
                # Tiefpunkte unterhalb beschriften, Hochpunkte oberhalb
                unten = (m.name in ("Doppelboden", "Umgekehrte Kopf-Schulter") or text == "Bandbeginn"
                         or (text == "Ausbruch" and m.richtung == "bärisch"))
                ax.annotate(text, (d, p), xytext=(0, -12 if unten else 7), textcoords="offset points",
                            ha="center", va="top" if unten else "bottom", fontsize=7.5,
                            color=FARBE["tinte2"])
    marke(z.get("stopp", math.nan), FARBE["invalidierung"], "Invalidierung")
    if not math.isnan(z.get("widerstand", math.nan)):
        marke(z["widerstand"], FARBE["tinte2"], "Widerstand", ":")

    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=4, fontsize=8, frameon=False)
    ax.set_ylabel(f"Kurs in {w}", fontsize=8, color=FARBE["tinte2"])
    ax.set_title(f"{z['name']} ({z['ticker']}) – Schluss {datum_de(z['datum'])} – "
                 f"Score {z['gesamt']}/{z['max_punkte']}", fontsize=10, color=FARBE["tinte"], loc="left",
                 pad=24)

    ax_rsi.plot(x, r.loc[x], color=FARBE["tinte"], lw=1.0)
    for grenze in (30, 70):
        ax_rsi.axhline(grenze, color=FARBE["tinte2"], lw=0.8, ls="--")
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_yticks([30, 70])
    ax_rsi.set_ylabel("RSI 14", fontsize=8, color=FARBE["tinte2"])

    pfad.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(pfad, dpi=130, bbox_inches="tight", facecolor=FARBE["flaeche"])
    plt.close(fig)
    return pfad


# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------

def schreibe_bericht(tabelle: pd.DataFrame, kurse: dict[str, pd.DataFrame], meta: dict,
                     top: int = 8, ordner: Path = PROJEKT / "reports",
                     stichtag: date | None = None) -> Path:
    """Schreibt reports/JJJJ-MM-TT.md und die Charts nach reports/charts/."""
    stichtag = stichtag or date.today()
    charts = {}
    for _, z in tabelle.head(top).iterrows():
        name = f"{stichtag.isoformat()}_{z['ticker'].replace('^', '')}.png"
        zeichne_chart(kurse[z["ticker"]], z.to_dict(), ordner / "charts" / name)
        charts[z["ticker"]] = f"charts/{name}"
    pfad = ordner / f"{stichtag.isoformat()}.md"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(bericht_markdown(tabelle, top, stichtag, meta, charts), encoding="utf-8")
    return pfad


# --------------------------------------------------------------------------
# Stil, Recherche-Vorlage, Recherche einlesen
# --------------------------------------------------------------------------

STILE = {
    "ueberverkauft": "RSI unter 40",
    "muster": "bullisches Muster bestätigt oder in Bildung",
    "ausbruch": "Ausbruch nach oben oder laufende Seitwärtsphase",
}


def filtere_stil(tabelle: pd.DataFrame, stil: str | None) -> pd.DataFrame:
    """Optionaler Stil-Filter von /top-aktien (siehe ``STILE``)."""
    if not stil:
        return tabelle
    if stil not in STILE:
        raise ValueError(f"Unbekannter Stil {stil!r}, erlaubt: {', '.join(STILE)}")

    def passt(z) -> bool:
        muster = z["alle_muster"] or []
        if stil == "ueberverkauft":
            return z["rsi14"] < 40
        if stil == "muster":
            return any(m.richtung == "bullisch" and m.status in ("bestätigt", "in Bildung") for m in muster)
        return any(m.name == "Ausbruch nach oben" and m.status == "bestätigt" or m.name == "Seitwärtsphase"
                   for m in muster)

    return tabelle[tabelle.apply(passt, axis=1)].reset_index(drop=True)


RECHERCHE_FELDER = ["K", "k_begruendung", "kurstreiber", "warum_bewegt", "termine", "bull", "bear",
                    "eingepreist", "bewertung", "setup", "quellen"]


def schreibe_recherche_vorlage(tabelle: pd.DataFrame, top: int, pfad: Path, stichtag: date,
                               kurstreiber_regel: list[str]) -> Path | None:
    """YAML-Vorlage für die Websuche. Überschreibt nie eine vorhandene Datei."""
    if pfad.exists():
        return None
    kopf = [
        f"# Recherche zu den Kandidaten vom {datum_de(stichtag)} – auszufüllen per Websuche.",
        "# Regeln: nichts schätzen oder erfinden; fehlt etwas, \"nicht gefunden\" eintragen.",
        "# Jede Aussage mit Quelle und Datum unter quellen.",
        "# K (Kurstreiber, 0–10):",
        *[f"#   {r}" for r in kurstreiber_regel],
        "# eingepreist: niedrig | mittel | hoch",
        "# termine: Liste von {datum: JJJJ-MM-TT, ereignis: Text, quelle: Text}",
        "# Ideen, die nach der Recherche nicht in den Bericht sollen, ganz löschen.",
        "",
    ]
    ideen = {}
    for _, z in tabelle.head(top).iterrows():
        ideen[z["ticker"]] = {
            "name": z["name"],
            "hinweis": (f"Score vorläufig {z['gesamt']}/{z['max_punkte']}, Kurs {preis(z['kurs'], z['waehrung'])} "
                        f"(Schluss {datum_de(z['datum'])}), Zahlentermin laut Yahoo: "
                        f"{datum_de(z['zahlentermin_yahoo']) if _hat(z.to_dict(), 'zahlentermin_yahoo') else 'nicht gefunden'}"),
            **{f: ([] if f in ("termine", "quellen") else None) for f in RECHERCHE_FELDER},
        }
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text("\n".join(kopf) + yaml.safe_dump({"stichtag": stichtag.isoformat(), "ideen": ideen},
                                                     allow_unicode=True, sort_keys=False, width=100),
                    encoding="utf-8")
    return pfad


def lade_recherche(pfad: Path) -> dict:
    """Recherche-Datei lesen und prüfen (K 0–10, eingepreist, Termine mit Datum)."""
    daten = yaml.safe_load(Path(pfad).read_text(encoding="utf-8")) or {}
    ideen = daten.get("ideen") or {}
    fehler = []
    for t, r in ideen.items():
        k = r.get("K")
        if not isinstance(k, int) or not 0 <= k <= 10:
            fehler.append(f"{t}: K muss eine ganze Zahl von 0 bis 10 sein (ist {k!r})")
        if r.get("eingepreist") not in (None, "niedrig", "mittel", "hoch"):
            fehler.append(f"{t}: eingepreist muss niedrig, mittel oder hoch sein")
        for termin in r.get("termine") or []:
            if "datum" not in termin or "ereignis" not in termin:
                fehler.append(f"{t}: Termin braucht datum und ereignis")
    if fehler:
        raise ValueError("Recherche-Datei unvollständig:\n" + "\n".join(fehler))
    return daten


def wende_recherche_an(tabelle: pd.DataFrame, recherche: dict, cfg: dict) -> pd.DataFrame:
    """Übernimmt K und die Texte; Bericht enthält dann genau die recherchierten Ideen."""
    from analyst.scoring import gesamtscore, klasse

    ideen = recherche.get("ideen") or {}
    fehlend = sorted(set(ideen) - set(tabelle["ticker"]))
    if fehlend:
        log.warning("Recherche enthält Ticker ohne aktuelle Bewertung: %s", ", ".join(fehlend))
    zeilen = []
    for _, z in tabelle[tabelle["ticker"].isin(ideen)].iterrows():
        z = z.to_dict()
        r = ideen[z["ticker"]]
        z["K"] = int(r["K"])
        z["recherche"] = r
        teile = {b: z.get(b, 0) for b in ("T", "M", "R", "B", "K", "E")}
        z["gesamt"], z["max_punkte"] = gesamtscore(teile, cfg)
        z["klasse"] = klasse(z["gesamt"], z["max_punkte"], cfg)
        zeilen.append(z)
    if not zeilen:
        return tabelle.iloc[:0]
    return pd.DataFrame(zeilen).sort_values(cfg["sortierung"], ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------
# Kommandozeile
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """Lauf für /top-aktien.

    Stufe 1 (ohne --recherche): Daten, Filter, Technik, Vorauswahl, Fundamentaldaten
      -> Kandidatenbericht reports/JJJJ-MM-TT.md und Vorlage reports/JJJJ-MM-TT_recherche.yaml
    Stufe 2 (mit --recherche DATEI): K und Texte aus der Websuche übernehmen
      -> Endbericht; mit --journal Eintrag ins Ideen-Journal
    """
    import argparse
    import logging

    from analyst.data import (kennzahlen_fuer_filter, lade_markt, lade_marktkap,
                              lade_wechselkurse)
    from analyst.fundamentals import ergaenze_fundamentals, lade_fundamentals
    from analyst.scoring import bewerte_universum, lade_scoring_config
    from analyst.universe import lade_config, lade_universum, wende_filter_an

    p = argparse.ArgumentParser(description="Screener-Bericht erstellen")
    p.add_argument("--markt", nargs="*", help="de, us, jp (Standard: alle)")
    p.add_argument("--stil", choices=list(STILE), help="optionaler Stil-Filter")
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--kandidaten", type=int, default=40, help="Vorauswahl für Fundamentaldaten")
    p.add_argument("--max-kurs", type=float, help="Preisgrenze in Euro")
    p.add_argument("--recherche", type=Path, help="ausgefüllte Recherche-Datei (Stufe 2)")
    p.add_argument("--ohne-fundamentals", action="store_true")
    p.add_argument("--nur-cache", action="store_true")
    p.add_argument("--journal", action="store_true", help="Ideen in journal/ideen.csv eintragen")
    p.add_argument("--ausgabe", type=Path, default=PROJEKT / "reports", help="Ordner für Bericht und Vorlage")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stichtag = date.today()

    cfg_u, cfg_s = lade_config(), lade_scoring_config()
    aktien = lade_universum(cfg_u, args.markt)
    kurse = lade_markt(cfg_u, aktien, nur_cache=args.nur_cache)
    fx = lade_wechselkurse(cfg_u, nur_cache=args.nur_cache)
    kap = lade_marktkap([a.ticker for a in aktien], nur_cache=args.nur_cache,
                        max_alter_tage=cfg_u.get("marktkap_max_alter_tage", 7))
    behalten, _ = wende_filter_an(kennzahlen_fuer_filter(aktien, kurse, fx, kap),
                                  cfg_u["filter"], args.max_kurs)
    tabelle = filtere_stil(bewerte_universum(aktien, kurse, behalten, cfg_s), args.stil)
    vorauswahl = tabelle.head(args.kandidaten)
    if not args.ohne_fundamentals and len(vorauswahl):
        fund = lade_fundamentals(list(vorauswahl["ticker"]), details=True, nur_cache=args.nur_cache)
        basis = lade_fundamentals(list(behalten["ticker"]), details=False, nur_cache=True)
        # Jahresendkurse für den eigenen KGV-Median: 10-Jahres-Cache des Backtests, falls vorhanden
        from analyst.data import DATENORDNER, KursCache, lade_kurse

        lang = lade_kurse(list(vorauswahl["ticker"]), None, cache=KursCache(DATENORDNER / "prices_10y"),
                          nur_cache=True)
        vorauswahl = ergaenze_fundamentals(vorauswahl, fund, basis, kurse, cfg_s, kurse_historie=lang,
                                           maerkte={a.ticker: a.markt for a in aktien})
    top = args.top
    if args.recherche:
        vorauswahl = wende_recherche_an(vorauswahl, lade_recherche(args.recherche), cfg_s)
        top = len(vorauswahl)

    meta = {
        "universum": len(aktien), "gefiltert": len(behalten),
        "max_kurs": args.max_kurs or cfg_u["filter"]["max_kurs_eur"],
        "kursstand": {cfg_u["maerkte"][m]["name"]: g["datum"].max()
                      for m, g in vorauswahl.groupby("markt")} if len(vorauswahl) else {},
        "wechselkurse": {w: (s.index[-1], float(s.iloc[-1])) for w, s in fx.items()},
        "gewichte": cfg_s["gewichte"], "gewichte_grund": cfg_s.get("gewichte_grund", ""),
        "stil": STILE.get(args.stil, "") if args.stil else "",
        "kandidaten": min(args.kandidaten, len(tabelle)) if not args.ohne_fundamentals else 0,
        "recherchiert": bool(args.recherche),
    }
    pfad = schreibe_bericht(vorauswahl, kurse, meta, top, ordner=args.ausgabe, stichtag=stichtag)
    print(f"Bericht: {pfad}")
    if not args.recherche:
        vorlage = schreibe_recherche_vorlage(vorauswahl, top, args.ausgabe / f"{stichtag}_recherche.yaml",
                                             stichtag, cfg_s.get("kurstreiber_regel", []))
        print(f"Recherche-Vorlage: {vorlage}" if vorlage else
              "Recherche-Vorlage existiert bereits – nicht überschrieben")
    if args.journal:
        from analyst.journal import ideen_aus_bewertung, lade_journal, speichere_journal, trage_ein

        quelle = str(pfad.relative_to(PROJEKT)) if pfad.is_relative_to(PROJEKT) else str(pfad)
        neue = ideen_aus_bewertung(vorauswahl, kurse, top, quelle)
        journal, anzahl = trage_ein(lade_journal(), neue)
        speichere_journal(journal)
        print(f"Journal: {anzahl} neue Ideen eingetragen ({len(journal)} gesamt)")


if __name__ == "__main__":
    main()
