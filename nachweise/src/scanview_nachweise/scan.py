"""Schwachstellen-Scan der SBOMs mit trivy.

Zwei getrennte Schritte:

1. `db_aktualisieren` — der **einzige Netzschritt** dieser Aufgabe: lädt die trivy-DB in
   einen Cache außerhalb des Repos (Standard `~/.cache/scanview-nachweise/trivy`) und schreibt
   `db-stand.json` (Zeit laut trivy-`metadata.json`, SHA-256 über `trivy.db`, Netzschritt fürs
   Manifest).
2. `scanne` — trivy im SBOM-Modus **ohne Netz** (`--network none`, `--offline-scan`,
   `--skip-db-update`, `--skip-java-db-update`, `--skip-vex-repo-update`), Cache und Eingaben nur
   lesend gemountet. `--show-suppressed` ist Pflicht: trivy wendet die VEX-Datei selbst an und
   würde Treffer, deren Aussage nach unseren Regeln (Lockfile-Hash, 90 Tage, Begründung) ungültig
   ist, sonst still verschwinden lassen. Freigeben darf nur `vex.scan_gate`.

Fail-closed: DB älter als 24 h (bei verspäteter Quelle: bis 72 h, wenn vor höchstens 24 h
neu geladen), fehlend oder außerhalb des protokollierten Schritts verändert,
unbekannte Schwere, unlesbare trivy-Ausgabe → `NachweisFehler`. `UNKNOWN` zählt als hoch."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import urllib.parse
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .artefakte import Netzschritt
from .fehler import NachweisFehler
from .werkzeuge import Werkzeug, ausfuehren, docker_befehl

if TYPE_CHECKING:
    from .osv import OsvStand

#: Cache der trivy-DB — bewusst außerhalb des Repos.
STANDARD_CACHE = Path.home() / ".cache" / "scanview-nachweise" / "trivy"
DB_HOECHSTALTER = timedelta(hours=24)
#: Veröffentlicht die Quelle verspätet, gilt ein älterer Inhalt bis zu dieser Grenze — aber nur,
#: wenn er höchstens `DB_HOECHSTALTER` vor der Prüfung geladen wurde (dann ist er nachweislich
#: der neueste veröffentlichte Stand).
DB_VERZOEGERT_HOECHSTALTER = timedelta(hours=72)
DB_STAND = "db-stand.json"

#: trivy-Schwere → Schwere des Befundregisters. UNKNOWN zählt als hoch.
SCHWERE = {"CRITICAL": "kritisch", "HIGH": "hoch", "MEDIUM": "mittel", "LOW": "niedrig",
           "UNKNOWN": "hoch"}

#: bom-ref-Präfixe des Image-Inhalts im Nachweis-SBOM der Werkzeug-Images (`sbom.erzeuge_bilder`):
#: die beiden Container-Images selbst (`image:…`) und ihr Paketinhalt (`pdal|…`, `untwine|…`).
IMAGE_PRAEFIXE = ("pdal|", "untwine|", "image:")
#: Name dieses SBOMs (wie `sbom.BILDER`; drift-getestet). Nur hier gilt die Image-Ausnahme der
#: Abdeckung — im ausgelieferten Import-SBOM muss jede Komponente gescannt sein.
BILDER = "importbilder"

_CACHE_IM_CONTAINER = "/cache"
_SBOM_IM_CONTAINER = "/eingabe/sbom.cdx.json"
_VEX_IM_CONTAINER = "/eingabe/vex.openvex.json"
# Nie ins Netz: trivy prüft sonst Versionen, Telemetrie, Java-DB und VEX-Repositorys online.
_OHNE_NETZ = ("--offline-scan", "--skip-db-update", "--skip-java-db-update",
              "--skip-vex-repo-update", "--skip-version-check", "--disable-telemetry")


def jetzt() -> datetime:
    """Aktuelle UTC-Zeit (in Tests ersetzbar). Die DB-Frische misst gegen die echte Uhr, nie
    gegen `--heute`."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class DbStand:
    """Stand der trivy-DB: `zeit` aus `metadata.json` (`feld` sagt, ob `UpdatedAt` oder ersatzweise
    `DownloadedAt`), SHA-256 über `trivy.db`, und der Netzschritt der protokollierten
    Aktualisierung (fürs Manifest)."""

    zeit: datetime
    sha256: str
    feld: str
    netzschritt: Netzschritt | None = None
    #: Inhalt älter als 24 h, aber frisch geladen (Quelle verspätet).
    verzoegert: bool = False


@dataclass(frozen=True)
class Treffer:
    """Ein Schwachstellen-Treffer. `paket` ist die PURL (ohne PURL: `name@version`),
    `status_vex` der Status, mit dem trivy den Treffer per VEX unterdrückt hat (nur Auskunft —
    freigeben entscheidet das Gate), `artefakt` das Release-Artefakt des SBOMs, `bom_ref` die
    Komponente im SBOM (Image-Inhalt beginnt mit `pdal|`/`untwine|`)."""

    id: str
    paket: str
    version: str
    schwere: str
    status_vex: str | None
    artefakt: str | None = None
    bom_ref: str | None = None
    #: Weitere Kennungen derselben Schwachstelle (OSV-Aliase); VEX gleicht ID und Aliase ab.
    aliase: tuple[str, ...] = ()


def purl_schluessel(purl: str) -> str:
    """Vergleichsform einer PURL: prozentdekodiert (syft schreibt die Debian-Epoche als „1%3A…“,
    trivy als „1:…“ — gemeint ist dieselbe Komponente)."""
    return urllib.parse.unquote(purl)


# --- trivy-DB -------------------------------------------------------------------------------------

def _zeit(wert: object) -> datetime | None:
    if not isinstance(wert, str):
        return None
    try:
        # trivy schreibt 9 Nachkommastellen; Python ≥ 3.11 kürzt auf Mikrosekunden.
        zeit = datetime.fromisoformat(wert)
    except ValueError:
        return None
    return zeit.astimezone(UTC) if zeit.tzinfo is not None else None


def _sha256_datei(pfad: Path) -> str:
    h = hashlib.sha256()
    with pfad.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _db_lesen(cache: Path) -> tuple[datetime, str, str]:
    """(Zeit, Feld, SHA-256) aus `<cache>/db/`; alles Fehlende → `db-alter`."""
    db = cache / "db"
    try:
        metadaten = json.loads((db / "metadata.json").read_text(encoding="utf-8"))
    except OSError:
        raise NachweisFehler("db-alter", f"{db / 'metadata.json'} fehlt — erst "
                                         "`scanview-nachweise db-aktualisieren`") from None
    except json.JSONDecodeError:
        raise NachweisFehler("db-alter", f"{db / 'metadata.json'} ist kein JSON") from None
    if not isinstance(metadaten, dict):
        raise NachweisFehler("db-alter", f"{db / 'metadata.json'}: kein Objekt")
    for feld in ("UpdatedAt", "DownloadedAt"):
        if feld in metadaten:
            zeit = _zeit(metadaten[feld])
            if zeit is None:
                raise NachweisFehler("db-alter", f"metadata.json: {feld} „{metadaten[feld]}“ "
                                                 "ist keine Zeit mit Zeitzone")
            break
    else:
        raise NachweisFehler("db-alter", "metadata.json ohne UpdatedAt/DownloadedAt")
    if not (db / "trivy.db").is_file():
        raise NachweisFehler("db-alter", f"{db / 'trivy.db'} fehlt")
    return zeit, feld, _sha256_datei(db / "trivy.db")


def _netzschritt_aus(roh: object) -> Netzschritt | None:
    if not isinstance(roh, dict):
        return None
    befehl, cwd, zeit = roh.get("befehl"), roh.get("cwd"), roh.get("zeit")
    if isinstance(befehl, list) and all(isinstance(b, str) for b in befehl) \
            and isinstance(cwd, str) and isinstance(zeit, str):
        return Netzschritt(befehl, cwd, zeit)
    return None


def db_pruefen(cache: Path, jetzt: datetime) -> DbStand:
    """DB-Stand prüfen: Inhalt höchstens 24 h alt (und nicht aus der Zukunft). Älter bis 72 h nur,
    wenn der protokollierte Ladeschritt höchstens 24 h zurückliegt — die Quelle hat dann
    nachweislich nichts Neueres veröffentlicht (`verzoegert`). Sonst oder bei fehlender DB
    `NachweisFehler("db-alter")`. Der SHA-256 muss zu `db-stand.json` der letzten
    protokollierten Aktualisierung passen — sonst wurde die DB außerhalb des Netzschritts
    verändert (`db-stand`)."""
    zeit, feld, sha = _db_lesen(cache)
    alter = jetzt - zeit
    if alter < timedelta(0):
        raise NachweisFehler("db-alter", f"trivy-DB-Zeit {zeit.isoformat()} liegt in der "
                                         "Zukunft")
    try:
        stand = json.loads((cache / DB_STAND).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise NachweisFehler("db-stand", f"{cache / DB_STAND} fehlt oder ist kein JSON — DB "
                                         "nicht über `db-aktualisieren` geladen") from None
    if not isinstance(stand, dict) or stand.get("sha256") != sha:
        raise NachweisFehler("db-stand", "trivy.db weicht vom protokollierten Stand "
                                         f"({DB_STAND}) ab — erneut `db-aktualisieren`")
    if stand.get("zeit") != zeit.isoformat() or stand.get("feld") != feld:
        # Gleicher Hash, aber metadata.json verändert (z. B. UpdatedAt vorgedreht).
        raise NachweisFehler("db-stand", f"metadata.json ({feld} {zeit.isoformat()}) weicht vom "
                                         f"protokollierten Stand ({stand.get('feld')} "
                                         f"{stand.get('zeit')}) ab — erneut `db-aktualisieren`")
    netzschritt = _netzschritt_aus(stand.get("netzschritt"))
    verzoegert = alter > DB_HOECHSTALTER
    if verzoegert:
        _verzoegert_pruefen(alter, feld, zeit, netzschritt, jetzt)
    return DbStand(zeit, sha, feld, netzschritt, verzoegert)


def ladezeit(stand: DbStand) -> datetime | None:
    """Zeitpunkt des protokollierten Ladeschritts (None ohne Netzschritt)."""
    return _zeit(stand.netzschritt.zeit) if stand.netzschritt else None


def _verzoegert_pruefen(alter: timedelta, feld: str, zeit: datetime,
                        netzschritt: Netzschritt | None, jetzt: datetime) -> None:
    stunden = f"{alter.total_seconds() / 3600:.1f} h alt ({feld} {zeit.isoformat()})"
    if alter > DB_VERZOEGERT_HOECHSTALTER:
        raise NachweisFehler("db-alter", f"trivy-DB ist {stunden}, erlaubt sind 24 h, bei "
                                         "verspäteter Quelle höchstens 72 h")
    geladen = _zeit(netzschritt.zeit) if netzschritt else None
    if geladen is None or not timedelta(0) <= jetzt - geladen <= DB_HOECHSTALTER:
        raise NachweisFehler("db-alter", f"trivy-DB ist {stunden} und seit über 24 h nicht neu "
                                         "geladen — erst `scanview-nachweise db-aktualisieren`")


def _trivy(werkzeuge: dict[str, Werkzeug]) -> Werkzeug:
    w = werkzeuge.get("trivy")
    if w is None:
        raise NachweisFehler("werkzeuge", "trivy fehlt in werkzeuge.json")
    return w


def db_aktualisieren(cache: Path, werkzeuge: dict[str, Werkzeug]) -> DbStand:
    """trivy-DB laden (Netz — einziger Netzschritt dieser Aufgabe) und `db-stand.json`
    schreiben. Die übrige Container-Härtung bleibt bestehen; der Cache ist das einzige
    Schreibziel."""
    w = _trivy(werkzeuge)
    cache.mkdir(parents=True, exist_ok=True)
    args = ["image", "--download-db-only", "--cache-dir", _CACHE_IM_CONTAINER, "--no-progress",
            "--skip-version-check", "--disable-telemetry"]
    schritt = Netzschritt(["trivy", *args], "trivy-cache",
                          datetime.now(UTC).isoformat(timespec="seconds"))
    ausfuehren(docker_befehl(w, args, {}, {cache: _CACHE_IM_CONTAINER}, netz=True),
               "db-aktualisieren")
    zeit, feld, sha = _db_lesen(cache)
    stand = DbStand(zeit, sha, feld, schritt)
    (cache / DB_STAND).write_text(json.dumps({
        "zeit": zeit.isoformat(), "feld": feld, "sha256": sha,
        "netzschritt": {"befehl": schritt.befehl, "cwd": schritt.cwd, "zeit": schritt.zeit},
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return stand


# --- Scan -----------------------------------------------------------------------------------------

def trivy_scan_befehl(sbom: Path, cache: Path, vex: Path, w: Werkzeug) -> list[str]:
    """Gehärteter trivy-Aufruf im SBOM-Modus: kein Netz, Cache/SBOM/VEX nur lesend."""
    args = ["sbom", "--cache-dir", _CACHE_IM_CONTAINER, *_OHNE_NETZ, "--no-progress",
            "--format", "json", "--vex", _VEX_IM_CONTAINER, "--show-suppressed",
            _SBOM_IM_CONTAINER]
    return docker_befehl(w, args, {cache: _CACHE_IM_CONTAINER, sbom: _SBOM_IM_CONTAINER,
                                   vex: _VEX_IM_CONTAINER})


def flach_fuer_scan(bom: dict[str, Any]) -> dict[str, Any]:
    """Kopie des SBOMs mit flacher Komponentenliste (bom-refs unverändert). trivy 0.74.0 prüft
    verschachtelte Komponenten nicht (Probe 28.09.2026: die von `ol` mitgebrachten
    earcut/geotiff/pbf im Web-SBOM blieben ungescannt) — das geprüfte SBOM selbst bleibt
    verschachtelt, nur trivy bekommt die flache Kopie."""
    flach = copy.deepcopy(bom)
    liste: list[Any] = []

    def sammeln(komponenten: object) -> None:
        for k in komponenten if isinstance(komponenten, list) else []:
            if isinstance(k, dict):
                innen = k.pop("components", None)
                liste.append(k)
                sammeln(innen)

    if "components" in flach:
        sammeln(flach["components"])
        flach["components"] = liste
    return flach


def trivy_json(sbom: Path, cache: Path, vex: Path, werkzeuge: dict[str, Werkzeug]
               ) -> dict[str, Any]:
    """Rohe trivy-JSON-Ausgabe eines Scans (für Auswertung und Abdeckung). trivy bekommt eine
    flache Kopie des SBOMs (`flach_fuer_scan`) in einem temporären Ordner."""
    w = _trivy(werkzeuge)
    try:
        bom = json.loads(sbom.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise NachweisFehler("scan-sbom", f"{sbom}: nicht lesbar ({e})") from None
    if not isinstance(bom, dict):
        raise NachweisFehler("scan-sbom", f"{sbom}: kein JSON-Objekt")
    with tempfile.TemporaryDirectory(prefix="scanview-scan-") as tmp:
        kopie = Path(tmp) / "sbom.cdx.json"
        kopie.write_text(json.dumps(flach_fuer_scan(bom)), encoding="utf-8")
        ausgabe = ausfuehren(trivy_scan_befehl(kopie, cache, vex, w), "scan-trivy").stdout
    try:
        daten = json.loads(ausgabe)
    except json.JSONDecodeError:
        raise NachweisFehler("scan-ausgabe", f"{sbom.name}: trivy lieferte kein JSON") from None
    if not isinstance(daten, dict):
        raise NachweisFehler("scan-ausgabe", f"{sbom.name}: trivy-Ausgabe ist kein Objekt")
    return daten


def _liste(roh: object, wo: str) -> list[Any]:
    if roh is None:
        return []
    if not isinstance(roh, list):
        raise NachweisFehler("scan-ausgabe", f"{wo}: Liste erwartet")
    return roh


def _ergebnisse(daten: dict[str, Any]) -> list[dict[str, Any]]:
    if "Results" not in daten and "SchemaVersion" not in daten:
        raise NachweisFehler("scan-ausgabe", "trivy-Ausgabe ohne SchemaVersion/Results")
    ergebnisse = _liste(daten.get("Results"), "Results")
    if not all(isinstance(r, dict) for r in ergebnisse):
        raise NachweisFehler("scan-ausgabe", "Results: Objekte erwartet")
    return ergebnisse


def _treffer(v: object, status_vex: str | None, artefakt: str | None) -> Treffer:
    if not isinstance(v, dict):
        raise NachweisFehler("scan-ausgabe", "Schwachstelle ist kein Objekt")
    kennung, name, version = v.get("VulnerabilityID"), v.get("PkgName"), \
        v.get("InstalledVersion")
    if not (isinstance(kennung, str) and kennung and isinstance(name, str)
            and isinstance(version, str)):
        raise NachweisFehler("scan-ausgabe", f"Schwachstelle ohne VulnerabilityID/PkgName/"
                                             f"InstalledVersion: {str(v)[:200]}")
    schwere = v.get("Severity")
    if schwere not in SCHWERE:
        raise NachweisFehler("scan-schwere", f"{kennung}: unbekannte Schwere „{schwere}“")
    roh_ident = v.get("PkgIdentifier")
    ident: dict[str, Any] = roh_ident if isinstance(roh_ident, dict) else {}
    purl, bom_ref = ident.get("PURL"), ident.get("BOMRef")
    return Treffer(kennung, purl if isinstance(purl, str) and purl else f"{name}@{version}",
                   version, SCHWERE[schwere], status_vex, artefakt=artefakt,
                   bom_ref=bom_ref if isinstance(bom_ref, str) else None)


def werte_trivy_aus(daten: dict[str, Any], artefakt: str | None) -> list[Treffer]:
    """Treffer aus der trivy-JSON: `Vulnerabilities` (ohne VEX-Status — `Status` dort ist die
    Upstream-Fix-Verfügbarkeit, keine VEX-Aussage) plus die per VEX unterdrückten aus
    `ExperimentalModifiedFindings` (mit dem VEX-Status, den trivy angewandt hat)."""
    treffer: list[Treffer] = []
    for r in _ergebnisse(daten):
        for v in _liste(r.get("Vulnerabilities"), "Vulnerabilities"):
            treffer.append(_treffer(v, None, artefakt))
        for m in _liste(r.get("ExperimentalModifiedFindings"), "ExperimentalModifiedFindings"):
            if not isinstance(m, dict):
                raise NachweisFehler("scan-ausgabe", "ModifiedFinding ist kein Objekt")
            if m.get("Type", "vulnerability") != "vulnerability":
                continue      # z. B. unterdrückte Lizenzbefunde — nicht Gegenstand des Gates
            status = m.get("Status")
            if not isinstance(status, str) or not isinstance(m.get("Finding"), dict):
                raise NachweisFehler("scan-ausgabe", "unterdrückter Treffer ohne Status/Finding")
            treffer.append(_treffer(m["Finding"], status, artefakt))
    return treffer


def scanne(sbom: Path, cache: Path, vex: Path, werkzeuge: dict[str, Werkzeug], *,
           artefakt: str | None = None) -> list[Treffer]:
    """SBOM mit trivy scannen (ohne Netz) und auswerten."""
    return werte_trivy_aus(trivy_json(sbom, cache, vex, werkzeuge), artefakt)


def je_komponente(treffer: list[Treffer], bom: dict[str, Any]) -> list[Treffer]:
    """Treffer je SBOM-Komponente mit derselben PURL vervielfachen (bom-refs sortiert). trivy
    fasst gleiche PURLs zusammen und nennt nur eine, nicht stabile bom-ref — steckt ein Paket in
    beiden Images des Imports, gehört der Treffer zu beiden. Treffer ohne PURL-Treffer im SBOM
    bleiben unverändert."""
    refs: dict[str, list[str]] = {}
    for k in _komponenten(bom.get("components")):
        if isinstance(k.get("purl"), str) and isinstance(k.get("bom-ref"), str):
            refs.setdefault(purl_schluessel(k["purl"]), []).append(k["bom-ref"])
    ergebnis: list[Treffer] = []
    for t in treffer:
        passend = sorted(set(refs.get(purl_schluessel(t.paket), [])))
        if not passend:
            ergebnis.append(t)
        ergebnis += [replace(t, bom_ref=r) for r in passend]
    return ergebnis


# --- Abdeckung ------------------------------------------------------------------------------------

def _komponenten(liste: object) -> Iterator[dict[str, Any]]:
    for k in liste if isinstance(liste, list) else []:
        if isinstance(k, dict):
            yield k
            yield from _komponenten(k.get("components"))


def abdeckung(bom: dict[str, Any], daten: dict[str, Any], artefakt: str,
              ersatz: dict[str, str] | None = None) -> dict[str, Any]:
    """Welche SBOM-Komponenten hat trivy überhaupt geprüft? Eine Komponente gilt als gescannt,
    wenn ihre `bom-ref` oder ihre PURL in `Results[].Packages` auftaucht (trivy fasst gleiche
    PURLs verschiedener Images zusammen). Dateikomponenten zählen nicht mit. „0 Treffer“ heißt
    nur für gescannte Komponenten „nichts bekannt“ — der Rest steht in `ungescannt` je PURL-Typ
    bzw. „ohne PURL“; `ungescannt_pflicht` nennt die bom-refs, die gescannt sein müssen (die
    CLI bricht dann ab): im Nachweis-SBOM der Werkzeug-Images (`BILDER`) alles außer dem
    Image-Inhalt, in jedem Artefakt-SBOM (web, server, mcp, import) jede ungescannte Komponente —
    dort gibt es keinen Image-Inhalt, ein `pdal|`-Präfix ist dort keine Ausnahme. `ersatz`
    (bom-ref → PURL) deckt eine Ersatzprüfung ab (Eigenbauten über
    OSV, `osv.ersatz_fuer`): Eine Komponente mit genau dieser bom-ref **und** PURL bleibt in
    `ungescannt` gezählt und steht in `ersatz`, fällt aber aus `ungescannt_pflicht`.
    Komponenten `type: machine-learning-model` (unverändert übernommene Modelle, per SHA-256 im
    SBOM festgelegt) führt keine Schwachstellen-Datenbank; sie bleiben in `ungescannt` gezählt und
    stehen offen in `modelle`, fallen aber aus `ungescannt_pflicht`. Der Parser, der sie lädt, ist
    eine gewöhnliche (gescannte) Paketkomponente."""
    refs: set[str] = set()
    purls: set[str] = set()
    nach_typ: dict[str, int] = {}
    for r in _ergebnisse(daten):
        pakete = _liste(r.get("Packages"), "Packages")
        typ = str(r.get("Type", "?"))
        nach_typ[typ] = nach_typ.get(typ, 0) + len(pakete)
        for p in pakete:
            ident = p.get("Identifier") if isinstance(p, dict) else None
            if isinstance(ident, dict):
                if isinstance(ident.get("BOMRef"), str):
                    refs.add(ident["BOMRef"])
                if isinstance(ident.get("PURL"), str):
                    purls.add(purl_schluessel(ident["PURL"]))
    komponenten = [k for k in _komponenten(bom.get("components")) if k.get("type") != "file"]
    ungescannt: Counter[str] = Counter()
    eigene: list[str] = []
    ersatz_refs = dict(ersatz or {})
    ersetzt: list[str] = []
    modelle: list[str] = []
    for k in komponenten:
        purl = k.get("purl")
        if k.get("bom-ref") in refs or (isinstance(purl, str) and purl_schluessel(purl) in purls):
            continue
        ungescannt[purl.split("/", 1)[0] if isinstance(purl, str) and purl else "ohne PURL"] += 1
        ref = str(k.get("bom-ref", k.get("name", "?")))
        if ref in ersatz_refs and purl == ersatz_refs[ref]:
            ersetzt.append(ref)
        elif k.get("type") == "machine-learning-model":
            modelle.append(ref)
        elif artefakt != BILDER or not ref.startswith(IMAGE_PRAEFIXE):
            eigene.append(ref)
    return {"komponenten": len(komponenten),
            "gescannt": len(komponenten) - sum(ungescannt.values()),
            "ungescannt": dict(sorted(ungescannt.items())),
            "ungescannt_pflicht": eigene,
            "ersatz": sorted(ersetzt),
            "modelle": sorted(modelle),
            "nach_typ": dict(sorted(nach_typ.items()))}


# --- Gesamtlauf über die Artefakt-SBOMs -----------------------------------------------------------

def sbom_dateien(ordner: Path, namen: Iterable[str]) -> dict[str, Path]:
    """Je Artefakt genau ein SBOM `scanview-<name>-<version>.cdx.json`. Das Gesamt-SBOM wird nicht
    gescannt (gleiche Komponenten, Treffer doppelt, ohne Artefaktbezug für die Lockdatei)."""
    if not ordner.is_dir():
        raise NachweisFehler("scan-sbom", f"{ordner}: SBOM-Ordner fehlt")
    dateien: dict[str, Path] = {}
    for name in namen:
        kandidaten = sorted(ordner.glob(f"scanview-{name}-*.cdx.json"))
        if len(kandidaten) != 1:
            raise NachweisFehler("scan-sbom", f"{ordner}: für Artefakt {name} "
                                              f"{len(kandidaten)} SBOMs statt genau einem")
        dateien[name] = kandidaten[0]
    return dateien


def pruefe_sbom_vollstaendig(sboms: dict[str, Path], erwartet: Iterable[str]) -> None:
    """Je erwartetem Artefakt genau ein vorhandenes SBOM, keines darüber hinaus — sonst
    `scan-sbom` (ein fehlendes SBOM hieße: dieses Artefakt wurde nie geprüft)."""
    soll = set(erwartet)
    fehlend = sorted(soll - set(sboms)) + sorted(n for n in soll & set(sboms)
                                                  if not sboms[n].is_file())
    fremd = sorted(set(sboms) - soll)
    if fehlend or fremd:
        raise NachweisFehler("scan-sbom", "SBOM fehlt für: " + (", ".join(fehlend) or "-")
                             + "; unerwartet: " + (", ".join(fremd) or "-"))


@dataclass(frozen=True)
class ScanErgebnis:
    """DB-Stand, alle Treffer (je Komponente) und je Artefakt SBOM-Name, SHA-256, Trefferzahlen
    je Schwere und Abdeckung."""

    stand: DbStand
    treffer: list[Treffer]
    artefakte: dict[str, Any]


def scanne_alle(sboms: dict[str, Path], cache: Path, vex: Path, werkzeuge: dict[str, Werkzeug],
                *, uhr: datetime, osv: OsvStand | None = None) -> ScanErgebnis:
    """DB-Frische gegen `uhr` (die echte Uhr, nie ein Stichtag), dann je Artefakt-SBOM trivy
    ohne Netz. Fail-closed: ohne Prüfung hieße „0 Treffer“ nur „nicht geprüft“ — ein SBOM ohne
    Komponenten oder eine ungescannte Pflichtkomponente bricht mit `scan-abdeckung` ab.
    Ausgenommen ist allein der Image-Inhalt des Imports (conda ohne PURL); er steht offen in der
    Abdeckung und als Sammelbefund B-SCAN-IMG-* im Register. Eigenbauten prüft statt trivy der
    OSV-Stand (`osv`, ohne Netz geladen); fehlt er oder deckt eine Eigenbau-Komponente nicht
    genau ab, bleibt sie Pflicht und der Lauf bricht ab. OSV-Funde kommen zu den Treffern."""
    from . import osv as osv_modul  # zur Laufzeit: osv importiert Treffer aus diesem Modul
    stand = db_pruefen(cache, uhr)
    treffer: list[Treffer] = []
    artefakte: dict[str, Any] = {}
    for name, pfad in sboms.items():
        daten = trivy_json(pfad, cache, vex, werkzeuge)
        try:
            roh = pfad.read_bytes()
            bom = json.loads(roh.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise NachweisFehler("scan-sbom", f"{pfad.name}: nicht lesbar ({e})") from None
        ersatz = osv_modul.ersatz_fuer(bom, osv)
        eigene = [*je_komponente(werte_trivy_aus(daten, name), bom),
                  *osv_modul.treffer(bom, osv, name)]
        abd = abdeckung(bom, daten, name, ersatz)
        if abd["komponenten"] == 0:
            raise NachweisFehler("scan-abdeckung", f"{pfad.name}: SBOM ohne Komponenten — "
                                                   "nichts, was trivy prüfen könnte")
        if abd["ungescannt_pflicht"]:
            offen = abd["ungescannt_pflicht"]
            raise NachweisFehler("scan-abdeckung", f"{pfad.name}: {len(offen)} Komponente(n) "
                                                   "von trivy nicht geprüft: "
                                                   + ", ".join(offen[:20])
                                                   + (" — Eigenbau: OSV-Stand fehlt oder deckt "
                                                      "ihn nicht ab, `db-aktualisieren`"
                                                      if any(o.startswith("eigenbau:")
                                                             for o in offen) else ""))
        artefakte[name] = {
            "sbom": pfad.name,
            "sha256": hashlib.sha256(roh).hexdigest(),
            # Eindeutig je (ID, PURL): derselbe Treffer in beiden Images zählt einmal.
            "treffer": {s: len({(t.id, t.paket) for t in eigene if t.schwere == s})
                        for s in ("kritisch", "hoch", "mittel", "niedrig")},
            "abdeckung": abd,
        }
        treffer += eigene
    return ScanErgebnis(stand, treffer, artefakte)
