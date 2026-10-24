"""Release-Manifest: verbindet Quellcommit, Exportcommit und die SHA-256
aller Artefakte, SBOMs und Berichte mit Werkzeug-Pins, trivy-DB-Stand, Netzschritten und dem
Ergebnis je Prüfung. Die Datei ist **kanonisches JSON** (`kanonisch`), damit die SSH-Signatur
(`ssh-keygen -Y sign`, Namespace `scanview-release`, Kennung `release@scanview`) über genau
diese Bytes eindeutig nachprüfbar bleibt.

Was die Signatur belegt: Die Kette lief vollständig auf der Release-Workstation (nur dort liegt
der Schlüssel) — **nicht**, dass ein Mensch das Release freigegeben hat."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artefakte import Artefakt, Netzschritt
from .fehler import NachweisFehler
from .scan import DbStand
from .werkzeuge import Werkzeug, gefilterte_umgebung

#: Schema neuer Manifeste. Jede Änderung einer Pflichtliste hebt das Schema; gelesen werden alle
#: Schemata aus `PFLICHT_JE_SCHEMA`, jedes gegen seine eigenen Listen (archivierte Manifeste
#: bleiben prüfbar, ein Host mit älterem Prüfer lehnt ein neueres Schema ab).
SCHEMA = "scanview-release-manifest/3"
SCHEMA_1 = "scanview-release-manifest/1"
SCHEMA_2 = "scanview-release-manifest/2"
NAMESPACE = "scanview-release"
KENNUNG = "release@scanview"
MANIFEST_DATEI = "manifest.json"
SIGNATUR_ENDUNG = ".sig"
#: Einzig zulässige Prüfergebnisse. „gescheitert“ gibt es nicht: die Kette bricht vorher ab.
PRUEFUNG_WERTE = frozenset({"bestanden", "nicht-anwendbar"})
#: Ausgelieferte Artefakte je Schema (was auf den Instanzen bereitgestellt wird). Fest verdrahtet,
#: damit eine falsche Kennzeichnung kein ausgeliefertes Artefakt still von der Hash-Prüfung
#: ausnimmt; eine Änderung ist eine bewusste Code-Änderung mit Review. Bis Schema /2 web, server
#: und mcp; ab /3 auch import (die Import-Umgebung des Jobläufers läuft auf dem Server).
#: Archivierte Manifeste werden gegen die Menge ihres eigenen Schemas geprüft.
AUSGELIEFERT_JE_SCHEMA: dict[str, frozenset[str]] = {
    SCHEMA_1: frozenset({"web", "server", "mcp"}),
    SCHEMA_2: frozenset({"web", "server", "mcp"}),
    SCHEMA: frozenset({"web", "server", "mcp", "import"}),
}
#: Auslieferungsmenge neuer Manifeste.
AUSGELIEFERT = AUSGELIEFERT_JE_SCHEMA[SCHEMA]
#: Pflichtangaben der Bau-Umgebung: Toolchain-Versionen und Architektur.
UMGEBUNG_PFLICHT = ("node", "npm", "python", "uv", "docker", "architektur")
#: Prüfungen, die jedes Manifest mit „bestanden“ nennen muss (die Stufen der Release-Kette,
#: die `_bestanden` setzen). Fehlt eine oder steht sie auf „nicht-anwendbar“, ist das Manifest
#: unvollständig — vor dem Signieren wie beim Deploy (release_pruefen.py, drift-getestet).
#: Berichte, die jedes Manifest genau so nennt: die Stufenberichte bis `export` (Reihenfolge wie
#: release.STUFEN), das Scan-Ergebnis, die Werkzeugbefunde der SBOM-Validierung und das
#: Exportmanifest. Drift-getestet gegen release.STUFEN und release_pruefen.py.
_BERICHTE_1 = ("stufe-einfrieren", "stufe-register", "stufe-qualitaetstor", "stufe-sbom",
               "stufe-scan", "stufe-negativkontrollen", "stufe-lizenzen", "stufe-matrix",
               "stufe-export", "scan-ergebnis", "werkzeugbefunde", "exportmanifest")
#: Gepinnte Werkzeuge, die jedes Manifest genau so nennt (= Einträge von werkzeuge.json,
#: drift-getestet).
_WERKZEUGE_1 = ("trivy", "syft", "cyclonedx-npm", "cyclonedx-py", "cyclonedx-py-py312",
                "cyclonedx-cli", "sbomqs", "gitleaks", "shellcheck", "deploy-probe")
_PRUEFUNGEN_1 = ("register", "befunde", "negativtests", "retests", "qualitaetstor", "sbom",
                 "scan", "negativkontrollen", "lizenzen", "export")
#: Pflichtlisten des aktuellen Schemas (/2): bei den Berichten die Stufenberichte `stufe-sast`,
#: `stufe-fuzzing` und `stufe-egress` (an ihrer Stelle in release.STUFEN), der eigene
#: npm-Signaturbericht der Bau-Stufe, die Rohberichte von Semgrep und Bandit, die JUnit-Berichte
#: des Fuzzings je Identität und die beiden Berichte des Egress-Tests; bei den Werkzeugen die
#: Prüfwerkzeuge für SAST, Fuzzing, DAST und den Egress-Test (Reihenfolge wie werkzeuge.json);
#: bei den Prüfungen die Commit-Signatur (Stufe `einfrieren`), die npm-Signaturprüfung (Stufe
#: `sbom`), `sast`, `fuzzing` und `egress`.
PFLICHT_BERICHTE = ("stufe-einfrieren", "stufe-register", "stufe-qualitaetstor", "stufe-sast",
                    "stufe-sbom", "stufe-fuzzing", "stufe-egress", "stufe-scan",
                    "stufe-negativkontrollen", "stufe-lizenzen", "stufe-matrix", "stufe-export",
                    "scan-ergebnis", "werkzeugbefunde", "exportmanifest", "npm-signaturen",
                    "sast-semgrep", "sast-bandit", "fuzzing-admin", "fuzzing-betrachter",
                    "egress-statisch", "egress-browser")
PFLICHT_WERKZEUGE = (*_WERKZEUGE_1, "semgrep", "bandit", "schemathesis", "zap", "caddy-test",
                     "playwright")
PFLICHT_PRUEFUNGEN = (*_PRUEFUNGEN_1, "commit-signatur", "npm-signaturen", "sast",
                      "fuzzing", "egress")
#: Je Schema genau diese Pflichtlisten (Berichte, Werkzeuge, Prüfungen); /1 ist eingefroren.
#: /3 unterscheidet sich von /2 nur in der Auslieferungsmenge (`AUSGELIEFERT_JE_SCHEMA`).
#: Drift-getestet gegen deploy/platform/release_pruefen.py.
PFLICHT_JE_SCHEMA: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    SCHEMA_1: (_BERICHTE_1, _WERKZEUGE_1, _PRUEFUNGEN_1),
    SCHEMA_2: (PFLICHT_BERICHTE, PFLICHT_WERKZEUGE, PFLICHT_PRUEFUNGEN),
    SCHEMA: (PFLICHT_BERICHTE, PFLICHT_WERKZEUGE, PFLICHT_PRUEFUNGEN),
}

_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHA512 = re.compile(r"[0-9a-f]{128}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")
_FELDER = ("schema", "version", "erstellt", "quellcommit", "exportcommit", "artefakte", "sboms",
           "berichte", "werkzeuge", "db_stand", "netzschritte", "pruefungen", "umgebung",
           "trockenlauf")
_STDERR_AUSZUG = 2000


def ausgeliefert_fuer(manifest: dict[str, Any]) -> frozenset[str]:
    """Auslieferungsmenge des Schemas eines (gelesenen) Manifests; unbekanntes Schema → Fehler."""
    schema = manifest.get("schema")
    if not isinstance(schema, str) or schema not in AUSGELIEFERT_JE_SCHEMA:
        raise NachweisFehler("manifest", f"unbekanntes Schema „{schema}“")
    return AUSGELIEFERT_JE_SCHEMA[schema]


def kanonisch(obj: Any) -> bytes:
    """Kanonische Bytes: Schlüssel sortiert, UTF-8 ohne Escapes, ohne Leerraum, ohne Zeilenende."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _sha256(pfad: Path) -> str:
    try:
        return hashlib.sha256(pfad.read_bytes()).hexdigest()
    except OSError as e:
        raise NachweisFehler("manifest", f"{pfad}: nicht lesbar ({e.strerror})") from None


def pruefe_version(version: str, regel: str) -> None:
    """Versionsnummer `X.Y.Z[-…]` — schließt zugleich Pfadbestandteile wie `..` oder `/` aus."""
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise NachweisFehler(regel, f"Version „{version}“ ist keine gültige Versionsnummer "
                                    "(X.Y.Z)")


def _pruefe_pruefungen(pruefungen: object) -> dict[str, str]:
    if not isinstance(pruefungen, dict) or not pruefungen:
        raise NachweisFehler("manifest", "keine Prüfung angegeben — ein Manifest ohne "
                                         "Prüfergebnisse belegt nichts")
    for name, wert in pruefungen.items():
        if not isinstance(name, str) or not name or wert not in PRUEFUNG_WERTE:
            raise NachweisFehler("manifest", f"Prüfung „{name}“: Ergebnis „{wert}“ unzulässig "
                                             f"(nur {', '.join(sorted(PRUEFUNG_WERTE))})")
    return dict(pruefungen)


def _pruefe_umgebung(umgebung: object) -> dict[str, str]:
    """Umgebung = {Werkzeug: Versionsangabe}; jede Pflichtangabe vorhanden und nicht leer."""
    if not isinstance(umgebung, dict):
        raise NachweisFehler("manifest", "Umgebung fehlt (Toolchain-Versionen, Architektur)")
    fehlend = [k for k in UMGEBUNG_PFLICHT
               if not isinstance(umgebung.get(k), str) or not umgebung[k].strip()]
    falsch = [k for k, v in umgebung.items() if not isinstance(k, str) or not isinstance(v, str)]
    if fehlend or falsch:
        raise NachweisFehler("manifest", "Umgebung unvollständig: "
                                         f"{', '.join(sorted({*fehlend, *map(str, falsch)}))}")
    return dict(umgebung)


def _netzschritt(s: Netzschritt) -> dict[str, Any]:
    return {"befehl": list(s.befehl), "cwd": s.cwd, "zeit": s.zeit}


def baue_manifest(
    version: str, quellcommit: str, exportcommit: str | None, artefakte: list[Artefakt],
    sboms: list[Path], berichte: dict[str, Path], werkzeuge: dict[str, Werkzeug],
    db_stand: DbStand, pruefungen: dict[str, str], *, ausgeliefert: Collection[str],
    umgebung: dict[str, str], trockenlauf: bool = False, veroeffentlichbar: bool,
    sperrgrund: str | None = None,
    netzschritte: list[Netzschritt] | None = None, erstellt: datetime | None = None,
) -> dict[str, Any]:
    """Manifest als JSON-fähiges dict. Alle Hashes werden aus den Dateien neu berechnet; weicht
    ein Artefakt von seinem Bau-Hash ab, ist das ein Fehler (fail-closed). `ausgeliefert` nennt
    die Artefakte, die das Release an Instanzen ausliefert (`AUSGELIEFERT`); alle übrigen
    stehen mit `"ausgeliefert": false` nur zur Information im Manifest. Dateien stehen mit ihrem
    Dateinamen drin (ohne Pfad), damit das Manifest vom Ablageort unabhängig bleibt.
    `umgebung` nennt Toolchain-Versionen und Architektur des Baus (`UMGEBUNG_PFLICHT`);
    `trockenlauf` kennzeichnet ein Manifest, das nie archiviert oder ausgeliefert wird.
    `veroeffentlichbar`/`sperrgrund` halten fest, ob der öffentliche Export dieses
    Releases veröffentlicht werden darf (gesperrt nur mit Grund)."""
    pruefe_version(version, "manifest")
    veroeffentlichung = _pruefe_veroeffentlichung(
        {"veroeffentlichbar": veroeffentlichbar, "grund": sperrgrund}, "manifest")
    umg = _pruefe_umgebung(umgebung)
    if not isinstance(trockenlauf, bool):
        raise NachweisFehler("manifest", "trockenlauf muss true oder false sein")
    if not isinstance(quellcommit, str) or not _COMMIT.fullmatch(quellcommit):
        raise NachweisFehler("manifest", f"Quellcommit „{quellcommit}“ ist kein voller "
                                         "Commit-Hash")
    if exportcommit is None or not _COMMIT.fullmatch(exportcommit):
        raise NachweisFehler("manifest", f"Exportcommit „{exportcommit}“ ist kein voller "
                                         "Commit-Hash")
    pruef = _pruefe_pruefungen(pruefungen)

    namen = [a.name for a in artefakte]
    doppelt = sorted({n for n in namen if namen.count(n) > 1})
    if doppelt:
        raise NachweisFehler("manifest", f"Artefakt doppelt: {', '.join(doppelt)}")
    if set(ausgeliefert) != AUSGELIEFERT:
        soll = ", ".join(sorted(AUSGELIEFERT))
        raise NachweisFehler("manifest", f"ausgeliefert muss genau {soll} sein, "
                                         f"angegeben: "
                                         f"{', '.join(sorted(ausgeliefert)) or '(nichts)'}")
    unbekannt = sorted(AUSGELIEFERT - set(namen))
    if unbekannt:
        raise NachweisFehler("manifest", f"ausgeliefert, aber nicht gebaut: "
                                         f"{', '.join(unbekannt)}")
    artefakt_liste = []
    for a in artefakte:
        ist = _sha256(a.pfad)
        if ist != a.sha256:
            raise NachweisFehler("manifest", f"Artefakt {a.name}: SHA-256 {ist} weicht vom "
                                             f"Bau-Hash {a.sha256} ab")
        artefakt_liste.append({"name": a.name, "datei": a.pfad.name, "sha256": ist,
                               "sha512": a.sha512, "ausgeliefert": a.name in ausgeliefert})

    sbom_hashes: dict[str, str] = {}
    for pfad in sboms:
        if pfad.name in sbom_hashes:
            raise NachweisFehler("manifest", f"SBOM doppelt: {pfad.name}")
        sbom_hashes[pfad.name] = _sha256(pfad)

    daten = {
        "schema": SCHEMA,
        "version": version,
        "erstellt": (erstellt or datetime.now(UTC)).isoformat(),
        "quellcommit": quellcommit,
        "exportcommit": exportcommit,
        "artefakte": artefakt_liste,
        "sboms": sbom_hashes,
        "berichte": {name: {"datei": p.name, "sha256": _sha256(p)}
                     for name, p in berichte.items()},
        "werkzeuge": {name: {"image": w.image, "version": w.version}
                      for name, w in werkzeuge.items()},
        "db_stand": {"zeit": db_stand.zeit.isoformat(), "feld": db_stand.feld,
                     "sha256": db_stand.sha256,
                     "netzschritt": (_netzschritt(db_stand.netzschritt)
                                     if db_stand.netzschritt else None)},
        "netzschritte": [_netzschritt(s) for s in netzschritte or []],
        "pruefungen": pruef,
        "umgebung": umg,
        "trockenlauf": trockenlauf,
        "veroeffentlichung": veroeffentlichung,
    }
    # Dieselbe Vollständigkeitsprüfung wie beim Lesen: signiert wird nur ein vollständiges
    # Manifest.
    _pruefe_inhalt(daten, "neues Manifest")
    return daten


def schreibe_manifest(manifest: dict[str, Any], pfad: Path) -> Path:
    """Manifest als kanonische Bytes schreiben; eine vorhandene Datei wird nie überschrieben."""
    if pfad.exists():
        raise NachweisFehler("manifest", f"{pfad}: existiert schon — wird nicht überschrieben")
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with pfad.open("xb") as f:
        f.write(kanonisch(manifest))
    return pfad


def _pruefe_veroeffentlichung(wert: object, wo: str) -> dict[str, Any]:
    """`{"veroeffentlichbar": bool, "grund": str | None}` — gesperrt nur mit Grund, frei nur
    ohne."""
    if not (isinstance(wert, dict) and set(wert) == {"veroeffentlichbar", "grund"}
            and isinstance(wert["veroeffentlichbar"], bool)):
        raise NachweisFehler("manifest", f"{wo}: veroeffentlichung ungültig: {wert!r}"[:300])
    grund = wert["grund"]
    if wert["veroeffentlichbar"] and grund is not None:
        raise NachweisFehler("manifest", f"{wo}: veröffentlichbar, aber mit Sperrgrund")
    if not wert["veroeffentlichbar"] and not (isinstance(grund, str) and grund.strip()):
        raise NachweisFehler("manifest", f"{wo}: Veröffentlichung gesperrt ohne Grund")
    return {"veroeffentlichbar": wert["veroeffentlichbar"], "grund": grund}


def _pruefe_artefaktliste(liste: object, wo: str) -> None:
    if not isinstance(liste, list):
        raise NachweisFehler("manifest", f"{wo}: Artefaktliste fehlt")
    for a in liste:
        if not (isinstance(a, dict)
                and isinstance(a.get("name"), str) and a["name"]
                and isinstance(a.get("datei"), str) and a["datei"]
                and isinstance(a.get("sha256"), str) and _SHA256.fullmatch(a["sha256"])
                and isinstance(a.get("sha512"), str) and _SHA512.fullmatch(a["sha512"])
                and isinstance(a.get("ausgeliefert"), bool)):
            raise NachweisFehler("manifest", f"{wo}: Artefakteintrag unvollständig oder mit "
                                             f"falschen Typen: {a!r}"[:500])


def _hex(wert: object, muster: re.Pattern[str]) -> bool:
    return isinstance(wert, str) and muster.fullmatch(wert) is not None


def _dateiname(wert: object) -> bool:
    return isinstance(wert, str) and bool(wert) and "/" not in wert and wert not in (".", "..")


def _pruefe_inhalt(daten: dict[str, Any], wo: str) -> None:
    """Vollständigkeit über die Feldnamen hinaus: Exportcommit, SBOMs aller
    ausgelieferten Artefakte, Berichte, Werkzeuge, DB-Stand, Netzschritte, Veröffentlichung und
    alle Pflichtprüfungen bestanden — jeweils mit den richtigen Typen und Hash-Formen."""
    def fehler(text: str) -> NachweisFehler:
        return NachweisFehler("manifest", f"{wo}: {text}")

    if daten.get("schema") not in PFLICHT_JE_SCHEMA:
        raise fehler(f"unbekanntes Schema „{daten.get('schema')}“")
    pflicht_berichte, pflicht_werkzeuge, pflicht_pruefungen = PFLICHT_JE_SCHEMA[daten["schema"]]
    if not _hex(daten.get("exportcommit"), _COMMIT):
        raise fehler("Exportcommit fehlt oder ist kein voller Commit-Hash")
    sboms = daten.get("sboms")
    if not isinstance(sboms, dict) or not all(
            _dateiname(n) and _hex(h, _SHA256) for n, h in sboms.items()):
        raise fehler("SBOM-Liste fehlt oder ist ungültig (Dateiname → SHA-256)")
    soll = AUSGELIEFERT_JE_SCHEMA[daten["schema"]]
    # Wie release_pruefen.py: genau die Auslieferungsmenge des Schemas, schon beim Laden — ein
    # /2-Manifest mit ausgeliefertem import oder ein /3-Manifest ohne ist kein gültiges Manifest.
    _pruefe_artefaktliste(daten.get("artefakte"), wo)
    kennzeichnung = {a["name"] for a in daten["artefakte"] if a["ausgeliefert"] is True}
    if kennzeichnung != soll:
        raise fehler(f"ausgeliefert: {', '.join(sorted(kennzeichnung)) or '(nichts)'} — "
                     f"erwartet genau {', '.join(sorted(soll))}")
    fehlend = sorted(n for n in soll
                     if f"scanview-{n}-{daten.get('version')}.cdx.json" not in sboms)
    if fehlend:
        raise fehler(f"SBOM fehlt für ausgelieferte Artefakte: {', '.join(fehlend)}")
    berichte = daten.get("berichte")
    if not isinstance(berichte, dict) or not berichte or not all(
            isinstance(n, str) and n and isinstance(b, dict) and set(b) == {"datei", "sha256"}
            and _dateiname(b["datei"]) and _hex(b["sha256"], _SHA256)
            for n, b in berichte.items()):
        raise fehler("Berichte fehlen oder sind ungültig ({datei, sha256} je Bericht)")
    if set(berichte) != set(pflicht_berichte):
        raise fehler(f"Berichte nicht vollständig: fehlend "
                     f"{sorted(set(pflicht_berichte) - set(berichte)) or '-'}, unbekannt "
                     f"{sorted(set(berichte) - set(pflicht_berichte)) or '-'}")
    werkzeuge = daten.get("werkzeuge")
    if not isinstance(werkzeuge, dict) or not werkzeuge or not all(
            isinstance(w, dict) and set(w) == {"image", "version"}
            and all(isinstance(w[k], str) and w[k] for k in w) for w in werkzeuge.values()):
        raise fehler("Werkzeuge fehlen oder sind ungültig ({image, version} je Werkzeug)")
    if set(werkzeuge) != set(pflicht_werkzeuge):
        raise fehler(f"Werkzeuge nicht vollständig: fehlend "
                     f"{sorted(set(pflicht_werkzeuge) - set(werkzeuge)) or '-'}, unbekannt "
                     f"{sorted(set(werkzeuge) - set(pflicht_werkzeuge)) or '-'}")
    db = daten.get("db_stand")
    if not (isinstance(db, dict) and set(db) == {"zeit", "feld", "sha256", "netzschritt"}
            and isinstance(db["zeit"], str) and db["zeit"] and isinstance(db["feld"], str)
            and db["feld"] and _hex(db["sha256"], _SHA256)
            and (db["netzschritt"] is None or _ist_netzschritt(db["netzschritt"]))):
        raise fehler("DB-Stand fehlt oder ist ungültig")
    netz = daten.get("netzschritte")
    if not isinstance(netz, list) or not all(_ist_netzschritt(s) for s in netz):
        raise fehler("Netzschritte ungültig")
    if "veroeffentlichung" not in daten:
        raise fehler("Feld veroeffentlichung fehlt")
    _pruefe_veroeffentlichung(daten["veroeffentlichung"], wo)
    pruefungen = daten.get("pruefungen")
    offen = [p for p in pflicht_pruefungen
             if not isinstance(pruefungen, dict) or pruefungen.get(p) != "bestanden"]
    if offen:
        raise fehler(f"Pflichtprüfungen nicht bestanden oder fehlend: {', '.join(offen)}")


def _ist_netzschritt(s: object) -> bool:
    return (isinstance(s, dict) and set(s) == {"befehl", "cwd", "zeit"}
            and isinstance(s["befehl"], list) and bool(s["befehl"])
            and all(isinstance(x, str) for x in s["befehl"])
            and isinstance(s["cwd"], str) and isinstance(s["zeit"], str) and bool(s["zeit"]))


def _parse(roh: bytes, wo: str) -> dict[str, Any]:
    """Manifest-Bytes prüfen: exakt kanonisch (sonst wäre die Signatur über andere Bytes als die
    geprüften denkbar), alle Felder vorhanden, Schema, Prüfergebnisse und Artefakttypen gültig."""
    try:
        daten = json.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise NachweisFehler("manifest", f"{wo}: kein gültiges JSON ({e})") from None
    if not isinstance(daten, dict) or kanonisch(daten) != roh:
        raise NachweisFehler("manifest", f"{wo}: nicht in kanonischer Form")
    fehlend = [f for f in _FELDER if f not in daten]
    if fehlend:
        raise NachweisFehler("manifest", f"{wo}: Felder fehlen: {', '.join(fehlend)}")
    if daten["schema"] not in PFLICHT_JE_SCHEMA:
        raise NachweisFehler("manifest", f"{wo}: unbekanntes Schema „{daten['schema']}“")
    if not isinstance(daten["version"], str):
        raise NachweisFehler("manifest", f"{wo}: Version fehlt")
    pruefe_version(daten["version"], "manifest")
    _pruefe_pruefungen(daten["pruefungen"])
    _pruefe_artefaktliste(daten["artefakte"], wo)
    _pruefe_umgebung(daten["umgebung"])
    if not isinstance(daten["trockenlauf"], bool):
        raise NachweisFehler("manifest", f"{wo}: trockenlauf muss true oder false sein")
    _pruefe_inhalt(daten, wo)
    return daten


def _lesen(pfad: Path, regel: str, was: str) -> bytes:
    try:
        return pfad.read_bytes()
    except OSError as e:
        raise NachweisFehler(regel, f"{pfad}: {was} nicht lesbar ({e.strerror})") from None


def lade_manifest(pfad: Path) -> dict[str, Any]:
    """Manifest **ohne** Signaturprüfung lesen und prüfen (für Aufrufer, die die Signatur schon
    über dieselben Bytes geprüft haben oder nur die Form brauchen). Wer Hashes gegen das
    Manifest prüft, nimmt `lade_signiertes_manifest`."""
    return _parse(_lesen(pfad, "manifest", "Manifest"), str(pfad))


def _ssh_keygen(befehl: list[str], eingabe: bytes | None) -> subprocess.CompletedProcess[bytes]:
    try:
        if eingabe is None:
            return subprocess.run(["ssh-keygen", *befehl], stdin=subprocess.DEVNULL,
                                  capture_output=True, check=False, env=gefilterte_umgebung())
        return subprocess.run(["ssh-keygen", *befehl], input=eingabe, capture_output=True,
                              check=False, env=gefilterte_umgebung())
    except OSError as e:
        raise NachweisFehler("signatur", f"ssh-keygen nicht ausführbar: {e.strerror}") from None


def _stderr(ergebnis: subprocess.CompletedProcess[bytes]) -> str:
    text = ergebnis.stderr.decode("utf-8", "replace").strip()[-_STDERR_AUSZUG:]
    return text or "(keine Ausgabe auf stderr)"


def signiere(manifest_pfad: Path, schluessel: Path) -> Path:
    """`ssh-keygen -Y sign` mit Namespace `scanview-release` → `<datei>.sig`. Eine vorhandene
    Signatur wird nie überschrieben."""
    sig = manifest_pfad.with_name(manifest_pfad.name + SIGNATUR_ENDUNG)
    if sig.exists():
        raise NachweisFehler("signatur", f"{sig}: existiert schon — wird nicht überschrieben")
    if not manifest_pfad.is_file():
        raise NachweisFehler("signatur", f"{manifest_pfad}: Manifest fehlt")
    if not schluessel.is_file():
        raise NachweisFehler("signatur", f"{schluessel}: Signierschlüssel fehlt")
    ergebnis = _ssh_keygen(["-q", "-Y", "sign", "-f", str(schluessel), "-n", NAMESPACE,
                            str(manifest_pfad)], None)
    if ergebnis.returncode != 0 or not sig.is_file():
        raise NachweisFehler("signatur", f"Signieren fehlgeschlagen (Exit {ergebnis.returncode}):"
                                         f" {_stderr(ergebnis)}")
    return sig


def _verifiziere(roh: bytes, manifest_pfad: Path, allowed_signers: Path) -> None:
    """Signatur über genau die übergebenen Bytes prüfen (`-Y verify` liest sie von stdin)."""
    sig = manifest_pfad.with_name(manifest_pfad.name + SIGNATUR_ENDUNG)
    for pfad, was in ((sig, "Signatur (.sig)"), (allowed_signers, "allowed_signers")):
        if not pfad.is_file():
            raise NachweisFehler("signatur", f"{pfad}: {was} fehlt")
    ergebnis = _ssh_keygen(["-Y", "verify", "-f", str(allowed_signers), "-I", KENNUNG,
                            "-n", NAMESPACE, "-s", str(sig)], roh)
    if ergebnis.returncode != 0:
        raise NachweisFehler("signatur", f"{manifest_pfad.name}: Signatur ungültig "
                                         f"(Exit {ergebnis.returncode}): {_stderr(ergebnis)}")


def _manifest_bytes(manifest_pfad: Path) -> bytes:
    if not manifest_pfad.is_file():
        raise NachweisFehler("signatur", f"{manifest_pfad}: Manifest fehlt")
    return _lesen(manifest_pfad, "signatur", "Manifest")


def pruefe_signatur(manifest_pfad: Path, allowed_signers: Path) -> None:
    """`ssh-keygen -Y verify` gegen `allowed_signers` mit Kennung `release@scanview` und
    Namespace `scanview-release`; maßgeblich ist allein der Exit-Code."""
    _verifiziere(_manifest_bytes(manifest_pfad), manifest_pfad, allowed_signers)


def lade_signiertes_manifest(manifest_pfad: Path, allowed_signers: Path) -> dict[str, Any]:
    """Manifest **einmal** lesen, die Signatur über genau diese Bytes prüfen und dieselben Bytes
    parsen — kein Zeitfenster, in dem eine ausgetauschte Datei ungeprüft ausgewertet würde."""
    roh = _manifest_bytes(manifest_pfad)
    _verifiziere(roh, manifest_pfad, allowed_signers)
    return _parse(roh, str(manifest_pfad))


def _hashes(pfad: Path) -> tuple[str, str]:
    h256, h512 = hashlib.sha256(), hashlib.sha512()
    try:
        with pfad.open("rb") as f:
            while block := f.read(1 << 20):
                h256.update(block)
                h512.update(block)
    except OSError as e:
        raise NachweisFehler("artefakt-hash", f"{pfad}: nicht lesbar ({e.strerror})") from None
    return h256.hexdigest(), h512.hexdigest()


def pruefe_artefakte(manifest: dict[str, Any], verzeichnis: Path, *,
                     nur_ausgeliefert: bool = False) -> None:
    """SHA-256 und SHA-512 jedes Artefakts in `verzeichnis/<datei>` gegen das Manifest. Mit
    `nur_ausgeliefert` nur die ausgelieferten (Deploy hat nur diese). In jedem Fall
    müssen genau die Artefakte aus `AUSGELIEFERT_JE_SCHEMA` (Schema des Manifests) als
    ausgeliefert im Manifest stehen."""
    _pruefe_artefaktliste(manifest.get("artefakte"), "Manifest")
    eintraege: list[dict[str, Any]] = manifest["artefakte"]
    soll = ausgeliefert_fuer(manifest)
    kennzeichnung = {a["name"] for a in eintraege if a["ausgeliefert"] is True}
    if kennzeichnung != soll:
        raise NachweisFehler("artefakt-hash", "als ausgeliefert gekennzeichnet: "
                                              f"{', '.join(sorted(kennzeichnung)) or '(nichts)'}"
                                              f" — erwartet {', '.join(sorted(soll))}")
    geprueft = 0
    for a in eintraege:
        if nur_ausgeliefert and a["ausgeliefert"] is not True:
            continue
        if Path(a["datei"]).name != a["datei"] or a["datei"] in (".", ".."):
            raise NachweisFehler("artefakt-hash", f"{a['name']}: Dateiname „{a['datei']}“ mit "
                                                  "Pfadanteil")
        pfad = verzeichnis / a["datei"]
        if not pfad.is_file():
            raise NachweisFehler("artefakt-hash", f"{a['name']}: {pfad} fehlt")
        ist256, ist512 = _hashes(pfad)
        if ist256 != a["sha256"]:
            raise NachweisFehler("artefakt-hash", f"{a['name']}: {pfad.name} hat SHA-256 "
                                                  f"{ist256}, Manifest {a['sha256']}")
        if ist512 != a["sha512"]:
            raise NachweisFehler("artefakt-hash", f"{a['name']}: {pfad.name} hat SHA-512 "
                                                  f"{ist512}, Manifest {a['sha512']}")
        geprueft += 1
    if geprueft == 0:
        raise NachweisFehler("artefakt-hash", "kein Artefakt geprüft")
