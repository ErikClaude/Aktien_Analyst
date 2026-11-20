---
description: Screener + Websuche – die interessantesten Aktien mit Begründung, Bear-Fall und Terminen
argument-hint: "[de|us|jp] [ueberverkauft|muster|ausbruch] [max 100]"
---

# /top-aktien

Arbeite als nüchterner Trader-Analyst nach CLAUDE.md (Abschnitte 1, 2, 5, 9, 10).
Chart zuerst, dann Fundament. Skeptisch gegenüber jedem Signal. Nichts erfinden.

Argumente: `$ARGUMENTS`

## 0. Argumente lesen

- Markt: `de`, `us`, `jp` (mehrere möglich) → `--markt de us`
- Stil: `ueberverkauft`, `muster`, `ausbruch` → `--stil …`
- Preisgrenze: eine Zahl (z. B. `max 100` oder `100`) → `--max-kurs 100`
- Ohne Argumente: alle Märkte, kein Stil, Preisgrenze aus `config/universe.yaml` (150 €).
- Unklares Argument: nachfragen statt raten.

Merke dir die Optionen als `OPTS` – Stufe 1 und Stufe 2 müssen dieselben bekommen.

## 1. Journal nachführen

```bash
PYTHONPATH=src python -m analyst.journal aktualisieren
PYTHONPATH=src python -m analyst.journal auswerten
```

Lies die Auswertung. Gibt es für einen Horizont ≥ 20 Ideen, fasse das Ergebnis im Chat
in 2–3 Sätzen zusammen – auch wenn es schlecht ist.

## 2. Stufe 1: Kandidaten

```bash
PYTHONPATH=src python -m analyst.report OPTS --top 8
```

- Lädt Kurse neu (nach Handelsschluss), filtert, rechnet Technik, holt für die
  40 technisch stärksten Fundamentaldaten und schreibt
  `reports/JJJJ-MM-TT.md` (Kandidatenbericht) und `reports/JJJJ-MM-TT_recherche.yaml` (Vorlage).
- Blockiert das Netz Yahoo: mit `--nur-cache` wiederholen und den Datenstand im Chat nennen.
  Nicht mit Tricks umgehen (CLAUDE.md, Bauweise).
- Steht bei vielen Ideen „Branche: zu wenige Vergleichswerte“: einmal
  `python scripts/aktualisiere_fundamentals.py` laufen lassen (dauert, ca. 20 Minuten) und Stufe 1 wiederholen.

## 3. Websuche je Kandidat

Lies den Kandidatenbericht und die Vorlage. Für jede Idee (Chart zuerst!):

1. **Termine** in den nächsten 8 Wochen: Zahlen, Kapitalmarkttag, Produktstart,
   Studiendaten, Urteile, Zinsentscheid mit Branchenbezug. Datum aus Investor Relations
   oder seriöser Quelle bestätigen. Yahoo-Termine im Bericht sind ungeprüft.
2. **Warum bewegt?** Grund für starke Kursbewegungen der letzten Wochen –
   vorübergehend oder dauerhaft?
3. **Nachrichten und Risiken:** binäre Ereignisse, Gewinnwarnungen, Verschuldung,
   laufende Übernahmen.
4. **Bewertung prüfen:** Passt das Fazit (günstig/fair/teuer)? Value-Falle?
5. **Eingepreist?** niedrig / mittel / hoch – mit kurzer Begründung.

Regeln:
- Jede Aussage mit Quelle und Datum. Kurse aus der Websuche können verzögert sein.
- Fehlt etwas: „nicht gefunden“. Nie Termine, Zahlen oder Zitate erfinden.
- Keine Kaufbefehle, keine Prozentwahrscheinlichkeiten, nie „sicher“ oder „sehr wahrscheinlich“.
- Ideen mit Ausschlussgrund (reiner Social-Media-Hype, Übernahme zu festem Preis,
  Penny Stock) aus der Vorlage **löschen** und im Chat nennen.

## 4. Vorlage ausfüllen

In `reports/JJJJ-MM-TT_recherche.yaml` je Idee:

- `K` (0–10) nach der Regel im Kopf der Datei, `k_begruendung` (1 Satz)
- `kurstreiber` (1–2 Sätze), `warum_bewegt` (1–2 Sätze)
- `termine`: Liste `{datum: JJJJ-MM-TT, ereignis: …, quelle: …}`
- `bull`, `bear` (je 1–2 Sätze; der Bear-Fall nennt die Marke, an der die Idee falsch wäre)
- `eingepreist`: niedrig | mittel | hoch
- `bewertung` (1 Satz Einordnung), optional `setup` (2–3 Sätze, ersetzt den automatischen Text)
- `quellen`: Liste „Quelle, Datum: Inhalt“

## 5. Stufe 2: Endbericht und Journal

```bash
PYTHONPATH=src python -m analyst.report OPTS --nur-cache --recherche reports/JJJJ-MM-TT_recherche.yaml --journal
```

`--nur-cache`, damit der Endbericht auf denselben Daten beruht wie die Kandidaten.
Bei einem Fehler in der Recherche-Datei (z. B. K fehlt) die Datei korrigieren und wiederholen.
Lies den fertigen Bericht einmal ganz und prüfe ihn gegen CLAUDE.md Abschnitt 5.

## 6. Zusammenfassung im Chat

Tablet-freundlich, keine Tabellen:

- Je Idee 3–5 Zeilen: **Name (Ticker) – Score x/40**, Setup in einem Satz,
  Invalidierungsmarke, nächster Termin, Bear-Fall in einem Satz.
- „Termine der nächsten Wochen“ chronologisch.
- Ehrlich zur Methode: Der technische Score hatte im Backtest keinen robusten Vorsprung
  (reports/backtest_*.md); B, K, E sind noch unbelegt – Prüfstein ist das Journal.
- Pfad zum Bericht.
- Schlusssatz: „Recherche-Ideen, keine Anlageberatung – Kurse und Termine vor dem Kauf selbst prüfen.“

## 7. Sichern

Bericht, Charts, Recherche-Datei und `journal/ideen.csv` committen und pushen.
