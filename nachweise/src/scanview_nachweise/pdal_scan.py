"""Schwachstellenprüfung der PDAL-Umgebung (conda-Pakete): OSV-Ersatz und blinde Restpakete.

trivy führt für `pkg:conda` keine Daten (`scan.BLINDE_PURL_TYPEN`). Zwei Wege ersetzen das:

1. **OSV-Ersatz** (`import/pdal-umgebung/osv.toml`, an den SHA-256 von `pixi.lock` gebunden): je
   Paket, dessen Upstream-Repo OSV.dev im Ökosystem GIT führt, Repo, Release-Tag und Commit, dazu
   eine **Gegenprobe** — ein älterer Release-Tag desselben Repos mit einem bekannten Eintrag, den
   OSV per Commit **und** per Tag melden muss. Bleibt die Gegenprobe leer, gilt das Paket als
   blind (fail-closed). Der Netzschritt (`aktualisieren`, Teil von `db-aktualisieren`) schreibt
   `osv-pdal-stand.json` in den Scan-Cache; der Scan lädt ihn ohne Netz (≤ 24 h). Jeder Fund ist
   ein Treffer „hoch“; freigeben kann ihn nur eine VEX-Aussage im Abschnitt `[[vex]]` derselben
   Datei (gebunden an den Lock-SHA; Aussagen in `vex.openvex.json` gelten für diese Pakete nie).
2. **Blinde Restpakete** (`import/pdal-umgebung/scan-blind.toml`): Pakete ohne Schwachstellendaten.
   Sie fallen nur dann aus der Pflicht-Abdeckung, wenn sie dort genau gelistet sind (Name,
   Version, Build, SHA-256, Lock-SHA, Präfix `umgebung-pdal|`) **und** der genannte Befund im
   geladenen Register „akzeptiert“ ist **und** seine `akzeptanz_bindung` gleich `blind_bindung`
   der aktuellen Liste ist (Inhalt: Befund-ID, Lock-SHA, Paketmenge). Eine Akzeptanz gilt so nur
   der Liste, die Andre gesehen hat: Ein verschobenes Paket oder eine nach Lock-Auffrischung neu
   gebundene Liste braucht eine neue Akzeptanz. Fristen prüft allein das Befund-Gate
   (`gate.py`).
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import osv
from .fehler import NachweisFehler
from .pdal_umgebung import LOCK, ORDNER, REF_PRAEFIX, Paket, lies_lock, lock_sha256
from .register import Befund
from .scan import Treffer

OSV_TOML = ORDNER / "osv.toml"
SCAN_BLIND = ORDNER / "scan-blind.toml"
STAND = "osv-pdal-stand.json"
#: Lockdatei, an die VEX-Aussagen zu Paketen der Umgebung gebunden sind (`vex.lockdatei_von`).
LOCKDATEI = str(LOCK)
HOECHSTALTER = timedelta(hours=24)
_REPO = re.compile(r"https://(github\.com|gitlab\.gnome\.org)/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PAKETFELDER = ("name", "version", "build", "repo", "tag", "commit", "gegenprobe_tag",
                "gegenprobe_commit", "gegenprobe_erwartet")
_VEXFELDER = ("schwachstelle", "paket", "status", "justification", "impact_statement", "zeit",
              "pruefer")


@dataclass(frozen=True)
class Eintrag:
    """Ein Paket in `osv.toml`; `bom_ref`/`purl` wie in `pdal_umgebung.komponenten`."""

    name: str
    version: str
    build: str
    repo: str
    tag: str
    commit: str
    gegenprobe_tag: str
    gegenprobe_commit: str
    gegenprobe_erwartet: str
    bom_ref: str
    purl: str


@dataclass(frozen=True)
class OsvToml:
    lock_sha256: str
    pakete: tuple[Eintrag, ...]
    vex: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class PdalStand:
    """Geladener `osv-pdal-stand.json`: je gedecktem Paket (Gegenprobe bestanden) bom-ref →
    (PURL, Funde); `blind` nennt die Pakete mit leerer Gegenprobe."""

    zeit: datetime
    lock_sha256: str
    gedeckt: dict[str, tuple[str, tuple[osv.Fund, ...]]]
    blind: tuple[str, ...]
    sha256: str


# --- osv.toml ------------------------------------------------------------------------------------

def _toml(pfad: Path, regel: str) -> dict[str, Any]:
    try:
        return tomllib.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise NachweisFehler(regel, f"{pfad}: nicht lesbar ({e})") from None


def _eintrag(roh: object, pakete: dict[str, Paket]) -> Eintrag:
    if not isinstance(roh, dict) or set(roh) != set(_PAKETFELDER) \
            or not all(isinstance(roh[f], str) and roh[f] for f in _PAKETFELDER):
        raise NachweisFehler("osv-pdal", f"osv.toml: Paket braucht genau {', '.join(_PAKETFELDER)}")
    p = pakete.get(roh["name"])
    if p is None or (p.version, p.build) != (roh["version"], roh["build"]):
        raise NachweisFehler("osv-pdal", f"osv.toml: {roh['name']} {roh['version']} "
                                         f"{roh['build']} steht so nicht im Lock")
    if not _REPO.fullmatch(roh["repo"]) or not _COMMIT.fullmatch(roh["commit"]) \
            or not _COMMIT.fullmatch(roh["gegenprobe_commit"]):
        raise NachweisFehler("osv-pdal", f"osv.toml: {p.name}: Repo (github.com, "
                                         "gitlab.gnome.org) oder Commit unlesbar")
    if roh["gegenprobe_tag"] == roh["tag"]:
        raise NachweisFehler("osv-pdal", f"osv.toml: {p.name}: Gegenprobe = geprüfter Tag")
    return Eintrag(**{f: roh[f] for f in _PAKETFELDER}, bom_ref=f"{REF_PRAEFIX}{p.purl}",
                   purl=p.purl)


def lade_osv_toml(wurzel: Path) -> OsvToml:
    """`osv.toml` laden und an das aktuelle Lock binden: anderer Lock-SHA → `osv-pdal` (die Datei
    gilt dann nicht, Neuerhebung nötig); jedes Paket muss genau so im Lock stehen."""
    lock_text = (wurzel / LOCK).read_text(encoding="utf-8")
    daten = _toml(wurzel / OSV_TOML, "osv-pdal")
    soll = lock_sha256(lock_text)
    if daten.get("lock_sha256") != soll:
        raise NachweisFehler("osv-pdal", f"{OSV_TOML} gehört nicht zu diesem Lock (SHA-256 {soll})"
                                         " — neu erheben")
    unbekannt = set(daten) - {"lock_sha256", "paket", "vex"}
    if unbekannt:
        raise NachweisFehler("osv-pdal", f"{OSV_TOML}: unbekannte Schlüssel {sorted(unbekannt)}")
    pakete = {p.name: p for p in lies_lock(lock_text)}
    eintraege = tuple(_eintrag(r, pakete) for r in daten.get("paket", []))
    if len({e.name for e in eintraege}) != len(eintraege):
        raise NachweisFehler("osv-pdal", f"{OSV_TOML}: Paket doppelt")
    vex_roh = daten.get("vex", [])
    if not isinstance(vex_roh, list) or not all(isinstance(v, dict) for v in vex_roh):
        raise NachweisFehler("osv-pdal", f"{OSV_TOML}: [[vex]] unlesbar")
    return OsvToml(soll, eintraege, tuple(vex_roh))


def vex_aussagen(wurzel: Path) -> list[Any]:
    """VEX-Aussagen aus `osv.toml` `[[vex]]` als `vex.VexAussage`, gebunden an `pixi.lock`
    (Lockdatei + Lock-SHA der Datei). Ohne `osv.toml` keine. Produkt = PURL des Pakets im Lock."""
    from .vex import STATUS, VexAussage  # zur Laufzeit: vex importiert dieses Modul

    if not (wurzel / OSV_TOML).is_file():
        return []
    t = lade_osv_toml(wurzel)
    purls = {p.name: p.purl for p in lies_lock((wurzel / LOCK).read_text(encoding="utf-8"))}
    aussagen = []
    for nr, v in enumerate(t.vex, 1):
        if set(v) != set(_VEXFELDER) or v.get("paket") not in purls \
                or v.get("status") not in STATUS or not isinstance(v.get("zeit"), datetime) \
                or v["zeit"].tzinfo is None:
            raise NachweisFehler("osv-pdal", f"{OSV_TOML}: [[vex]] Nr. {nr} braucht genau "
                                             f"{', '.join(_VEXFELDER)} (zeit mit Zeitzone, "
                                             "paket aus dem Lock)")
        aussagen.append(VexAussage(
            schwachstelle=str(v["schwachstelle"]), produkte=(purls[v["paket"]],),
            status=str(v["status"]), justification=str(v["justification"]),
            impact_statement=str(v["impact_statement"]), zeit=v["zeit"].astimezone(UTC),
            lock_hash=f"sha256:{t.lock_sha256}", pruefer=str(v["pruefer"]), lockdatei=LOCKDATEI))
    return aussagen


# --- Netzschritt ---------------------------------------------------------------------------------

def _abfrage(e: Eintrag, tag: str, commit: str, holen: osv.Holen) -> tuple[list[osv.Fund], ...]:
    return osv._per_commit(commit, holen), osv._per_tag(e.repo, tag, holen)


def _tags_pruefen(e: Eintrag, tag_commit: osv.TagCommit) -> None:
    for tag, commit in ((e.tag, e.commit), (e.gegenprobe_tag, e.gegenprobe_commit)):
        aufgeloest = tag_commit(e.repo, tag)
        if aufgeloest != commit:
            raise NachweisFehler("osv-tag", f"{e.name}: Tag {tag} in {e.repo} zeigt auf "
                                            f"{aufgeloest or 'nichts'}, erklärt ist {commit} — "
                                            "osv.toml korrigieren")


def _gegenprobe(e: Eintrag, holen: osv.Holen) -> bool:
    """Gegenprobe bestanden, wenn OSV den erwarteten Eintrag per Commit **und** per Tag meldet."""
    return all(any(e.gegenprobe_erwartet in (f.id, *f.aliases) for f in weg)
               for weg in _abfrage(e, e.gegenprobe_tag, e.gegenprobe_commit, holen))


def aktualisieren(wurzel: Path, cache: Path, *, holen: osv.Holen = osv._post,
                  uhr: Callable[[], datetime] = lambda: datetime.now(UTC),
                  tag_commit: osv.TagCommit = osv._tag_commit_git) -> PdalStand | None:
    """Netzschritt: Tags prüfen, je Paket Gegenprobe und Abfrage (Commit und Tag vereinigt),
    `osv-pdal-stand.json` schreiben. Ohne `osv.toml` kein Netzschritt (`None`)."""
    if not (wurzel / OSV_TOML).is_file():
        return None
    t = lade_osv_toml(wurzel)
    for e in t.pakete:
        _tags_pruefen(e, tag_commit)
    zeit = uhr()
    eintraege = []
    for e in t.pakete:
        ok = _gegenprobe(e, holen)
        funde = osv._vereinigt(*_abfrage(e, e.tag, e.commit, holen)) if ok else ()
        eintraege.append({"bom_ref": e.bom_ref, "purl": e.purl, "repo": e.repo, "tag": e.tag,
                          "commit": e.commit, "gegenprobe": {
                              "tag": e.gegenprobe_tag, "commit": e.gegenprobe_commit,
                              "erwartet": e.gegenprobe_erwartet, "bestanden": ok},
                          "funde": [{"id": f.id, "aliases": list(f.aliases),
                                     "modified": f.modified} for f in funde]})
    inhalt = json.dumps({"zeit": zeit.isoformat(timespec="seconds"), "url": osv.OSV_URL,
                         "lock_sha256": t.lock_sha256, "pakete": eintraege},
                        indent=2, ensure_ascii=False) + "\n"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / STAND).write_text(inhalt, encoding="utf-8")
    return lade_stand(cache, zeit)


# --- Ohne Netz -----------------------------------------------------------------------------------

def _stand_eintrag(e: dict[str, Any]) -> tuple[str, str, bool, tuple[osv.Fund, ...]]:
    gp = e["gegenprobe"]
    if not isinstance(gp, dict) or not isinstance(gp.get("bestanden"), bool) \
            or not str(e["bom_ref"]).startswith(REF_PRAEFIX):
        raise ValueError(f"{e.get('bom_ref')}: Gegenprobe oder Präfix unlesbar")
    funde = tuple(osv.Fund(str(f["id"]), tuple(map(str, f.get("aliases", []))),
                           str(f.get("modified", ""))) for f in e["funde"])
    if funde and not gp["bestanden"]:
        raise ValueError(f"{e['bom_ref']}: Funde ohne bestandene Gegenprobe")
    return str(e["bom_ref"]), str(e["purl"]), gp["bestanden"], funde


def lade_stand(cache: Path, uhr: datetime) -> PdalStand:
    """`osv-pdal-stand.json` laden: ≤ 24 h, nicht aus der Zukunft, Form wie geschrieben."""
    pfad = cache / STAND
    try:
        roh = pfad.read_bytes()
        daten = json.loads(roh.decode("utf-8"))
        zeit = datetime.fromisoformat(daten["zeit"])
        lock = str(daten["lock_sha256"])
        eintraege = [_stand_eintrag(e) for e in daten["pakete"]]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise NachweisFehler("osv-pdal-stand", f"{pfad} fehlt oder ist unlesbar ({e}) — erst "
                                               "`db-aktualisieren`") from None
    if zeit.tzinfo is None or not _SHA256.fullmatch(lock):
        raise NachweisFehler("osv-pdal-stand", f"{pfad}: Zeit ohne Zeitzone oder Lock-SHA kaputt")
    alter = uhr - zeit
    if alter > HOECHSTALTER or alter < timedelta(0):
        raise NachweisFehler("osv-pdal-stand", f"OSV-Stand der PDAL-Umgebung ist "
                                               f"{alter.total_seconds() / 3600:.1f} h alt (erlaubt "
                                               "0 bis 24 h) — erneut `db-aktualisieren`")
    return PdalStand(zeit, lock, {r: (p, f) for r, p, ok, f in eintraege if ok},
                     tuple(sorted(r for r, _p, ok, _f in eintraege if not ok)),
                     hashlib.sha256(roh).hexdigest())


def _komponenten(liste: object) -> Iterator[dict[str, Any]]:
    for k in liste if isinstance(liste, list) else []:
        if isinstance(k, dict):
            yield k
            yield from _komponenten(k.get("components"))


def _eigenschaft(k: dict[str, Any], name: str) -> str | None:
    return next((p.get("value") for p in k.get("properties", [])
                 if isinstance(p, dict) and p.get("name") == name), None)


def umgebung_komponenten(bom: dict[str, Any]) -> list[dict[str, Any]]:
    return [k for k in _komponenten(bom.get("components"))
            if str(k.get("bom-ref", "")).startswith(REF_PRAEFIX)]


def stand_falls_noetig(sboms: Iterable[Path], cache: Path, uhr: datetime) -> PdalStand | None:
    """Stand laden, wenn eines der SBOMs Komponenten der PDAL-Umgebung enthält (sonst `None`)."""
    for pfad in sboms:
        try:
            bom = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(bom, dict) and umgebung_komponenten(bom):
            return lade_stand(cache, uhr)
    return None


def _gedeckt(bom: dict[str, Any], stand: PdalStand | None) -> list[dict[str, Any]]:
    """Komponenten, die der Stand genau abdeckt: bom-ref und PURL wie im Stand, Lock-SHA der
    Komponente = Lock-SHA des Stands (anderes Lock → nichts gedeckt)."""
    if stand is None:
        return []
    return [k for k in umgebung_komponenten(bom)
            if _eigenschaft(k, "scanview:lock-sha256") == stand.lock_sha256
            and k["bom-ref"] in stand.gedeckt and k.get("purl") == stand.gedeckt[k["bom-ref"]][0]]


def ersatz_fuer(bom: dict[str, Any], stand: PdalStand | None) -> dict[str, str]:
    """bom-ref → PURL der per OSV gedeckten Komponenten (wie `osv.ersatz_fuer`)."""
    return {k["bom-ref"]: str(k["purl"]) for k in _gedeckt(bom, stand)}


def treffer(bom: dict[str, Any], stand: PdalStand | None, artefakt: str) -> list[Treffer]:
    """OSV-Funde der gedeckten Komponenten als Treffer „hoch“ (mit Aliasen für VEX)."""
    if stand is None:
        return []
    return [Treffer(f.id, str(k["purl"]), str(k.get("version", "")), "hoch", None,
                    artefakt=artefakt, bom_ref=k["bom-ref"], aliase=f.aliases)
            for k in sorted(_gedeckt(bom, stand), key=lambda k: k["bom-ref"])
            for f in stand.gedeckt[k["bom-ref"]][1]]


def stand_bericht(stand: PdalStand) -> dict[str, Any]:
    return {"zeit": stand.zeit.isoformat(), "lock_sha256": stand.lock_sha256,
            "sha256": stand.sha256, "blind": list(stand.blind),
            "gedeckt": {r: [f.id for f in fs] for r, (_p, fs) in stand.gedeckt.items()}}


# --- Blinde Restpakete ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScanBlind:
    """`scan-blind.toml`: Lock-SHA, Befund-ID und je Paket (Name, Version, Build, SHA-256)."""

    lock_sha256: str
    befund: str
    pakete: frozenset[tuple[str, str, str, str]]


def lade_scan_blind(pfad: Path) -> ScanBlind:
    daten = _toml(pfad, "scan-blind")
    pakete = daten.get("paket", [])
    felder = {"name", "version", "build", "sha256"}
    if not isinstance(daten.get("lock_sha256"), str) or not isinstance(daten.get("befund"), str) \
            or set(daten) != {"lock_sha256", "befund", "paket"} or not isinstance(pakete, list) \
            or not all(isinstance(p, dict) and set(p) == felder
                       and all(isinstance(p[f], str) for f in felder) for p in pakete):
        raise NachweisFehler("scan-blind", f"{pfad}: braucht lock_sha256, befund und "
                                           "[[paket]] mit name, version, build, sha256")
    return ScanBlind(daten["lock_sha256"], daten["befund"],
                     frozenset((p["name"], p["version"], p["build"], p["sha256"]) for p in pakete))


def blind_bindung(blind: ScanBlind) -> str:
    """SHA-256 über den Inhalt der Liste in kanonischer Form (Befund-ID, Lock-SHA, sortierte
    Pakete als JSON) — unabhängig von Kommentaren und Reihenfolge in `scan-blind.toml`. Diesen
    Wert trägt die Akzeptanz des Befunds als `akzeptanz_bindung`
    (`scanview-nachweise pdal-umgebung blind-bindung` gibt ihn aus)."""
    kanonisch = json.dumps({"befund": blind.befund, "lock_sha256": blind.lock_sha256,
                            "pakete": sorted(list(p) for p in blind.pakete)},
                           ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(kanonisch.encode("utf-8")).hexdigest()


def blind_ausnahme(blind: ScanBlind | None, befunde: Iterable[Befund]) -> ScanBlind | None:
    """Die Liste gilt nur, wenn ihr Befund im geladenen Register „akzeptiert“ ist; nicht
    akzeptiert → `None` (die Pakete bleiben Pflicht). Akzeptiert, aber ohne oder mit anderer
    `akzeptanz_bindung` als `blind_bindung(blind)` → `NachweisFehler("scan-blind")` mit beiden
    Werten: Die Akzeptanz galt einem anderen Inhalt. Eine abgelaufene Frist meldet das
    Befund-Gate, nicht diese Funktion."""
    if blind is None:
        return None
    befund = next((b for b in befunde if b.id == blind.befund), None)
    if befund is None or befund.status != "akzeptiert":
        return None
    soll = blind_bindung(blind)
    if befund.akzeptanz_bindung != soll:
        raise NachweisFehler("scan-blind", f"{befund.id}: akzeptanz_bindung "
                                           f"{befund.akzeptanz_bindung or 'fehlt'}, die aktuelle "
                                           f"Blind-Liste hat {soll} — die Akzeptanz galt einem "
                                           "anderen Inhalt (Paket verschoben, Lock neu?); neu "
                                           "vorlegen")
    return blind


def blind_refs(bom: dict[str, Any], blind: ScanBlind | None) -> set[str]:
    """bom-refs der Komponenten, die `blind` genau listet: Präfix `umgebung-pdal|`, Lock-SHA der
    Komponente = Lock-SHA der Liste, Name, Version, Build und Paket-SHA-256 gleich."""
    if blind is None:
        return set()
    refs = set()
    for k in umgebung_komponenten(bom):
        sha = next((h.get("content") for h in k.get("hashes", [])
                    if isinstance(h, dict) and h.get("alg") == "SHA-256"), None)
        schluessel = (k.get("name"), k.get("version"), _eigenschaft(k, "scanview:conda-build"),
                      sha)
        if _eigenschaft(k, "scanview:lock-sha256") == blind.lock_sha256 \
                and schluessel in blind.pakete:
            refs.add(str(k["bom-ref"]))
    return refs


@dataclass(frozen=True)
class ScanEingaben:
    """Was ein Scan über die PDAL-Umgebung braucht: Stand (ohne Netz), wirksame Blind-Liste und
    die VEX-Aussagen aus `osv.toml`. `blind_befund` ist der akzeptierte Befund, der die Liste
    wirksam macht (nur mit `blind`): Wer die Ausnahme nutzt, prüft seine Frist mit
    `gate.befund_gate`."""

    stand: PdalStand | None
    blind: ScanBlind | None
    vex: list[Any]
    blind_befund: Befund | None = None


def scan_eingaben(wurzel: Path, sboms: Iterable[Path], cache: Path, uhr: datetime,
                  befunde: Callable[[], Iterable[Befund]]) -> ScanEingaben:
    """`befunde` lädt das Register erst, wenn es eine Blind-Liste gibt."""
    blind = lade_scan_blind(wurzel / SCAN_BLIND) if (wurzel / SCAN_BLIND).is_file() else None
    stand = stand_falls_noetig(sboms, cache, uhr)
    if blind is None:
        return ScanEingaben(stand, None, vex_aussagen(wurzel))
    geladen = list(befunde())
    wirksam = blind_ausnahme(blind, geladen)
    befund = next(b for b in geladen if b.id == blind.befund) if wirksam else None
    return ScanEingaben(stand, wirksam, vex_aussagen(wurzel), befund)
