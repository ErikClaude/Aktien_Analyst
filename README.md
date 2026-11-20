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
