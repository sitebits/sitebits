"""Uploads in Stücken: Pfadprüfung, Ablage per Ordner-Deskriptor, Kopfbyte-Signatur, Aufräumen.

Ein Upload ist eine angekündigte Dateiliste (relative Pfade mit Größe). Der Webdienst legt die
Ordner und leeren Dateien selbst unter `<uploads>/<id>/` an — Segment für Segment relativ zum
Deskriptor des Elternordners, ohne Symlinks zu folgen, nie über zusammengesetzte Pfadtexte. Die
Stücke schreibt er an die angekündigte Stelle (`pwrite`), danach rechnet er die SHA-256 jeder
Datei selbst nach.

Vom Inhalt liest der Webdienst nur die Kopfbytes (Signatur je Endung); jeder Parser fremder
Dateien läuft im isolierten Auftrag des Import-Workers. Uploads liegen außerhalb der von Caddy
ausgelieferten Paketwurzel und werden nie ausgeliefert.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import stat
import struct
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Stückgröße des Protokolls; die Body-Grenze des Reverse-Proxys liegt knapp darüber (65 MiB).
STUECK = 64 * 1024 * 1024
MAX_DATEIEN = 10_000
MAX_TIEFE = 6
SEGMENT_RE = re.compile(r"[A-Za-z0-9._ -]{1,200}")
UPLOAD_ID_RE = re.compile(r"[0-9a-f]{32}")
# Halbe (und nicht mehr genutzte) Uploads werden nach so langer Zeit ohne Fortschritt entfernt.
FRIST_S = 24 * 3600
KOPF_BYTES = 4096
_ORDNER = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_CONTENT_RANGE_RE = re.compile(r"bytes ([0-9]{1,15})-([0-9]{1,15})/([0-9]{1,15})")
_DIGEST_RE = re.compile(r"sha-256=:([A-Za-z0-9+/]{43}=):")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class UploadFehler(ValueError):
    """Eingabe abgelehnt; der Text geht ohne Pfade an Mensch oder Agent."""


# --- Pfade ---------------------------------------------------------------------------------


def pruefe_pfade(pfade: Iterable[Any]) -> list[tuple[str, ...]]:
    """Relative Pfade prüfen; liefert sie als Segmenttupel in Eingabereihenfolge.

    Erlaubt: bis zu `MAX_TIEFE` Segmente nach `SEGMENT_RE`, kein `.`/`..`, kein führender oder
    doppelter Schrägstrich. Abgelehnt: Groß/Klein-Dubletten (auch bei Ordnern: `A/x` neben
    `a/y`), eine Datei mit demselben Namen wie ein Ordner, mehr als `MAX_DATEIEN` Einträge."""
    liste = list(pfade)
    if not liste:
        raise UploadFehler("Die Dateiliste ist leer.")
    if len(liste) > MAX_DATEIEN:
        raise UploadFehler(f"Höchstens {MAX_DATEIEN} Dateien je Upload.")
    ergebnis: list[tuple[str, ...]] = []
    dateien: set[str] = set()
    ordner: dict[str, str] = {}  # klein → Originalschreibung
    for p in liste:
        if not isinstance(p, str) or not p:
            raise UploadFehler("Jeder Pfad muss ein nicht leerer Text sein.")
        teile = tuple(p.split("/"))
        if len(teile) > MAX_TIEFE:
            raise UploadFehler(f"Pfad zu tief (höchstens {MAX_TIEFE} Ebenen).")
        for t in teile:
            if t in (".", "..") or not SEGMENT_RE.fullmatch(t):
                raise UploadFehler(
                    "Ungültiger Pfad: erlaubt sind Buchstaben A–Z, Ziffern, Punkt, Bindestrich, "  # noqa: RUF001
                    "Unterstrich und Leerzeichen, getrennt durch „/“, ohne „.“ und „..“.")
        for tiefe in range(1, len(teile)):
            praefix = "/".join(teile[:tiefe])
            klein = praefix.lower()
            if ordner.setdefault(klein, praefix) != praefix:
                raise UploadFehler("Zwei Ordner unterscheiden sich nur in Groß/Kleinschreibung.")
        klein = p.lower()
        if klein in dateien:
            raise UploadFehler("Zwei Dateien unterscheiden sich nur in Groß/Kleinschreibung "
                               "oder sind doppelt.")
        dateien.add(klein)
        ergebnis.append(teile)
    if dateien & set(ordner):
        raise UploadFehler("Ein Name ist zugleich Datei und Ordner.")
    return ergebnis


# --- Ablage über Deskriptoren --------------------------------------------------------------


def _ordner_fd(wurzel: Path, uid: str, teile: tuple[str, ...], *, anlegen: bool) -> int:
    """Deskriptor des Ordners `<wurzel>/<uid>/<teile…>`; jeder Schritt relativ zum vorigen mit
    O_NOFOLLOW. `anlegen` legt fehlende Ordner an (0750 bzw. mit setgid der Wurzel)."""
    if not UPLOAD_ID_RE.fullmatch(uid):
        raise UploadFehler("Upload-ID ungültig.")
    fd = os.open(wurzel, _ORDNER)
    try:
        for t in (uid, *teile):
            if anlegen:
                try:
                    os.mkdir(t, 0o750, dir_fd=fd)
                    neu = True
                except FileExistsError:
                    neu = False
            nfd = os.open(t, _ORDNER, dir_fd=fd)
            os.close(fd)
            fd = nfd
            if anlegen and neu:
                # Die Umask des Webdienstes (0077) ließe den Worker nicht lesen; setgid hält die
                # Gruppe des Upload-Ordners für alles darunter.
                os.fchmod(fd, 0o2750 if os.fstat(fd).st_mode & stat.S_ISGID else 0o750)
        return fd
    except BaseException:
        os.close(fd)
        raise


def anlegen(wurzel: Path, uid: str, pfade: list[tuple[str, ...]]) -> None:
    """Upload-Ordner mit allen Unterordnern und leeren Dateien (0640) anlegen."""
    if not UPLOAD_ID_RE.fullmatch(uid):
        raise UploadFehler("Upload-ID ungültig.")
    wfd = os.open(wurzel, _ORDNER)
    try:
        os.mkdir(uid, 0o750, dir_fd=wfd)  # scheitert, wenn es ihn schon gibt (zufällige ID)
        ufd = os.open(uid, _ORDNER, dir_fd=wfd)
        os.fchmod(ufd, 0o2750 if os.fstat(ufd).st_mode & stat.S_ISGID else 0o750)
        os.close(ufd)
    finally:
        os.close(wfd)
    for teile in pfade:
        fd = _ordner_fd(wurzel, uid, teile[:-1], anlegen=True)
        try:
            dfd = os.open(teile[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                          | os.O_CLOEXEC, 0o640, dir_fd=fd)
            os.fchmod(dfd, 0o640)
            os.close(dfd)
        finally:
            os.close(fd)


def _datei_fd(wurzel: Path, uid: str, teile: tuple[str, ...], flags: int) -> int:
    fd = _ordner_fd(wurzel, uid, teile[:-1], anlegen=False)
    try:
        dfd = os.open(teile[-1], flags | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
    finally:
        os.close(fd)
    st = os.fstat(dfd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(dfd)
        raise UploadFehler("Upload-Datei ist keine reguläre Datei.")
    return dfd


def entfernen(wurzel: Path, uid: str) -> None:
    """Upload-Ordner entfernen (rmtree arbeitet mit Deskriptoren und folgt keinen Symlinks)."""
    if not UPLOAD_ID_RE.fullmatch(uid):
        raise UploadFehler("Upload-ID ungültig.")
    ziel = wurzel / uid
    if ziel.is_symlink():
        ziel.unlink()
    elif ziel.exists():
        shutil.rmtree(ziel)


def content_range(kopf: str | None, groesse: int) -> tuple[int, int]:
    """`bytes a-b/n` → (a, Länge); n muss die angekündigte Größe sein."""
    t = _CONTENT_RANGE_RE.fullmatch(kopf or "")
    if t is None:
        raise UploadFehler("Content-Range fehlt oder ist ungültig (bytes a-b/gesamt).")
    a, b, n = (int(x) for x in t.groups())
    if n != groesse or b < a or b >= n:
        raise UploadFehler("Content-Range passt nicht zur angekündigten Dateigröße.")
    laenge = b - a + 1
    if a % STUECK or laenge != min(STUECK, n - a):
        raise UploadFehler(f"Stücke haben genau {STUECK} Bytes (das letzte den Rest) und beginnen "
                           "an einer Stückgrenze.")
    return a, laenge


def content_digest(kopf: str | None) -> bytes:
    """`Content-Digest: sha-256=:<base64>:` (RFC 9530) → 32 Bytes."""
    for teil in (kopf or "").split(","):
        t = _DIGEST_RE.fullmatch(teil.strip())
        if t is not None:
            return base64.b64decode(t.group(1))
    raise UploadFehler("Content-Digest mit sha-256 fehlt.")


class Schreiber:
    """Ein Stück an Stelle `a` schreiben; Hash läuft mit. Bei Abbruch wird zurückgekürzt."""

    def __init__(self, wurzel: Path, uid: str, teile: tuple[str, ...], a: int) -> None:
        self.fd = _datei_fd(wurzel, uid, teile, os.O_WRONLY)
        self.a = a
        self.pos = a
        self.h = hashlib.sha256()

    def schreiben(self, block: bytes) -> None:
        self.h.update(block)
        ansicht = memoryview(block)
        while ansicht:
            n = os.pwrite(self.fd, ansicht, self.pos)
            self.pos += n
            ansicht = ansicht[n:]

    def fertig(self, digest: bytes) -> bool:
        """True, wenn der Hash passt (dann fsync); sonst wird auf `a` zurückgekürzt."""
        try:
            if self.h.digest() != digest:
                os.ftruncate(self.fd, self.a)
                return False
            os.fsync(self.fd)
            return True
        finally:
            os.close(self.fd)

    def abbrechen(self) -> None:
        try:
            os.ftruncate(self.fd, self.a)
        finally:
            os.close(self.fd)


def sha256_und_kopf(wurzel: Path, uid: str, teile: tuple[str, ...], groesse: int,
                    ) -> tuple[str, bytes]:
    """SHA-256 der ganzen Datei (nur Bytes lesen, nichts deuten) und die ersten Kopfbytes."""
    fd = _datei_fd(wurzel, uid, teile, os.O_RDONLY)
    try:
        if os.fstat(fd).st_size != groesse:
            raise UploadFehler("Datei ist nicht vollständig.")
        h = hashlib.sha256()
        kopf = b""
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                break
            if len(kopf) < KOPF_BYTES:
                kopf += block[:KOPF_BYTES - len(kopf)]
            h.update(block)
        return h.hexdigest(), kopf
    finally:
        os.close(fd)


# --- Signaturen (nur Kopfbytes) --------------------------------------------------------------


def _glb(k: bytes, _n: int) -> bool:
    return len(k) >= 12 and k[:4] == b"glTF" and struct.unpack("<I", k[4:8])[0] == 2


def _ply(k: bytes, _n: int) -> bool:
    return k.startswith((b"ply\n", b"ply\r\n"))


def _spz(k: bytes, _n: int) -> bool:
    # SPZ ist gzip-verpackt; die eigentliche Kennung steckt im komprimierten Strom und wird erst
    # im isolierten Auftrag geprüft (der Webdienst entpackt nichts).
    return k.startswith(b"\x1f\x8b\x08")


def _splat(_k: bytes, n: int) -> bool:
    # Rohformat ohne Kennung: 32 Bytes je Gaussian.
    return n % 32 == 0


def _text(k: bytes, _n: int) -> bool:
    return b"\x00" not in k


SIGNATUREN: dict[str, tuple[str, Any]] = {
    ".glb": ("glb", _glb),
    ".ply": ("ply", _ply),
    ".spz": ("spz", _spz),
    ".splat": ("splat", _splat),
    ".laz": ("laz", lambda k, _n: k.startswith(b"LASF")),
    ".las": ("las", lambda k, _n: k.startswith(b"LASF")),
    ".e57": ("e57", lambda k, _n: k.startswith(b"ASTM-E57")),
    ".obj": ("obj", _text),
    ".mtl": ("mtl", _text),
    ".png": ("png", lambda k, _n: k.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("jpg", lambda k, _n: k.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("jpg", lambda k, _n: k.startswith(b"\xff\xd8\xff")),
}
ARCHIVE = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst")


def endung(name: str) -> str:
    i = name.rfind(".")
    return name[i:].lower() if i > 0 else ""


def pruefe_endungen(pfade: list[tuple[str, ...]]) -> None:
    for teile in pfade:
        e = endung(teile[-1])
        if e in ARCHIVE:
            raise UploadFehler("Archive werden nicht angenommen – den Ordner entpacken und als "  # noqa: RUF001
                               "Ordner hochladen.")
        if e not in SIGNATUREN:
            raise UploadFehler(
                f"Dateityp {e or '(ohne Endung)'} wird nicht angenommen. Erlaubt: "
                f"{', '.join(sorted(SIGNATUREN))}.")


def signatur(name: str, kopf: bytes, groesse: int) -> str:
    """Art der Datei, wenn Endung und Kopfbytes zusammenpassen; sonst UploadFehler."""
    art, pruefen = SIGNATUREN.get(endung(name), (None, None))
    if art is None or pruefen is None or not pruefen(kopf, groesse):
        raise UploadFehler(f"Inhalt passt nicht zur Endung {endung(name) or '(ohne)'}.")
    return str(art)


# --- Datenbank -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Datei:
    nr: int
    pfad: str
    groesse: int
    stand: int
    sha256: str | None
    art: str | None

    @property
    def teile(self) -> tuple[str, ...]:
        return tuple(self.pfad.split("/"))


@dataclass(frozen=True)
class Upload:
    id: str
    erstellt_von: int | None
    erstellt_am: int
    geaendert_am: int
    status: str
    groesse: int
    kanal: str
    dateien: tuple[Datei, ...]

    def als_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "groesse": self.groesse,
            "erstelltAm": self.erstellt_am, "erstelltVon": self.erstellt_von,
            "geaendertAm": self.geaendert_am, "ablaufAm": self.geaendert_am + FRIST_S,
            "kanal": self.kanal, "stueckBytes": STUECK,
            "stand": sum(d.stand for d in self.dateien),
            "dateien": [{"nr": d.nr, "pfad": d.pfad, "groesse": d.groesse, "stand": d.stand,
                         "sha256": d.sha256, "art": d.art} for d in self.dateien],
        }


def neue_id() -> str:
    return secrets.token_hex(16)


def belegt(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COALESCE(SUM(groesse), 0) FROM uploads").fetchone()[0])


def eintragen(conn: sqlite3.Connection, uid: str, nutzer_id: int, kanal: str,
              dateien: list[tuple[str, int]], now: int) -> None:
    conn.execute(
        "INSERT INTO uploads (id, erstellt_von, erstellt_am, geaendert_am, status, groesse, kanal)"
        " VALUES (?,?,?,?,?,?,?)",
        (uid, nutzer_id, now, now, "offen", sum(g for _p, g in dateien), kanal))
    conn.executemany(
        "INSERT INTO upload_dateien (upload_id, nr, pfad, groesse) VALUES (?,?,?,?)",
        [(uid, nr, p, g) for nr, (p, g) in enumerate(dateien)])


def lesen(conn: sqlite3.Connection, uid: str) -> Upload | None:
    if not UPLOAD_ID_RE.fullmatch(uid):
        return None
    r = conn.execute("SELECT * FROM uploads WHERE id=?", (uid,)).fetchone()
    if r is None:
        return None
    rows = conn.execute("SELECT * FROM upload_dateien WHERE upload_id=? ORDER BY nr", (uid,))
    dateien = tuple(Datei(nr=int(d["nr"]), pfad=d["pfad"], groesse=int(d["groesse"]),
                          stand=int(d["stand"]), sha256=d["sha256"], art=d["art"])
                    for d in rows.fetchall())
    return Upload(id=r["id"], erstellt_von=r["erstellt_von"], erstellt_am=int(r["erstellt_am"]),
                  geaendert_am=int(r["geaendert_am"]), status=r["status"],
                  groesse=int(r["groesse"]), kanal=r["kanal"], dateien=dateien)


def liste(conn: sqlite3.Connection) -> list[Upload]:
    ids = [r[0] for r in conn.execute("SELECT id FROM uploads ORDER BY erstellt_am DESC, id")]
    return [u for u in (lesen(conn, i) for i in ids) if u is not None]


def stand_setzen(conn: sqlite3.Connection, uid: str, nr: int, alt: int, neu: int,
                 now: int) -> bool:
    """Stand nur weiterschieben, wenn er noch `alt` ist (doppeltes PUT schreibt nicht zweimal)."""
    cur = conn.execute(
        "UPDATE upload_dateien SET stand=? WHERE upload_id=? AND nr=? AND stand=?",
        (neu, uid, nr, alt))
    if cur.rowcount != 1:
        return False
    conn.execute("UPDATE uploads SET geaendert_am=? WHERE id=?", (now, uid))
    return True


def abschliessen(conn: sqlite3.Connection, uid: str, pruef: dict[int, tuple[str, str]],
                 now: int) -> None:
    for nr, (sha, art) in pruef.items():
        conn.execute("UPDATE upload_dateien SET sha256=?, art=? WHERE upload_id=? AND nr=?",
                     (sha, art, uid, nr))
    conn.execute("UPDATE uploads SET status='abgeschlossen', geaendert_am=? WHERE id=?",
                 (now, uid))


def austragen(conn: sqlite3.Connection, uid: str) -> None:
    conn.execute("DELETE FROM upload_dateien WHERE upload_id=?", (uid,))
    conn.execute("DELETE FROM uploads WHERE id=?", (uid,))


def abgelaufen(conn: sqlite3.Connection, now: int | None = None) -> list[str]:
    jetzt = int(time.time()) if now is None else now
    return [r[0] for r in conn.execute(
        "SELECT id FROM uploads WHERE geaendert_am < ?", (jetzt - FRIST_S,))]


def ist_sha256(wert: Any) -> bool:
    return isinstance(wert, str) and _SHA256_RE.fullmatch(wert) is not None
