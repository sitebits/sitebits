# 0014 Lizenz EUPL-1.2 und Herkunftserklärung (DCO)

- **Status:** angenommen
- **Datum:** 2026-10-08
- **Ersetzt:** den Lizenzteil von [0009](0009-lizenz-agpl.md); dessen Regeln zu Drittkomponenten gelten weiter.

## Kontext

scanview richtet sich an Büros, Vereine, Stiftungen, Behörden und Hochschulen in Deutschland und
der EU. Die bisherige Lizenz `AGPL-3.0-only` erfüllte das Ziel „wer eine geänderte Fassung als
Netzdienst anbietet, gibt den Quelltext weiter“, ist aber auf US-Recht zugeschnitten und regelt
weder Gerichtsstand noch anwendbares Recht. Die geplante Contributor License Agreement diente einer
Doppellizenz, die nicht mehr verfolgt wird.

## Entscheidung

- scanview steht unter der **European Union Public Licence v. 1.2** (`EUPL-1.2`), englische
  Fassung des Lizenztexts (alle Amtssprachen sind gleichwertig).
- Lizenzgeber und Rechteinhaber ist **Andre Stiebitz als natürliche Person**. Unternehmen, die
  Instanzen betreiben oder Dienstleistungen dazu anbieten, nutzen scanview wie alle anderen unter
  der EUPL.
  **Nachtrag 2026-10-16:** Mit der schriftlichen Rechteeinräumung vom 15.10.2026 hat Andre Stiebitz der
  Stiebitz Souverain Systems UG (haftungsbeschränkt) die ausschließlichen Nutzungsrechte eingeräumt. Seitdem ist
  die UG Lizenzgeberin und Rechteinhaberin; Andre Stiebitz bleibt als Urheber genannt (§ 13 UrhG).
- Externe Beiträge mit **Developer Certificate of Origin 1.1** (`Signed-off-by`) statt
  Contributor License Agreement; keine Rechteübertragung.
- Kennzeichnung der Dateien nach REUSE (`REUSE.toml`, `LICENSES/`).

## Begründung

- Die EUPL behandelt das Zugänglichmachen wesentlicher Funktionen über ein Netz als Verbreitung
  (Art. 1) und verlangt dann die Bereitstellung des Quelltexts (Art. 5). Das Copyleft-Ziel der AGPL
  bleibt damit erhalten.
- Gerichtsstand am Sitz des Lizenzgebers und das Recht seines Mitgliedstaats (Art. 14 und 15).
- Haftungs- und Gewährleistungsausschluss mit ausdrücklichem Vorbehalt für zwingendes Recht wie
  die Produkthaftung (Art. 7 und 8).
- Alle eingebundenen Abhängigkeiten sind permissiv lizenziert; GPL-Werkzeuge laufen nur als
  externer Prozess im Import. Der Wechsel berührt sie nicht.

## Konsequenzen

- `LICENSE`, `LICENSES/EUPL-1.2.txt`, README, CONTRIBUTING, der Quelltext-Link im Portal und die
  SBOM (eigene Komponenten `EUPL-1.2`) sind umgestellt.
- Der Quelltext-Link je Instanz (`SCANVIEW_QUELLTEXT_URL`) bleibt Pflicht für Betreiber, die
  scanview für andere anbieten (Art. 5).
- Releases bis einschließlich 0.8.2 bleiben unter `AGPL-3.0-only`; ab 0.9.0 gilt `EUPL-1.2`.
