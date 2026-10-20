"""Nur-Lese-Links (F28, ADR 0016): Gäste sehen Wolke und Meshes
eines Projekts ohne Konto.

Ablauf: Die Projektleitung legt einen Link an (`/teilen#t=<Token>`, Token im Fragment, nur sein
SHA-256 in der DB, optional ein einfaches Passwort als argon2-Hash). Die Teilen-Seite tauscht das
Token per POST gegen das HttpOnly-Cookie `GAST_COOKIE` einer Gast-Sitzung. Jede Anfrage prüft
Ablauf, Widerruf und den Projekt-Schalter neu (sofort wirksam).

Ein Gast ist keine Rolle: `zugriff.projekt()` kennt ihn nicht (alle /api/v1-Wege bleiben 401),
`/api/authz` lässt ihn nur an die Dateien der Positivliste (`gast_paket`) und nur im Projekt des
Links. Die Positivliste entsteht exakt aus den Ebenen `pointcloud` und `mesh` (glTF-Verweise
rekursiv aufgelöst) plus der Draufsicht der Wolke — keine Präfixe. Ein Verweis, der das Paket
verlässt oder kodiert ist, nimmt die ganze Mesh-Ebene aus der Gast-Ansicht (fail closed).
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
import sqlite3
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fotos_tor, protokoll
from .db import transaktion
from .tokens import hash_token, new_token

log = logging.getLogger("scanview.teilen")

GAST_COOKIE = "__Host-scanview-gast"
TAGE_STANDARD = 14
TAGE_MAX = 90
NAME_MAX = 80
# Bewusst einfach (Andre 14.10.: „Alibi-Passwort, nichts Kompliziertes“) — der Link selbst trägt
# 256 bit; das Passwort ist nur eine zweite Hürde für weitergereichte Links.
PASSWORT_MIN = 4
PASSWORT_MAX = 128
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
# Paketpfad wie in routen_api._DATA_RE, ohne führendes /data/<pid>/.
_REST_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_GLB_JSON_MAX = 16 * 1024 * 1024
# Abrufe je Link und Minute über /api/authz (jede Range-Anfrage der Wolke zählt). Der Viewer holt
# beim schnellen Bewegen einige hundert COPC-Knoten je Minute; die Grenze bremst nur Massenabzug.
ABRUFE_PRO_MINUTE = 3000
# Austausch Token → Gast-Sitzung: je IP und global (gegen Durchprobieren und Flut).
AUSTAUSCH_JE_IP_PRO_MINUTE = 20
AUSTAUSCH_GLOBAL_PRO_MINUTE = 300

UNGUELTIG = "Dieser Link ist ungültig, abgelaufen oder wurde zurückgezogen."
AUSGESCHALTET = "Nur-Lese-Links sind für dieses Projekt ausgeschaltet."


def _jetzt(now: int | None) -> int:
    return int(time.time()) if now is None else now


@dataclass(frozen=True)
class Link:
    id: int
    project_id: str
    name: str
    erstellt_am: int
    ablauf: int
    widerrufen_am: int | None
    abrufe: int
    letzter_abruf: int | None
    mit_passwort: bool

    def als_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "erstellt": self.erstellt_am,
                "ablauf": self.ablauf, "widerrufen": self.widerrufen_am, "abrufe": self.abrufe,
                "letzterAbruf": self.letzter_abruf, "passwort": self.mit_passwort}


def _link(r: sqlite3.Row) -> Link:
    return Link(id=int(r["id"]), project_id=str(r["project_id"]), name=str(r["name"]),
                erstellt_am=int(r["erstellt_am"]), ablauf=int(r["ablauf"]),
                widerrufen_am=r["widerrufen_am"], abrufe=int(r["abrufe"]),
                letzter_abruf=r["letzter_abruf"], mit_passwort=r["passwort_hash"] is not None)


def liste(conn: sqlite3.Connection, pid: str) -> list[Link]:
    return [_link(r) for r in conn.execute(
        "SELECT * FROM teilen_links WHERE project_id=? ORDER BY id DESC", (pid,))]


def anlegen(conn: sqlite3.Connection, pid: str, *, name: str, tage: int,
            passwort_hash: str | None, von: int, now: int | None = None) -> tuple[Link, str]:
    """Legt einen Link an und gibt ihn mit dem Klartext-Token zurück (nur dieses eine Mal)."""
    if not 1 <= tage <= TAGE_MAX:
        raise ValueError(tage)
    n = _jetzt(now)
    token = new_token()
    with transaktion(conn):
        cur = conn.execute(
            "INSERT INTO teilen_links (project_id, name, token_hash, passwort_hash, erstellt_von,"
            " erstellt_am, ablauf) VALUES (?,?,?,?,?,?,?)",
            (pid, name, hash_token(token), passwort_hash, von, n, n + tage * 86400))
        lid = int(cur.lastrowid or 0)
        protokoll.ereignis(conn, kanal="web", nutzer_id=von, project_id=pid,
                           art="teilen.angelegt", objekt_id=str(lid),
                           nachher={"name": name, "ablauf": n + tage * 86400,
                                    "passwort": passwort_hash is not None}, now=n)
    r = conn.execute("SELECT * FROM teilen_links WHERE id=?", (lid,)).fetchone()
    return _link(r), token


def widerrufen(conn: sqlite3.Connection, pid: str, lid: int, *, von: int,
               now: int | None = None) -> Link | None:
    """Widerruft den Link und beendet alle seine Gast-Sitzungen. None: kein Link dieses Projekts.
    Ein schon widerrufener Link bleibt unverändert (kein zweites Ereignis)."""
    n = _jetzt(now)
    with transaktion(conn):
        r = conn.execute("SELECT * FROM teilen_links WHERE id=? AND project_id=?",
                         (lid, pid)).fetchone()
        if r is None:
            return None
        if r["widerrufen_am"] is None:
            conn.execute("UPDATE teilen_links SET widerrufen_am=? WHERE id=?", (n, lid))
            conn.execute("DELETE FROM gast_sitzungen WHERE link_id=?", (lid,))
            protokoll.ereignis(conn, kanal="web", nutzer_id=von, project_id=pid,
                               art="teilen.widerrufen", objekt_id=str(lid),
                               vorher={"name": r["name"]}, now=n)
    r = conn.execute("SELECT * FROM teilen_links WHERE id=?", (lid,)).fetchone()
    return _link(r)


def _hash(token: str) -> str | None:
    return hash_token(token) if _TOKEN_RE.fullmatch(token) else None


@dataclass(frozen=True)
class GueltigerLink:
    id: int
    project_id: str
    ablauf: int
    passwort_hash: str | None


def link_zum_token(conn: sqlite3.Connection, token: str,
                   now: int | None = None) -> GueltigerLink | None:
    """Der Link zu diesem Token, sofern gültig: nicht widerrufen, nicht abgelaufen, Schalter an."""
    h = _hash(token)
    if h is None:
        return None
    r = conn.execute(
        "SELECT l.id, l.project_id, l.ablauf, l.passwort_hash, l.widerrufen_am,"
        " p.nur_lese_links FROM teilen_links l JOIN projekte p ON p.project_id = l.project_id"
        " WHERE l.token_hash=?", (h,)).fetchone()
    if (r is None or r["widerrufen_am"] is not None or r["ablauf"] <= _jetzt(now)
            or not r["nur_lese_links"]):
        return None
    return GueltigerLink(id=int(r["id"]), project_id=str(r["project_id"]),
                         ablauf=int(r["ablauf"]), passwort_hash=r["passwort_hash"])


def gast_anlegen(conn: sqlite3.Connection, link: GueltigerLink, now: int | None = None) -> str:
    """Gast-Sitzung bis zum Ablauf des Links; zählt den Abruf am Link (Freigabe statt Person)."""
    n = _jetzt(now)
    token = new_token()
    with transaktion(conn):
        conn.execute("INSERT INTO gast_sitzungen (token_hash, link_id, erstellt_am, ablauf)"
                     " VALUES (?,?,?,?)", (hash_token(token), link.id, n, link.ablauf))
        conn.execute("UPDATE teilen_links SET abrufe = abrufe + 1, letzter_abruf=? WHERE id=?",
                     (n, link.id))
    return token


def gast_beenden(conn: sqlite3.Connection, token: str) -> None:
    h = _hash(token)
    if h is not None:
        conn.execute("DELETE FROM gast_sitzungen WHERE token_hash=?", (h,))


@dataclass(frozen=True)
class Gast:
    link_id: int
    project_id: str
    name: str
    ablauf: int


def gast(conn: sqlite3.Connection, token: str | None, now: int | None = None) -> Gast | None:
    """Gast aus dem Cookie — bei jeder Anfrage neu gegen Ablauf, Widerruf und Schalter geprüft
    (ein indizierter SELECT, kein Schreiben)."""
    h = _hash(token) if token else None
    if h is None:
        return None
    r = conn.execute(
        "SELECT g.ablauf AS g_ablauf, l.id, l.project_id, l.name, l.ablauf, l.widerrufen_am,"
        " p.nur_lese_links FROM gast_sitzungen g JOIN teilen_links l ON l.id = g.link_id"
        " JOIN projekte p ON p.project_id = l.project_id WHERE g.token_hash=?", (h,)).fetchone()
    n = _jetzt(now)
    if (r is None or r["widerrufen_am"] is not None or not r["nur_lese_links"]
            or r["ablauf"] <= n or r["g_ablauf"] <= n):
        return None
    return Gast(link_id=int(r["id"]), project_id=str(r["project_id"]), name=str(r["name"]),
                ablauf=int(r["ablauf"]))


def aufraeumen(conn: sqlite3.Connection, now: int | None = None) -> int:
    return conn.execute("DELETE FROM gast_sitzungen WHERE ablauf < ?", (_jetzt(now),)).rowcount


# --- Positivliste --------------------------------------------------------------------------------


def _paketpfad(basis: str, ref: str) -> str | None:
    """Verweis `ref` relativ zur Datei `basis` (beide paketrelativ) → normierter Paketpfad oder
    None, wenn er das Paket verlässt, absolut, kodiert-trickreich oder sonst unzulässig ist."""
    # Kodierte Verweise (%2e, %2f, auch harmlose %20) gar nicht erst deuten: fail closed.
    if not ref or ref.startswith(("/", "\\")) or ":" in ref or "\\" in ref or "%" in ref:
        return None
    pfad = posixpath.normpath(posixpath.join(posixpath.dirname(basis), ref))
    if pfad.startswith("../") or pfad == ".." or pfad.startswith("/"):
        return None
    if not _REST_RE.fullmatch(pfad) or any(not s or s.startswith(".") for s in pfad.split("/")):
        return None
    # Fotos (Personen) nie über einen Gast-Link, auch nicht als Mesh-Textur.
    if fotos_tor.ist_fotopfad(pfad):
        return None
    return pfad


def _gltf_json(datei: Path) -> dict[str, Any] | None:
    """JSON-Teil einer .glb/.gltf-Datei; None bei kaputter Datei."""
    try:
        with datei.open("rb") as f:
            kopf = f.read(20)
            if datei.suffix.lower() == ".gltf":
                f.seek(0)
                roh = f.read(_GLB_JSON_MAX + 1)
            else:
                if len(kopf) < 20 or kopf[:4] != b"glTF":
                    return None
                laenge, art = struct.unpack("<I4s", kopf[12:20])
                if art != b"JSON" or laenge > _GLB_JSON_MAX:
                    return None
                roh = f.read(laenge)
        if len(roh) > _GLB_JSON_MAX:
            return None
        daten = json.loads(roh.decode("utf-8"))
    except (OSError, ValueError, struct.error):
        return None
    return daten if isinstance(daten, dict) else None


def _gltf_dateien(paket: Path, pfad: str, tiefe: int = 0) -> set[str] | None:
    """Die glTF-Datei und alle Dateien, auf die sie verweist (Buffer, Bilder), rekursiv für
    eingebettete .gltf-Verweise. None, wenn ein Verweis unzulässig ist oder fehlt."""
    if tiefe > 4:
        return None
    datei = paket / pfad
    if not datei.is_file() or datei.is_symlink():
        return None
    daten = _gltf_json(datei)
    if daten is None:
        return None
    out = {pfad}
    for feld in ("buffers", "images"):
        eintraege = daten.get(feld) or []
        if not isinstance(eintraege, list):
            return None
        for e in eintraege:
            uri = e.get("uri") if isinstance(e, dict) else None
            if uri is None or (isinstance(uri, str) and uri.startswith("data:")):
                continue
            if not isinstance(uri, str):
                return None
            ziel = _paketpfad(pfad, uri)
            if ziel is None or not (paket / ziel).is_file() or (paket / ziel).is_symlink():
                return None
            if ziel.lower().endswith((".gltf", ".glb")):
                unter = _gltf_dateien(paket, ziel, tiefe + 1)
                if unter is None:
                    return None
                out |= unter
            else:
                out.add(ziel)
    return out


@dataclass(frozen=True)
class GastPaket:
    manifest: dict[str, Any]
    dateien: frozenset[str]


_GAST_EBENEN = ("pointcloud", "mesh")
# Kopfteile des Manifests, die der Viewer braucht und die keine anderen Ebenen beschreiben.
_GAST_KOPF = ("schemaVersion", "projectId", "revision", "title", "frame", "initialView")


def _mobilstufe(paket: Path, ebene: dict[str, Any], teile: set[str]) -> dict[str, Any]:
    """Mobilstufe (`mobileUrl`, für Handys) ist optional: ist sie erreichbar, kommen ihre Dateien
    in `teile`; fehlt sie oder ist der Verweis unzulässig, bekommt der Gast die Ebene ohne die
    `mobile*`-Felder — nicht gar keine Ebene."""
    if "mobileUrl" not in ebene:
        return ebene
    ziel = _paketpfad("project.json", str(ebene.get("mobileUrl", "")))
    mobil = _gltf_dateien(paket, ziel) if ziel is not None else None
    if mobil is None:
        log.warning("Gast-Ansicht %s: Mobilstufe von Mesh %s ausgelassen", paket.name,
                    ebene.get("id"))
        return {k: v for k, v in ebene.items() if not k.startswith("mobile")}
    teile |= mobil
    return ebene


def _bauen(paket: Path) -> GastPaket | None:
    try:
        m = json.loads((paket / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or not isinstance(m.get("layers"), list):
        return None
    dateien: set[str] = set()
    ebenen: list[dict[str, Any]] = []
    for ebene in m["layers"]:
        if not isinstance(ebene, dict) or ebene.get("kind") not in _GAST_EBENEN:
            continue
        if ebene["kind"] == "pointcloud":
            ziel = _paketpfad("project.json", str(ebene.get("url", "")))
            if ziel is None or not (paket / ziel).is_file():
                log.warning("Gast-Ansicht %s: Punktwolke %s ausgelassen", paket.name,
                            ebene.get("id"))
                continue
            dateien.add(ziel)
        else:
            if ebene.get("format") != "gltf":
                continue
            teile: set[str] = set()
            for feld in ("url", "previewUrl"):
                ziel = _paketpfad("project.json", str(ebene.get(feld, "")))
                gefunden = _gltf_dateien(paket, ziel) if ziel is not None else None
                if gefunden is None:
                    teile = set()
                    break
                teile |= gefunden
            if not teile:
                log.warning("Gast-Ansicht %s: Mesh %s ausgelassen (Verweis unzulässig oder fehlt)",
                            paket.name, ebene.get("id"))
                continue
            ebene = _mobilstufe(paket, ebene, teile)
            dateien |= teile
        ebenen.append(ebene)
    if not any(e.get("kind") == "pointcloud" for e in ebenen):
        return None
    manifest = {k: m[k] for k in _GAST_KOPF if k in m}
    manifest["layers"] = ebenen
    tv = m.get("topview")
    if isinstance(tv, dict):
        ziel = _paketpfad("project.json", str(tv.get("url", "")))
        if ziel is not None and (paket / ziel).is_file():
            manifest["topview"] = tv
            dateien.add(ziel)
    return GastPaket(manifest=manifest, dateien=frozenset(dateien))


_cache: dict[Path, tuple[tuple[int, int], GastPaket | None]] = {}
_sperre = threading.Lock()


def gast_paket(paket: Path) -> GastPaket | None:
    """Bereinigtes Manifest und Positivliste eines Pakets; zwischengespeichert nach (mtime, Größe)
    von project.json — ein neu eingespieltes Paket ersetzt project.json immer mit."""
    try:
        st = (paket / "project.json").stat()
    except OSError:
        return None
    stand = (st.st_mtime_ns, st.st_size)
    with _sperre:
        alt = _cache.get(paket)
        if alt is not None and alt[0] == stand:
            return alt[1]
    wert = _bauen(paket)
    with _sperre:
        _cache[paket] = (stand, wert)
    return wert


# --- Drosseln ------------------------------------------------------------------------------------


class Fenster:
    """Gleitendes 60-s-Fenster je Schlüssel, threadsicher, im Speicher (ein Prozess)."""

    def __init__(self, grenze: int, fenster_s: float = 60.0) -> None:
        self.grenze = grenze
        self.fenster_s = fenster_s
        self._lock = threading.Lock()
        self._zeiten: dict[object, deque[float]] = {}

    def erlaubt(self, schluessel: object) -> bool:
        jetzt = time.monotonic()
        with self._lock:
            for k in [k for k, z in self._zeiten.items() if z[-1] <= jetzt - self.fenster_s]:
                del self._zeiten[k]
            zeiten = self._zeiten.setdefault(schluessel, deque())
            while zeiten and zeiten[0] <= jetzt - self.fenster_s:
                zeiten.popleft()
            if len(zeiten) >= self.grenze:
                return False
            zeiten.append(jetzt)
            return True


@dataclass
class Drosseln:
    austausch_ip: Fenster
    austausch_global: Fenster
    abrufe: Fenster

    @classmethod
    def neu(cls) -> Drosseln:
        return cls(Fenster(AUSTAUSCH_JE_IP_PRO_MINUTE), Fenster(AUSTAUSCH_GLOBAL_PRO_MINUTE),
                   Fenster(ABRUFE_PRO_MINUTE))
