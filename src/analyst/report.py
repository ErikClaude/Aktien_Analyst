"""Markdown-Bericht und Charts.

Aufbau pro Idee nach CLAUDE.md Abschnitt 5. Felder, für die noch keine
Daten vorliegen (Bewertung, Kurstreiber, Eingepreist?), stehen ausdrücklich
als "nicht bewertet" bzw. "nicht geprüft" im Bericht – nichts wird geschätzt.
Bull- und Bear-Fall sind hier rein technisch; die inhaltliche Fassung
schreibt Claude beim Befehl /top-aktien nach der Websuche.

Aufruf:  PYTHONPATH=src python -m analyst.report [--markt de us jp] [--top 8] [--max-kurs 100]
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pandas as pd

from analyst.scoring import _fmt
from analyst.universe import PROJEKT

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
    saetze.append("Fundamentaldaten noch nicht bewertet.")
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


def idee_markdown(z: dict, rang: int, chart: str | None) -> str:
    """Abschnitt für eine Idee (tablet-freundlich: Listen, keine Tabellen)."""
    w = z["waehrung"]
    idx = INDEXNAMEN.get(z["index"], z["index"])
    bull, bear = bull_bear(z)
    gebuehr = gebuehr_anteil(z["kurs_eur"])
    abstand_stopp = ((z["kurs"] - z["stopp"]) / z["kurs"] * 100
                     if not math.isnan(z["stopp"]) else math.nan)
    kreuzung = (f" · {z['kreuzung']} am {datum_de(z['kreuzung_datum'])}" if z["kreuzung"] else "")
    eur_hinweis = "" if w == "EUR" else f" ≈ {preis(z['kurs_eur'], 'EUR')}"

    teile = [
        f"### {rang}. {z['name']} ({z['ticker']}, {boerse(z['ticker'])}) – Score {z['gesamt']}/{z['max_punkte']}",
        f"T {z['T']} · M {z['M']} · R {z['R']} · B, K, E: nicht bewertet · **{z['klasse']}**",
        "",
        f"**Setup:** {setup_text(z)}",
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
        "**Muster**",
        *[f"- {x}" for x in muster_zeilen(z)],
        "",
        f"**Invalidierung der Idee:** {preis(z['stopp'], w)} ({z['stopp_art']}), "
        f"{prozent(-abstand_stopp) if not math.isnan(abstand_stopp) else 'Abstand nicht gefunden'} vom Kurs",
        "",
        "**Bewertung:** nicht bewertet (Fundamentaldaten folgen mit dem Modul fundamentals)",
        "",
        "**Kurstreiber:** nicht geprüft (Websuche folgt mit /top-aktien)",
        "",
        f"**Bull-Fall (technisch):** {bull}",
        "",
        f"**Bear-Fall (technisch):** {bear}",
        "",
        "**Eingepreist?** nicht bewertet",
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
        f"- M {z['M']}: " + ("; ".join(z["gruende_M"]) or "kein bullisches Muster"),
        f"- R {z['R']}: " + "; ".join(z["gruende_R"]),
        "",
    ]
    if chart:
        teile += [f"![Chart {z['ticker']}]({chart})", ""]
    fx = "" if w == "EUR" else f"; Wechselkurs EUR/{w} Yahoo Finance"
    teile += [f"**Quellen:** Kursdaten Yahoo Finance (yfinance), Schluss {datum_de(z['datum'])}; "
              f"Index {idx} Yahoo Finance{fx}", ""]
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
    "**Nackenlinie / Ausbruchsmarke:** Linie, deren Durchbruch ein Muster bestätigt.",
    "**Invalidierung:** Marke, bei deren Unterschreiten die Idee falsch ist.",
    "**Messziel:** klassische Faustregel (Musterhöhe ab der Nackenlinie), keine Prognose.",
]


def bericht_markdown(tabelle: pd.DataFrame, top: int, stichtag: date, meta: dict,
                     charts: dict[str, str] | None = None) -> str:
    """Gesamter Bericht als Markdown.

    ``meta``: Kontext für den Kopf (universum, gefiltert, ausgeschlossen_gruende,
    kursstand je Markt, wechselkurse, max_kurs).
    """
    charts = charts or {}
    ideen = tabelle.head(top)
    max_p = int(ideen["max_punkte"].iloc[0]) if len(ideen) else 30
    kopf = [
        f"# Aktien-Screener – {datum_de(stichtag)}",
        "",
        f"Ausbaustufe 1: Score aus Technik, Muster und Risiko (0–{max_p}). "
        "Bewertung, Kurstreiber und Erwartungen sind noch nicht enthalten.",
        "",
        "**Datenlage**",
        f"- Universum: {meta['universum']} Aktien, nach Filtern {meta['gefiltert']} "
        f"(Kurs unter {zahl(meta['max_kurs'], 0)} €, Marktkap. ab 1 Mrd. €), bewertet {len(tabelle)}",
        *[f"- Kursstand {k}: {datum_de(v)}" for k, v in meta.get("kursstand", {}).items()],
        *[f"- Wechselkurs: 1 € = {zahl(v[1], 4)} {k} (Stand {datum_de(v[0])})"
          for k, v in meta.get("wechselkurse", {}).items()],
        "- Kurse aus Yahoo Finance können verzögert oder lückenhaft sein.",
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
        kopf.append(f"{i}. **{z['name']}** ({z['ticker']}) – {z['gesamt']}/{z['max_punkte']} "
                    f"{z['klasse']} – {z['trend']}, {kurz}")
    kopf += ["", "Hohes Potenzial geht fast immer mit hohem Risiko einher. "
             "Der Score fasst Indizien zusammen, er ist keine Prognose.", "", "## Ideen", ""]

    rumpf = [idee_markdown(z.to_dict(), i, charts.get(z["ticker"]))
             for i, (_, z) in enumerate(ideen.iterrows(), 1)]
    schluss = [
        "## Termine der nächsten Wochen",
        "",
        "Noch nicht geprüft – Termine kommen per Websuche mit /top-aktien. "
        "Bis dahin vor jedem Kauf den nächsten Zahlentermin selbst prüfen.",
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


def main(argv: list[str] | None = None) -> None:
    """Gesamter Lauf: Daten, Filter, Score, Bericht."""
    import argparse
    import logging

    from analyst.data import (kennzahlen_fuer_filter, lade_markt, lade_marktkap,
                              lade_wechselkurse)
    from analyst.scoring import bewerte_universum, lade_scoring_config
    from analyst.universe import lade_config, lade_universum, wende_filter_an

    p = argparse.ArgumentParser(description="Screener-Bericht erstellen")
    p.add_argument("--markt", nargs="*", help="de, us, jp (Standard: alle)")
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--max-kurs", type=float, help="Preisgrenze in Euro")
    p.add_argument("--nur-cache", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg_u, cfg_s = lade_config(), lade_scoring_config()
    aktien = lade_universum(cfg_u, args.markt)
    kurse = lade_markt(cfg_u, aktien, nur_cache=args.nur_cache)
    fx = lade_wechselkurse(cfg_u, nur_cache=args.nur_cache)
    kap = lade_marktkap([a.ticker for a in aktien], nur_cache=args.nur_cache,
                        max_alter_tage=cfg_u.get("marktkap_max_alter_tage", 7))
    behalten, _ = wende_filter_an(kennzahlen_fuer_filter(aktien, kurse, fx, kap),
                                  cfg_u["filter"], args.max_kurs)
    tabelle = bewerte_universum(aktien, kurse, behalten, cfg_s)
    meta = {
        "universum": len(aktien), "gefiltert": len(behalten),
        "max_kurs": args.max_kurs or cfg_u["filter"]["max_kurs_eur"],
        "kursstand": {cfg_u["maerkte"][m]["name"]: g["datum"].max()
                      for m, g in tabelle.groupby("markt")} if len(tabelle) else {},
        "wechselkurse": {w: (s.index[-1], float(s.iloc[-1])) for w, s in fx.items()},
    }
    pfad = schreibe_bericht(tabelle, kurse, meta, args.top)
    print(f"Bericht: {pfad}")


if __name__ == "__main__":
    main()
