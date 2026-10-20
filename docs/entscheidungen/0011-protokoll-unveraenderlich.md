# 0011 Unveränderliches Bearbeitungsprotokoll

- **Status:** angenommen
- **Datum:** 2026-09-27

## Kontext

Bearbeitungen an POIs, Messungen, Metadaten, Konten und Rechten sollen nachvollziehbar sein („wer
hat wann was geändert“). Freitexte in POIs und Beschreibungen können dabei Personenbezug enthalten,
und Konten müssen auf Verlangen anonymisiert werden können.

## Entscheidung

- Tabelle `ereignisse`, **nur anhängend**: SQLite-Trigger verbieten UPDATE und DELETE; jede
  schreibende Aktion aus Web, CLI oder API erzeugt ein Ereignis mit Vorher/Nachher. Keine IP, kein
  User-Agent; gespeichert wird die Nutzer-ID, die Anzeige löst den Namen auf.
- **Hinweis statt Schwärzen:** Formulare für POIs und Beschreibungen weisen darauf hin, dass der
  Text für alle Projektbeteiligten sichtbar und dauerhaft im Verlauf ist und keine Personendaten
  enthalten soll. Eine Schwärz-Ausnahme im Trigger würde einen neuen Manipulationsweg öffnen.
- `anonymisieren` ersetzt die Kontodaten; Freitexte im Protokoll bleiben unverändert.
- Der optionale Git-Export nutzt pseudonyme Autoren; Klarnamen nur mit Einwilligung der Person.

## Konsequenzen

- Das Protokoll ist als Nachweis belastbar.
- Trägt jemand dennoch Personendaten in einen Freitext ein, braucht ein Löschbegehren eine eigene,
  kontrollierte Ausnahme (Migration mit Protokollvermerk).
- Reste auf der Platte (freie Seiten, WAL, Sicherungen) verschwinden erst nach Checkpoint, VACUUM und
  Ablauf der Sicherungen; die Betriebsanleitung beschreibt den Handgriff.
