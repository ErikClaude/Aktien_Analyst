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
