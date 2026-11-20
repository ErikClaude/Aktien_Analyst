# Aktien_Analyst

Persönlicher Aktien-Analyst in Python (Details: `CLAUDE.md`).

## Einrichtung

```bash
pip install -r requirements.txt
pytest
```

## Daten

- Quelle: yfinance (Yahoo Finance, inoffiziell), Tagesdaten, 2 Jahre.
- Universum: DAX, S&P 500, Nikkei 225 – Listen in `config/tickers/`,
  aktualisieren mit `python scripts/aktualisiere_tickerlisten.py` (Quelle: Wikipedia).
- Kurse laden und Filter anwenden: `PYTHONPATH=src python -m analyst.data [--markt de us jp] [--nur-cache] [--max-kurs 100]`
- Cache in `data/prices/` und `data/meta/` (nicht versioniert). Erster Lauf ca. 6 Minuten,
  danach nur Nachladen nach Handelsschluss.

## Screener-Bericht

```bash
PYTHONPATH=src python -m analyst.report [--markt de us jp] [--top 8] [--max-kurs 100] [--nur-cache]
```

Schreibt `reports/JJJJ-MM-TT.md` und Charts nach `reports/charts/`.
Mit `--journal` werden die Ideen in `journal/ideen.csv` eingetragen.

## Journal

```bash
PYTHONPATH=src python -m analyst.journal aktualisieren   # Kurse nach 5/20/60 Handelstagen ergänzen
PYTHONPATH=src python -m analyst.journal auswerten       # Vergleich mit dem Index -> reports/journal_*.md
```
Score derzeit aus Technik, Muster, Risiko (0–30); Gewichte in `config/scoring.yaml`.

## Backtest

```bash
PYTHONPATH=src python -m analyst.backtest [--zeitraum 10y] [--prozesse 4] [--ordergroesse 500]
PYTHONPATH=src python -m analyst.backtest --nur-auswertung   # gespeicherte Beobachtungen neu auswerten
```

Lädt beim ersten Lauf 10 Jahre Kurse nach `data/prices_10y/` (ca. 6 Minuten), rechnet
dann ca. 3–4 Minuten. Ergebnis: `reports/backtest_JJJJ-MM-TT.md`, Rohdaten in `data/backtest/`.

## /top-aktien

Der Befehl `/top-aktien [de|us|jp] [ueberverkauft|muster|ausbruch] [max 100]` (Datei
`.claude/commands/top-aktien.md`) läuft in zwei Stufen:

```bash
PYTHONPATH=src python -m analyst.report --top 8                       # Stufe 1: Kandidaten + Recherche-Vorlage
# … Claude füllt reports/JJJJ-MM-TT_recherche.yaml per Websuche …
PYTHONPATH=src python -m analyst.report --nur-cache --recherche reports/JJJJ-MM-TT_recherche.yaml --journal
```

Fundamentaldaten (Yahoo) werden 7 Tage gecacht. Für den Branchenvergleich einmal pro Woche:
`python scripts/aktualisiere_fundamentals.py` (ca. 20 Minuten; bricht bei Yahoo-Sperre ab und
setzt beim nächsten Start fort).
