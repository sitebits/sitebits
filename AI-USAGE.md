# KI-Assistenz in scanview

scanview wird mit Unterstützung von KI-Werkzeugen entwickelt (Code, Tests, Dokumentation).
Verantwortlich bleibt der Mensch:

- **Ein Mensch entscheidet** über Ziel, Umfang und Architektur. Tragende Entscheidungen stehen in
  [`docs/entscheidungen/`](docs/entscheidungen/).
- **Ein Mensch prüft und nimmt ab.** Jede Änderung durchläuft dieselben Prüfungen wie von Hand
  geschriebener Code: Typprüfung, Linter, Tests, statische Analyse und vor jedem Release die
  vollständige Nachweiskette (`nachweise/`).
- **Kennzeichnung:** Commits, an denen ein KI-Werkzeug mitgearbeitet hat, tragen einen
  `Co-Authored-By`-Trailer mit dem Namen des Werkzeugs.
- **Abzeichnen** (`Signed-off-by`, siehe [`CONTRIBUTING.md`](CONTRIBUTING.md)) darf nur ein Mensch.
  Ein KI-Werkzeug zeichnet nie selbst ab.
