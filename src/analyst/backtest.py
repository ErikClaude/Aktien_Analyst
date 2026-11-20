"""Backtest: Hätten Score und Muster historisch einen Vorsprung gebracht?

Ablauf:
  1. Stichtage: alle ``schritt_tage`` Handelstage (Kalender des S&P 500),
     sobald genug Vorlauf für 200-Tage-Linie, 52-Wochen-Band und
     12-Monats-Stärke vorliegt.
  2. An jedem Stichtag wird jede Aktie NUR mit den Kursen bis zu diesem Tag
     bewertet (abgeschnittene Daten – kein Blick in die Zukunft). Muster
     sehen nur Eckpunkte, die am Stichtag schon feststanden.
  3. Preisfilter zum damaligen Kurs und Wechselkurs (< 150 €).
  4. Danach: Rendite nach h Handelstagen minus Rendite des Heimatindex
     (Überrendite in Prozentpunkten).

Statistik: Je Gruppe wird zuerst der Mittelwert pro Stichtag gebildet und
dann über die Stichtage gemittelt; t = Mittel / (Std / √Anzahl Stichtage).
Beim 60-Tage-Horizont überlappen sich Stichtage im 20-Tage-Abstand – der
t-Wert ist dort zu optimistisch.

Bekannte Verzerrungen (stehen auch im Bericht):
  - Survivorship Bias: getestet werden die HEUTIGEN Indexmitglieder.
  - Keine historische Marktkapitalisierung -> kein Größenfilter.
  - Musterregeln wurden an Charts von 2026 nachgeschärft (nicht unabhängig).
  - Renditen in Handelswährung gegen den Heimatindex, ohne Währungseffekt.
"""

from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from analyst.data import in_euro
from analyst.patterns import MusterRegeln
from analyst.scoring import bewerte_aktie


@dataclass(frozen=True)
class Einstellungen:
    """Parameter des Backtests."""

    schritt_tage: int = 20             # Abstand der Stichtage (Handelstage)
    vorlauf_tage: int = 260            # Mindesthistorie einer Aktie am Stichtag
    fenster_tage: int = 300            # je Stichtag nur so viele Tage rechnen (Tempo; reicht für alle Indikatoren)
    horizonte: tuple[int, ...] = (5, 20, 60)
    top_n: int = 8                     # wie im Bericht
    max_kurs_eur: float = 150.0
    ordergroesse_eur: float = 500.0    # für die Gebührenrechnung
    gebuehr_eur: float = 0.99          # je Order (Kauf und Verkauf)
    min_stichtage: int = 12            # darunter keine Aussage
    t_schwelle: float = 2.0            # ab hier gilt ein Unterschied als belastbar


# --------------------------------------------------------------------------
# Beobachtungen
# --------------------------------------------------------------------------

def stichtage(kalender: pd.DatetimeIndex, einst: Einstellungen) -> list[pd.Timestamp]:
    """Jeder ``schritt_tage``-te Handelstag nach dem Vorlauf, mit Platz für den längsten Horizont."""
    ende = len(kalender) - 1
    return [kalender[i] for i in range(einst.vorlauf_tage, ende + 1, einst.schritt_tage)]


def _muster_text(alle) -> str:
    """Alle Muster als "Name|Status|Sicherheit;..." für die spätere Auswertung."""
    return ";".join(f"{m.name}|{m.status}|{m.sicherheit}" for m in alle)


def beobachtungen_aktie(
    aktie,
    df: pd.DataFrame,
    index_df: pd.DataFrame | None,
    fx: dict[str, pd.Series],
    tage: list[pd.Timestamp],
    cfg: dict,
    einst: Einstellungen = Einstellungen(),
    regeln: MusterRegeln = MusterRegeln(),
) -> list[dict]:
    """Bewertung und spätere Renditen einer Aktie an allen Stichtagen."""
    zeilen = []
    close = df["Close"]
    idx_close = index_df["Close"] if index_df is not None and len(index_df) else None
    for tag in tage:
        pos = int(close.index.searchsorted(tag, side="right")) - 1
        if pos < einst.vorlauf_tage or (tag - close.index[pos]).days > 5:
            continue  # zu wenig Historie oder Aktie handelte damals nicht
        kurs = float(close.iloc[pos])
        try:
            kurs_eur = float(in_euro(close.iloc[[pos]], aktie.waehrung, fx).iloc[0])
        except KeyError:
            kurs_eur = math.nan
        if not kurs_eur < einst.max_kurs_eur:
            continue  # Preisfilter zum damaligen Kurs (NaN fällt auch heraus)

        # Nur Daten bis zum Stichtag – kein Blick in die Zukunft
        teil = df.iloc[max(0, pos + 1 - einst.fenster_tage):pos + 1]
        idx_teil = index_df.loc[:close.index[pos]].iloc[-einst.fenster_tage:] if idx_close is not None else None
        z = bewerte_aktie(teil, idx_teil, cfg, regeln)
        haupt = z["haupt_muster"]
        zeile = {
            "stichtag": close.index[pos], "ticker": aktie.ticker, "markt": aktie.markt,
            "kurs": kurs, "kurs_eur": kurs_eur,
            "T": z["T"], "M": z["M"], "R": z["R"], "gesamt": z["gesamt"], "klasse": z["klasse"],
            "trend": z["trend"], "rsi14": z["rsi14"], "stopp": z["stopp"],
            "haupt_muster": haupt.name if haupt is not None else "",
            "haupt_status": haupt.status if haupt is not None else "",
            "muster_liste": _muster_text(z["alle_muster"]),
        }
        # Spätere Renditen (Formel wie im Journal)
        for h in einst.horizonte:
            ziel = pos + h
            r = ir = math.nan
            gerissen = False
            if ziel < len(close):
                r = (close.iloc[ziel] / kurs - 1) * 100
                if idx_close is not None:
                    i0, i1 = idx_close.asof(close.index[pos]), idx_close.asof(close.index[ziel])
                    ir = (i1 / i0 - 1) * 100
                if not math.isnan(z["stopp"]):
                    gerissen = bool((close.iloc[pos + 1:ziel + 1] < z["stopp"]).any())
            zeile[f"r_{h}"] = r
            zeile[f"idx_{h}"] = ir
            zeile[f"ue_{h}"] = r - ir
            zeile[f"stopp_{h}"] = gerissen
        zeilen.append(zeile)
    return zeilen


def _arbeit(paket):
    """Hilfsfunktion für die Parallelisierung (muss auf Modulebene stehen)."""
    return beobachtungen_aktie(*paket)


def beobachtungen(
    aktien: list,
    kurse: dict[str, pd.DataFrame],
    fx: dict[str, pd.Series],
    cfg: dict,
    einst: Einstellungen = Einstellungen(),
    kalender: pd.DatetimeIndex | None = None,
    prozesse: int = 1,
) -> pd.DataFrame:
    """Alle Beobachtungen (Aktie × Stichtag) als Tabelle, mit Top-N-Markierung."""
    if kalender is None:
        kalender = kurse["^GSPC"].index
    tage = stichtage(kalender, einst)
    pakete = [(a, kurse[a.ticker], kurse.get(a.index), fx, tage, cfg, einst)
              for a in aktien if a.ticker in kurse]
    if prozesse > 1:
        with ProcessPoolExecutor(prozesse) as ex:
            teile = list(ex.map(_arbeit, pakete, chunksize=4))
    else:
        teile = [_arbeit(p) for p in pakete]
    df = pd.DataFrame([z for t in teile for z in t])
    if df.empty:
        return df
    # Stichtage der Märkte vereinheitlichen (Feiertage): auf den Kalender-Stichtag abbilden
    df["periode"] = pd.DatetimeIndex(tage).searchsorted(df["stichtag"], side="left")
    df = df.sort_values(["periode", "gesamt", "T", "R"], ascending=[True, False, False, False])
    df["rang"] = df.groupby("periode").cumcount() + 1
    df["top"] = df["rang"] <= einst.top_n
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------
# Statistik
# --------------------------------------------------------------------------

def _t_wert(reihe: pd.Series) -> float:
    """t = Mittel / (Std / √n) über die Stichtage."""
    n = len(reihe)
    if n < 2:
        return math.nan
    std = float(reihe.std(ddof=1))
    return float(reihe.mean()) / (std / math.sqrt(n)) if std > 0 else math.nan


def gruppen_statistik(beob: pd.DataFrame, maske: pd.Series, h: int,
                      basis_je_tag: pd.Series | None = None) -> dict:
    """Kennzahlen einer Gruppe für Horizont h.

    ue_mittel   = Mittel über Stichtage der Ø-Überrendite je Stichtag (Pp.)
    t           = ue_mittel / (Std der Stichtagsmittel / √Anzahl Stichtage)  – Test gegen null
    diff        = Mittel über Stichtage von (Gruppe − Messlatte) am selben Stichtag
    t_diff      = t-Wert dieser Differenzen                              – Test gegen die Messlatte
    trefferquote= Anteil Beobachtungen mit Überrendite > 0
    """
    g = beob[maske & beob[f"ue_{h}"].notna()]
    je_tag = g.groupby("periode")[f"ue_{h}"].mean()
    n_tage = len(je_tag)
    mittel = float(je_tag.mean()) if n_tage else math.nan
    t = _t_wert(je_tag)
    diff = t_diff = math.nan
    if basis_je_tag is not None and n_tage:
        d = je_tag - basis_je_tag.reindex(je_tag.index)
        diff, t_diff = float(d.mean()), _t_wert(d)
    return {
        "beobachtungen": len(g), "stichtage": n_tage, "diff_pp": diff, "t_diff": t_diff,
        "rendite_pct": float(g.groupby("periode")[f"r_{h}"].mean().mean()) if n_tage else math.nan,
        "ue_mittel_pp": mittel, "ue_median_pp": float(g[f"ue_{h}"].median()) if len(g) else math.nan,
        "t": t, "trefferquote_pct": float((g[f"ue_{h}"] > 0).mean() * 100) if len(g) else math.nan,
        "stopp_quote_pct": float(g[f"stopp_{h}"].mean() * 100) if len(g) else math.nan,
    }


def rang_korrelation(beob: pd.DataFrame, spalte: str, h: int) -> dict:
    """Spearman-Rangkorrelation zwischen ``spalte`` und Überrendite, je Stichtag, dann gemittelt."""
    werte = []
    for _, g in beob[beob[f"ue_{h}"].notna()].groupby("periode"):
        if len(g) >= 10 and g[spalte].nunique() > 1:
            werte.append(g[spalte].rank().corr(g[f"ue_{h}"].rank()))
    s = pd.Series(werte, dtype=float)
    t = s.mean() / (s.std(ddof=1) / math.sqrt(len(s))) if len(s) > 1 and s.std() > 0 else math.nan
    return {"ic": float(s.mean()) if len(s) else math.nan, "t": t, "stichtage": len(s)}


def muster_masken(beob: pd.DataFrame) -> dict[str, pd.Series]:
    """Eine Maske je Kombination Mustername + Status (über alle Muster einer Beobachtung)."""
    masken = {}
    paare = set()
    for liste in beob["muster_liste"]:
        for eintrag in filter(None, liste.split(";")):
            name, status, _ = eintrag.split("|")
            paare.add((name, status))
    for name, status in sorted(paare):
        schluessel = f"{name}|{status}|"
        masken[f"{name} – {status}"] = beob["muster_liste"].str.contains(schluessel, regex=False)
    return masken


def gruppen(beob: pd.DataFrame) -> dict[str, dict[str, pd.Series]]:
    """Alle Vergleichsgruppen, thematisch sortiert."""
    alle = pd.Series(True, index=beob.index)
    band = pd.cut(beob["gesamt"], [-1, 8, 14, 20, 30], labels=["0–8", "9–14", "15–20", "21–30"])
    ueberverkauft = beob["rsi14"] < 30
    return {
        "Messlatte und Score": {
            "Alle Aktien im Filter (Messlatte)": alle,
            "Top 8 nach Score": beob["top"],
            **{f"Klasse {k}": beob["klasse"] == k for k in ("stark", "gemischt", "schwach")},
            **{f"Score {b}": band == b for b in band.cat.categories},
        },
        "Teil-Scores": {
            "Technik hoch (T ≥ 7)": beob["T"] >= 7, "Technik niedrig (T ≤ 3)": beob["T"] <= 3,
            "Muster vorhanden (M > 0)": beob["M"] > 0, "kein bullisches Muster (M = 0)": beob["M"] == 0,
            "Risiko gering (R ≥ 8)": beob["R"] >= 8, "Risiko hoch (R ≤ 3)": beob["R"] <= 3,
        },
        "RSI und Trend": {
            "RSI < 30 im Aufwärtstrend": ueberverkauft & (beob["trend"] == "Aufwärtstrend"),
            "RSI < 30 im Abwärtstrend": ueberverkauft & (beob["trend"] == "Abwärtstrend"),
            "RSI > 70": beob["rsi14"] > 70,
        },
        "Muster": muster_masken(beob),
    }


def auswerten(beob: pd.DataFrame, einst: Einstellungen = Einstellungen()) -> pd.DataFrame:
    """Statistik für alle Gruppen und Horizonte."""
    basis = {h: beob[beob[f"ue_{h}"].notna()].groupby("periode")[f"ue_{h}"].mean()
             for h in einst.horizonte}
    zeilen = []
    for thema, gr in gruppen(beob).items():
        for name, maske in gr.items():
            for h in einst.horizonte:
                zeilen.append({"thema": thema, "gruppe": name, "horizont": h,
                               **gruppen_statistik(beob, maske, h, basis[h])})
    return pd.DataFrame(zeilen)


MESSLATTE = "Alle Aktien im Filter (Messlatte)"


def urteil(z: pd.Series, einst: Einstellungen) -> str:
    """Nüchterne Einordnung gegenüber der Messlatte (Differenz je Stichtag und deren t-Wert)."""
    if z["gruppe"] == MESSLATTE:
        return "Messlatte"
    if z["beobachtungen"] == 0:
        return "keine Fälle"
    if z["stichtage"] < einst.min_stichtage:
        return "zu wenige Stichtage"
    if math.isnan(z["t_diff"]):
        return "nicht bestimmbar"
    if z["t_diff"] >= einst.t_schwelle:
        return "Vorsprung belastbar"
    if z["t_diff"] <= -einst.t_schwelle:
        return "Nachteil belastbar"
    return "kein belastbarer Unterschied"


# --------------------------------------------------------------------------
# Bericht
# --------------------------------------------------------------------------

def _z(x: float, einheit: str = "Pp.", stellen: int = 2) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "nicht gefunden"
    return f"{'+' if x > 0 else ''}{x:.{stellen}f}".replace(".", ",") + f" {einheit}"


def gebuehr_pct(einst: Einstellungen) -> float:
    """Kauf + Verkauf in % der Ordergröße."""
    return 2 * einst.gebuehr_eur / einst.ordergroesse_eur * 100


def backtest_markdown(beob: pd.DataFrame, aus: pd.DataFrame, einst: Einstellungen,
                      stichtag: date | None = None) -> str:
    """Ergebnisbericht (Listen, keine Tabellen)."""
    stichtag = stichtag or date.today()
    perioden = beob.groupby("periode")["stichtag"].max()
    t = [
        f"# Backtest – {stichtag:%d.%m.%Y}",
        "",
        "**Aufbau**",
        f"- Zeitraum der Stichtage: {perioden.min():%d.%m.%Y} bis {perioden.max():%d.%m.%Y}, "
        f"{len(perioden)} Stichtage im Abstand von {einst.schritt_tage} Handelstagen",
        f"- {len(beob)} Beobachtungen, {beob['ticker'].nunique()} Aktien, "
        f"Preisfilter < {einst.max_kurs_eur:.0f} € zum damaligen Kurs",
        "- Score nur aus Kursen bis zum Stichtag (Technik, Muster, Risiko; 0–30)",
        "- Überrendite = Rendite der Aktie minus Rendite des Heimatindex (Pp. = Prozentpunkte)",
        "- Δ = Abstand zur Messlatte am selben Stichtag, gemittelt; t-Wert dieses Abstands: "
        "ab ±2 gilt der Unterschied als belastbar. Beim 60-Tage-Horizont überlappen die "
        "Stichtage, der t-Wert ist dort zu optimistisch.",
        "",
        "**Bekannte Verzerrungen**",
        "- Survivorship Bias: getestet werden die heutigen Indexmitglieder. Abgestürzte und "
        "aus dem Index entfernte Firmen fehlen – das schönt alle Gruppen, auch die Messlatte.",
        "- Kein Größenfilter (historische Marktkapitalisierung nicht verfügbar).",
        "- Die Musterregeln wurden an Charts von 2026 nachgeschärft; für diesen Zeitraum ist "
        "der Test nicht unabhängig.",
        "- Renditen in Handelswährung, ohne Währungseffekt; Kursdaten Yahoo Finance.",
        "",
        "**Lesart:** Entscheidend ist der Vergleich mit der Messlatte „Alle Aktien im Filter“, "
        "nicht mit null.",
        "",
    ]
    # Rangkorrelation
    t += ["## Ordnet der Score die Aktien richtig?", ""]
    for spalte, name in (("gesamt", "Gesamtscore"), ("T", "Technik"), ("M", "Muster"), ("R", "Risiko")):
        teile = []
        for h in einst.horizonte:
            rk = rang_korrelation(beob, spalte, h)
            teile.append(f"{h} T: {_z(rk['ic'], '', 3).strip()} (t {_z(rk['t'], '', 1).strip()})")
        t.append(f"- **{name}** – Rangkorrelation mit der Überrendite: " + "; ".join(teile))
    t += ["- Lesart: 0 = kein Zusammenhang; schon 0,03–0,05 gelten bei Aktien als nennenswert, "
          "wenn der t-Wert ≥ 2 ist.", ""]

    for thema in aus["thema"].unique():
        t += [f"## {thema}", ""]
        for h in einst.horizonte:
            teil = aus[(aus["thema"] == thema) & (aus["horizont"] == h)]
            mess = aus[(aus["gruppe"] == MESSLATTE) & (aus["horizont"] == h)]
            messlatte = float(mess["ue_mittel_pp"].iloc[0])
            t += [f"**Nach {h} Handelstagen** (Messlatte {_z(messlatte)})", ""]
            for _, z in teil.iterrows():
                if z["beobachtungen"] == 0:
                    t.append(f"- {z['gruppe']}: keine Fälle")
                    continue
                vergleich = ("" if z["gruppe"] == MESSLATTE else
                             f"; Δ {_z(z['diff_pp'])} (t {_z(z['t_diff'], '', 1).strip()})")
                t.append(
                    f"- {z['gruppe']}: Überrendite Ø {_z(z['ue_mittel_pp'])}{vergleich}, "
                    f"Treffer {z['trefferquote_pct']:.0f} %, Invalidierung gerissen "
                    f"{z['stopp_quote_pct']:.0f} %, {z['beobachtungen']} Beob. – "
                    f"**{urteil(z, einst)}**")
            t.append("")

    # Gebühren
    top = aus[(aus["gruppe"] == "Top 8 nach Score")].set_index("horizont")
    g = gebuehr_pct(einst)
    t += ["## Nach Gebühren", "",
          f"Kauf und Verkauf kosten bei {einst.ordergroesse_eur:.0f} € Ordergröße zusammen "
          f"{_z(g, '%').lstrip('+')} des Einsatzes.", ""]
    for h in einst.horizonte:
        if h in top.index:
            t.append(f"- Top 8, {h} Tage: Überrendite Ø {_z(top.at[h, 'ue_mittel_pp'])} → "
                     f"nach Gebühren {_z(top.at[h, 'ue_mittel_pp'] - g)}")
    t += ["", "Ein schlechtes Ergebnis wird genauso berichtet wie ein gutes. "
          "Vergangene Ergebnisse sagen nichts Sicheres über die Zukunft.", ""]
    return "\n".join(t)


# --------------------------------------------------------------------------
# Kommandozeile
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """python -m analyst.backtest [--zeitraum 10y] [--prozesse 4] [--nur-cache]"""
    import argparse
    import logging
    import os

    from analyst.data import DATENORDNER, KursCache, lade_markt, lade_wechselkurse
    from analyst.scoring import lade_scoring_config
    from analyst.universe import PROJEKT, lade_config, lade_universum

    p = argparse.ArgumentParser(description="Backtest des Signal-Scores")
    p.add_argument("--zeitraum", default="10y")
    p.add_argument("--markt", nargs="*")
    p.add_argument("--prozesse", type=int, default=os.cpu_count() or 1)
    p.add_argument("--ordergroesse", type=float, default=500.0)
    p.add_argument("--nur-cache", action="store_true")
    p.add_argument("--nur-auswertung", action="store_true",
                   help="gespeicherte Beobachtungen neu auswerten, nichts neu rechnen")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ordner = DATENORDNER / "backtest"
    einst = Einstellungen(ordergroesse_eur=args.ordergroesse)

    if args.nur_auswertung:
        beob = lade_beobachtungen(ordner / "beobachtungen.csv")
        _schreibe_ergebnis(beob, einst, ordner, PROJEKT)
        return

    cfg_u, cfg_s = lade_config(), lade_scoring_config()
    aktien = lade_universum(cfg_u, args.markt)
    cache = KursCache(DATENORDNER / f"prices_{args.zeitraum}")
    kurse = lade_markt(cfg_u, aktien, cache=cache, zeitraum=args.zeitraum, nur_cache=args.nur_cache)
    # Kalender der Stichtage: S&P 500, sonst der Index des ersten gewählten Marktes
    maerkte = args.markt or list(cfg_u["maerkte"])
    kal_index = "^GSPC" if "^GSPC" in kurse else cfg_u["maerkte"][maerkte[0]]["index"]
    fx = lade_wechselkurse(cfg_u, cache=cache, zeitraum=args.zeitraum, nur_cache=args.nur_cache)
    einst = Einstellungen(max_kurs_eur=cfg_u["filter"]["max_kurs_eur"], ordergroesse_eur=args.ordergroesse)

    beob = beobachtungen(aktien, kurse, fx, cfg_s, einst, kalender=kurse[kal_index].index,
                         prozesse=args.prozesse)
    ordner.mkdir(parents=True, exist_ok=True)
    beob.to_csv(ordner / "beobachtungen.csv", index=False)
    _schreibe_ergebnis(beob, einst, ordner, PROJEKT)


def lade_beobachtungen(pfad) -> pd.DataFrame:
    """Gespeicherte Beobachtungen lesen (für eine erneute Auswertung)."""
    beob = pd.read_csv(pfad, parse_dates=["stichtag"])
    beob["muster_liste"] = beob["muster_liste"].fillna("")
    for s in ("haupt_muster", "haupt_status"):
        beob[s] = beob[s].fillna("")
    return beob


def _schreibe_ergebnis(beob: pd.DataFrame, einst: Einstellungen, ordner, projekt) -> None:
    """Auswertung als CSV und Bericht als Markdown speichern."""
    aus = auswerten(beob, einst)
    aus.to_csv(ordner / "auswertung.csv", index=False)
    ziel = projekt / "reports" / f"backtest_{date.today().isoformat()}.md"
    ziel.write_text(backtest_markdown(beob, aus, einst), encoding="utf-8")
    print(f"Bericht: {ziel}")


if __name__ == "__main__":
    main()
