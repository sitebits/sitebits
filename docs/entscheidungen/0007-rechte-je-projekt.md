# 0007 Rechte je Projekt

- **Status:** angenommen
- **Datum:** 2026-09-27

## Kontext

Eine Instanz enthält Projekte unterschiedlicher Auftraggeber. Wer ein Projekt nicht sehen darf,
soll auch nicht erfahren, dass es existiert.

## Entscheidung

- Zwei Rollen **je Projekt**: **Betrachter** (lesen, Draufsicht, Deep-Link) und **Bearbeiter**
  (zusätzlich Titel und Beschreibung, POIs, Messungen, Export). **Admins** haben implizit alle Rechte
  und verwalten Konten, Einladungen und Rechte.
- Keine Gruppen oder Organisationen.
- Jede Prüfung serverseitig bei jeder Anfrage. Ein Projekt ohne Recht verhält sich wie ein nicht
  vorhandenes (**404 statt 403**), ebenso Verwaltungsrouten für Nicht-Admins.
- API-Tokens tragen höchstens die Rechte des Kontos, eingeschränkt auf lesen oder lesen+schreiben.

## Konsequenzen

- Rechte-Matrix Nutzer × Projekt im Portal und gleichwertig per CLI; jede Änderung wird protokolliert.
- Mandantentrennung über Gruppen wäre eine spätere, eigene Entscheidung.

## Nachtrag 2026-10: Rolle Rückmelder

Zwischen Betrachter und Bearbeiter kommt eine dritte Rolle je Projekt hinzu: **Rückmelder**
(`rueckmelder`). Ein Rückmelder darf POIs anlegen und nur seine eigenen ändern oder löschen; die
Prüfung des Urhebers läuft serverseitig in derselben Transaktion wie das Schreiben. Seine Einträge
tragen erzwungen die Kategorie „Rückmeldung“, je Rückmelder und Projekt sind höchstens 100 aktive
Einträge erlaubt. Messungen, Projekttitel und -beschreibung, Export und Verlauf bleiben Bearbeitern
vorbehalten. API-Tokens eines Rückmelders erben seine Rolle und tragen nie mehr Rechte als sie.
Die Migration 0009 baut die Tabelle `project_roles` mit erweitertem `CHECK` neu auf, vorhandene
Rollen bleiben erhalten; sie läuft nur vorwärts, der Rückweg ist die Sicherung. Eine eigene
Entscheidung ist nicht nötig, weil die Rolle die Rechte je Projekt nur um eine Stufe erweitert.

## Nachtrag 2026-10: Projektleitung

Über dem Bearbeiter steht eine vierte Stufe: die **Projektleitung** (`projektleitung`). Die Prüfung
folgt einer Rangfolge (Betrachter, Rückmelder, Bearbeiter, Projektleitung; Admin ist das
Kontomerkmal) statt einer Menge schreibender Rollen. Die Projektleitung darf alles, was ein
Bearbeiter darf, und verwaltet ihr Projekt: Beteiligte einladen, die Stufen darunter vergeben und
entziehen, Gewerke, Kategorien, Schalter. Sie vergibt nie ihre eigene Stufe und ändert keine Admins
und keine andere Projektleitung; die Prüfung liest Ziel und alte Rolle in derselben
Schreibtransaktion. Die Verwaltung läuft nur mit Browser-Sitzung, nie mit einem API-Token. Eine
projektgebundene Einladung an ein bestehendes Konto erteilt den Zugriff ohne Zurücksetzen der
Anmeldung und stuft nie herab; das Einlösen jeder Einladung stuft eine vorhandene Rolle ebenfalls
nie herab. Gewerke und Kategorien gehören je zu einem Projekt. Die Migration 0010 läuft nur vorwärts,
der Rückweg ist die Sicherung. Eine eigene Entscheidung ist nicht nötig, weil die Rolle die Rechte je
Projekt um eine weitere Stufe erweitert.

Bewusst hingenommen: Beim Einladen erfährt die Projektleitung, ob zu einer E-Mail-Adresse schon
ein Konto besteht, und auch, ob es gesperrt ist. Jede Einladung steht im Verlauf (`einladung.angelegt`,
`recht.vergeben`); offene Einladungen sind je Projekt auf 200 begrenzt.
