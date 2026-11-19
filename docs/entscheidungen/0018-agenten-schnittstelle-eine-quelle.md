# 0018 Agenten-Schnittstelle aus einer Quelle: Fehlercodes, Fall-Tabelle, Vertrag

- **Status:** angenommen
- **Datum:** 2026-11-12
- **Ergänzt:** [0008](0008-api-first-token-mcp.md) (dort: „Werkzeuge von Hand geschrieben, per Test gegen die OpenAPI geprüft“)

## Kontext

API, Client und MCP-Server sind drei Wege zu denselben Funktionen. Bisher prüfte ein Test nur, dass jedes
MCP-Werkzeug einen Pfad der OpenAPI trifft. Ob es sendet, was die API annimmt, ob jede Operation ein Werkzeug oder
einen Grund dagegen hat und wie eine Ablehnung beim Agenten ankommt, war von Hand gepflegt (Tabelle in
`docs/ki-paritaet.md`, Meldungstexte je HTTP-Status im MCP-Server). Agenten konnten nur auf Text und Status
reagieren.

Geplant ist außerdem ein aus der API erzeugter MCP-Server auf der Instanz (Remote-MCP) mit eigener Anmeldung. Er ist
zurückgestellt: Er schafft eine neue Vertrauensgrenze und braucht eine eigene Sicherheitsprüfung und Entscheidung.

## Entscheidung

1. **Feste Fehlercodes.** Jede Fehlerantwort trägt `code` und, wo vorhanden, `hinweis`; bestimmt an einer Stelle
   (`fehlercodes.py`, über `web.fehler`). Codes werden nur ergänzt. Ein 404 trägt immer denselben Code und
   Hinweis, damit er nicht verrät, ob es ein Projekt gibt.
2. **Eine Fall-Tabelle, drei Läufer.** `docs/public/api/fehlerfaelle.json` beschreibt Ablehnungsfälle mit Status
   und Code. Der Server-Läufer spielt sie gegen die echte Anwendung, Client und MCP prüfen, dass Code und Hinweis
   bei ihnen ankommen. Ein neuer Ablehnungsfall bekommt zuerst eine Zeile.
3. **Vertrag MCP ↔ OpenAPI.** Jede Operation der öffentlichen OpenAPI hat ein Werkzeug (`endpunkte.json`) oder
   eine Begründung (`ohne_werkzeug.json`). Was ein Werkzeug sendet, wird gegen das Request-Schema der Operation
   validiert. Die Werkzeuge selbst bleiben von Hand geschrieben: Beschreibungen für Sprachmodelle, Datenschutz und
   der Zuschnitt nach Aufgaben sind keine Übersetzung der OpenAPI.
4. **Wissen reist mit.** Die Regeln für Agenten stehen einmal in `docs/public/agenten-anleitung.md`, wortgleich als
   MCP-Ressource und als Skill des Clients (Drift-Test).
5. Erzeugte Werkzeuge, Ausdünnen auf aufgabenbezogene Werkzeuge und Remote-MCP bleiben zurückgestellt, bis ein
   Kunde Remote-MCP will.

## Konsequenzen

- „Jede Funktion hat API und MCP oder einen Grund“ ist maschinell geprüft statt nur in der Paritätstabelle.
- Grenze des Vertrags: Die Request-Schemas beschreiben `daten` von Befunden und Messungen als freies Objekt.
  Geprüft wird die Hülle (Pflichtfelder, Typen, `version`); den Inhalt prüfen die Server-Tests.
- Eine neue Operation ohne Werkzeug und ohne Begründung bricht den MCP-Test, eine neue Ablehnung ohne eigenen Code
  bekommt den Standard ihres HTTP-Status.
- Vorbild: die Agenten-Architektur von pascalorg/editor (Vertrag, Operation, Ausführung; Ablehnungen als
  Antworten; Wissensparität), gesichtet am 12.11.2026.
