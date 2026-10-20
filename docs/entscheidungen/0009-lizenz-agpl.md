# 0009 Lizenz AGPL-3.0-only und Umgang mit Drittkomponenten

- **Status:** ersetzt durch [0014](0014-lizenz-eupl.md) (Lizenz); die Regeln zu Drittkomponenten gelten weiter
- **Datum:** 2026-09-27

## Kontext

scanview wird als selbst betreibbare Plattform angeboten und soll quelloffen sein. Wer eine
geänderte Fassung als Netzdienst anbietet, soll den Quelltext dieser Fassung weitergeben müssen.
Gleichzeitig soll der Hersteller Beiträge zusätzlich unter anderen Lizenzen anbieten können.

## Entscheidung

- scanview steht unter der **GNU Affero General Public License v3.0 only** (`AGPL-3.0-only`).
- Externe Beiträge nur mit **Contributor License Agreement**; bis der Text vorliegt, werden keine
  externen Beiträge übernommen.
- Drittkomponenten: Im Viewer, im Server und im MCP-Server nur permissive Lizenzen (MIT, BSD,
  Apache-2.0, ISC u. Ä.). Unveränderte GPL-/AGPL-Werkzeuge nur als **isolierter externer Prozess im
  Import-Werkzeug**; MPL-2.0 ist zugelassen.
- Lizenzen der Abhängigkeiten werden je Release gegen eine Liste zulässiger Lizenzen geprüft;
  `docs/THIRD_PARTY.md` und `THIRD_PARTY_LICENSES.txt` im Web-Bundle führen sie auf.

## Konsequenzen

- Das Portal verlinkt Lizenzen und Quelltext (AGPL §13), sobald eine Quelltext-Adresse gesetzt ist.
- Neue Abhängigkeiten mit Copyleft im ausgelieferten Code sind ausgeschlossen, bis eigens
  entschieden.
