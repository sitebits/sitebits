#!/usr/bin/env python3
"""Release-Verzeichnis gegen das signierte Manifest prüfen und sicher entpacken (Deploy).

Läuft auf dem Zielhost **ohne** das Paket `scanview_nachweise`, nur mit der Standardbibliothek
(Python ≥ 3.11) und `ssh-keygen`. Die Regeln entsprechen `nachweise/.../manifest.py`; ein Test
(`nachweise/tests/test_deploy.py`) hält die duplizierten Konstanten gleich.

    release_pruefen.py pruefen   <release-verzeichnis> <allowed_signers>
    release_pruefen.py entpacken <release-verzeichnis> <allowed_signers> <ziel>

Release-Verzeichnis (genau diese Einträge, nichts sonst; `scanview-nachweise deploy-ordner`
stellt es aus dem Archiv zusammen):

    manifest.json  manifest.json.sig  abschluss.json  abschluss.json.sig
    artefakte/scanview-{web,server,mcp,import}-<version>.tar.gz   (import ab Schema /3)

Ablauf (fail-closed, jede Abweichung → `NACHWEIS-FEHLER <regel>: …`, Exit 2):

1. `allowed_signers` ist die Vertrauensdatei des Hosts und darf nicht im Release liegen.
2. Das Verzeichnis enthält genau die erwarteten Einträge, keine Symlinks.
3. Manifest **einmal** lesen, die Signatur über genau diese Bytes prüfen
   (`ssh-keygen -Y verify`, Kennung `release@scanview`, Namespace `scanview-release`) und
   dieselben Bytes parsen: kanonische Form, alle Pflichtfelder des aktuellen Schemas mit
   vollständigem Inhalt (Exportcommit, SBOMs der ausgelieferten Artefakte, Berichte, Werkzeuge,
   DB-Stand, Veröffentlichung, alle Pflichtprüfungen „bestanden“), kein Trockenlauf,
   ausgeliefert genau die Menge des Schemas (bis /2 web/server/mcp, ab /3 dazu import) mit festen
   Dateinamen.
   Danach der Abschlussnachweis (`abschluss.json`, eigener Namespace `scanview-abschluss`,
   entsteht erst nach Archivierung und Tag): Signatur über genau die gelesenen Bytes, kanonisch,
   gehört zu genau diesem Manifest (SHA-256 der Manifest-Bytes, Version, Quell-/Exportcommit).
   Ein Release, dessen Kette nach dem Signieren des Manifests abbrach, hat keinen — abgelehnt.
4. Jeden ausgelieferten Tarball in einen privaten Ordner kopieren und dabei SHA-256 und SHA-512
   bilden; alles Weitere liest nur noch diese Kopie (kein Austausch zwischen Prüfen und Entpacken).
5. Tar-Inhalt prüfen: nur reguläre Dateien und Ordner, relative Pfade ohne `..`, nur unter den
   Wurzeln des Artefakts, keine Doppelten, Modus nur 0644/0755.
6. `entpacken`: web, server und (ab /3) import nach `<ziel>/baum` (mcp läuft beim Nutzer, nicht
   auf dem Host — geprüft, nicht entpackt). Ausgabe auf stdout: `schlüssel=wert`-Zeilen für das
   Update-Protokoll; bei Ablehnung `grund=<regel>` plus die bis dahin feststehenden Felder.
   Die Regel ist ein fester Bezeichner aus `[a-z-]`, also ohne Leerzeichen."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import IO, Any

#: Wie manifest.SCHEMA / PFLICHT_JE_SCHEMA (drift-getestet): neue Manifeste tragen /3; jedes
#: bekannte Schema wird gegen seine eigenen Pflichtlisten geprüft, ein unbekanntes abgelehnt.
SCHEMA = "scanview-release-manifest/3"
SCHEMA_1 = "scanview-release-manifest/1"
SCHEMA_2 = "scanview-release-manifest/2"
NAMESPACE = "scanview-release"
KENNUNG = "release@scanview"
MANIFEST_DATEI = "manifest.json"
SIGNATUR_DATEI = "manifest.json.sig"
ABSCHLUSS_DATEI = "abschluss.json"
ABSCHLUSS_SIGNATUR = "abschluss.json.sig"
ABSCHLUSS_SCHEMA = "scanview-release-abschluss/1"
ABSCHLUSS_NAMESPACE = "scanview-abschluss"
ABSCHLUSS_FELDER = ("schema", "version", "manifest_sha256", "quellcommit", "exportcommit",
                    "archiv_commit", "tag", "erstellt")
ARTEFAKT_ORDNER = "artefakte"
PRUEFUNG_WERTE = frozenset({"bestanden", "nicht-anwendbar"})
#: Wie manifest.AUSGELIEFERT_JE_SCHEMA (drift-getestet).
AUSGELIEFERT_JE_SCHEMA: dict[str, frozenset[str]] = {
    SCHEMA_1: frozenset({"web", "server", "mcp"}),
    SCHEMA_2: frozenset({"web", "server", "mcp"}),
    SCHEMA: frozenset({"web", "server", "mcp", "import"}),
}
AUSGELIEFERT = AUSGELIEFERT_JE_SCHEMA[SCHEMA]
UMGEBUNG_PFLICHT = ("node", "npm", "python", "uv", "docker", "architektur")
#: Wie manifest.PFLICHT_BERICHTE / PFLICHT_WERKZEUGE (drift-getestet): genau diese.
_BERICHTE_1 = ("stufe-einfrieren", "stufe-register", "stufe-qualitaetstor", "stufe-sbom",
               "stufe-scan", "stufe-negativkontrollen", "stufe-lizenzen", "stufe-matrix",
               "stufe-export", "scan-ergebnis", "werkzeugbefunde", "exportmanifest")
_WERKZEUGE_1 = ("trivy", "syft", "cyclonedx-npm", "cyclonedx-py", "cyclonedx-py-py312",
                "cyclonedx-cli", "sbomqs", "gitleaks", "shellcheck", "deploy-probe")
#: Jede muss mit „bestanden“ im Manifest stehen (wie manifest.PFLICHT_PRUEFUNGEN).
_PRUEFUNGEN_1 = ("register", "befunde", "negativtests", "retests", "qualitaetstor", "sbom",
                 "scan", "negativkontrollen", "lizenzen", "export")
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
PFLICHT_JE_SCHEMA: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    SCHEMA_1: (_BERICHTE_1, _WERKZEUGE_1, _PRUEFUNGEN_1),
    SCHEMA_2: (PFLICHT_BERICHTE, PFLICHT_WERKZEUGE, PFLICHT_PRUEFUNGEN),
    SCHEMA: (PFLICHT_BERICHTE, PFLICHT_WERKZEUGE, PFLICHT_PRUEFUNGEN),
}
FELDER = ("schema", "version", "erstellt", "quellcommit", "exportcommit", "artefakte", "sboms",
          "berichte", "werkzeuge", "db_stand", "netzschritte", "pruefungen", "umgebung",
          "trockenlauf")
#: Wurzeln, unter denen die Dateien eines Artefakts liegen dürfen (wie artefakte.py sie baut).
WURZELN: dict[str, tuple[str, ...]] = {
    "web": ("web/dist/",),
    "server": ("server/", "deploy/platform/"),
    "mcp": ("mcp-server/",),
    "import": ("import/",),
}
#: Auf dem Host installiert werden nur diese (soweit das Schema sie ausliefert); mcp läuft beim
#: Nutzer.
ENTPACKEN = ("web", "server", "import")
REIHENFOLGE = ("web", "server", "mcp", "import")

_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHA512 = re.compile(r"[0-9a-f]{128}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")
_MODI = (0o644, 0o755)
#: Fester Pfad statt PATH-Suche (läuft als root); einrichten.sh prüft, dass er existiert.
SSH_KEYGEN = "/usr/bin/ssh-keygen"
_BLOCK = 1 << 20


class Fehler(Exception):
    def __init__(self, regel: str, text: str) -> None:
        super().__init__(f"{regel}: {text}")
        self.regel = regel
        self.text = text


def _kanonisch(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _oeffne_regulaer(pfad: Path, regel: str) -> int:
    """Reguläre Datei öffnen, ohne einem Symlink zu folgen. O_NONBLOCK, damit eine zwischen
    Layout-Prüfung und open() untergeschobene FIFO das open() nicht blockiert; erst nach der
    fstat-Prüfung wird wieder blockierend gelesen."""
    try:
        fd = os.open(pfad, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as e:
        raise Fehler(regel, f"{pfad}: nicht lesbar ({e.strerror})") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise Fehler(regel, f"{pfad}: keine reguläre Datei")
    os.set_blocking(fd, True)
    return fd


def _lies_datei(pfad: Path, regel: str) -> bytes:
    """Reguläre Datei lesen, ohne einem Symlink zu folgen."""
    with os.fdopen(_oeffne_regulaer(pfad, regel), "rb") as f:
        return f.read()


def _pruefe_vertrauensdatei(allowed_signers: Path, release: Path) -> None:
    if not allowed_signers.is_file():
        raise Fehler("signatur", f"{allowed_signers}: allowed_signers fehlt")
    anker, wurzel = allowed_signers.resolve(), release.resolve()
    if anker == wurzel or wurzel in anker.parents:
        raise Fehler("signatur", f"{allowed_signers}: liegt im Release — maßgeblich ist allein "
                                 "die Vertrauensdatei des Hosts (/etc/scanview/allowed_signers)")


def _pruefe_verzeichnis(release: Path) -> None:
    if release.is_symlink() or not release.is_dir():
        raise Fehler("release-verzeichnis", f"{release}: kein Verzeichnis")
    ist = sorted(os.listdir(release))
    soll = sorted((MANIFEST_DATEI, SIGNATUR_DATEI, ABSCHLUSS_DATEI, ABSCHLUSS_SIGNATUR,
                   ARTEFAKT_ORDNER))
    if ist != soll:
        raise Fehler("release-verzeichnis", f"{release}: enthält {', '.join(ist) or '(nichts)'}"
                                            f" — erwartet genau {', '.join(soll)}")
    for name in (MANIFEST_DATEI, SIGNATUR_DATEI, ABSCHLUSS_DATEI, ABSCHLUSS_SIGNATUR):
        if not stat.S_ISREG(os.lstat(release / name).st_mode):
            raise Fehler("release-verzeichnis", f"{name}: keine reguläre Datei")
    if not stat.S_ISDIR(os.lstat(release / ARTEFAKT_ORDNER).st_mode):
        raise Fehler("release-verzeichnis", f"{ARTEFAKT_ORDNER}: kein Verzeichnis")


def _verifiziere(roh: bytes, sig: bytes, allowed_signers: Path, privat: Path, *,
                 namespace: str = NAMESPACE, datei: str = MANIFEST_DATEI,
                 regel: str = "signatur") -> None:
    sig_kopie = privat / f"{datei}.sig"
    sig_kopie.write_bytes(sig)
    try:
        ergebnis = subprocess.run(
            [SSH_KEYGEN, "-Y", "verify", "-f", str(allowed_signers), "-I", KENNUNG,
             "-n", namespace, "-s", str(sig_kopie)],
            input=roh, capture_output=True, check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})  # nichts aus der root-Umgebung
    except OSError as e:
        raise Fehler(regel, f"ssh-keygen nicht ausführbar: {e.strerror}") from None
    if ergebnis.returncode != 0:
        text = ergebnis.stderr.decode("utf-8", "replace").strip()[-2000:]
        raise Fehler(regel, f"{datei}: Signatur ungültig "
                            f"(Exit {ergebnis.returncode}): {text or '(keine Ausgabe)'}")


def _pruefe_abschluss(roh: bytes, manifest_roh: bytes, daten: dict[str, Any]) -> None:
    """Abschlussnachweis gehört zu genau diesem Manifest (wie abschluss.parse)."""
    try:
        ab = json.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise Fehler("abschluss", f"kein gültiges JSON ({e})") from None
    if not isinstance(ab, dict) or _kanonisch(ab) != roh:
        raise Fehler("abschluss", "nicht in kanonischer Form")
    if sorted(ab) != sorted(ABSCHLUSS_FELDER) or ab["schema"] != ABSCHLUSS_SCHEMA:
        raise Fehler("abschluss", "Felder oder Schema unzulässig")
    soll = {"version": daten["version"],
            "manifest_sha256": hashlib.sha256(manifest_roh).hexdigest(),
            "quellcommit": daten["quellcommit"], "exportcommit": daten["exportcommit"],
            "tag": f"v{daten['version']}"}
    abweichend = [k for k, v in soll.items() if ab[k] != v]
    if abweichend:
        raise Fehler("abschluss", f"gehört nicht zu diesem Manifest: {', '.join(abweichend)}")
    if not _hex(ab["archiv_commit"], _COMMIT) or not isinstance(ab["erstellt"], str) \
            or not ab["erstellt"]:
        raise Fehler("abschluss", "Archiv-Commit oder Zeitpunkt ungültig")


def _parse(roh: bytes) -> dict[str, Any]:
    """Dieselben Regeln wie manifest._parse, dazu die Deploy-Regeln (kein Trockenlauf, feste
    Auslieferungsmenge und Dateinamen)."""
    try:
        daten = json.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise Fehler("manifest", f"kein gültiges JSON ({e})") from None
    if not isinstance(daten, dict) or _kanonisch(daten) != roh:
        raise Fehler("manifest", "nicht in kanonischer Form")
    fehlend = [f for f in FELDER if f not in daten]
    if fehlend:
        raise Fehler("manifest", f"Felder fehlen: {', '.join(fehlend)}")
    if daten["schema"] not in PFLICHT_JE_SCHEMA:
        raise Fehler("manifest", f"unbekanntes Schema „{daten['schema']}“ (bekannt: "
                                 f"{', '.join(sorted(PFLICHT_JE_SCHEMA))})")
    version = daten["version"]
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise Fehler("manifest", f"Version „{version}“ ist keine gültige Versionsnummer")
    if not isinstance(daten["quellcommit"], str) or not _COMMIT.fullmatch(daten["quellcommit"]):
        raise Fehler("manifest", "Quellcommit ist kein voller Commit-Hash")
    pruefungen = daten["pruefungen"]
    if not isinstance(pruefungen, dict) or not pruefungen or \
            any(w not in PRUEFUNG_WERTE for w in pruefungen.values()):
        raise Fehler("manifest", "Prüfergebnisse fehlen oder sind unzulässig")
    umgebung = daten["umgebung"]
    if not isinstance(umgebung, dict) or any(
            not isinstance(umgebung.get(k), str) or not umgebung[k].strip()
            for k in UMGEBUNG_PFLICHT):
        raise Fehler("manifest", "Umgebung unvollständig")
    if daten["trockenlauf"] is not False:
        raise Fehler("manifest", "Trockenlauf-Manifest (oder ungültiger Wert) — wird nie "
                                 "ausgeliefert")
    liste = daten["artefakte"]
    if not isinstance(liste, list):
        raise Fehler("manifest", "Artefaktliste fehlt")
    namen: list[str] = []
    for a in liste:
        if not (isinstance(a, dict)
                and isinstance(a.get("name"), str) and a["name"]
                and isinstance(a.get("datei"), str) and a["datei"]
                and isinstance(a.get("sha256"), str) and _SHA256.fullmatch(a["sha256"])
                and isinstance(a.get("sha512"), str) and _SHA512.fullmatch(a["sha512"])
                and isinstance(a.get("ausgeliefert"), bool)):
            raise Fehler("manifest", f"Artefakteintrag unvollständig: {a!r}"[:500])
        namen.append(a["name"])
    if len(set(namen)) != len(namen):
        raise Fehler("manifest", "Artefakt doppelt")
    ausgeliefert = {a["name"] for a in liste if a["ausgeliefert"]}
    soll = AUSGELIEFERT_JE_SCHEMA[daten["schema"]]
    if ausgeliefert != soll:
        raise Fehler("manifest", f"ausgeliefert: {', '.join(sorted(ausgeliefert)) or '(nichts)'}"
                                 f" — erwartet genau {', '.join(sorted(soll))}")
    for a in liste:
        if a["ausgeliefert"] and a["datei"] != f"scanview-{a['name']}-{version}.tar.gz":
            raise Fehler("manifest", f"{a['name']}: Dateiname „{a['datei']}“ unzulässig")
    _pruefe_inhalt(daten)
    return daten


def _hex(wert: object, muster: re.Pattern[str]) -> bool:
    return isinstance(wert, str) and muster.fullmatch(wert) is not None


def _dateiname(wert: object) -> bool:
    return isinstance(wert, str) and bool(wert) and "/" not in wert and wert not in (".", "..")


def _ist_netzschritt(s: object) -> bool:
    return (isinstance(s, dict) and set(s) == {"befehl", "cwd", "zeit"}
            and isinstance(s["befehl"], list) and bool(s["befehl"])
            and all(isinstance(x, str) for x in s["befehl"])
            and isinstance(s["cwd"], str) and isinstance(s["zeit"], str) and bool(s["zeit"]))


def _pruefe_inhalt(daten: dict[str, Any]) -> None:
    """Vollständig heißt mehr als vorhandene Feldnamen (wie manifest._pruefe_inhalt):
    Exportcommit, SBOMs der ausgelieferten Artefakte, Berichte, Werkzeuge, DB-Stand,
    Netzschritte, Veröffentlichung, alle Pflichtprüfungen bestanden — gegen die Pflichtlisten des
    Schemas im Manifest."""
    pflicht_berichte, pflicht_werkzeuge, pflicht_pruefungen = PFLICHT_JE_SCHEMA[daten["schema"]]
    if not _hex(daten.get("exportcommit"), _COMMIT):
        raise Fehler("manifest", "Exportcommit fehlt oder ist kein voller Commit-Hash")
    sboms = daten.get("sboms")
    if not isinstance(sboms, dict) or not all(
            _dateiname(n) and _hex(h, _SHA256) for n, h in sboms.items()):
        raise Fehler("manifest", "SBOM-Liste fehlt oder ist ungültig")
    fehlend = sorted(n for n in AUSGELIEFERT_JE_SCHEMA[daten["schema"]]
                     if f"scanview-{n}-{daten.get('version')}.cdx.json" not in sboms)
    if fehlend:
        raise Fehler("manifest", f"SBOM fehlt für ausgelieferte Artefakte: {', '.join(fehlend)}")
    berichte = daten.get("berichte")
    if not isinstance(berichte, dict) or not berichte or not all(
            isinstance(n, str) and n and isinstance(b, dict) and set(b) == {"datei", "sha256"}
            and _dateiname(b["datei"]) and _hex(b["sha256"], _SHA256)
            for n, b in berichte.items()):
        raise Fehler("manifest", "Berichte fehlen oder sind ungültig")
    if set(berichte) != set(pflicht_berichte):
        raise Fehler("manifest", "Berichte nicht vollständig (Pflichtliste)")
    werkzeuge = daten.get("werkzeuge")
    if not isinstance(werkzeuge, dict) or not werkzeuge or not all(
            isinstance(w, dict) and set(w) == {"image", "version"}
            and all(isinstance(w[k], str) and w[k] for k in w) for w in werkzeuge.values()):
        raise Fehler("manifest", "Werkzeuge fehlen oder sind ungültig")
    if set(werkzeuge) != set(pflicht_werkzeuge):
        raise Fehler("manifest", "Werkzeuge nicht vollständig (Pflichtliste)")
    db = daten.get("db_stand")
    if not (isinstance(db, dict) and set(db) == {"zeit", "feld", "sha256", "netzschritt"}
            and isinstance(db["zeit"], str) and db["zeit"] and isinstance(db["feld"], str)
            and db["feld"] and _hex(db["sha256"], _SHA256)
            and (db["netzschritt"] is None or _ist_netzschritt(db["netzschritt"]))):
        raise Fehler("manifest", "DB-Stand fehlt oder ist ungültig")
    netz = daten.get("netzschritte")
    if not isinstance(netz, list) or not all(_ist_netzschritt(s) for s in netz):
        raise Fehler("manifest", "Netzschritte ungültig")
    v = daten.get("veroeffentlichung")
    if not (isinstance(v, dict) and set(v) == {"veroeffentlichbar", "grund"}
            and isinstance(v["veroeffentlichbar"], bool)
            and (v["grund"] is None if v["veroeffentlichbar"]
                 else isinstance(v["grund"], str) and bool(v["grund"].strip()))):
        raise Fehler("manifest", "Feld veroeffentlichung fehlt oder ist ungültig")
    pruefungen = daten["pruefungen"]
    offen = [p for p in pflicht_pruefungen if pruefungen.get(p) != "bestanden"]
    if offen:
        raise Fehler("manifest", f"Pflichtprüfungen nicht bestanden oder fehlend: "
                                 f"{', '.join(offen)}")


def _ausgelieferte(daten: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["name"]: a for a in daten["artefakte"] if a["ausgeliefert"]}


def _pruefe_artefaktordner(ordner: Path, artefakte: dict[str, dict[str, Any]]) -> None:
    ist = sorted(os.listdir(ordner))
    soll = sorted(a["datei"] for a in artefakte.values())
    if ist != soll:
        raise Fehler("release-verzeichnis", f"{ARTEFAKT_ORDNER}/ enthält "
                                            f"{', '.join(ist) or '(nichts)'} — erwartet genau "
                                            f"{', '.join(soll)}")
    for name in ist:
        if not stat.S_ISREG(os.lstat(ordner / name).st_mode):
            raise Fehler("release-verzeichnis", f"{ARTEFAKT_ORDNER}/{name}: keine reguläre "
                                                "Datei (Symlink?)")


def _kopiere_mit_hash(quelle: Path, ziel: Path) -> tuple[str, str]:
    """Streamend kopieren und dabei hashen; die Kopie ist danach die einzige Lesequelle."""
    h256, h512 = hashlib.sha256(), hashlib.sha512()
    fd = _oeffne_regulaer(quelle, "release-verzeichnis")
    with os.fdopen(fd, "rb") as ein, open(ziel, "xb") as aus:
        while block := ein.read(_BLOCK):
            h256.update(block)
            h512.update(block)
            aus.write(block)
    return h256.hexdigest(), h512.hexdigest()


def _erlaubter_ordner(name: str, wurzeln: tuple[str, ...]) -> bool:
    """Ordner: eine Wurzel selbst, ein Vorfahr einer Wurzel oder darunter."""
    mit = name + "/"
    return any(w.startswith(mit) or mit.startswith(w) for w in wurzeln)


def _pruefe_mitglied(m: tarfile.TarInfo, wurzeln: tuple[str, ...], artefakt: str) -> None:
    wo = f"{artefakt}: „{m.name}“"
    teile = m.name.split("/")
    if not m.name or m.name.startswith("/") or any(t in ("", ".", "..") for t in teile):
        raise Fehler("tar-inhalt", f"{wo}: Pfad nicht erlaubt (absolut, leer, . oder ..)")
    if m.type == tarfile.DIRTYPE:
        if not _erlaubter_ordner(m.name, wurzeln):
            raise Fehler("tar-inhalt", f"{wo}: Ordner außerhalb von {', '.join(wurzeln)}")
    elif m.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
        if not m.name.startswith(wurzeln):
            raise Fehler("tar-inhalt", f"{wo}: Datei außerhalb von {', '.join(wurzeln)}")
    else:
        raise Fehler("tar-inhalt", f"{wo}: Typ {m.type!r} nicht erlaubt (nur Datei/Ordner; "
                                   "kein Symlink, Hardlink, Gerät, FIFO)")
    if stat.S_IMODE(m.mode) not in _MODI:
        raise Fehler("tar-inhalt", f"{wo}: Modus {stat.S_IMODE(m.mode):o} nicht erlaubt "
                                   "(nur 644/755)")


def _pruefe_tar(pfad: Path, artefakt: str, baum: Path | None) -> None:
    """Jedes Mitglied prüfen; mit `baum` zugleich entpacken (O_EXCL, ohne Symlinks zu folgen)."""
    wurzeln = WURZELN[artefakt]
    gesehen: set[str] = set()
    try:
        with tarfile.open(pfad, "r:gz") as tar:
            for m in tar:
                _pruefe_mitglied(m, wurzeln, artefakt)
                if m.name in gesehen:
                    raise Fehler("tar-inhalt", f"{artefakt}: „{m.name}“ doppelt")
                gesehen.add(m.name)
                if baum is not None:
                    _schreibe(tar, m, baum)
    except (tarfile.TarError, EOFError, OSError) as e:
        raise Fehler("tar-inhalt", f"{artefakt}: Tarball nicht lesbar ({e})") from None
    if not any(n.startswith(wurzeln) for n in gesehen):
        raise Fehler("tar-inhalt", f"{artefakt}: keine Datei unter {', '.join(wurzeln)}")


def _ordner_anlegen(baum: Path, relativ: str) -> Path:
    ziel = baum
    for teil in relativ.split("/"):
        ziel = ziel / teil
        try:
            os.mkdir(ziel, 0o755)
        except FileExistsError:
            if not stat.S_ISDIR(os.lstat(ziel).st_mode):
                raise Fehler("tar-inhalt", f"{relativ}: {ziel} ist kein Ordner") from None
        os.chmod(ziel, 0o755)  # mkdir unterliegt der umask
    return ziel


def _schreibe(tar: tarfile.TarFile, m: tarfile.TarInfo, baum: Path) -> None:
    if m.type == tarfile.DIRTYPE:
        _ordner_anlegen(baum, m.name)
        return
    eltern, _, name = m.name.rpartition("/")
    ordner = _ordner_anlegen(baum, eltern) if eltern else baum
    quelle: IO[bytes] | None = tar.extractfile(m)
    if quelle is None:
        raise Fehler("tar-inhalt", f"„{m.name}“: nicht lesbar")
    fd = os.open(ordner / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as aus:
        shutil.copyfileobj(quelle, aus, _BLOCK)
        os.fchmod(aus.fileno(), stat.S_IMODE(m.mode))


def pruefe(release: Path, allowed_signers: Path, privat: Path,
           baum: Path | None = None, bekannt: dict[str, str] | None = None) -> dict[str, str]:
    """Vollständige Prüfung; `privat` ist ein leerer, nur für den Aufrufer lesbarer Ordner für
    die Kopien. Mit `baum` werden web, server und (ab /3) import dorthin entpackt. Rückgabe:
    Protokollfelder.
    `bekannt` sammelt unterwegs, was für die Protokollzeile eines **abgelehnten** Releases
    feststeht: der Hash der vorgelegten Manifest-Bytes, Version und Quellcommit erst nach
    gültiger Signatur (ungeprüfter Inhalt wird nicht als Version protokolliert)."""
    bekannt = {} if bekannt is None else bekannt
    _pruefe_vertrauensdatei(allowed_signers, release)
    _pruefe_verzeichnis(release)
    roh = _lies_datei(release / MANIFEST_DATEI, "release-verzeichnis")
    bekannt["manifest_sha256"] = hashlib.sha256(roh).hexdigest()
    sig = _lies_datei(release / SIGNATUR_DATEI, "release-verzeichnis")
    _verifiziere(roh, sig, allowed_signers, privat)
    daten = _parse(roh)
    bekannt["version"] = daten["version"]
    bekannt["quellcommit"] = daten["quellcommit"]
    ab_roh = _lies_datei(release / ABSCHLUSS_DATEI, "release-verzeichnis")
    ab_sig = _lies_datei(release / ABSCHLUSS_SIGNATUR, "release-verzeichnis")
    _verifiziere(ab_roh, ab_sig, allowed_signers, privat, namespace=ABSCHLUSS_NAMESPACE,
                 datei=ABSCHLUSS_DATEI, regel="abschluss")
    _pruefe_abschluss(ab_roh, roh, daten)
    artefakte = _ausgelieferte(daten)
    _pruefe_artefaktordner(release / ARTEFAKT_ORDNER, artefakte)

    reihenfolge = [n for n in REIHENFOLGE if n in artefakte]
    kopien: dict[str, Path] = {}
    for name in reihenfolge:
        a = artefakte[name]
        kopie = privat / a["datei"]
        ist256, ist512 = _kopiere_mit_hash(release / ARTEFAKT_ORDNER / a["datei"], kopie)
        if ist256 != a["sha256"]:
            raise Fehler("artefakt-hash", f"{name}: SHA-256 {ist256}, Manifest {a['sha256']}")
        if ist512 != a["sha512"]:
            raise Fehler("artefakt-hash", f"{name}: SHA-512 weicht vom Manifest ab")
        kopien[name] = kopie
    # Erst alle Tarballs vollständig prüfen, dann entpacken: ein unsicherer Eintrag in einem
    # späteren Artefakt soll keinen halben Baum hinterlassen.
    for name in reihenfolge:
        _pruefe_tar(kopien[name], name, None)
    if baum is not None:
        os.mkdir(baum, 0o755)
        for name in ENTPACKEN:
            if name in kopien:
                _pruefe_tar(kopien[name], name, baum)
    return {
        "version": daten["version"],
        "manifest_sha256": hashlib.sha256(roh).hexdigest(),
        "quellcommit": daten["quellcommit"],
        "artefakte": ",".join(f"{n}:{artefakte[n]['sha256']}" for n in reihenfolge),
    }


def _ausgabe(felder: dict[str, str]) -> None:
    for schluessel, wert in felder.items():
        print(f"{schluessel}={wert}")


def main(argv: list[str]) -> int:
    bekannt: dict[str, str] = {}
    try:
        if len(argv) == 3 and argv[0] == "pruefen":
            release, signers = Path(argv[1]), Path(argv[2])
            with tempfile.TemporaryDirectory(prefix="scanview-release-") as tmp:
                felder = pruefe(release, signers, Path(tmp), bekannt=bekannt)
            _ausgabe(felder)
            print(f"Release {felder['version']} geprüft: Signatur gültig, "
                  f"{felder['artefakte'].count(':')} Artefakte übereinstimmend, Inhalt zulässig.",
                  file=sys.stderr)
            return 0
        if len(argv) == 4 and argv[0] == "entpacken":
            release, signers, ziel = Path(argv[1]), Path(argv[2]), Path(argv[3])
            try:
                os.mkdir(ziel, 0o700)
            except OSError as e:
                raise Fehler("release-ziel", f"{ziel}: nicht neu anlegbar ({e.strerror}) — "
                                             "muss ein neuer, leerer Pfad sein") from None
            os.chmod(ziel, 0o700)
            kopien = ziel / "kopien"
            os.mkdir(kopien, 0o700)
            try:
                felder = pruefe(release, signers, kopien, ziel / "baum", bekannt)
            finally:
                shutil.rmtree(kopien)
            _ausgabe({**felder, "baum": str(ziel / "baum")})
            return 0
    except Fehler as f:
        print(f"NACHWEIS-FEHLER {f.regel}: {f.text}", file=sys.stderr)
        # stdout: Felder für die Protokollzeile `ergebnis=abgelehnt grund=<regel>`.
        _ausgabe({"grund": f.regel, **bekannt})
        return 2
    print("Aufruf: release_pruefen.py pruefen <release-verzeichnis> <allowed_signers>\n"
          "        release_pruefen.py entpacken <release-verzeichnis> <allowed_signers> <ziel>",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
