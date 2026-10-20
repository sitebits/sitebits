# scanview-nachweise

Orchestriert die Freigabekette für Release-Nachweise (SBOM, Schwachstellen-Scan, signiertes
Manifest) der scanview-Plattform. Laufzeit bewusst ohne Fremdabhängigkeiten — nur die
Standardbibliothek; gehärtete Fremdwerkzeuge laufen als per Digest gepinnte Container.

Einrichten: `uv sync --locked`. CLI: `uv run scanview-nachweise <befehl>`:

- `register pruefen` — Kontrollmatrix, Anwendbarkeit, Befundregister und Negativtest-Suite
  (`docs/compliance/*.toml`) validieren, inkl. Querprüfung: jeder Retest/Negativtest zeigt auf
  einen vorhandenen Test bzw. Punkt von `caddy-pruefen.sh`, jeder Bezug auf einen Befund oder
  eine Kontrolle.
- `register darstellen [--pruefen] [--betrieb <archiv>]` — validieren und die Markdown-Fassungen erzeugen; mit `--betrieb` zusätzlich `docs/compliance/betriebsnachweise.md` (jüngster Betriebsnachweis je Instanz aus dem Archiv)
  (`docs/compliance/{anwendbarkeit,kontrollmatrix,befunde}.md`,
  `docs/public/{kontrollmatrix,befunde}.md`); mit `--pruefen` nur vergleichen (Exit 2 bei Drift,
  schreibt nichts).
- `gate befunde [--heute JJJJ-MM-TT]` — Befundregister gegen die Release-Schwelle;
  Exit 1 und je blockierendem Befund eine Zeile `BLOCKIERT …`.
- `gate negativtests` — Sicherheits-Negativtests je Kategorie ausführen (je Teilprojekt
  `uv run --locked pytest -q <nodeids>`); Instanzkategorien werden nur gelistet. Exit 1, wenn
  eine Kategorie rot ist.
- `sbom erzeugen --worktree <frischer Worktree> --version <v> --ziel <ordner>
  [--ersteller <mail>] [--arbeitsordner <ordner>]` — vier Release-Artefakte bauen, je Artefakt
  und gesamt ein SBOM nach BSI TR-03183-2 (CycloneDX 1.6) erzeugen, Lieferumfang-Abdeckung
  fail-closed prüfen (Ausnahmen nur begründet in `abdeckung-ausnahmen.toml`), mit cyclonedx-cli
  und sbomqs validieren. Installiert fehlende SBOM-Werkzeuge aus ihren Lockfiles (Netzschritt).
- `db-aktualisieren [--cache <ordner>]` — trivy-DB laden (einziger Netzschritt des Scans,
  Standard-Cache `~/.cache/scanview-nachweise/trivy`), protokolliert in `db-stand.json`.
- `scan --sbom-ordner <ziel>/sbom [--cache <ordner>] [--vex <datei>] [--heute JJJJ-MM-TT]` —
  die vier Artefakt-SBOMs mit trivy ohne Netz scannen; DB höchstens 24 h alt; VEX-Gate
  (`docs/compliance/vex.openvex.json`) plus Befund-Gate; schreibt `scan-ergebnis.json`. Exit 1
  und je blockierendem Treffer/Befund eine Zeile `BLOCKIERT …`, Treffer „mittel“ ohne VEX als
  `VORSCHLAG B-SCAN-…`.
- `manifest pruefen <manifest.json> [--allowed-signers <datei>] [--artefakte <ordner>]
  [--nur-ausgeliefert]` — SSH-Signatur (`manifest.json.sig`, Namespace `scanview-release`,
  Kennung `release@scanview`, Standard `nachweise/allowed_signers`), kanonische Form und
  optional die SHA-256 der Artefakte prüfen.
- `pruefen sast [--wurzel <worktree>] [--arbeitsordner <ordner>]` — Semgrep (vendierter
  Regelsatz, gegen `sast/regeln.lock` geprüft) und Bandit ohne Netz über jede versionierte
  Quelldatei; Gate gegen Befundregister (Fingerabdruck) und `docs/compliance/sast-ausnahmen.toml`.
  Schreibt die Rohberichte und `sast.json`; Exit 1 und je blockierendem Fund `BLOCKIERT …`.
- `pruefen fuzzing [--wurzel <worktree>] [--arbeitsordner <ordner>]` — Schemathesis gegen eine
  Wegwerf-Instanz aus dem Worktree (gebautes `web/dist`, Server-venv; Caddy auf
  `localhost:18090`, Server auf `127.0.0.1:18097`, beide Ports frei; synthetische Pakete), je als
  Betrachter und Admin mit Seed, Beispielzahl, Checks und Ausschlüssen aus `fuzzing.toml`.
  Schreibt `berichte/fuzzing-{admin,betrachter}.xml` und `fuzzing.json`; Exit 1 und je
  Fehlschlag `BLOCKIERT …`.
- `pruefen egress [--wurzel <worktree>] [--arbeitsordner <ordner>]` — Egress-Test: jede absolute URL
  im gebauten `web/dist` gegen `docs/compliance/egress-ausnahmen.toml` und ein Headless-Chromium
  (Container `playwright`, Host-Netz) gegen eine Wegwerf-Instanz mit dem synthetischen Testpaket
  `nachweise/tests/fixtures/egress/paket` (Ports wie beim Fuzzing). Erfasst jede Anfrage von
  Anmelde-, Barrierefreiheits-, Portal-, Viewer- und Verlaufsseite; Kontrollanfragen belegen die
  Erfassung. Schreibt `berichte/egress-{statisch,browser}.json` und `egress.json`; Exit 1 und je
  Fund `BLOCKIERT …`.
- `pruefen negativkontrollen [--arbeitsordner <ordner>] [--ausgabe <datei>]` — die
  Negativkontrollen mit echten Werkzeugen; jede muss mit genau ihrer Regel abbrechen
  (trivy-DB ≤ 24 h nötig). Schreibt `negativkontrollen.json`.
- `release <commit> --version X.Y.Z [--bestaetigt <diff-hash>] [--trocken]` — die fail-closed
  Release-Kette (einfrieren → register → qualitaetstor → sast → sbom → fuzzing → egress →
  scan → negativkontrollen → lizenzen → matrix → export → manifest → archiv → tag → abschluss),
  Berichte unter
  `<arbeitsordner>/berichte/<stufe>.json`. Braucht `SCANVIEW_BEGRIFFE` (Begriffsliste) und
  `SCANVIEW_VAULT` (Vault-Wurzel für `vault:*`-Nachweise). Checkliste: `docs/public/release.md`;
  Nachweisquellen der Kontrollmatrix: `nachweisquellen.toml`; Lizenz-Positivliste:
  `lizenzen.toml`.
- `abschluss --version X.Y.Z` — Wiederherstellung nach einem Abbruch hinter dem Archivieren:
  prüft den signierten Tag im privaten Repo und den Tag im Export-Repo gegen das archivierte
  Manifest, dann signierter Abschlussnachweis (`abschluesse/<v>/`, Namespace
  `scanview-abschluss`) im Archiv.
- `deploy-ordner --version X.Y.Z --ziel <ordner>` — Release-Ordner für
  `einrichten.sh --release` aus dem committeten Archiv (Manifest, Abschluss, die drei
  ausgelieferten Tarballs; Signaturen und Hashes vorher geprüft).
- `version`.

Signieren und Archivieren übernimmt die Release-Kette (`release.py`; `archiv.archiviere` prüft die
Signatur der abgelegten Kopie). Release-Schlüssel:
`~/.ssh/scanview-release_ed25519` (öffentlicher Teil `signierschluessel.pub`, Prüfkennung
`allowed_signers`; Schlüsselwechsel: Zeile anhängen, nie löschen). Die Signatur belegt, dass die
Kette vollständig auf der Release-Workstation lief — nicht, dass der Autor das Release freigegeben hat.

Exit-Codes: 0 = in Ordnung, 1 = Gate rot, 2 = Regelverletzung/Fehler (`NACHWEIS-FEHLER …`).

Fremdwerkzeuge (trivy, syft, cyclonedx-*, sbomqs, gitleaks) sind in `werkzeuge.json` per
Digest gepinnt; Werkzeuge ohne eigenes Image haben ihr Lockfile unter `werkzeuge/<name>/`.
Auswahl und Probe-Ergebnisse: `docs/compliance/werkzeuge.md`.

Qualitätstor: `uv run ruff check . && uv run mypy src && uv run pytest`.

Tests, die ein externes Werkzeug brauchen (gpg, Docker mit gepinntem Image), überspringen
ohne das Werkzeug mit dem Grund „WERKZEUG FEHLT“ und wiederholen die Auslassung am Ende des
Laufs als Warnung. In der Release-Kette (Stufe `qualitaetstor`) setzt `SCANVIEW_QUALITAETSTOR=1`
automatisch für die pytest-Läufe, dort ist ein fehlendes Werkzeug ein Fehlschlag; von Hand per
Opt-in: `SCANVIEW_QUALITAETSTOR=1 uv run pytest`.
