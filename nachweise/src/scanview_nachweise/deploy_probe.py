"""setfacl/getfacl für die Probe des Freischalt-Skripts (Test `test_paket_freischalten.py`).

Das gepinnte Probe-Image (`deploy-probe` in `werkzeuge.json`) bringt `libacl1`, aber nicht die
Werkzeuge `setfacl`/`getfacl` mit. Die Probe soll ACLs echt setzen und lesen, nicht nur
nachahmen. Deshalb wird das Debian-Paket `acl` in genau der Version der Bibliothek im Image
einmalig geladen (Online-Schritt, wie die übrigen Werkzeugordner), gegen den SHA-256 aus
`werkzeuge/deploy-probe/acl.lock.json` geprüft (der Wert stammt aus dem Packages-Index von
Debian) und nur `usr/bin/getfacl`/`usr/bin/setfacl` werden nach `werkzeuge/deploy-probe/bin/`
entpackt (gitignoriert). Dazu kommt eine Marke mit dem SHA-256 des Lockfiles; ein `bin/` ohne
passende Marke wird nicht benutzt und nicht still überschrieben.

    uv run python -m scanview_nachweise.deploy_probe      # im Ordner nachweise/

Nur Standardbibliothek.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .fehler import NachweisFehler

REGEL = "deploy-probe"
LOCK = "acl.lock.json"
MARKE = ".scanview-bereitstellung.json"
ORDNER = Path(__file__).resolve().parents[2] / "werkzeuge" / "deploy-probe"
MAX_DEB = 4 * 1024 * 1024


@dataclass(frozen=True)
class Lock:
    url: str
    sha256: str
    dateien: tuple[str, ...]
    lock_sha256: str


def lade_lock(ordner: Path) -> Lock:
    pfad = ordner / LOCK
    try:
        roh = pfad.read_bytes()
        daten = json.loads(roh)
    except (OSError, ValueError) as e:
        raise NachweisFehler(REGEL, f"{pfad} nicht lesbar: {e}") from None
    if not isinstance(daten, dict):
        raise NachweisFehler(REGEL, f"{pfad}: kein JSON-Objekt")
    for feld in ("url", "sha256", "dateien"):
        if feld not in daten:
            raise NachweisFehler(REGEL, f"{pfad}: Feld „{feld}“ fehlt")
    url, sha, dateien = daten["url"], daten["sha256"], daten["dateien"]
    if not isinstance(url, str) or not url.startswith("https://"):
        raise NachweisFehler(REGEL, f"{pfad}: „url“ muss https:// sein")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise NachweisFehler(REGEL, f"{pfad}: „sha256“ ungültig")
    if (not isinstance(dateien, list) or not dateien
            or not all(isinstance(d, str) and d.startswith("usr/bin/") and ".." not in d
                       for d in dateien)):
        raise NachweisFehler(REGEL, f"{pfad}: „dateien“ muss eine Liste von usr/bin/… sein")
    return Lock(url, sha, tuple(dateien), hashlib.sha256(roh).hexdigest())


def bereit(ordner: Path = ORDNER) -> Path | None:
    """`bin/`, wenn es zur aktuellen Lock-Datei passt, sonst None."""
    try:
        lock = lade_lock(ordner)
        marke = json.loads((ordner / "bin" / MARKE).read_text(encoding="utf-8"))
    except (NachweisFehler, OSError, ValueError):
        return None
    bin_ = ordner / "bin"
    if marke != {"lock_sha256": lock.lock_sha256}:
        return None
    if not all((bin_ / Path(d).name).is_file() for d in lock.dateien):
        return None
    return bin_


def _data_tar(deb: bytes) -> bytes:
    """Das `data.tar.*`-Mitglied eines .deb (ar-Archiv)."""
    if not deb.startswith(b"!<arch>\n"):
        raise NachweisFehler(REGEL, "kein .deb (ar-Kopf fehlt)")
    pos = 8
    while pos + 60 <= len(deb):
        kopf = deb[pos:pos + 60]
        name = kopf[:16].decode("ascii").strip().rstrip("/")
        groesse = int(kopf[48:58].decode("ascii").strip())
        inhalt = deb[pos + 60:pos + 60 + groesse]
        if name.startswith("data.tar"):
            return inhalt
        pos += 60 + groesse + (groesse % 2)
    raise NachweisFehler(REGEL, "data.tar fehlt im .deb")


def _entpacken(deb: bytes, dateien: tuple[str, ...], ziel: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(_data_tar(deb)), mode="r:*") as tar:
        mitglieder = {m.name.removeprefix("./"): m for m in tar.getmembers()}
        for datei in dateien:
            m = mitglieder.get(datei)
            if m is None or not m.isreg():
                raise NachweisFehler(REGEL, f"{datei} fehlt im Paket oder ist keine Datei")
            f = tar.extractfile(m)
            assert f is not None  # isreg() geprüft
            aus = ziel / Path(datei).name
            aus.write_bytes(f.read())
            aus.chmod(0o755)


def _laden(url: str, quelle: Path | None) -> bytes:
    if quelle is not None:
        return quelle.read_bytes()
    # Nur https (lade_lock); der SHA-256 wird danach gegen das Lock geprüft.
    with urllib.request.urlopen(url, timeout=60) as antwort:
        daten: bytes = antwort.read(MAX_DEB + 1)
    if len(daten) > MAX_DEB:
        raise NachweisFehler(REGEL, f"{url}: größer als {MAX_DEB} Byte")
    return daten


def bereitstellen(ordner: Path = ORDNER, *, quelle: Path | None = None) -> Path:
    """Lädt (oder liest `quelle`), prüft und entpackt; gibt `bin/` zurück. Netz nur, wenn
    `bin/` fehlt und keine `quelle` angegeben ist."""
    lock = lade_lock(ordner)
    fertig = bereit(ordner)
    if fertig is not None:
        return fertig
    bin_ = ordner / "bin"
    if bin_.exists():
        raise NachweisFehler(REGEL, f"{bin_} existiert ohne passende Marke — von Hand prüfen "
                                    "und entfernen")
    deb = _laden(lock.url, quelle)
    ist = hashlib.sha256(deb).hexdigest()
    if ist != lock.sha256:
        raise NachweisFehler(REGEL, f"SHA-256 des Pakets {ist} ≠ {lock.sha256} (Lock)")
    with tempfile.TemporaryDirectory(dir=ordner) as t:
        neu = Path(t) / "bin"
        neu.mkdir()
        _entpacken(deb, lock.dateien, neu)
        (neu / MARKE).write_text(json.dumps({"lock_sha256": lock.lock_sha256}),
                                 encoding="utf-8")
        shutil.move(str(neu), bin_)
    return bin_


def main() -> None:
    try:
        print(bereitstellen())
    except NachweisFehler as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
