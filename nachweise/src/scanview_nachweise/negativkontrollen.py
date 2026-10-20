"""Negativkontrollen der Nachweiskette: je Regel ein fester Testfall, der
**nachweislich** abbricht — mit echten Werkzeugen (trivy, gitleaks, ssh-keygen) und genau der
erwarteten Regel. Ein Abbruch aus einem anderen Grund (Werkzeug fehlt, DB veraltet, Image nicht
vorhanden) zählt als **gescheitert**, nicht als bestanden: sonst bewiese die Kontrolle nichts.

Die Kontrollen:

1. `verwundbar` — Fixture-SBOM mit `pkg:npm/lodash@4.17.20` → Scan-Gate blockiert
   (CVE-2021-23337).
2. `db-veraltet` — derselbe DB-Stand, 25 h später geprüft → `db-alter`.
3. `sbom-fehlt` — ein ausgeliefertes Artefakt ohne SBOM → `scan-sbom`.
4. `unzugeordnete-datei` — zusätzliche Datei im Lieferumfang ohne Komponente → `sbom-abdeckung`.
5. `vex-under-investigation` — VEX-Aussage `under_investigation` → Scan-Gate blockiert.
6. `testsecret-im-export` — scanview-Token im Exportpaket → `export-geheimnis` (gitleaks) mit
   der Regel `scanview-api-token` im Bericht; ein Werkzeugfehler zählt nicht.
7. `manifest-veraendert` — Manifest nach der Signatur verändert bzw. Signatur fehlt →
   `signatur`.
8. `unsignierter-commit` — Wegwerf-Archiv mit Vertrauensanker, signierter Stichtag, danach ein
   unsignierter Commit → `commit-signatur`.
9. `npm-signatur-ungueltig` — echte `npm audit signatures`-Fundmeldung (Fixture) durch dieselbe
   Auswertungsfunktion wie die Bau-Stufe → `npm-signaturen`, auch mit einer (wirkungslosen)
   Ausnahme für dasselbe Paket (Ausnahmen gelten nur für fehlende, nie für ungültige Signaturen).
10. `sast-fund-unregistriert` — echter Semgrep- und Bandit-Lauf über absichtlich verwundbare
   Fixtures, bewertet gegen das echte Befundregister und die echten Ausnahmen → `sast-gate`,
   mit Funden ab mittel von beiden Werkzeugen.
11. `fuzzing-serverfehler` — echter Schemathesis-Lauf gegen einen absichtlich kaputten Dienst
   (Fixture, stdlib, freier Loopback-Port) mit einer Operation, die immer 500 liefert →
   `fuzzing-gate`, und zwar nur mit dieser Operation.

12. `egress-fremdanfrage` — im Browser erfasste Anfragen (Fixture) mit einer fehlgeschlagenen
   Anfrage an ein fremdes Ziel und einem Verstoß gegen die Inhaltssicherheitsrichtlinie durch
   dieselbe Auswertung wie die Stufe `egress` → `egress-gate`, und zwar mit dem fremden Ziel.
13. `egress-bundle-url` — Bundle mit einer absoluten URL im Skript und im Stylesheet (Fixture)
   durch dieselbe statische Prüfung ohne Ausnahmen → `egress-gate`, und zwar mit dem fremden Host.

Nichts davon berührt das echte Archiv oder einen echten Schlüssel: Schlüssel, Manifest,
Exportpaket und Git-Repos entstehen im Arbeitsordner; die trivy-DB wird nur gelesen."""

from __future__ import annotations

import hashlib
import json
import secrets
import select
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import (
    egress,
    export,
    fuzzing,
    lint_ratsche,
    manifest,
    register,
    sast,
    sbom,
    scan,
    signaturen,
    vex,
)
from .artefakte import Artefakt, Datei, werte_npm_signaturen_aus
from .fehler import NachweisFehler
from .werkzeuge import Werkzeug, gefilterte_umgebung, git_befehl

SCHEMA = "scanview-negativkontrollen/1"
FIXTURE = Path("nachweise/tests/fixtures/verwundbar/scanview-web-0.0.0.cdx.json")
VERWUNDBAR_PURL = "pkg:npm/lodash@4.17.20"
VERWUNDBAR_ID = "CVE-2021-23337"
NPM_UNGUELTIG_FIXTURE = Path("nachweise/tests/fixtures/npm-signaturen/ungueltig.json")
DATEI = "negativkontrollen.json"
KAPUTT_FIXTURE = Path("nachweise/tests/fixtures/fuzzing/kaputt_server.py")
KAPUTT_OPERATION = "GET /api/v1/kaputt"
EGRESS_ANFRAGEN_FIXTURE = Path("nachweise/tests/fixtures/egress/anfragen_fremd.json")
EGRESS_BUNDLE_FIXTURE = Path("nachweise/tests/fixtures/egress/bundle_fremd")
EGRESS_FREMD = "https://cdn.example.org/x.js"
EGRESS_BUNDLE_HOST = "tiles.example.org"


@dataclass(frozen=True)
class Kontext:
    """Eingaben aller Kontrollen: `wurzel` (Repo bzw. eingefrorener Worktree: Fixture,
    `.gitleaks.toml`), `cache` (trivy-DB, nur gelesen), gepinnte Werkzeuge, ein frischer
    Arbeitsordner und die echte Uhr."""

    wurzel: Path
    cache: Path
    werkzeuge: dict[str, Werkzeug]
    arbeitsordner: Path
    uhr: datetime


@dataclass(frozen=True)
class Kontrolle:
    name: str
    beschreibung: str
    regel: str                 # erwartete Regel des Abbruchs
    enthaelt: str | None       # muss zusätzlich in der Meldung stehen (z. B. die CVE)
    lauf: Callable[[Kontext], None]


# --- Hilfen --------------------------------------------------------------------------------------

def _leeres_vex(ordner: Path) -> Path:
    pfad = ordner / "leer.openvex.json"
    pfad.write_text(json.dumps({"@context": "https://openvex.dev/ns/v0.2.0",
                                "@id": "urn:scanview:vex:negativkontrolle",
                                "author": "Negativkontrolle", "version": 1,
                                "timestamp": "2026-09-28T00:00:00Z", "statements": []}),
                    encoding="utf-8")
    return pfad


def _voraussetzung(was: str, lauf: Callable[[], object]) -> None:
    """Vorbedingung einer Kontrolle: scheitert sie, ist das **kein** erwarteter Abbruch, sondern
    `negativkontrolle-voraussetzung` (z. B. veraltete DB vor dem Scan, ssh-keygen kaputt)."""
    try:
        lauf()
    except NachweisFehler as e:
        raise NachweisFehler("negativkontrolle-voraussetzung", f"{was}: {e}") from None


def _db_frisch(k: Kontext) -> scan.DbStand:
    stand: list[scan.DbStand] = []
    _voraussetzung("trivy-DB aktuell (sonst `db-aktualisieren`)",
                   lambda: stand.append(scan.db_pruefen(k.cache, k.uhr)))
    return stand[0]


def _scan_gate(k: Kontext, vex_pfad: Path, lock_hashes: dict[str, str]) -> None:
    """Fixture echt scannen und wie die Release-Kette bewerten: blockiert → `scan-gate`."""
    _db_frisch(k)
    fixture = k.wurzel / FIXTURE
    daten = scan.trivy_json(fixture, k.cache, vex_pfad, k.werkzeuge)
    treffer = scan.werte_trivy_aus(daten, "web")
    blockiert = vex.treffer_gate(treffer, vex.lade_vex(vex_pfad), k.uhr.date(),
                                 lock_hashes=lock_hashes)
    if blockiert:
        raise NachweisFehler("scan-gate", "; ".join(blockiert))


def _ordner(k: Kontext, name: str) -> Path:
    ordner = k.arbeitsordner / name
    ordner.mkdir(parents=True)
    return ordner


# --- Die Kontrollen --------------------------------------------------------------------------

def _verwundbar(k: Kontext) -> None:
    _scan_gate(k, _leeres_vex(_ordner(k, "verwundbar")), {})


def _db_veraltet(k: Kontext) -> None:
    stand = _db_frisch(k)  # aktuell gültig — sonst prüfte die Kontrolle nichts
    # 25 h nach Inhalt und nach dem Laden: weder frisch noch als verspäteter Stand nachgewiesen.
    geladen = scan.ladezeit(stand) or stand.zeit
    scan.db_pruefen(k.cache, max(stand.zeit, geladen) + timedelta(hours=25))


def _sbom_fehlt(k: Kontext) -> None:
    # Dieselbe Prüfung, die die Release-Stufe „scan“ vor dem Scannen ausführt.
    ordner = _ordner(k, "sbom-fehlt")
    sboms = {}
    for name in sorted(manifest.AUSGELIEFERT - {"server"}):
        sboms[name] = ordner / f"scanview-{name}-0.0.0.cdx.json"
        shutil.copyfile(k.wurzel / FIXTURE, sboms[name])
    scan.pruefe_sbom_vollstaendig(sboms, sorted(manifest.AUSGELIEFERT))


def _unzugeordnete_datei(k: Kontext) -> None:
    bom = json.loads((k.wurzel / FIXTURE).read_text(encoding="utf-8"))
    dateien = ["web/dist/index.html", "web/dist/fremd.bin"]
    artefakt = Artefakt("web", k.arbeitsordner / "scanview-web-0.0.0.tar.gz", "0" * 64,
                        "0" * 128, [Datei(p, hashlib.sha512(p.encode()).hexdigest(), 1)
                                    for p in dateien])
    # Die echten Ausnahmen des geprüften Stands: eine zu breite Ausnahme ließe die Kontrolle
    # scheitern (sie bräche nicht ab) — genau das soll sie zeigen.
    ausnahmen = sbom.lade_ausnahmen(k.wurzel / "nachweise" / "abdeckung-ausnahmen.toml")
    sbom.pruefe_abdeckung(bom, artefakt, ausnahmen)


def _vex_dokument(k: Kontext, pfad: Path, lock: str, **aussage: str) -> Path:
    zeit = k.uhr.isoformat(timespec="seconds")
    pfad.write_text(json.dumps({
        "@context": "https://openvex.dev/ns/v0.2.0", "@id": "urn:scanview:vex:negativkontrolle",
        "author": "Negativkontrolle", "version": 1, "timestamp": zeit,
        "statements": [{
            "vulnerability": {"name": VERWUNDBAR_ID},
            "products": [{"@id": VERWUNDBAR_PURL}],
            "timestamp": zeit,
            "scanview": {"lock_hash": lock, "lockdatei": vex.LOCKDATEIEN["web"],
                         "pruefer": "Negativkontrolle"},
            **aussage,
        }],
    }), encoding="utf-8")
    return pfad


def _vex_under_investigation(k: Kontext) -> None:
    ordner = _ordner(k, "vex")
    lock = "sha256:" + "0" * 64
    locks = {vex.LOCKDATEIEN["web"]: lock}
    # Vorbedingung: dieselbe Aussage als not_affected mit Begründung gibt die CVE frei — sonst
    # bewiese das Blockieren unten nichts über den Status under_investigation.
    frei = _vex_dokument(k, ordner / "frei.openvex.json", lock, status="not_affected",
                         justification="vulnerable_code_not_in_execute_path",
                         impact_statement="Negativkontrolle: template() wird nie aufgerufen")

    def freigegeben() -> None:
        try:
            _scan_gate(k, frei, locks)
        except NachweisFehler as e:
            if VERWUNDBAR_ID in e.text:
                raise NachweisFehler("vex-freigabe", "not_affected gab die CVE nicht frei: "
                                                     + e.text) from None
    _voraussetzung("not_affected gibt frei", freigegeben)
    pruefen = _vex_dokument(k, ordner / "vex.openvex.json", lock, status="under_investigation")
    _scan_gate(k, pruefen, locks)


def _testsecret(k: Kontext) -> None:
    ordner = _ordner(k, "export")
    # Zur Laufzeit erzeugt, nie als Literal im Repo (sonst schlüge gitleaks im echten Export an).
    token = "sv" + "_" + secrets.token_urlsafe(32)[:43]
    (ordner / "konfiguration.py").write_text(f'API_TOKEN = "{token}"\n', encoding="utf-8")
    export.pruefe_geheimnisse(ordner, k.werkzeuge, k.wurzel / export.GITLEAKS_KONFIG)


def _manifest_veraendert(k: Kontext) -> None:
    ordner = _ordner(k, "manifest")
    schluessel = ordner / "wegwerf_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "negativkontrolle",
                    "-f", str(schluessel)], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True, env=gefilterte_umgebung())
    typ, wert = schluessel.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    signers = ordner / "allowed_signers"
    signers.write_text(f'{manifest.KENNUNG} namespaces="{manifest.NAMESPACE}" {typ} {wert}\n',
                       encoding="utf-8")
    daten = {"schema": manifest.SCHEMA, "version": "0.0.0", "erstellt": k.uhr.isoformat()}
    pfad = ordner / manifest.MANIFEST_DATEI
    pfad.write_bytes(manifest.kanonisch(daten))
    sig = manifest.signiere(pfad, schluessel)
    # Unverändert gültig — sonst prüfte die Kontrolle nichts.
    _voraussetzung("unverändertes Manifest gültig", lambda: manifest.pruefe_signatur(pfad, signers))
    pfad.write_bytes(manifest.kanonisch({**daten, "version": "0.0.1"}))
    try:
        manifest.pruefe_signatur(pfad, signers)
    except NachweisFehler as e:
        # Zweiter Fall derselben Regel: ohne Signatur darf es ebenso wenig gelingen.
        sig.unlink()
        try:
            manifest.pruefe_signatur(pfad, signers)
        except NachweisFehler:
            raise e from None
        raise NachweisFehler("negativkontrolle", "Manifest ohne Signatur galt als gültig"
                             ) from None


def _wegwerf_schluessel(ordner: Path, kommentar: str) -> tuple[Path, str, str]:
    schluessel = ordner / "wegwerf_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", kommentar,
                    "-f", str(schluessel)], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True, env=gefilterte_umgebung())
    typ, wert = schluessel.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    return schluessel, typ, wert


def _unsignierter_commit(k: Kontext) -> None:
    """Wie die Stufe `einfrieren`: Anker aus einem (Wegwerf-)Archiv, Prüfung ab dem Stichtag."""
    ordner = _ordner(k, "commit-signatur")
    schluessel, typ, wert = _wegwerf_schluessel(ordner, "negativkontrolle")
    identitaet = "negativkontrolle@scanview.invalid"

    def git(repo: Path, *args: str) -> str:
        # Feste Konfiguration je Aufruf, damit weder Nutzer- noch Systemkonfiguration (Hooks,
        # Signierschlüssel) den Ausgang bestimmen; signiert wird mit der Schlüsseldatei.
        return subprocess.run(
            git_befehl("-c", "user.name=Negativkontrolle",
                       "-c", f"user.email={identitaet}", "-c", "gpg.format=ssh",
                       "-c", f"user.signingkey={schluessel}",
                       "-c", "commit.gpgsign=false", *args, repo=repo),
            check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env=gefilterte_umgebung()).stdout.strip()

    repo, archiv = ordner / "repo", ordner / "archiv"
    for r in (repo, archiv):
        r.mkdir()
        git(r, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-S", "-m", "stichtag")
    stichtag = git(repo, "rev-parse", "HEAD")
    anker = archiv / signaturen.ANKER_ORDNER
    anker.mkdir()
    (anker / signaturen.SIGNERS_DATEI).write_text(
        f'{identitaet} namespaces="git" {typ} {wert}\n', encoding="utf-8")
    (anker / signaturen.WIDERRUF_DATEI).write_text("", encoding="utf-8")
    (anker / signaturen.STICHTAG_DATEI).write_text(
        f'stichtag_commit = "{stichtag}"\naltstaende = []\nzweige = ["main"]\n',
        encoding="utf-8")
    git(archiv, "add", signaturen.ANKER_ORDNER)
    git(archiv, "commit", "-q", "-S", "-m", "vertrauensanker")
    geladen = signaturen.lade_anker(archiv, ordner / "anker-ablage",
                                    genesis=git(archiv, "rev-parse", "HEAD"))
    # Der signierte Stichtag besteht — sonst prüfte die Kontrolle nichts.
    _voraussetzung("signierter Stichtag gültig", lambda: signaturen.pruefe_seit_stichtag(
        repo, geladen, stichtag))
    git(repo, "commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "unsigniert")
    signaturen.pruefe_seit_stichtag(repo, geladen, git(repo, "rev-parse", "HEAD"))


def _npm_signatur_ungueltig(k: Kontext) -> None:
    """Dieselbe Auswertungsfunktion wie die Bau-Stufe (`artefakte.werte_npm_signaturen_aus`) mit
    einer echten `npm audit signatures`-Fundmeldung (Fixture): eine ungültige Registry-Signatur
    bricht immer ab — auch mit einer (hier gezielt wirkungslosen) Ausnahme für dasselbe Paket.
    Eine Ausnahme gilt nur für fehlende Signaturen; würde eine ungültige stattdessen versehentlich
    über den ausnahmefähigen Zweig laufen, gäbe diese Ausnahme sie durch und die Kontrolle bräche
    nicht ab — das deckt genau diesen Regressionsfall auf."""
    text = (k.wurzel / NPM_UNGUELTIG_FIXTURE).read_text(encoding="utf-8")
    daten = json.loads(text)
    installiert = frozenset(e["location"] for e in (*daten["invalid"], *daten["missing"]))
    werte_npm_signaturen_aus(text, installiert=installiert, ausnahmen={
        "boese-paket@1.2.3": "Wird ignoriert — für eine ungültige Signatur gibt es keine Ausnahme"})


def _sast_fund_unregistriert(k: Kontext) -> None:
    """Dieselben Läufe wie die Stufe `sast`, nur über die Fixtures: nicht registrierte Funde ab
    mittel blockieren. Beide Werkzeuge müssen einen solchen Fund liefern, sonst bewiese die
    Kontrolle für eines von ihnen nichts. Bandit muss installiert sein (`pruefen sast` bzw. die
    Stufe `sast` stellen es bereit) — ein fehlendes Werkzeug ist kein erwarteter Abbruch."""
    ordner = _ordner(k, "sast")
    ziele = (sast.FIXTURE_ORDNER,)
    funde: list[sast.Fund] = []
    _voraussetzung("Semgrep-Lauf über die Fixtures", lambda: funde.extend(sast.semgrep_laufen(
        k.wurzel, k.werkzeuge, ordner / sast.BERICHT_SEMGREP, ziele=ziele)[0]))
    _voraussetzung("Bandit-Lauf über die Fixtures", lambda: funde.extend(sast.bandit_laufen(
        k.wurzel, k.werkzeuge, ordner / sast.BERICHT_BANDIT, ziele=ziele)[0]))
    befunde = register.lade_befunde(k.wurzel / register.BEFUNDE)
    blockiert = sast.sast_gate(funde, befunde, sast.lade_ausnahmen(k.wurzel / sast.AUSNAHMEN))
    ab_mittel = {f.werkzeug for f in funde if f.schwere in ("hoch", "mittel")}
    if ab_mittel != set(sast.WERKZEUGE):
        raise NachweisFehler("negativkontrolle", "Fixtures ohne Fund ab mittel bei "
                             + ", ".join(sorted(set(sast.WERKZEUGE) - ab_mittel)))
    if blockiert:
        raise NachweisFehler("sast-gate", "; ".join(blockiert))


def _fuzzing_serverfehler(k: Kontext) -> None:
    """Derselbe Schemathesis-Lauf wie die Stufe `fuzzing` (Konfiguration des Worktrees), aber
    gegen den kaputten Dienst: er muss genau an dessen 500-Operation blockieren. Kein
    Fehlschlag oder einer an der gesunden Operation beweist nichts (anderer Grund)."""
    ordner = _ordner(k, "fuzzing")
    konfig: list[fuzzing.Konfig] = []
    _voraussetzung("Fuzzing-Konfiguration", lambda: konfig.append(
        fuzzing.lade_konfig(k.wurzel / "nachweise" / fuzzing.KONFIG)))
    cookie, token = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    prozess = subprocess.Popen([sys.executable, str(k.wurzel / KAPUTT_FIXTURE), "0", cookie,
                                token], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                               text=True, env=gefilterte_umgebung())
    try:
        port = _portzeile(prozess)
        inst = fuzzing.Testinstanz(url=f"http://127.0.0.1:{port}", admin_cookie=cookie,
                                   betrachter_token=token, arbeitsordner=ordner)
        ergebnis: list[fuzzing.FuzzErgebnis] = []
        _voraussetzung("Schemathesis-Lauf gegen den kaputten Dienst", lambda: ergebnis.append(
            fuzzing.schemathesis_laufen(inst, "admin", konfig[0], k.werkzeuge,
                                        ordner / "fuzzing-kaputt.xml",
                                        nachweise=k.wurzel / "nachweise")))
    finally:
        prozess.terminate()
        try:
            prozess.wait(10)
        except subprocess.TimeoutExpired:
            prozess.kill()
            prozess.wait()
    blockiert = fuzzing.fuzzing_gate(ergebnis)
    fremd = [b for b in blockiert if f"admin: {KAPUTT_OPERATION}:" not in b]
    if fremd or not blockiert:
        raise NachweisFehler("negativkontrolle", "Fehlschläge außerhalb der kaputten Operation "
                                                 f"bzw. keiner: {'; '.join(fremd) or '-'}")
    raise NachweisFehler("fuzzing-gate", "; ".join(blockiert))


def _egress_fremdanfrage(k: Kontext) -> None:
    """Dieselbe Auswertung wie die Stufe `egress` (`egress_gate`) mit aufgezeichneten Anfragen
    (Fixture): Kontrollanfragen und Abdeckung sind in Ordnung, aber eine Anfrage an einen
    fremden Ursprung und ein Verstoß gegen die Richtlinie gegen einen fremden Host sind
    enthalten — beide müssen blockieren. Ein Abbruch wegen fehlender Kontrollanfrage oder
    Abdeckung (`egress-erfassung`, `egress-abdeckung`) zählt nicht."""
    ergebnis: list[dict[str, Any]] = []
    _voraussetzung("aufgezeichnete Anfragen (Fixture)", lambda: ergebnis.append(
        json.loads((k.wurzel / EGRESS_ANFRAGEN_FIXTURE).read_text(encoding="utf-8"))))
    blockiert = egress.egress_gate(ergebnis[0], ergebnis[0]["origin"])
    if len(blockiert) != 2:
        raise NachweisFehler("negativkontrolle", "zwei blockierende Meldungen erwartet, "
                                                 f"{len(blockiert)} erhalten")
    raise NachweisFehler("egress-gate", "; ".join(blockiert))


def _egress_bundle_url(k: Kontext) -> None:
    """Dieselbe statische Prüfung wie die Stufe `egress` über ein Bundle mit absoluten URLs
    (Fixture) ohne jede Ausnahme: beide Funde müssen blockieren."""
    funde: list[egress.UrlFund] = []
    _voraussetzung("Bundle-Fixture", lambda: funde.extend(
        egress.bundle_urls(k.wurzel / EGRESS_BUNDLE_FIXTURE)))
    blockiert = egress.statisch_gate(funde, [])
    if len(blockiert) != 2:
        raise NachweisFehler("negativkontrolle", "zwei blockierende Meldungen erwartet, "
                                                 f"{len(blockiert)} erhalten")
    raise NachweisFehler("egress-gate", "; ".join(blockiert))


def _ruff_blind_except(k: Kontext) -> None:
    ordner = _ordner(k, "ruff-ratsche")
    (ordner / "pyproject.toml").write_text(
        '[tool.ruff.lint]\nselect = ["E", "F"]\nextend-select = ["BLE", "C90", "ASYNC"]\n',
        encoding="utf-8")
    (ordner / "ruff-basis.json").write_text("{}\n", encoding="utf-8")
    (ordner / "neu.py").write_text(
        'try:\n    int("x")\nexcept Exception:\n    print("Fehler")\n', encoding="utf-8")
    lint_ratsche.pruefe_teil(ordner, befehl=[str(k.wurzel / "nachweise/.venv/bin/ruff")])


KONTROLLEN: tuple[Kontrolle, ...] = (
    Kontrolle("verwundbar", "bekannt verwundbare Fixture-Komponente → Scan-Gate blockiert",
              "scan-gate", VERWUNDBAR_ID, _verwundbar),
    Kontrolle("db-veraltet", "trivy-DB 25 h alt → Abbruch", "db-alter", None, _db_veraltet),
    Kontrolle("sbom-fehlt", "SBOM eines ausgelieferten Artefakts entfernt → Abbruch",
              "scan-sbom", "server", _sbom_fehlt),
    Kontrolle("unzugeordnete-datei", "zusätzliche unzugeordnete Datei im Artefakt → Abbruch",
              "sbom-abdeckung", "web/dist/fremd.bin", _unzugeordnete_datei),
    Kontrolle("vex-under-investigation", "VEX-Aussage under_investigation → blockiert",
              "scan-gate", VERWUNDBAR_ID, _vex_under_investigation),
    # Nur ein bestätigter Fund der Token-Regel zählt: ein Werkzeugfehler hat die
    # Regel export-geheimnis-werkzeug, ein Fund einer anderen Regel nennt deren ID.
    Kontrolle("testsecret-im-export", "Testsecret im Export → Abbruch", "export-geheimnis",
              "scanview-api-token", _testsecret),
    Kontrolle("manifest-veraendert", "Manifest nach Signatur verändert bzw. ohne Signatur → "
              "Prüfung scheitert", "signatur", None, _manifest_veraendert),
    Kontrolle("unsignierter-commit", "unsignierter Commit nach dem Stichtag → Abbruch",
              "commit-signatur", "unsigniert", _unsignierter_commit),
    Kontrolle("npm-signatur-ungueltig", "ungültige npm-Registry-Signatur (Fixture) → Abbruch, "
              "auch mit einer (wirkungslosen) Ausnahme für dasselbe Paket",
              "npm-signaturen", "ungültige Registry-Signatur: boese-paket@1.2.3",
              _npm_signatur_ungueltig),
    Kontrolle("sast-fund-unregistriert", "Semgrep und Bandit über verwundbare Fixtures, Funde "
              "ab mittel nicht im Befundregister → SAST-Gate blockiert", "sast-gate",
              f"{sast.FIXTURE_ORDNER}/verwundbar.py", _sast_fund_unregistriert),
    Kontrolle("fuzzing-serverfehler", "Schemathesis gegen einen Dienst mit einer 500-Operation "
              "→ Fuzzing-Gate blockiert genau diese", "fuzzing-gate", KAPUTT_OPERATION,
              _fuzzing_serverfehler),
    Kontrolle("egress-fremdanfrage", "erfasste Anfrage an ein fremdes Ziel und Richtlinien-"
              "Verstoß → Egress-Gate blockiert", "egress-gate", EGRESS_FREMD,
              _egress_fremdanfrage),
    Kontrolle("egress-bundle-url", "absolute URLs im Bundle ohne Ausnahme → Egress-Gate "
              "blockiert", "egress-gate", EGRESS_BUNDLE_HOST, _egress_bundle_url),
    Kontrolle("ruff-blind-except", "neuer blinder except → Ruff-Ratsche blockiert",
              "lint-ratsche", "BLE001", _ruff_blind_except),
)


def _eine(k: Kontext, kontrolle: Kontrolle) -> dict[str, Any]:
    erwartet = kontrolle.regel + (f" ({kontrolle.enthaelt})" if kontrolle.enthaelt else "")
    try:
        kontrolle.lauf(k)
    except NachweisFehler as e:
        passt = e.regel == kontrolle.regel and (kontrolle.enthaelt is None
                                                or kontrolle.enthaelt in e.text)
        return {"beschreibung": kontrolle.beschreibung, "erwartet": erwartet,
                "ergebnis": "abgebrochen wie erwartet" if passt else "falscher Grund",
                "meldung": str(e)[:2000]}
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as e:
        return {"beschreibung": kontrolle.beschreibung, "erwartet": erwartet,
                "ergebnis": "falscher Grund", "meldung": f"{type(e).__name__}: {e}"[:2000]}
    return {"beschreibung": kontrolle.beschreibung, "erwartet": erwartet,
            "ergebnis": "nicht abgebrochen", "meldung": ""}


def _portzeile(prozess: subprocess.Popen[str], sekunden: float = 30) -> int:
    """Erste Zeile des Fixture-Dienstes (sein Port), mit Zeitlimit statt blockierendem Lesen."""
    if prozess.stdout is None or not select.select([prozess.stdout], [], [], sekunden)[0]:
        raise NachweisFehler("negativkontrolle", f"Fixture-Dienst meldet keinen Port "
                                                 f"(nach {sekunden:g} s)")
    zeile = prozess.stdout.readline().strip()
    if not zeile.isdigit():
        raise NachweisFehler("negativkontrolle", f"Fixture-Dienst meldet keinen Port: "
                                                 f"„{zeile[:80]}“")
    return int(zeile)


def _baum(wurzel: Path) -> str | None:
    """Git-Baum von `nachweise/` im Commit der Wurzel (Bindung des Ergebnisses an den Code)."""
    try:
        r = subprocess.run(git_befehl("rev-parse", "HEAD:nachweise", repo=wurzel),
                           capture_output=True, text=True, check=False,
                           stdin=subprocess.DEVNULL, env=gefilterte_umgebung())
    except OSError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def ausfuehren(k: Kontext, *, ausgabe: Path,
               kontrollen: tuple[Kontrolle, ...] | None = None) -> dict[str, Any]:
    """Alle Kontrollen ausführen, Ergebnis nach `ausgabe` schreiben (nie überschreiben). Bestanden
    nur, wenn **jede** mit ihrer Regel abbricht; sonst `NachweisFehler("negativkontrollen")`
    — die Datei steht dann mit `"ergebnis": "gescheitert"` zur Durchsicht da."""
    if ausgabe.exists():
        raise NachweisFehler("negativkontrollen", f"{ausgabe}: existiert schon — wird nicht "
                                                  "überschrieben")
    k.arbeitsordner.mkdir(parents=True, exist_ok=True)
    ergebnisse = {c.name: _eine(k, c) for c in (KONTROLLEN if kontrollen is None else kontrollen)}
    gescheitert = [n for n, e in ergebnisse.items() if e["ergebnis"] != "abgebrochen wie erwartet"]
    bericht: dict[str, Any] = {
        "schema": SCHEMA,
        "zeit": k.uhr.isoformat(timespec="seconds"),
        "nachweise_baum": _baum(k.wurzel),
        "werkzeuge": {n: {"image": k.werkzeuge[n].image, "version": k.werkzeuge[n].version}
                      for n in ("trivy", "gitleaks", "semgrep", "bandit", "schemathesis")
                      if n in k.werkzeuge},
        "kontrollen": ergebnisse,
        "ergebnis": "gescheitert" if gescheitert else "bestanden",
    }
    ausgabe.parent.mkdir(parents=True, exist_ok=True)
    with ausgabe.open("x", encoding="utf-8") as f:
        f.write(json.dumps(bericht, indent=2, ensure_ascii=False) + "\n")
    if gescheitert:
        raise NachweisFehler("negativkontrollen", "nicht wie erwartet abgebrochen: "
                             + "; ".join(f"{n} ({ergebnisse[n]['ergebnis']}: "
                                         f"{ergebnisse[n]['meldung'][:300]})"
                                         for n in gescheitert))
    return bericht
