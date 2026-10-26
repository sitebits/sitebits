"""Freigabe von Panoramen und Fotos, an Dateien gebunden (F20).

Personen auf Panoramen und Baustellenfotos: Betrachter und Rückmelder bekommen eine Bilddatei
dieser Ebenen nur, wenn sie zu einer **aktiven Freigabe** gehört — Pfad und SHA-256 stehen in
`freigabe_dateien`, und die Datei auf der Platte hat genau diesen Hash. Alles andere unter einem
Panorama- oder Fotopfad ist für sie 404: ausgetauschte Bilder, Original- und Sicherungsordner
(`panos.alt/`, `fotos-alt/`, …), Dateien, die kein Manifest nennt. Bearbeiter, Projektleitung und
Admin sehen den Prüfstand.

Wann das Tor greift:
- Fotos (`images…`, `fotos…`): immer. Ein Status `released` in `images.json` allein reicht nicht.
- Panoramen (`pano…`): wenn das Paket es verlangt (`stations.json` → `anonymization.required`,
  schreibt jeder Import ab dieser Version) oder die Projektleitung „Panoramen nur freigegeben
  zeigen“ eingeschaltet hat (Standard für neu eingerichtete Projekte). Altpakete ohne Block
  bleiben bis dahin sichtbar — akzeptiertes Übergangsrisiko, im Portal gekennzeichnet.
  `stations.json` selbst enthält keine Pixel und bleibt sichtbar.

Freigeben (Projektleitung): Der Server leitet die Dateiliste aus dem Manifest der Ebene ab,
hasht jede Datei selbst und speichert (Pfad, SHA-256). Art `anonymisiert` verlangt eine geprüfte
Anonymisierung im Manifest (`status: reviewed`, Werkzeug, Manifest-SHA-256, Hash je Datei vom
Import) und dass jede Datei noch diesen Hash hat. Art `personenfrei` (nur Admin) erklärt eine
Ebene ohne Personen (Altbestand); sie wird protokolliert.

Hash-Zwischenspeicher je Datei nach (Gerät, Inode, Größe, mtime, ctime): mtime lässt sich mit
`utime` zurücksetzen, ctime ohne root nicht — eine ausgetauschte Datei wird deshalb neu gehasht.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import konten, protokoll
from .db import transaktion

EBENEN = ("panos", "fotos")
ARTEN = ("anonymisiert", "personenfrei")
MINDESTROLLE = "bearbeiter"
STATIONS_FILE = "stations.json"
IMAGES_FILE = "images.json"
MANIFEST = {"panos": STATIONS_FILE, "fotos": IMAGES_FILE}
MANIFEST_MAX = 64 * 1024 * 1024
DATEIEN_MAX = 200_000
BILD_ENDUNGEN = (".jpg", ".jpeg", ".png", ".webp", ".ktx2")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_REV_RE = re.compile(r"[0-9a-f]{16}")
_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]{1,200}")
_LEVEL_FILE_RE = re.compile(r"[A-Za-z0-9_-]*\{level\}[A-Za-z0-9_-]*\.(?:jpg|jpeg|png|webp)")

NICHT_FREIGEBBAR = "Diese Ebene lässt sich so nicht freigeben: {}"
KEINE_EBENE = "Das Paket hat diese Ebene nicht."
NUR_ADMIN = "„personenfrei“ erklärt nur ein Admin."
SCHON_FREIGEGEBEN = "Die Ebene ist schon freigegeben – erst zurücknehmen, dann neu freigeben."  # noqa: RUF001 (Gedankenstrich im Nutzertext)
KEINE_FREIGABE = "Für diese Ebene gibt es keine aktive Freigabe."


class FreigabeFehler(ValueError):
    """Nutzersichtbar (Deutsch), ohne Pfade der Instanz."""


def ebene_von(rest: str) -> str | None:
    """Ebene eines Paketpfads, breit statt genau (Groß-/Kleinschreibung egal).

    Jedes erste Segment, das mit „fotos“/„images“ bzw. „pano“ beginnt, gehört zur Ebene — auch
    Arbeits- und Sicherungskopien (fotos.neu/, panos.alt/, panos-original/, images.alt.json).
    `stations.json` beginnt nicht mit „pano“ und bleibt außen vor (keine Pixel)."""
    erstes = rest.lower().split("/", 1)[0]
    if erstes.startswith(("fotos", "images")):
        return "fotos"
    if erstes.startswith("pano"):
        return "panos"
    return None


# --- Hashes ------------------------------------------------------------------------------------

_HASH_CACHE_MAX = 50_000
_hashes: OrderedDict[tuple[int, int], tuple[int, int, int, str]] = OrderedDict()
_hash_sperre = threading.Lock()


def datei_sha256(pfad: Path) -> str | None:
    """SHA-256 einer regulären Datei; None, wenn sie fehlt oder keine reguläre Datei ist."""
    try:
        st = pfad.stat()
    except OSError:
        return None
    if not (st.st_mode & 0o170000 == 0o100000):
        return None
    schluessel = (st.st_dev, st.st_ino)
    stand = (st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    with _hash_sperre:
        alt = _hashes.get(schluessel)
        if alt is not None and alt[:3] == stand:
            _hashes.move_to_end(schluessel)
            return alt[3]
    h = hashlib.sha256()
    try:
        with pfad.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return None
    wert = h.hexdigest()
    with _hash_sperre:
        _hashes[schluessel] = (*stand, wert)
        _hashes.move_to_end(schluessel)
        while len(_hashes) > _HASH_CACHE_MAX:
            _hashes.popitem(last=False)
    return wert


# --- Manifest der Ebene ------------------------------------------------------------------------


@dataclass(frozen=True)
class Manifest:
    """Was das Paket über die Ebene sagt. `dateien`: abgeleitete Liste der Bilddateien,
    `hashes`: vom Import eingetragene SHA-256 je Datei (nur geprüfte Anonymisierung)."""

    vorhanden: bool
    pflicht: bool = False
    status: str = "unreviewed"
    werkzeug: str | None = None
    manifest_sha256: str | None = None
    dateien: tuple[str, ...] = ()
    hashes: dict[str, str] = field(default_factory=dict)
    fehler: str | None = None


_KEIN = Manifest(vorhanden=False)
_manifeste: dict[Path, tuple[tuple[int, int, int, int], Manifest]] = {}
_manifest_sperre = threading.Lock()


def _pfad_ok(pfad: str, ebene: str) -> bool:
    teile = pfad.split("/")
    return (all(_SEGMENT_RE.fullmatch(t) and not t.startswith(".") for t in teile)
            and ebene_von(pfad) == ebene)


def _anon(d: dict[str, Any]) -> dict[str, Any]:
    a = d.get("anonymization")
    return a if isinstance(a, dict) else {}


def _hashliste(a: dict[str, Any], ebene: str) -> dict[str, str]:
    roh = a.get("files")
    if not isinstance(roh, dict):
        return {}
    return {str(k): str(v) for k, v in roh.items()
            if isinstance(k, str) and isinstance(v, str) and _SHA_RE.fullmatch(v)
            and _pfad_ok(k, ebene)}


def _panos(d: dict[str, Any]) -> tuple[list[str], str | None]:
    stations = d.get("stations")
    if not isinstance(stations, list):
        return [], "stations.json ohne Standpunktliste"
    out: list[str] = []
    for s in stations:
        p = s.get("panorama") if isinstance(s, dict) else None
        if p is None:
            continue
        if not isinstance(p, dict) or p.get("kind") != "cube":
            return [], "Panoramaform nicht unterstützt"
        faces, levels, datei = p.get("faces"), p.get("levels"), p.get("levelFile")
        if (not isinstance(faces, dict) or not isinstance(levels, list)
                or not isinstance(datei, str) or not _LEVEL_FILE_RE.fullmatch(datei)
                or not all(type(lv) is int and 0 < lv <= 65536 for lv in levels)):
            return [], "Panoramaangaben unvollständig"
        for ordner in faces.values():
            if not isinstance(ordner, str):
                return [], "Panoramaangaben unvollständig"
            for lv in levels:
                pfad = f"{ordner.rstrip('/')}/{datei.format(level=lv)}"
                if not _pfad_ok(pfad, "panos"):
                    return [], "Panoramapfad unzulässig"
                out.append(pfad)
    return out, None


def _fotos(paket: Path, d: dict[str, Any]) -> tuple[list[str], str | None]:
    bilder = d.get("images")
    if not isinstance(bilder, list):
        return [], "images.json ohne Bildliste"
    out: list[str] = [IMAGES_FILE]
    for b in bilder:
        if not isinstance(b, dict) or not isinstance(b.get("id"), str) \
                or not _ID_RE.fullmatch(b["id"]):
            return [], "Foto ohne gültige ID"
        rev = b.get("rev")
        if rev is not None and (not isinstance(rev, str) or not _REV_RE.fullmatch(rev)):
            return [], "Foto mit ungültiger Revision"
        ordner = f"fotos/{b['id']}" + (f"/{rev}" if rev else "")
        basis = paket / ordner
        if basis.is_symlink() or not basis.is_dir():
            return [], "Fotoordner fehlt"
        for wurzel, verz, namen in os.walk(basis, followlinks=False):
            verz.sort()
            for n in sorted(namen):
                voll = Path(wurzel) / n
                if voll.suffix.lower() not in BILD_ENDUNGEN or voll.is_symlink():
                    continue
                rel = voll.relative_to(paket).as_posix()
                if not _pfad_ok(rel, "fotos"):
                    return [], "Fotopfad unzulässig"
                out.append(rel)
                if len(out) > DATEIEN_MAX:
                    return [], "zu viele Dateien"
    return out, None


def _lesen(paket: Path, ebene: str) -> Manifest:
    datei = paket / MANIFEST[ebene]
    try:
        if datei.stat().st_size > MANIFEST_MAX:
            return Manifest(vorhanden=True, pflicht=True, fehler="Manifest zu groß")
        d = json.loads(datei.read_bytes())
    except FileNotFoundError:
        return _KEIN
    except (OSError, ValueError):
        return Manifest(vorhanden=True, pflicht=True, fehler="Manifest nicht lesbar")
    if not isinstance(d, dict):
        return Manifest(vorhanden=True, pflicht=True, fehler="Manifest nicht lesbar")
    a = _anon(d)
    dateien, fehler = _panos(d) if ebene == "panos" else _fotos(paket, d)
    werkzeug = a.get("tool")
    msha = a.get("manifestSha256")
    return Manifest(
        vorhanden=True,
        # Fotos immer; Panoramen, wenn der Import den Block geschrieben hat (Pflicht ab Werk).
        pflicht=ebene == "fotos" or (bool(a) and a.get("required") is not False),
        status=str(a.get("status")) if isinstance(a.get("status"), str) else "unreviewed",
        werkzeug=werkzeug if isinstance(werkzeug, str) and 0 < len(werkzeug) <= 80 else None,
        manifest_sha256=msha if isinstance(msha, str) and _SHA_RE.fullmatch(msha) else None,
        dateien=tuple(dateien), hashes=_hashliste(a, ebene), fehler=fehler)


def manifest(paket: Path, ebene: str) -> Manifest:
    """Zwischengespeichert nach dem Stand der Manifestdatei (Gerät, Inode, mtime, Größe)."""
    datei = paket / MANIFEST[ebene]
    try:
        st = datei.stat()
    except OSError:
        return _KEIN
    stand = (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    with _manifest_sperre:
        alt = _manifeste.get(datei)
        if alt is not None and alt[0] == stand:
            return alt[1]
    wert = _lesen(paket, ebene)
    with _manifest_sperre:
        _manifeste[datei] = (stand, wert)
    return wert


# --- Tor ---------------------------------------------------------------------------------------


def tor_aktiv(paket: Path, ebene: str, panoramen_nur_freigegeben: bool) -> bool:
    if ebene == "fotos":
        return True
    return panoramen_nur_freigegeben or manifest(paket, "panos").pflicht


def _freigabe_sha(conn: sqlite3.Connection, pid: str, ebene: str, pfad: str) -> str | None:
    r = conn.execute(
        "SELECT d.sha256 FROM freigaben f JOIN freigabe_dateien d ON d.freigabe_id = f.id"
        " WHERE f.project_id=? AND f.ebene=? AND f.zurueckgenommen_am IS NULL AND d.pfad=?",
        (pid, ebene, pfad)).fetchone()
    return None if r is None else str(r["sha256"])


def gesperrt(conn: sqlite3.Connection, paket: Path, pid: str, rest: str, rolle: str | None,
             panoramen_nur_freigegeben: bool) -> bool:
    """True = 404 für diese Anfrage. Eine indizierte Abfrage, Hash aus dem Zwischenspeicher."""
    ebene = ebene_von(rest)
    if ebene is None or konten.mindestens(rolle, MINDESTROLLE):
        return False
    if not tor_aktiv(paket, ebene, panoramen_nur_freigegeben):
        return False
    soll = _freigabe_sha(conn, pid, ebene, rest)
    return soll is None or datei_sha256(paket / rest) != soll


def aktiv(conn: sqlite3.Connection, pid: str, ebene: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM freigaben WHERE project_id=? AND ebene=? AND zurueckgenommen_am IS NULL",
        (pid, ebene)).fetchone()
    return row


def sichtbar(conn: sqlite3.Connection, paket: Path, pid: str, ebene: str, rolle: str | None,
             panoramen_nur_freigegeben: bool) -> bool:
    """Darf die Rolle die Ebene grundsätzlich laden (Paketübersicht, Projektkarte)?
    Einzelne ausgetauschte Dateien prüft erst das Tor."""
    if konten.mindestens(rolle, MINDESTROLLE):
        return True
    if not tor_aktiv(paket, ebene, panoramen_nur_freigegeben):
        return True
    return aktiv(conn, pid, ebene) is not None


# --- Freigeben, zurücknehmen, Stand ------------------------------------------------------------


def _soll_hashes(paket: Path, m: Manifest, art: str) -> dict[str, str]:
    if m.fehler is not None:
        raise FreigabeFehler(NICHT_FREIGEBBAR.format(m.fehler))
    if not m.dateien:
        raise FreigabeFehler(NICHT_FREIGEBBAR.format("keine Bilddateien im Manifest"))
    if art == "anonymisiert":
        if m.status != "reviewed" or m.werkzeug is None or m.manifest_sha256 is None:
            raise FreigabeFehler(NICHT_FREIGEBBAR.format(
                "keine geprüfte Anonymisierung im Paket (Import mit Anonymisierungsmanifest)"))
        fehlend = [p for p in m.dateien if p not in m.hashes and not p.endswith(".json")]
        if fehlend or set(m.hashes) - set(m.dateien):
            raise FreigabeFehler(NICHT_FREIGEBBAR.format(
                "Dateiliste der Anonymisierung passt nicht zum Manifest"))
    out: dict[str, str] = {}
    abweichend = 0
    for p in m.dateien:
        ist = datei_sha256(paket / p)
        if ist is None:
            raise FreigabeFehler(NICHT_FREIGEBBAR.format(f"{len(m.dateien)} Dateien erwartet, "
                                                         "mindestens eine fehlt"))
        if art == "anonymisiert" and p in m.hashes and m.hashes[p] != ist:
            abweichend += 1
        out[p] = ist
    if abweichend:
        raise FreigabeFehler(NICHT_FREIGEBBAR.format(
            f"{abweichend} Dateien weichen von der geprüften Anonymisierung ab"))
    return out


def freigeben(conn: sqlite3.Connection, paket: Path, pid: str, ebene: str, art: str, *,
              von: int, ist_admin: bool, kanal: str = "web", now: int | None = None
              ) -> dict[str, Any]:
    if ebene not in EBENEN or art not in ARTEN:
        raise ValueError(ebene)
    if art == "personenfrei" and not ist_admin:
        raise FreigabeFehler(NUR_ADMIN)
    m = manifest(paket, ebene)
    if not m.vorhanden:
        raise FreigabeFehler(KEINE_EBENE)
    if aktiv(conn, pid, ebene) is not None:
        raise FreigabeFehler(SCHON_FREIGEGEBEN)
    hashes = _soll_hashes(paket, m, art)
    n = int(time.time()) if now is None else now
    with transaktion(conn):
        if aktiv(conn, pid, ebene) is not None:
            raise FreigabeFehler(SCHON_FREIGEGEBEN)
        fid = conn.execute(
            "INSERT INTO freigaben (project_id, ebene, art, werkzeug, manifest_sha256, dateien,"
            " freigegeben_von, freigegeben_am) VALUES (?,?,?,?,?,?,?,?)",
            (pid, ebene, art, m.werkzeug if art == "anonymisiert" else None,
             m.manifest_sha256 if art == "anonymisiert" else None, len(hashes), von, n),
        ).lastrowid
        conn.executemany(
            "INSERT INTO freigabe_dateien (freigabe_id, pfad, sha256) VALUES (?,?,?)",
            [(fid, p, h) for p, h in hashes.items()])
        protokoll.ereignis(
            conn, kanal=kanal, nutzer_id=von, project_id=pid, art="freigabe.erteilt",
            objekt_id=ebene, nachher={"art": art, "dateien": len(hashes),
                                      "werkzeug": m.werkzeug if art == "anonymisiert" else None,
                                      "manifestSha256": (m.manifest_sha256
                                                         if art == "anonymisiert" else None)},
            now=n)
    return stand(conn, paket, pid, ebene, pruefen=False)


def zuruecknehmen(conn: sqlite3.Connection, paket: Path, pid: str, ebene: str, *, von: int,
                  kanal: str = "web", now: int | None = None) -> dict[str, Any]:
    n = int(time.time()) if now is None else now
    with transaktion(conn):
        r = aktiv(conn, pid, ebene)
        if r is None:
            raise FreigabeFehler(KEINE_FREIGABE)
        conn.execute("UPDATE freigaben SET zurueckgenommen_am=?, zurueckgenommen_von=?"
                     " WHERE id=?", (n, von, r["id"]))
        protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, project_id=pid,
                           art="freigabe.zurueckgenommen", objekt_id=ebene,
                           vorher={"art": r["art"], "dateien": r["dateien"]}, now=n)
    return stand(conn, paket, pid, ebene, pruefen=False)


def stand(conn: sqlite3.Connection, paket: Path, pid: str, ebene: str,
          panoramen_nur_freigegeben: bool = False, *, pruefen: bool = False) -> dict[str, Any]:
    """Stand einer Ebene fürs Portal und für Agenten: Manifest, aktive Freigabe und wie viele
    freigegebene Dateien heute noch passen (`pruefen=False` spart das Hashen)."""
    m = manifest(paket, ebene)
    r = aktiv(conn, pid, ebene)
    frei: dict[str, Any] | None = None
    if r is not None:
        frei = {"art": r["art"], "dateien": int(r["dateien"]), "werkzeug": r["werkzeug"],
                "manifestSha256": r["manifest_sha256"], "freigegebenAm": int(r["freigegeben_am"])}
        # Wer freigegeben hat, steht im Verlauf (Protokoll), nicht in dieser Antwort: sie geht
        # auch an Agenten (MCP) und braucht keine Konto-ID.
        if pruefen:
            abweichend = sum(
                1 for z in conn.execute("SELECT pfad, sha256 FROM freigabe_dateien"
                                        " WHERE freigabe_id=?", (r["id"],))
                if datei_sha256(paket / str(z["pfad"])) != z["sha256"])
            frei["abweichend"] = abweichend
    return {
        "ebene": ebene,
        "vorhanden": m.vorhanden,
        "torAktiv": m.vorhanden and tor_aktiv(paket, ebene, panoramen_nur_freigegeben),
        "anonymisierung": {"status": m.status, "werkzeug": m.werkzeug,
                           "manifestSha256": m.manifest_sha256,
                           "geprueft": (m.status == "reviewed" and m.werkzeug is not None
                                        and m.manifest_sha256 is not None and bool(m.hashes))},
        "dateien": len(m.dateien),
        "fehler": m.fehler,
        "freigabe": frei,
    }
