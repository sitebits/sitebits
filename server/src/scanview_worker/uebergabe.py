"""`scanview-uebergabe`: fertige Importpakete in den Eingang des Freischalt-Skripts legen.

Läuft als eigener Systemnutzer `scanview-uebergabe` (nur Gruppe `scanview-deploy`, nie
`scanview`) — für das unveränderte Freischalt-Skript ist er ein gewöhnlicher Hochladender. Das
root-Skript `scanview-import-uebergabe` ruft ihn per `runuser` auf und startet danach selbst das
Freischalt-Skript; root liest dabei nie Dateiinhalte aus der Übergabe.

    scanview-uebergabe kopieren <projectId>       Übergabe → Eingang (neue Inodes)
    scanview-uebergabe melden <projectId> <WORT>  Ergebnis für den Worker ablegen
    scanview-uebergabe aufraeumen                 alte Meldungen und eigene Eingangsreste weg

Das Paket in der Übergabe hat ein Auftrag gebaut, der fremde Dateien gelesen hat: es ist fremd.
Kopiert werden nur Ordner und reguläre Dateien mit genau einem Namen, über Deskriptoren ohne
Symlink-Folgen, mit Grenzen für Anzahl, Tiefe und Größe. Feste Pfade, keine Umgebung.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
from pathlib import Path

UEBERGABE = Path("/srv/scanview/uebergabe")
MELDUNGEN = Path("/srv/scanview/uebergabe-meldungen")
EINGANG = Path("/srv/scanview/eingang")

ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
WORT_RE = re.compile(r"[A-Z_]{2,32}")
MAX_DATEIEN = 20_000
MAX_TIEFE = 12
MAX_BYTES = 200 * 1024**3
NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,200}")
_LESEN = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_ORDNER = _LESEN | os.O_DIRECTORY
_NEU = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


class Abgelehnt(Exception):
    pass


def _id(pid: str) -> str:
    if not ID_RE.fullmatch(pid):
        raise Abgelehnt("projectId ungültig")
    return pid


def _kopiere_ordner(qfd: int, zfd: int, tiefe: int, zaehler: list[int]) -> None:
    if tiefe > MAX_TIEFE:
        raise Abgelehnt("Paket zu tief verschachtelt")
    for name in sorted(os.listdir(qfd)):
        if not NAME_RE.fullmatch(name) or name in (".", ".."):
            raise Abgelehnt("unerlaubter Dateiname im Paket")
        st = os.lstat(name, dir_fd=qfd)
        if stat.S_ISDIR(st.st_mode):
            os.mkdir(name, 0o700, dir_fd=zfd)
            nq = os.open(name, _ORDNER, dir_fd=qfd)
            try:
                nz = os.open(name, _ORDNER, dir_fd=zfd)
                try:
                    _kopiere_ordner(nq, nz, tiefe + 1, zaehler)
                finally:
                    os.close(nz)
            finally:
                os.close(nq)
            continue
        if not stat.S_ISREG(st.st_mode):
            raise Abgelehnt("Symlink, FIFO oder Gerät im Paket")
        src = os.open(name, _LESEN, dir_fd=qfd)
        try:
            sst = os.fstat(src)
            if not stat.S_ISREG(sst.st_mode) or sst.st_nlink != 1:
                raise Abgelehnt("Hardlink oder Sonderdatei im Paket")
            zaehler[0] += 1
            zaehler[1] += sst.st_size
            if zaehler[0] > MAX_DATEIEN or zaehler[1] > MAX_BYTES:
                raise Abgelehnt("Paket zu groß")
            dst = os.open(name, _NEU, 0o600, dir_fd=zfd)
            try:
                rest = sst.st_size
                while rest:
                    block = os.read(src, min(1 << 20, rest))
                    if not block:
                        raise Abgelehnt("Datei während des Kopierens gekürzt")
                    rest -= len(block)
                    ansicht = memoryview(block)
                    while ansicht:
                        ansicht = ansicht[os.write(dst, ansicht):]
                os.fsync(dst)
            finally:
                os.close(dst)
            if os.fstat(src).st_size != sst.st_size:
                raise Abgelehnt("Datei während des Kopierens verändert")
        finally:
            os.close(src)


def kopieren(pid: str, *, uebergabe: Path | None = None, eingang: Path | None = None) -> None:
    """`<uebergabe>/<pid>/` → `<eingang>/<pid>/`; bei jedem Fehler bleibt im Eingang nichts."""
    _id(pid)
    uebergabe = UEBERGABE if uebergabe is None else uebergabe
    eingang = EINGANG if eingang is None else eingang
    wfd = os.open(uebergabe, _ORDNER)
    try:
        qfd = os.open(pid, _ORDNER, dir_fd=wfd)
    except OSError:
        raise Abgelehnt("Paket in der Übergabe fehlt oder ist kein Ordner") from None
    finally:
        os.close(wfd)
    efd = os.open(eingang, _ORDNER)
    try:
        try:
            os.mkdir(pid, 0o700, dir_fd=efd)
        except FileExistsError:
            raise Abgelehnt("Eingang für dieses Projekt ist belegt") from None
        try:
            zfd = os.open(pid, _ORDNER, dir_fd=efd)
            try:
                if os.fstat(zfd).st_uid != os.geteuid():
                    raise Abgelehnt("Eingangsordner gehört nicht der Übergabe")
                zaehler = [0, 0]
                _kopiere_ordner(qfd, zfd, 0, zaehler)
                if zaehler[0] == 0:
                    raise Abgelehnt("Paket ist leer")
            finally:
                os.close(zfd)
        except BaseException:
            _weg(eingang / pid)
            raise
    finally:
        os.close(efd)
        os.close(qfd)


def _weg(pfad: Path) -> None:
    if pfad.is_symlink():
        pfad.unlink()
    elif pfad.exists():
        shutil.rmtree(pfad)


def melden(pid: str, wort: str, *, meldungen: Path | None = None) -> None:
    _id(pid)
    meldungen = MELDUNGEN if meldungen is None else meldungen
    if not WORT_RE.fullmatch(wort):
        raise Abgelehnt("Meldewort ungültig")
    mfd = os.open(meldungen, _ORDNER)
    try:
        neu = f".{pid}.neu"
        try:
            os.unlink(neu, dir_fd=mfd)
        except FileNotFoundError:
            pass
        fd = os.open(neu, _NEU, 0o640, dir_fd=mfd)
        try:
            os.fchmod(fd, 0o640)
            os.write(fd, f"{wort}\n".encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(neu, pid, src_dir_fd=mfd, dst_dir_fd=mfd)
    finally:
        os.close(mfd)


def aufraeumen(*, uebergabe: Path | None = None, meldungen: Path | None = None,
               eingang: Path | None = None) -> None:
    """Meldungen ohne Paket in der Übergabe entfernen (der Worker hat das Ergebnis gelesen und
    sein Paket weggeräumt) und eigene, liegen gebliebene Eingangsordner löschen."""
    uebergabe = UEBERGABE if uebergabe is None else uebergabe
    meldungen = MELDUNGEN if meldungen is None else meldungen
    eingang = EINGANG if eingang is None else eingang
    for m in meldungen.iterdir():
        if m.name.startswith(".") or (ID_RE.fullmatch(m.name) and not (
                (uebergabe / m.name).is_dir() and not (uebergabe / m.name).is_symlink())):
            m.unlink(missing_ok=True)
    for e in eingang.iterdir():
        if ID_RE.fullmatch(e.name) and not e.is_symlink() and e.is_dir() \
                and e.lstat().st_uid == os.geteuid():
            _weg(e)


def main(argv: list[str] | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    try:
        if len(a) == 2 and a[0] == "kopieren":
            kopieren(a[1])
        elif len(a) == 3 and a[0] == "melden":
            melden(a[1], a[2])
        elif a == ["aufraeumen"]:
            aufraeumen()
        else:
            print("Aufruf: scanview-uebergabe kopieren <projectId> | melden <projectId> <WORT>"
                  " | aufraeumen", file=sys.stderr)
            return 2
    except (Abgelehnt, OSError) as e:
        text = str(e) if isinstance(e, Abgelehnt) else (e.strerror or "Fehler")
        print(f"scanview-uebergabe: {text}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
