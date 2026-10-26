"""Kommandozeile der Nachweiskette (`scanview-nachweise <befehl>`). Minimal gehalten (YAGNI):
spätere Aufgaben der Scheibe „Nachweise & Compliance“ hängen hier ihre Unterbefehle ein
(SBOM/Scan/Manifest, siehe Datei-Landkarte des Plans)."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from . import (
    abschluss,
    archiv,
    betrieb,
    darstellung,
    dast,
    egress,
    export,
    fuzzing,
    gate,
    lint_ratsche,
    manifest,
    negativkontrollen,
    osv,
    register,
    release,
    reproduzierbarkeit,
    sast,
    sbom,
    scan,
    securitytxt,
    vex,
    wochenscan,
)
from .fehler import NachweisFehler
from .werkzeuge import lade_werkzeuge

_VERSION = "0.12.0"


def _version(_args: argparse.Namespace) -> int:
    print(f"scanview-nachweise {_VERSION}")
    return 0


_MATRIX = register.MATRIX
# Exit-Code, wenn ein Gate rot ist (blockierender Befund, roter Negativtest). Getrennt von Exit 2
# (NachweisFehler: Register kaputt, Werkzeug fehlt …), damit Aufrufer beides unterscheiden können.
_GATE_ROT = 1


def _wurzel(args: argparse.Namespace) -> Path:
    """Repo-Wurzel: `--wurzel` oder das nächste Verzeichnis ab cwd aufwärts, das die
    Kontrollmatrix enthält (so läuft der Befehl auch aus `nachweise/` per `uv run`)."""
    if args.wurzel is not None:
        return Path(args.wurzel)
    start = Path.cwd().resolve()
    for kandidat in (start, *start.parents):
        if (kandidat / _MATRIX).is_file():
            return kandidat
    raise NachweisFehler("register", f"{_MATRIX} weder in {start} noch darüber gefunden "
                                     "(--wurzel angeben)")


def _register_laden(wurzel: Path) -> register.Register:
    return register.lade_register(wurzel)


def _register_pruefen(args: argparse.Namespace) -> int:
    r = _register_laden(_wurzel(args))
    rv = sum(k.release_voraussetzung for k in r.kontrollen)
    print(f"Register gültig: {len(r.kontrollen)} Kontrollen ({rv} Release-Voraussetzungen), "
          f"{len(r.anwendbarkeit.modelle)} Betriebsmodelle, {len(r.befunde)} Befunde, "
          f"{len(r.negativtests)} Negativtest-Kategorien")
    return 0


def _register_darstellen(args: argparse.Namespace) -> int:
    wurzel = _wurzel(args)
    if args.pruefen and args.betrieb is not None:
        # Die Betriebsnachweise hängen am Archiv, nicht am Commit; `--pruefen` kennt sie nicht.
        raise NachweisFehler("register-darstellung", "--pruefen vergleicht nur die Register-"
                                                     "Fassungen; --betrieb nicht dazu angeben")
    # Erst alles laden und erzeugen, dann schreiben: ein ungültiges Register hinterlässt keine
    # halb aktualisierten Dateien.
    inhalte = darstellung.fassungen(_register_laden(wurzel))
    if args.pruefen:
        # Nur vergleichen, nie schreiben: Drift zwischen TOML und committetem Markdown bricht ab.
        abweichend = darstellung.abweichungen(wurzel, inhalte)
        if abweichend:
            raise NachweisFehler("register-darstellung", "Markdown weicht von den Registern ab "
                                                         "(`register darstellen` ausführen und "
                                                         "committen): " + ", ".join(abweichend))
        print(f"Darstellung übereinstimmend: {len(inhalte)} Fassungen")
        return 0
    if args.betrieb is not None:
        archiv_pfad = Path(args.betrieb)
        if not (archiv_pfad / archiv.PRUEFUNGEN).is_dir():
            raise NachweisFehler("register-darstellung",
                                 f"{archiv_pfad}: kein Nachweis-Archiv (pruefungen/ fehlt)")
        kontrollen = _register_laden(wurzel).kontrollen
        inhalte[darstellung.BETRIEB_AUSGABE] = darstellung.betrieb_md(
            kontrollen, darstellung.betriebsergebnisse(kontrollen, archiv_pfad))
    for relativ, inhalt in inhalte.items():
        ziel = wurzel / relativ
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(inhalt, encoding="utf-8", newline="\n")
        print(f"geschrieben: {relativ}")
    return 0


def _heute(args: argparse.Namespace) -> date:
    if args.heute is None:
        # UTC wie `release.heute()` und `_sectxt_heute`: sonst trügen Nachweise derselben Nacht
        # (DAST in Ortszeit, Updates in UTC) zwischen 00:00 und 02:00 MESZ verschiedene Tage.
        return datetime.now(UTC).date()
    try:
        return date.fromisoformat(args.heute)
    except ValueError:
        raise NachweisFehler("befund-gate", f"--heute „{args.heute}“ ist kein Datum "
                                            "JJJJ-MM-TT") from None


def _gate_befunde(args: argparse.Namespace) -> int:
    # Volle Querprüfung (Retests vorhanden, Fix-Commits im Repo), nicht nur das Befundregister:
    # ein „behoben“ mit erfundenem Commit oder gelöschtem Retest darf das Gate nicht passieren.
    befunde = _register_laden(_wurzel(args)).befunde
    heute = _heute(args)
    blockiert = gate.befund_gate(befunde, heute)
    print(f"Befund-Gate {heute.isoformat()}: {len(befunde)} Befunde, "
          f"{len(blockiert)} blockierend")
    for zeile in blockiert:
        print(f"BLOCKIERT {zeile}")
    return _GATE_ROT if blockiert else 0


def _gate_negativtests(args: argparse.Namespace) -> int:
    wurzel = _wurzel(args)
    kategorien = register.lade_negativtests(wurzel / gate.NEGATIVTESTS)
    ergebnis = gate.negativtests_details(wurzel, kategorien=kategorien)
    for k in kategorien:
        if k.instanz:
            print(f"{k.id}: übersprungen (instanz — Deploy-Smoke, nicht Release-Gate)")
            continue
        e = ergebnis[k.id]
        zeile = f"{k.id}: {'grün' if e.gruen else 'rot'} ({e.ausgefuehrt} Tests gelaufen"
        print(zeile + (f"; {e.grund})" if e.grund else ")"))
    rot = [i for i, e in ergebnis.items() if not e.gruen]
    print(f"Negativtest-Gate: {len(ergebnis) - len(rot)}/{len(ergebnis)} Kategorien grün")
    return _GATE_ROT if rot else 0


def _messen_web_build(args: argparse.Namespace) -> int:
    # Kein Gate: Exit bleibt 0, ob gleich oder abweichend — nur ein echter Fehler
    # beim Bauen/Vergleichen (NachweisFehler) liefert Exit 2, über main() abgefangen.
    wurzel = _wurzel(args)
    arbeitsordner = (Path(args.arbeitsordner) if args.arbeitsordner
                     else Path(tempfile.mkdtemp(prefix="scanview-nachweise-build-")))
    ausgabe = Path(args.ausgabe) if args.ausgabe else None
    vergleich = reproduzierbarkeit.web_build_messen(wurzel, arbeitsordner=arbeitsordner,
                                                    ausgabe=ausgabe)
    ziel = ausgabe if ausgabe is not None else arbeitsordner / "build-vergleich.json"
    zustand = "gleich" if vergleich.gleich else f"abweichend ({len(vergleich.abweichend)})"
    print(f"Build-Vergleich web/: {zustand} — geschrieben: {ziel}")
    for zeile in vergleich.abweichend:
        print(f"ABWEICHUNG {zeile}")
    return 0


def _sbom_erzeugen(args: argparse.Namespace) -> int:
    zeilen = sbom.erzeugen(
        _wurzel(args), Path(args.worktree), args.version, Path(args.ziel),
        ersteller=args.ersteller,
        arbeitsordner=Path(args.arbeitsordner) if args.arbeitsordner else None)
    for zeile in zeilen:
        print(zeile)
    print(f"SBOMs geschrieben: {Path(args.ziel) / 'sbom'}")
    return 0


_VEX = Path("docs/compliance/vex.openvex.json")
_SCAN_ERGEBNIS = "scan-ergebnis.json"


def _cache(args: argparse.Namespace) -> Path:
    return Path(args.cache) if args.cache else scan.STANDARD_CACHE


def _db_aktualisieren(args: argparse.Namespace) -> int:
    wurzel = _wurzel(args)
    cache = _cache(args)
    stand = scan.db_aktualisieren(cache, lade_werkzeuge(wurzel / "nachweise" / "werkzeuge.json"))
    print(f"trivy-DB aktualisiert (Netzschritt): {stand.feld} {stand.zeit.isoformat()}, "
          f"sha256 {stand.sha256} — {cache / scan.DB_STAND}")
    _osv_aktualisieren(cache, osv.abfragen_aus_eigenbau(
        sbom.lade_eigenbau(wurzel / "nachweise" / "eigenbau.toml")))
    return 0


def _osv_aktualisieren(cache: Path, abfragen: list[osv.Abfrage]) -> None:
    """OSV-Stand für die Eigenbauten (Netzschritt neben der trivy-DB, Gegenprobe inklusive)."""
    stand = osv.aktualisieren(abfragen, cache)
    if stand is None:
        print("OSV: keine Eigenbauten — kein Netzschritt")
        return
    funde = ", ".join(f"{a.bom_ref}: {[f.id for f in fs] or 'keine'}"
                      for a, fs in stand.funde.items())
    print(f"OSV-Stand aktualisiert (Netzschritt, Gegenprobe und Tags geprüft): {funde} — "
          f"{cache / osv.OSV_STAND} (sha256 {stand.sha256[:16]}…)")


def _sbom_dateien(ordner: Path) -> dict[str, Path]:
    """Je Artefakt ein SBOM; dazu das Nachweis-SBOM der Werkzeug-Images, wenn es im Ordner liegt
    (die CLI bewertet streng wie bisher: auch dessen Treffer blockieren hier)."""
    namen = [*vex.LOCKDATEIEN]
    if ordner.is_dir() and any(ordner.glob(f"scanview-{scan.BILDER}-*.cdx.json")):
        namen.append(scan.BILDER)
    return scan.sbom_dateien(ordner, namen)


def _lock_hashes(wurzel: Path) -> dict[str, str]:
    return vex.lock_hashes(wurzel)


def _scan(args: argparse.Namespace) -> int:
    wurzel = _wurzel(args)
    ordner = Path(args.sbom_ordner)
    ziel = ordner / _SCAN_ERGEBNIS
    if ziel.exists():
        raise NachweisFehler("scan-ziel", f"{ziel}: existiert schon — wird nicht überschrieben")
    heute = _heute(args)
    echt = scan.jetzt().date()
    if heute < echt:
        # Ein zurückgedrehter Stichtag machte abgelaufene VEX-Aussagen und Befundfristen wieder
        # gültig; ein späterer (Vorschau) ist erlaubt.
        raise NachweisFehler("scan-heute", f"--heute {heute.isoformat()} liegt vor dem "
                                           f"aktuellen Datum {echt.isoformat()}")
    cache = _cache(args)
    werkzeuge = lade_werkzeuge(wurzel / "nachweise" / "werkzeuge.json")
    sboms = _sbom_dateien(ordner)
    vex_pfad = Path(args.vex) if args.vex else wurzel / _VEX
    aussagen = vex.lade_vex(vex_pfad)
    befunde = _register_laden(wurzel).befunde
    locks = _lock_hashes(wurzel)
    # DB-Frische gegen die echte Uhr, nicht gegen --heute (das gilt nur für Verfall/Befunde).
    osv_stand = osv.stand_falls_noetig(sboms.values(), cache, scan.jetzt())
    lauf = scan.scanne_alle(sboms, cache, vex_pfad, werkzeuge, uhr=scan.jetzt(), osv=osv_stand)
    stand, treffer, artefakte = lauf.stand, lauf.treffer, lauf.artefakte

    blockiert_scan = vex.treffer_gate(treffer, aussagen, heute, lock_hashes=locks)
    blockiert_befunde = gate.befund_gate(befunde, heute)
    vorschlaege = vex.befund_vorschlaege(treffer, aussagen, befunde, heute, lock_hashes=locks)
    meldungen = vex.vex_meldungen(aussagen, heute, lock_hashes=locks, treffer=treffer)
    gruen = not blockiert_scan and not blockiert_befunde
    ergebnis = {
        "heute": heute.isoformat(),
        "db": {"zeit": stand.zeit.isoformat(), "feld": stand.feld, "sha256": stand.sha256},
        "werkzeug": {"trivy": werkzeuge["trivy"].image, "version": werkzeuge["trivy"].version},
        "vex": {"datei": vex_pfad.name,
                "sha256": hashlib.sha256(vex_pfad.read_bytes()).hexdigest(),
                "aussagen": len(aussagen)},
        "lockdateien": locks,
        "artefakte": artefakte,
        "treffer": [{"id": t.id, "paket": t.paket, "version": t.version, "schwere": t.schwere,
                     "status_vex": t.status_vex, "artefakt": t.artefakt, "bom_ref": t.bom_ref,
                     "image_inhalt": vex.ist_image_inhalt(t)} for t in treffer],
        "blockierend_scan": blockiert_scan,
        "blockierend_befunde": blockiert_befunde,
        "vex_meldungen": meldungen,
        "befund_vorschlaege": vorschlaege,
        "ergebnis": "grün" if gruen else "blockiert",
    }
    ziel.write_text(json.dumps(ergebnis, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Scan {heute.isoformat()}: trivy-DB {stand.feld} {stand.zeit.isoformat()}, "
          f"{len(aussagen)} VEX-Aussagen")
    for name, a in artefakte.items():
        abd = a["abdeckung"]
        offen = ", ".join(f"{k} {v}" for k, v in abd["ungescannt"].items())
        print(f"{name}: " + ", ".join(f"{s} {n}" for s, n in a["treffer"].items())
              + f"; {abd['gescannt']}/{abd['komponenten']} Komponenten gescannt"
              + (f" (ungescannt: {offen})" if offen else ""))
    for zeile in meldungen:
        print(f"VEX-MELDUNG {zeile}")
    for zeile in [*blockiert_scan, *blockiert_befunde]:
        print(f"BLOCKIERT {zeile}")
    for zeile in vorschlaege:
        print(f"VORSCHLAG {zeile}")
    print(f"Scan-Gate: {'grün' if gruen else 'blockiert'} ({len(blockiert_scan)} Treffer, "
          f"{len(blockiert_befunde)} Befunde blockierend) — geschrieben: {ziel}")
    return 0 if gruen else _GATE_ROT


def _manifest_pruefen(args: argparse.Namespace) -> int:
    """Signatur des Manifests (vor dem Parsen, über dieselben Bytes), dann Form, dann — mit
    `--artefakte` — die SHA-256 der Artefakte. Ohne `--allowed-signers` gilt
    `<wurzel>/nachweise/allowed_signers`."""
    pfad = Path(args.manifest)
    signers = (Path(args.allowed_signers) if args.allowed_signers is not None
               else _wurzel(args) / "nachweise" / "allowed_signers")
    # Einmal lesen, Signatur über genau diese Bytes, dieselben Bytes parsen (kein TOCTOU).
    m = manifest.lade_signiertes_manifest(pfad, signers)
    print(f"Signatur gültig ({manifest.KENNUNG}, Namespace {manifest.NAMESPACE}): {pfad.name}, "
          f"Version {m['version']}")
    if args.artefakte is None:
        print("Artefakte nicht geprüft (--artefakte fehlt)")
        return 0
    manifest.pruefe_artefakte(m, Path(args.artefakte), nur_ausgeliefert=args.nur_ausgeliefert)
    anzahl = sum(1 for a in m["artefakte"] if a["ausgeliefert"] or not args.nur_ausgeliefert)
    print(f"{anzahl} Artefakt(e) mit dem Manifest übereinstimmend: {args.artefakte}")
    return 0


def _export(args: argparse.Namespace) -> int:
    """Öffentlichen Export erzeugen und prüfen. Ohne passendes `--bestaetigt` endet der
    Lauf nach der Diff-Ausgabe mit Exit 2 und nennt den zu bestätigenden Wert. Es wird nichts
    veröffentlicht: das Ergebnis liegt nur im lokalen Ziel."""
    # Zuerst die Begriffsliste: fehlt sie, wird nicht einmal das Ziel angelegt.
    begriffe = export.lade_begriffe()
    repo = Path(args.repo) if args.repo else Path.cwd()
    werkzeuge_pfad = (Path(args.werkzeuge) if args.werkzeuge
                      else repo / "nachweise" / "werkzeuge.json")
    vorheriges = export.lade_manifest(Path(args.vorheriges)) if args.vorheriges else None
    ergebnis = export.exportiere(
        repo, args.commit, Path(args.ziel),
        begriffe=begriffe, werkzeuge=lade_werkzeuge(werkzeuge_pfad),
        vorheriges=vorheriges, bestaetigt=args.bestaetigt,
        arbeitsordner=Path(args.arbeitsordner) if args.arbeitsordner else None)
    for schritt in ergebnis.netzschritte:
        print(f"NETZSCHRITT {schritt.zeit} {schritt.cwd}: {' '.join(schritt.befehl)}")
    if args.manifest_aus:
        ziel = Path(args.manifest_aus)
        if ziel.exists():
            raise NachweisFehler("export-manifest", f"{ziel}: existiert schon — wird nicht "
                                                    "überschrieben")
        ziel.write_text(ergebnis.manifest.als_json(), encoding="utf-8")
        print(f"Exportmanifest geschrieben: {ziel}")
    print(f"Export fertig: {args.ziel} (Commit {ergebnis.commit}, Diff {ergebnis.diff_hash})")
    return 0


def _pruefen_negativkontrollen(args: argparse.Namespace) -> int:
    """Alle Negativkontrollen mit echten Werkzeugen. Exit 0 nur, wenn jede mit
    ihrer Regel abbricht; sonst Exit 2 (die Ergebnisdatei steht trotzdem da)."""
    wurzel = _wurzel(args)
    arbeitsordner = (Path(args.arbeitsordner) if args.arbeitsordner
                     else Path(tempfile.mkdtemp(prefix="scanview-negativkontrollen-")))
    ausgabe = Path(args.ausgabe) if args.ausgabe else arbeitsordner / negativkontrollen.DATEI
    if ausgabe.exists():
        # Vor dem Lauf prüfen: sonst gäbe der finally-Zweig das alte Ergebnis als neues aus.
        raise NachweisFehler("negativkontrollen", f"{ausgabe}: existiert schon — wird nicht "
                                                  "überschrieben")
    k = negativkontrollen.Kontext(
        wurzel=wurzel, cache=_cache(args),
        werkzeuge=lade_werkzeuge(wurzel / "nachweise" / "werkzeuge.json"),
        arbeitsordner=arbeitsordner / "kontrollen", uhr=scan.jetzt())
    try:
        bericht = negativkontrollen.ausfuehren(k, ausgabe=ausgabe)
    finally:
        if ausgabe.is_file():
            for name, e in json.loads(ausgabe.read_text(encoding="utf-8"))["kontrollen"].items():
                print(f"{name}: {e['ergebnis']} — erwartet {e['erwartet']}")
    anzahl = len(bericht["kontrollen"])
    print(f"Negativkontrollen: {bericht['ergebnis']} ({anzahl}/{anzahl}) — geschrieben: "
          f"{ausgabe}")
    return 0


def _pruefen_ratsche(args: argparse.Namespace) -> int:
    for hinweis in lint_ratsche.pruefe(
            _wurzel(args), aktualisieren=args.basis_aktualisieren,
            teile=(args.teil,) if args.teil else lint_ratsche.TEILE):
        print(hinweis)
    print("Ruff-Ratsche bestanden")
    return 0


def _pruefen_sast(args: argparse.Namespace) -> int:
    """SAST (Semgrep + Bandit) über die Quellbäume von `--wurzel` (für belastbare Ergebnisse ein
    frischer Worktree), Gate gegen Befundregister und Ausnahmen. Rohberichte und `sast.json`
    im Arbeitsordner. Exit 0 ohne blockierende Funde, Exit 1 mit (Liste auf stdout), Exit 2 bei
    Regelverstößen (Regelsatz, Abdeckung, Werkzeugfehler)."""
    wurzel = _wurzel(args)
    arbeitsordner = (Path(args.arbeitsordner) if args.arbeitsordner
                     else Path(tempfile.mkdtemp(prefix="scanview-sast-")))
    ziel = arbeitsordner / "sast.json"
    if ziel.exists():
        raise NachweisFehler("sast-ausgabe", f"{ziel}: existiert schon — wird nicht "
                                             "überschrieben")
    e = sast.pruefen(wurzel, lade_werkzeuge(wurzel / "nachweise" / "werkzeuge.json"),
                     arbeitsordner)
    arbeitsordner.mkdir(parents=True, exist_ok=True)
    ziel.write_text(json.dumps(e.bericht, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for werkzeug, zahlen in e.bericht["zaehlprobe"].items():
        print(f"{werkzeug}: " + ", ".join(f"{s} {n}" for s, n in zahlen.items())
              + f" — {e.bericht['abdeckung'][werkzeug]} Quelldateien")
    for zeile in e.bericht["ausnahmen_ohne_fund"]:
        print(f"HINWEIS {zeile}")
    for zeile in e.blockiert:
        print(f"BLOCKIERT {zeile}")
    print(f"SAST: {'blockiert' if e.blockiert else 'grün'} — geschrieben: {ziel}")
    return _GATE_ROT if e.blockiert else 0


def _pruefen_dast(args: argparse.Namespace) -> int:
    """DAST (ZAP-Passivscan) gegen `--ziel` mit dem Sitzungswert aus `--cookie-datei`, Gate
    gegen Befundregister und Ausnahmen, Nachweis nach
    `<archiv>/pruefungen/<datum>[.<lauf>]-dast-<instanz>/`. `--instanz S` nur mit
    `--betreiber-freigabe <heutiges Datum, UTC>`. Exit 0 ohne blockierende Alarme, Exit 1 mit
    (Liste auf stdout; der Nachweis ist trotzdem geschrieben), Exit 2 bei Regelverstößen
    (Freigabe, Sitzung, Abdeckung, Werkzeugfehler, Archiv)."""
    heute = _heute(args)
    # Freigabe vor allem anderen: bei S wird weder die Cookie-Datei gelesen noch etwas gestartet.
    dast.pruefe_freigabe(args.instanz, args.ziel, args.betreiber_freigabe, heute)
    wurzel = _wurzel(args)
    cookie = dast.lies_cookie(Path(args.cookie_datei))
    werkzeuge = lade_werkzeuge(wurzel / "nachweise" / "werkzeuge.json")
    befunde = register.lade_befunde(wurzel / register.BEFUNDE)
    ausnahmen = dast.lade_ausnahmen(wurzel / dast.AUSNAHMEN)
    arbeit = Path(args.arbeitsordner) if args.arbeitsordner else dast.neuer_arbeitsordner()
    arbeit.mkdir(mode=0o700, exist_ok=True)
    try:
        e = dast.pruefen(args.instanz, args.ziel, cookie, werkzeuge, arbeit, Path(args.archiv),
                         heute=heute, befunde=befunde, ausnahmen=ausnahmen,
                         betreiber_freigabe=args.betreiber_freigabe)
    finally:
        # Zwischenstände (Ablauf, URL-Liste, Bericht) gehören dem Lauf; archiviert ist die
        # bereinigte Fassung.
        if not args.arbeitsordner:
            shutil.rmtree(arbeit, ignore_errors=True)
    zahlen = e.bericht["alarme"]
    print(f"{e.bericht['instanz']} {e.bericht['ziel']} (Version {e.bericht['version']}): "
          + ", ".join(f"{s} {n}" for s, n in zahlen.items())
          + f" — {e.bericht['abdeckung']['api_urls']} API-URLs mit Antwort 200")
    for zeile in e.blockiert:
        print(f"BLOCKIERT {zeile}")
    print(f"DAST: {'blockiert' if e.blockiert else 'grün'} — geschrieben: {e.ziel}")
    return _GATE_ROT if e.blockiert else 0


def _pruefen_fuzzing(args: argparse.Namespace) -> int:
    """API-Fuzzing gegen eine Wegwerf-Instanz aus `--wurzel` (frischer Worktree mit gebautem
    `web/dist` und Server-venv), je als Betrachter und Admin. JUnit-Berichte und `fuzzing.json` im
    Arbeitsordner. Exit 0 ohne Fehlschlag, Exit 1 mit (Liste auf stdout), Exit 2 bei
    Regelverstößen (Port belegt, Abdeckung, Sitzung verloren, Werkzeugfehler)."""
    wurzel = _wurzel(args)
    arbeitsordner = (Path(args.arbeitsordner) if args.arbeitsordner
                     else Path(tempfile.mkdtemp(prefix="scanview-fuzzing-")))
    ziel = arbeitsordner / "fuzzing.json"
    if ziel.exists():
        raise NachweisFehler("fuzzing-ausgabe", f"{ziel}: existiert schon — wird nicht "
                                                "überschrieben")
    e = fuzzing.pruefen(wurzel, lade_werkzeuge(wurzel / "nachweise" / "werkzeuge.json"),
                        arbeitsordner / "berichte", arbeitsordner)
    ziel.write_text(json.dumps(e.bericht, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for identitaet, lauf in e.bericht["laeufe"].items():
        print(f"{identitaet}: {lauf['operationen_geprueft']} von {lauf['operationen_schema']} "
              f"Operationen geprüft ({lauf['ausgeschlossen']} begründet ausgeschlossen), "
              f"{len(lauf['fehler'])} Fehlschläge")
        for zeile in lauf["hinweise"]:
            print(f"HINWEIS {zeile}")
    for zeile in e.blockiert:
        print(f"BLOCKIERT {zeile}")
    print(f"Fuzzing: {'blockiert' if e.blockiert else 'grün'} nach {e.bericht['dauer_sekunden']} s "
          f"— geschrieben: {ziel}")
    return _GATE_ROT if e.blockiert else 0


def _pruefen_egress(args: argparse.Namespace) -> int:
    """Egress-Test aus `--wurzel` (frischer Worktree mit gebautem `web/dist` und Server-venv):
    statische Prüfung des Bundles und Headless-Browser gegen eine Wegwerf-Instanz mit dem
    synthetischen Testpaket. Berichte und `egress.json` im Arbeitsordner. Exit 0 ohne Fund, Exit 1
    mit (Liste auf stdout), Exit 2 bei Regelverstößen (Port belegt, Erfassung oder Abdeckung
    nicht erreicht, Sitzung verloren, Werkzeugfehler)."""
    wurzel = _wurzel(args)
    arbeitsordner = (Path(args.arbeitsordner) if args.arbeitsordner
                     else Path(tempfile.mkdtemp(prefix="scanview-egress-")))
    ziel = arbeitsordner / "egress.json"
    if ziel.exists():
        raise NachweisFehler("egress-ausgabe", f"{ziel}: existiert schon — wird nicht "
                                               "überschrieben")
    e = egress.pruefen(wurzel, lade_werkzeuge(wurzel / "nachweise" / "werkzeuge.json"),
                       arbeitsordner / "berichte", arbeitsordner)
    ziel.write_text(json.dumps(e.bericht, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    s, b = e.bericht["statisch"], e.bericht["browser"]
    print(f"statisch: {s['dist_dateien']} Dateien, {s['funde']} absolute URLs "
          f"({s['ausgenommen']} ausgenommen), {len(s['blockierend'])} blockierend")
    print(f"Browser: {b['anfragen']} erfasste Anfragen auf {len(b['seiten'])} Seiten, "
          f"{b['punkte']} Punkte geladen, Kontrollanfragen erfasst, "
          f"{len(b['blockierend'])} blockierend")
    for zeile in e.bericht["hinweise"]:
        print(f"HINWEIS {zeile}")
    for zeile in e.blockiert:
        print(f"BLOCKIERT {zeile}")
    print(f"Egress: {'blockiert' if e.blockiert else 'grün'} nach {e.bericht['dauer_sekunden']} s "
          f"— geschrieben: {ziel}")
    return _GATE_ROT if e.blockiert else 0


def _release(args: argparse.Namespace) -> int:
    """Release-Kette. Exit 0 nur, wenn jede Stufe bestanden hat; sonst Exit 2 mit der
    Regel der abbrechenden Stufe. `--trocken`: ohne Archiv und Tag, Export in ein Wegwerf-Repo."""
    repo = Path(args.repo) if args.repo else Path.cwd()
    pfad = release.release(
        repo, args.commit, args.version,
        Path(args.archiv) if args.archiv else release.STANDARD_ARCHIV,
        Path(args.schluessel) if args.schluessel else release.STANDARD_SCHLUESSEL,
        Path(args.export_ziel) if args.export_ziel else release.STANDARD_EXPORT_ZIEL,
        args.bestaetigt, trocken=args.trocken,
        arbeitsordner=Path(args.arbeitsordner) if args.arbeitsordner else None,
        cache=Path(args.cache) if args.cache else None)
    art = "Trockenlauf" if args.trocken else "Release"
    print(f"{art} {args.version} fertig — Manifest: {pfad}")
    return 0


def _abschluss(args: argparse.Namespace) -> int:
    repo = Path(args.repo) if args.repo else Path.cwd()
    ziel = release.abschluss_nachholen(
        repo, Path(args.archiv) if args.archiv else release.STANDARD_ARCHIV,
        Path(args.schluessel) if args.schluessel else release.STANDARD_SCHLUESSEL,
        Path(args.export_ziel) if args.export_ziel else release.STANDARD_EXPORT_ZIEL,
        args.version)
    print(f"Abschluss {args.version} geschrieben und committet: {ziel}")
    return 0


def _deploy_ordner(args: argparse.Namespace) -> int:
    signers = (Path(args.allowed_signers) if args.allowed_signers is not None
               else _wurzel(args) / "nachweise" / "allowed_signers")
    ziel = abschluss.deploy_ordner(Path(args.archiv) if args.archiv else release.STANDARD_ARCHIV,
                                   args.version, Path(args.ziel), signers)
    print(f"Release-Ordner {args.version} geprüft und angelegt: {ziel}")
    return 0


def _sectxt_heute(args: argparse.Namespace) -> date:
    if args.heute is None:
        return datetime.now(UTC).date()  # die Ablauflogik rechnet in UTC, nicht in Ortszeit
    try:
        return date.fromisoformat(args.heute)
    except ValueError:
        raise NachweisFehler("securitytxt-eingabe", f"--heute „{args.heute}“ ist kein Datum "
                                                    "JJJJ-MM-TT") from None


def _securitytxt_erzeugen(args: argparse.Namespace) -> int:
    """security.txt (unsigniert) auf stdout; Signieren (`gpg --clearsign`) geschieht außerhalb."""
    wurzel = _wurzel(args) if args.vorlage is None or args.security_md is None else None
    vorlage = Path(args.vorlage) if args.vorlage else (
        wurzel / "deploy" / "platform" / "security.txt.vorlage" if wurzel else Path())
    security_md = Path(args.security_md) if args.security_md else (
        wurzel / "SECURITY.md" if wurzel else Path())
    sys.stdout.write(securitytxt.erzeuge(
        vorlage, security_md, kontakt=args.kontakt, schluessel_url=args.schluessel_url,
        kanonisch=args.kanonisch, richtlinie_url=args.richtlinie_url,
        heute=_sectxt_heute(args), tage=args.tage))
    return 0


def _securitytxt_pruefen(args: argparse.Namespace) -> int:
    """Datei per HTTPS abrufen und gegen den öffentlichen Schlüssel prüfen; mit `--archiv` und
    `--instanz` als Nachweis `pruefungen/<datum>[.<lauf>]-securitytxt-<instanz>/` schreiben."""
    if args.archiv is not None and args.instanz is None:
        raise NachweisFehler("securitytxt-eingabe", "--archiv braucht --instanz (L oder S)")
    if args.instanz is not None and args.archiv is None:
        raise NachweisFehler("securitytxt-eingabe", "--instanz gilt nur mit --archiv")
    heute = _sectxt_heute(args)
    schluessel = Path(args.schluessel)
    text = securitytxt.abrufen(args.url)
    ergebnis = securitytxt.pruefe(text, url=args.url, schluessel=schluessel, heute=heute,
                                  jetzt=datetime.now(UTC) if args.heute is None else None)
    print(f"security.txt gültig signiert: {args.url}")
    print(f"Fingerabdruck {ergebnis['fingerabdruck']}, Expires {ergebnis['expires']} "
          f"(noch {ergebnis['tage_bis_ablauf']} Tage)")
    if args.archiv is not None:
        ziel = archiv.schreibe_pruefung(
            Path(args.archiv), "securitytxt", args.instanz, ergebnis,
            {"security.txt": text.encode("utf-8"), "psirt-openpgp.asc": schluessel.read_bytes()},
            heute=heute)
        print(f"Nachweis geschrieben und committet: {ziel}")
    return 0


def _betrieb_updates(args: argparse.Namespace) -> int:
    """Update-Protokoll einer Instanz gegen die signierten Manifeste im Archiv auswerten,
    als `pruefungen/<datum>[.<lauf>]-updates-<instanz>/` archivieren und `betrieb/unterstuetzt.toml`
    fortschreiben. Exit 1 bei Abweichung (der Nachweis wird trotzdem geschrieben)."""
    heute = _sectxt_heute(args)
    archiv_pfad = Path(args.archiv)
    signers = (Path(args.allowed_signers) if args.allowed_signers is not None
               else _wurzel(args) / "nachweise" / "allowed_signers")
    try:
        roh = Path(args.protokoll).read_bytes()
        text = roh.decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise NachweisFehler("updates-format", f"{args.protokoll}: nicht lesbar ({e})") from None
    zeilen = betrieb.lies_updates(text)
    abweichungen = betrieb.pruefe_gegen_archiv(zeilen, archiv_pfad, signers)
    hinweise = betrieb.informative_zeilen(zeilen)
    stand = betrieb.aktueller_stand(zeilen)
    bisher: dict[str, str | None] = {
        i: v for i, v in betrieb.lies_unterstuetzt(
            archiv_pfad / archiv.BETRIEB / betrieb.UNTERSTUETZT_DATEI).items()
        if v != betrieb.NICHT_AUSGEWERTET}
    # Nur eine tatsächliche Auswertung ersetzt den bisherigen geprüften Stand der Instanz: ein
    # Lauf ohne ok-Zeile lässt ihn stehen; der Repo-Modus (ok-Zeile ohne geprüftes Release) ist
    # eine Auswertung und löscht den früheren Stand.
    staende = {i: bisher.get(i) for i in betrieb.INSTANZEN}
    if any(z.ergebnis == "ok" for z in zeilen):
        staende[args.instanz] = stand
    versionen = betrieb.unterstuetzte_versionen(archiv_pfad, staende)
    ergebnis = {
        "instanz": args.instanz, "stand": stand or betrieb.NICHT_AUSGEWERTET,
        "zeilen": len(zeilen), "abweichungen": abweichungen, "hinweise": hinweise,
        "protokoll_sha256": hashlib.sha256(roh).hexdigest(),
        "ergebnis": "abweichung" if abweichungen else "bestanden",
    }
    ziel = archiv.schreibe_pruefung(archiv_pfad, "updates", args.instanz, ergebnis,
                                    {"updates.log": roh}, heute=heute)
    archiv.schreibe_betrieb(archiv_pfad, betrieb.UNTERSTUETZT_DATEI,
                            betrieb.stelle_unterstuetzt_dar(versionen, staende, heute),
                            f"betrieb: unterstuetzt {heute.isoformat()} "
                            f"({args.instanz} {stand or betrieb.NICHT_AUSGEWERTET})")
    print(f"Instanz {args.instanz}: Stand {stand or betrieb.NICHT_AUSGEWERTET}, "
          f"{len(zeilen)} Zeilen, {len(abweichungen)} Abweichungen")
    for zeile in (*abweichungen, *hinweise):
        print(f"  {zeile}")
    for i in betrieb.INSTANZEN:
        print(f"  unterstützter Stand {i}: {staende[i] or betrieb.NICHT_AUSGEWERTET}")
    print(f"Unterstützte Versionen: {', '.join(versionen)}")
    print(f"Nachweis geschrieben und committet: {ziel}")
    return _GATE_ROT if abweichungen else 0


def _bekannt(pfad: str | None) -> set[str]:
    """Schwachstellen-IDs, die schon gemeldet sind: je Zeile eine, `#` beginnt einen Kommentar."""
    if pfad is None:
        return set()
    try:
        text = Path(pfad).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise NachweisFehler("wochenscan-bekannt", f"{pfad}: nicht lesbar ({e})") from None
    zeilen = (z.split("#", 1)[0].strip() for z in text.splitlines())
    return {z for z in zeilen if z}


def _wochenscan(args: argparse.Namespace) -> int:
    """Alle unterstützten Versionen des Archivs scannen; schreibt `<ausgabe>/ergebnis.json`.
    Exit 0 grün, 1 blockierend, 2 Fehler (`main`)."""
    wurzel = _wurzel(args)
    # Absolute Pfade: die Container-Mounts verlangen sie.
    cache = _cache(args).resolve()
    werkzeuge = lade_werkzeuge(wurzel / "nachweise" / "werkzeuge.json")
    archiv_pfad = Path(args.archiv).resolve()
    signers = (Path(args.allowed_signers) if args.allowed_signers is not None
               else wurzel / "nachweise" / "allowed_signers").resolve()
    vex_pfad = Path(args.vex).resolve()
    repo_pfad = Path(args.repo).resolve()
    ausgabe = Path(args.ausgabe).resolve()
    bekannt = _bekannt(args.bekannt)
    if ausgabe.exists():
        raise NachweisFehler("wochenscan-ausgabe", f"{ausgabe}: existiert schon — wird nicht "
                                                   "überschrieben")
    if args.db_aktualisieren:
        stand = scan.db_aktualisieren(cache, werkzeuge)
        print(f"trivy-DB aktualisiert (Netzschritt): {stand.feld} {stand.zeit.isoformat()}, "
              f"sha256 {stand.sha256}")
        _osv_aktualisieren(cache, [
            *osv.abfragen_aus_eigenbau(sbom.lade_eigenbau(wurzel / "nachweise" / "eigenbau.toml")),
            *wochenscan.osv_abfragen(archiv_pfad, signers)])
    # DB-Frische gegen die echte Uhr.
    erg = wochenscan.wochenscan(archiv_pfad, repo_pfad, vex_pfad, signers, cache,
                                werkzeuge, ausgabe, bekannt=bekannt,
                                jetzt=scan.jetzt())
    gruen = not erg.blockierend
    try:
        vex_sha = hashlib.sha256(vex_pfad.read_bytes()).hexdigest()
    except OSError as e:
        raise NachweisFehler("wochenscan-ausgabe", f"{vex_pfad}: nicht lesbar "
                                                   f"({e.strerror})") from None
    ergebnis = {
        "versionen": erg.versionen,
        "anker": {"datei": str(signers),
                  "sha256": hashlib.sha256(signers.read_bytes()).hexdigest()},
        "db": {"zeit": erg.db.zeit.isoformat(), "feld": erg.db.feld, "sha256": erg.db.sha256},
        "werkzeug": {"trivy": werkzeuge["trivy"].image, "version": werkzeuge["trivy"].version},
        "vex": {"datei": vex_pfad.name, "sha256": vex_sha},
        "artefakte": erg.artefakte,
        "treffer": {v: [{"id": t.id, "paket": t.paket, "version": t.version,
                         "schwere": t.schwere, "status_vex": t.status_vex,
                         "artefakt": t.artefakt} for t in ts]
                    for v, ts in erg.treffer_je_version.items()},
        "blockierend": erg.blockierend,
        "neu": erg.neu,
        "ergebnis": "grün" if gruen else "blockiert",
    }
    ziel = ausgabe / "ergebnis.json"
    try:
        ziel.write_text(json.dumps(ergebnis, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    except OSError as e:
        raise NachweisFehler("wochenscan-ausgabe", f"{ziel}: nicht schreibbar "
                                                   f"({e.strerror})") from None
    print(f"Wochenscan {', '.join(erg.versionen)}: trivy-DB {erg.db.feld} "
          f"{erg.db.zeit.isoformat()}; Vertrauensanker {signers}")
    for version, ts in erg.treffer_je_version.items():
        for t in ts:
            if t.schwere == "mittel":
                print(f"HINWEIS {version}: {t.id} (mittel, nicht blockierend) {t.paket} "
                      f"im Artefakt {t.artefakt}")
    for version, zeilen in erg.blockierend.items():
        for zeile in zeilen:
            print(f"BLOCKIERT {version}: {zeile}")
    print(f"Wochenscan: {'grün' if gruen else 'blockiert'} ({len(erg.neu)} neu) — "
          f"geschrieben: {ziel}")
    return 0 if gruen else _GATE_ROT


def _bauen_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scanview-nachweise")
    sub = parser.add_subparsers(dest="befehl", required=True)

    v = sub.add_parser("version", help="Version ausgeben")
    v.set_defaults(func=_version)

    reg = sub.add_parser("register", help="Compliance-Register (Kontrollmatrix, Anwendbarkeit, "
                                          "Befunde, Negativtests)")
    reg_sub = reg.add_subparsers(dest="register_befehl", required=True)
    for name, func, hilfe in [
        ("pruefen", _register_pruefen, "Register nur validieren"),
        ("darstellen", _register_darstellen,
         "Register validieren und die Markdown-Fassungen (privat + öffentlich) schreiben"),
    ]:
        p = reg_sub.add_parser(name, help=hilfe)
        p.add_argument("--wurzel", help="Repo-Wurzel (Standard: ab cwd aufwärts gesucht)")
        if name == "darstellen":
            p.add_argument("--pruefen", action="store_true",
                           help="nur vergleichen: Exit 2, wenn eine committete Fassung fehlt "
                                "oder von den Registern abweicht (schreibt nichts)")
            p.add_argument("--betrieb", metavar="ARCHIV",
                           help="zusätzlich docs/compliance/betriebsnachweise.md aus dem "
                                "jüngsten Nachweis je Instanz im Nachweis-Archiv schreiben "
                                "(Kontrollmatrix und Release-Prüfung bleiben archivfrei)")
        p.set_defaults(func=func)

    g = sub.add_parser("gate", help="Release-Gates (Befundregister, Negativtests)")
    g_sub = g.add_subparsers(dest="gate_befehl", required=True)
    gb = g_sub.add_parser("befunde", help="Befundregister gegen die Release-Schwelle prüfen")
    gb.add_argument("--heute", help="Stichtag JJJJ-MM-TT (Standard: heute)")
    gb.set_defaults(func=_gate_befunde)
    gn = g_sub.add_parser("negativtests",
                          help="Sicherheits-Negativtests je Kategorie ausführen (uv run pytest)")
    gn.set_defaults(func=_gate_negativtests)
    for p in (gb, gn):
        p.add_argument("--wurzel", help="Repo-Wurzel (Standard: ab cwd aufwärts gesucht)")

    m = sub.add_parser("messen", help="Reproduzierbarkeit messen (keine Gates)")
    m_sub = m.add_subparsers(dest="messen_befehl", required=True)
    mw = m_sub.add_parser("web-build", help="web/ zweimal in frischen Worktrees bauen und "
                                            "dateiweise vergleichen")
    mw.add_argument("--wurzel", help="Repo-Wurzel (Standard: ab cwd aufwärts gesucht)")
    mw.add_argument("--arbeitsordner", help="Ordner für die beiden Worktrees, ihre dist-Kopien "
                                           "und das Ergebnis (Standard: neues Temp-Verzeichnis)")
    mw.add_argument("--ausgabe", help="Zielpfad für build-vergleich.json (Standard: "
                                     "<arbeitsordner>/build-vergleich.json)")
    mw.set_defaults(func=_messen_web_build)

    s = sub.add_parser("sbom", help="SBOM nach BSI TR-03183-2 (CycloneDX 1.6)")
    s_sub = s.add_subparsers(dest="sbom_befehl", required=True)
    se = s_sub.add_parser("erzeugen", help="Artefakte bauen, je Artefakt und gesamt ein SBOM "
                                           "erzeugen, Abdeckung prüfen (fail-closed), validieren")
    se.add_argument("--wurzel", help="Repo-Wurzel mit nachweise/ (Standard: ab cwd aufwärts)")
    se.add_argument("--worktree", required=True,
                    help="frischer Worktree des Release-Commits (git worktree add --detach)")
    se.add_argument("--version", required=True, help="Release-Version, z. B. 0.1.0")
    se.add_argument("--ziel", required=True,
                    help="Ausgabeordner (artefakte/ und sbom/ werden darin angelegt)")
    se.add_argument("--ersteller", default=sbom.ERSTELLER, help="E-Mail des SBOM-Erstellers")
    se.add_argument("--arbeitsordner", help="Ordner für venvs/Image-Archive (Standard: "
                                           "temporär, danach gelöscht)")
    se.set_defaults(func=_sbom_erzeugen)

    da = sub.add_parser("db-aktualisieren", help="trivy-DB laden (einziger Netzschritt des "
                                                 "Scans, protokolliert in db-stand.json)")
    da.add_argument("--cache", help=f"DB-Cache (Standard: {scan.STANDARD_CACHE})")
    da.add_argument("--wurzel", help="Repo-Wurzel mit nachweise/ (Standard: ab cwd aufwärts)")
    da.set_defaults(func=_db_aktualisieren)

    sc = sub.add_parser("scan", help="SBOMs mit trivy scannen (ohne Netz), VEX- und Befund-Gate; "
                                     "Exit 0 grün, 1 blockiert, 2 Fehler")
    sc.add_argument("--sbom-ordner", required=True, help="Ordner mit den SBOMs aus `sbom "
                                                          "erzeugen` (<ziel>/sbom)")
    sc.add_argument("--cache", help=f"DB-Cache (Standard: {scan.STANDARD_CACHE})")
    sc.add_argument("--vex", help=f"OpenVEX-Datei (Standard: <wurzel>/{_VEX})")
    sc.add_argument("--heute", help="Stichtag JJJJ-MM-TT für VEX-Verfall und Befund-Gate "
                                    "(Standard: heute; die DB-Frische misst immer die Uhr)")
    sc.add_argument("--wurzel", help="Repo-Wurzel (Standard: ab cwd aufwärts gesucht)")
    sc.set_defaults(func=_scan)

    mf = sub.add_parser("manifest", help="signiertes Release-Manifest")
    mf_sub = mf.add_subparsers(dest="manifest_befehl", required=True)
    mp = mf_sub.add_parser("pruefen", help="Signatur (ssh-keygen -Y verify) und optional die "
                                           "Artefakt-Hashes prüfen")
    mp.add_argument("manifest", help="Pfad zu manifest.json (Signatur daneben: manifest.json.sig)")
    mp.add_argument("--allowed-signers", help="allowed_signers-Datei (Standard: "
                                              "<wurzel>/nachweise/allowed_signers)")
    mp.add_argument("--artefakte", help="Ordner mit den Artefakt-Dateien, deren SHA-256 geprüft "
                                        "wird")
    mp.add_argument("--nur-ausgeliefert", action="store_true",
                    help="nur die als ausgeliefert gekennzeichneten Artefakte prüfen")
    mp.add_argument("--wurzel", help="Repo-Wurzel mit nachweise/ (Standard: ab cwd aufwärts)")
    mp.set_defaults(func=_manifest_pruefen)

    ex = sub.add_parser("export", help="öffentlichen Export eines Commits erzeugen und prüfen "
                                       "(Positivliste, Freigaben, Begriffe, gitleaks, "
                                       "bestätigter Diff, Baubarkeit); veröffentlicht nichts")
    ex.add_argument("commit", help="Quell-Commit im privaten Repo")
    ex.add_argument("--ziel", required=True, help="leeres oder neues Zielverzeichnis")
    ex.add_argument("--repo", help="privates Repo (Standard: aktuelles Verzeichnis)")
    ex.add_argument("--vorheriges", help="Exportmanifest des vorigen Exports (ohne: alles neu)")
    ex.add_argument("--bestaetigt", help="SHA-256 des durchgesehenen Diffs (wird ohne Wert "
                                         "ausgegeben)")
    ex.add_argument("--manifest-aus", help="Exportmanifest (JSON) hierhin schreiben")
    ex.add_argument("--werkzeuge", help="werkzeuge.json (Standard: <repo>/nachweise/"
                                        "werkzeuge.json)")
    ex.add_argument("--arbeitsordner", help="Ordner für Bau-Abzug und gitleaks-Konfiguration "
                                           "(Standard: neues Temp-Verzeichnis)")
    ex.set_defaults(func=_export)

    rl = sub.add_parser("release", help="fail-closed Release-Kette: einfrieren, Gates, "
                                        "Qualitätstor, SAST, SBOM, Fuzzing, Egress, "
                                        "Scan, Negativkontrollen, Lizenzen, Matrix, Export, "
                                        "signiertes Manifest, Archiv, Tag, signierter Abschluss")
    rl.add_argument("commit", help="Release-Commit (signiert, auf einem Release-Zweig des "
                                   "Vertrauensankers)")
    rl.add_argument("--version", required=True, help="Version X.Y.Z (nicht im Archiv, kein Tag)")
    rl.add_argument("--bestaetigt", help="SHA-256 des durchgesehenen Export-Diffs")
    rl.add_argument("--trocken", action="store_true",
                    help="alles außer Archiv, Tag und Abschluss; Manifest mit trockenlauf: "
                         "true, Export in ein Wegwerf-Repo im Arbeitsordner")
    rl.add_argument("--repo", help="privates Repo (Standard: aktuelles Verzeichnis)")
    rl.add_argument("--archiv", help=f"Archiv-Repo (Standard: {release.STANDARD_ARCHIV})")
    rl.add_argument("--schluessel", help="privater Release-Schlüssel (Standard: "
                                         f"{release.STANDARD_SCHLUESSEL})")
    rl.add_argument("--export-ziel", help="lokales Export-Repo (Standard: "
                                          f"{release.STANDARD_EXPORT_ZIEL})")
    rl.add_argument("--arbeitsordner", help="frischer Ordner für Worktree, Artefakte, Berichte "
                                           "(Standard: neues Temp-Verzeichnis)")
    rl.add_argument("--cache", help=f"trivy-DB (Standard: {scan.STANDARD_CACHE})")
    rl.set_defaults(func=_release)

    ab = sub.add_parser("abschluss", help="Abschlussnachweis nachholen (Wiederherstellung nach "
                                          "einem Abbruch hinter dem Archivieren): prüft beide "
                                          "Tags gegen das archivierte Manifest, dann signierter "
                                          "Abschluss im Archiv")
    ab.add_argument("--version", required=True, help="Version X.Y.Z (im Archiv)")
    ab.add_argument("--repo", help="privates Repo (Standard: aktuelles Verzeichnis)")
    ab.add_argument("--archiv", help=f"Archiv-Repo (Standard: {release.STANDARD_ARCHIV})")
    ab.add_argument("--schluessel", help="privater Release-Schlüssel (Standard: "
                                         f"{release.STANDARD_SCHLUESSEL})")
    ab.add_argument("--export-ziel", help="lokales Export-Repo (Standard: "
                                          f"{release.STANDARD_EXPORT_ZIEL})")
    ab.set_defaults(func=_abschluss)

    do = sub.add_parser("deploy-ordner", help="Release-Ordner für einrichten.sh --release aus "
                                              "dem committeten Archiv zusammenstellen (Manifest, "
                                              "Abschluss, ausgelieferte Tarballs je Schema; alles "
                                              "vorher geprüft)")
    do.add_argument("--version", required=True, help="Version X.Y.Z")
    do.add_argument("--ziel", required=True, help="neuer Zielordner")
    do.add_argument("--archiv", help=f"Archiv-Repo (Standard: {release.STANDARD_ARCHIV})")
    do.add_argument("--allowed-signers", help="allowed_signers-Datei (Standard: "
                                              "<wurzel>/nachweise/allowed_signers)")
    do.add_argument("--wurzel", help="Repo-Wurzel mit nachweise/ (Standard: ab cwd aufwärts)")
    do.set_defaults(func=_deploy_ordner)

    pr = sub.add_parser("pruefen", help="Prüfungen der Nachweiskette selbst")
    pr_sub = pr.add_subparsers(dest="pruefen_befehl", required=True)
    rat = pr_sub.add_parser("ratsche", help="harte Ruff-Regeln und Zusatzregeln gegen Altbasis")
    rat.add_argument("--wurzel", help="Repo-Wurzel (Standard: ab cwd aufwärts)")
    rat.add_argument("--teil", choices=lint_ratsche.TEILE)
    rat.add_argument("--basis-aktualisieren", action="store_true", help="Basis nur senken")
    rat.set_defaults(func=_pruefen_ratsche)
    pn = pr_sub.add_parser("negativkontrollen",
                           help="alle Negativkontrollen mit echten Werkzeugen (trivy-DB ≤ 24 h "
                                "nötig); Exit 0 nur, wenn jede wie erwartet abbricht")
    pn.add_argument("--wurzel", help="Repo-Wurzel (Standard: ab cwd aufwärts gesucht)")
    pn.add_argument("--cache", help=f"trivy-DB (nur gelesen, Standard: {scan.STANDARD_CACHE})")
    pn.add_argument("--arbeitsordner", help="Ordner für Wegwerf-Schlüssel, -Manifest und "
                                           "-Export (Standard: neues Temp-Verzeichnis)")
    pn.add_argument("--ausgabe", help="Zielpfad für negativkontrollen.json (Standard: "
                                     "<arbeitsordner>/negativkontrollen.json)")
    pn.set_defaults(func=_pruefen_negativkontrollen)
    ps = pr_sub.add_parser("sast", help="Semgrep (vendierter Regelsatz) und Bandit ohne Netz "
                                        "über die Quellbäume, Gate gegen Befundregister und "
                                        "Ausnahmen; Exit 1 bei blockierenden Funden")
    ps.add_argument("--wurzel", help="Repo-Wurzel bzw. frischer Worktree (Standard: ab cwd "
                                     "aufwärts gesucht)")
    ps.add_argument("--arbeitsordner", help="Ordner für Rohberichte und sast.json (Standard: "
                                           "neues Temp-Verzeichnis)")
    ps.set_defaults(func=_pruefen_sast)
    pd = pr_sub.add_parser("dast", help="OWASP ZAP (passiver Scan) gegen eine Instanz mit dem "
                                        "Cookie eines Wegwerf-Kontos ohne Projektrechte; "
                                        "Nachweis im Archiv; S nur mit --betreiber-freigabe; "
                                        "Exit 1 bei blockierenden Alarmen")
    pd.add_argument("--ziel", required=True, help="https://host der Instanz (L: feste Adresse)")
    pd.add_argument("--instanz", required=True, choices=list(dast.INSTANZEN),
                    help="L (Testinstanz) oder S (Produktion, nur mit --betreiber-freigabe)")
    pd.add_argument("--archiv", required=True, help="Nachweis-Archiv (Git-Repo)")
    pd.add_argument("--cookie-datei", required=True,
                    help="Datei (Modus 0600) mit dem Sitzungswert des Wegwerf-Kontos")
    pd.add_argument("--betreiber-freigabe", help="Datum JJJJ-MM-TT (UTC) der Freigabe durch "
                                                 "den Betreiber (nur S, gilt für diesen Tag)")
    pd.add_argument("--arbeitsordner", help="Ordner für Ablauf, URL-Liste und Bericht (Standard: "
                                            "neues Temp-Verzeichnis, danach gelöscht)")
    pd.add_argument("--wurzel", help="Repo-Wurzel (Standard: ab cwd aufwärts gesucht)")
    pd.add_argument("--heute", help="Stichtag JJJJ-MM-TT (Standard: heute)")
    pd.set_defaults(func=_pruefen_dast)
    pf = pr_sub.add_parser("fuzzing", help="Schemathesis gegen eine Wegwerf-Instanz (localhost:"
                                           "18090/18097, synthetische Pakete) je als Betrachter "
                                           "und Admin; Exit 1 bei Fehlschlägen")
    pf.add_argument("--wurzel", help="frischer Worktree mit gebautem web/dist und Server-venv "
                                     "(Standard: ab cwd aufwärts gesucht)")
    pf.add_argument("--arbeitsordner", help="Ordner für Instanz, Berichte und fuzzing.json "
                                            "(Standard: neues Temp-Verzeichnis)")
    pf.set_defaults(func=_pruefen_fuzzing)
    pe = pr_sub.add_parser("egress", help="Egress-Test: Bundle auf absolute URLs prüfen und "
                                          "Portal/Viewer im Headless-Browser gegen eine "
                                          "Wegwerf-Instanz (localhost:18090/18097, synthetisches "
                                          "Testpaket); Exit 1 bei Funden")
    pe.add_argument("--wurzel", help="frischer Worktree mit gebautem web/dist und Server-venv "
                                     "(Standard: ab cwd aufwärts gesucht)")
    pe.add_argument("--arbeitsordner", help="Ordner für Instanz, Berichte und egress.json "
                                            "(Standard: neues Temp-Verzeichnis)")
    pe.set_defaults(func=_pruefen_egress)

    st = sub.add_parser("securitytxt", help="security.txt nach RFC 9116 (Meldeweg)")
    st_sub = st.add_subparsers(dest="securitytxt_befehl", required=True)
    se2 = st_sub.add_parser("erzeugen", help="security.txt aus der Vorlage auf stdout (unsigniert; "
                                             "verweigert, solange SECURITY.md den "
                                             "Postfach-Marker trägt)")
    se2.add_argument("--kontakt", required=True, help="E-Mail-Adresse des Meldepostfachs")
    se2.add_argument("--schluessel-url", required=True, help="https://-Adresse des öffentlichen "
                                                              "OpenPGP-Schlüssels")
    se2.add_argument("--kanonisch", required=True, help="https://-Adresse dieser Datei, endet "
                                                         "auf /.well-known/security.txt")
    se2.add_argument("--richtlinie-url", required=True, help="https://-Adresse der Richtlinie")
    se2.add_argument("--tage", type=int, default=securitytxt.TAGE_STANDARD,
                     help=f"Laufzeit in Tagen, höchstens {securitytxt.TAGE_MAX} "
                          f"(Standard: {securitytxt.TAGE_STANDARD})")
    se2.add_argument("--vorlage", help="Vorlage (Standard: deploy/platform/security.txt.vorlage)")
    se2.add_argument("--security-md", help="SECURITY.md (Standard: <wurzel>/SECURITY.md)")
    se2.add_argument("--wurzel", help="Repo-Wurzel (Standard: ab cwd aufwärts)")
    se2.add_argument("--heute", help="Stichtag JJJJ-MM-TT (Standard: heute)")
    se2.set_defaults(func=_securitytxt_erzeugen)
    sp = st_sub.add_parser("pruefen", help="security.txt per HTTPS abrufen und die "
                                           "OpenPGP-Klartextsignatur prüfen")
    sp.add_argument("--url", required=True, help="https://…/.well-known/security.txt")
    sp.add_argument("--schluessel", required=True, help="öffentlicher OpenPGP-Schlüssel (.asc)")
    sp.add_argument("--archiv", help="Nachweis-Archiv: Ergebnis als "
                                     "pruefungen/<datum>[.<lauf>]-securitytxt-<instanz>/ committen")
    sp.add_argument("--instanz", choices=["L", "S"], help="Instanz (mit --archiv)")
    sp.add_argument("--heute", help="Stichtag JJJJ-MM-TT (Standard: heute)")
    sp.set_defaults(func=_securitytxt_pruefen)

    bt = sub.add_parser("betrieb", help="Betrieb der Instanzen (Update-Protokoll)")
    bt_sub = bt.add_subparsers(dest="betrieb_befehl", required=True)
    bu = bt_sub.add_parser("updates", help="Update-Protokoll gegen die signierten Manifeste im "
                                           "Archiv auswerten und unterstützte Versionen "
                                           "festhalten")
    bu.add_argument("--instanz", required=True, choices=list(betrieb.INSTANZEN),
                    help="Instanz, von der das Protokoll stammt")
    bu.add_argument("--protokoll", required=True, help="Kopie von /var/log/scanview/updates.log")
    bu.add_argument("--archiv", required=True, help="Nachweis-Archiv (Git-Repo)")
    bu.add_argument("--allowed-signers", help="allowed_signers-Datei (Standard: "
                                              "<wurzel>/nachweise/allowed_signers)")
    bu.add_argument("--wurzel", help="Repo-Wurzel (Standard: ab cwd aufwärts)")
    bu.add_argument("--heute", help="Stichtag JJJJ-MM-TT (Standard: heute)")
    bu.set_defaults(func=_betrieb_updates)

    wo = sub.add_parser("wochenscan", help="alle unterstützten Versionen gegen eine frische "
                                           "Schwachstellen-DB scannen; Exit 0 grün, 1 "
                                           "blockierend, 2 Fehler")
    wo.add_argument("--archiv", required=True, help="Nachweis-Archiv (releases/, betrieb/)")
    wo.add_argument("--repo", required=True, help="Repo mit den Quellcommits der Releases")
    wo.add_argument("--vex", required=True, help="OpenVEX-Datei")
    wo.add_argument("--allowed-signers", help="allowed_signers-Datei als Vertrauensanker "
                                              "(Standard: <wurzel>/nachweise/allowed_signers)")
    wo.add_argument("--ausgabe", required=True, help="Ausgabeordner (darf nicht existieren)")
    wo.add_argument("--bekannt", help="Datei mit schon gemeldeten Schwachstellen-IDs (eine je "
                                      "Zeile)")
    wo.add_argument("--db-aktualisieren", action="store_true",
                    help="vorher die trivy-DB laden (protokollierter Online-Schritt)")
    wo.add_argument("--cache", help=f"DB-Cache (Standard: {scan.STANDARD_CACHE})")
    wo.add_argument("--wurzel", help="Repo-Wurzel mit nachweise/ (Standard: ab cwd aufwärts)")
    wo.set_defaults(func=_wochenscan)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Exit 0 bei Erfolg, Exit 2 bei unbekanntem Befehl (argparse) oder einer verletzten Regel
    (`NachweisFehler`, fail-closed) — nie eine unbehandelte Ausnahme nach außen."""
    parser = _bauen_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse meldet unbekannte/fehlende Befehle über SystemExit(2) (bzw. 0 bei --help) —
        # hier abgefangen statt durchgereicht, damit main() immer einen int liefert.
        return e.code if isinstance(e.code, int) else 2

    handler: Callable[[argparse.Namespace], int] = args.func
    try:
        return handler(args)
    except NachweisFehler as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
