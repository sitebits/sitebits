# Beiträge zu scanview

[English](CONTRIBUTING.md) · **Deutsch**

scanview steht unter der **European Union Public Licence v. 1.2** (`EUPL-1.2`, siehe `LICENSE`).
Copyright © 2026 Stiebitz Souverain Systems UG (haftungsbeschränkt). Urheber: Andre Stiebitz.

Öffentliches Repository: [sitebits/sitebits](https://github.com/sitebits/sitebits). Fehler und
Funktionswünsche bitte über [Issues](https://github.com/sitebits/sitebits/issues) melden.
Sicherheitslücken vertraulich nach [SECURITY.md](SECURITY.md) melden.

## Herkunftserklärung (Developer Certificate of Origin)

Beiträge (Code, Dokumentation, Tests) werden unter der EUPL-1.2 übernommen, ohne gesonderte
Rechteübertragung. Jeder Commit trägt die Zeile

```
Signed-off-by: Vorname Nachname <adresse@example.org>
```

(`git commit -s`). Damit bestätigt die beitragende Person das
[Developer Certificate of Origin 1.1](https://developercertificate.org/): Sie hat das Recht, den
Beitrag unter der Lizenz des Projekts einzureichen. Abzeichnen darf nur ein Mensch, nie ein
KI-Werkzeug; wie KI-Assistenz gekennzeichnet wird, steht in [`AI-USAGE.md`](AI-USAGE.md).

## Drittkomponenten

Lizenzen der verwendeten Bibliotheken und Werkzeuge: `docs/THIRD_PARTY.md`; die im Viewer
ausgelieferten Pakete stehen beim Build in `THIRD_PARTY_LICENSES.txt`.

## Was öffentlich wird

Das öffentliche Repository enthält je Release (und je Sicherheits-Hotfix) einen Abzug des
Quelltexts, erzeugt aus einem festen Commit des Entwicklungs-Repositorys. Welche Dateien dazu
gehören, bestimmt allein `veroeffentlichung/positivliste.txt`; jede neue oder geänderte Datei wird
vor der Veröffentlichung einzeln bestätigt.

- **Öffentlich:** Quelltext von Viewer, Plattform-Server und MCP-Server samt Tests,
  Build-Konfiguration und Lockfiles, Installations- und Betriebsdateien der Plattform, die
  Nachweiskette (`nachweise/`), Lizenztexte und die Produktdokumente unter `docs/public/`.
- **Privat bleiben:** Arbeitsnotizen und Übergaben, Entscheidungsprotokolle mit Personenbezug,
  Details aus Sicherheitsprüfungen, Betriebsdetails einzelner Installationen sowie Kunden- und
  Projektdaten. Wer etwas davon öffentlich beschreiben will, legt eine eigene, bereinigte Fassung
  unter `docs/public/` an.
- **Prüfungen vor jeder Veröffentlichung:** keine Symlinks oder Submodule, Bilder und
  Binärdateien nur mit freigegebenem Hash (`veroeffentlichung/freigaben.txt`), Suche nach
  Geheimnissen (gitleaks), Suche nach Kunden-, Projekt-, Host- und Nutzernamen, und der Abzug muss
  für sich allein bauen und seine Tests bestehen.
- Test-Fixtures enthalten nur synthetische Daten.

## Was ins Repository gehört

Das Entwicklungs-Repository enthält das Produkt: Quelltext, Tests, Build- und Betriebsdateien,
Produktdokumente (`docs/architektur.md`, `docs/entscheidungen/`, `docs/public/`) und die dauerhaften
Nachweise (Compliance-Register, Audit-Berichte; nicht öffentlich).

**Keine Arbeitsnotizen:** Übergaben (`HANDOFF*`), Eingangslisten (`INBOX*`), Sitzungs- und
Umsetzungsprotokolle (`*.ledger*`, `.superpowers/`, `docs/superpowers/`), Rohfassungen von Specs,
Plänen, Entscheidungsprotokollen und Abnahmen sowie Bildschirmfotos aus Kundendaten werden hier nicht
eingecheckt. Sie liegen in einem getrennten, privaten Arbeits-Repository. Tragende Entscheidungen
kommen in gekürzter, sachlicher Form als ADR nach `docs/entscheidungen/`. Der öffentliche Export
bricht ab, falls eine Arbeitsnotiz doch in die Positivliste gerät.

Commit-Betreffs beschreiben sachlich die Änderung am Produkt.

Öffentliche Beiträge sind vorerst ausgesetzt (siehe oben); ein Weg, Beiträge mit
Herkunftsnachweis zu übernehmen, folgt bei Bedarf.
