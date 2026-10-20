"""Bereitstellung von setfacl/getfacl für die Probe des Freischalt-Skripts (`deploy_probe`).

Ohne Netz: das .deb wird im Test gebaut (ar + data.tar.xz) und als lokale Quelle übergeben.
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from scanview_nachweise import deploy_probe
from scanview_nachweise.fehler import NachweisFehler

ECHTES_LOCK = Path(__file__).resolve().parents[1] / "werkzeuge" / "deploy-probe" / \
    "acl.lock.json"


def _ar(eintraege: list[tuple[str, bytes]]) -> bytes:
    aus = bytearray(b"!<arch>\n")
    for name, inhalt in eintraege:
        kopf = (f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(inhalt):<10}`\n")
        aus += kopf.encode("ascii") + inhalt
        if len(inhalt) % 2:
            aus += b"\n"
    return bytes(aus)


def _deb(dateien: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    puffer = io.BytesIO()
    with tarfile.open(fileobj=puffer, mode="w:xz") as tar:
        for name, inhalt in dateien.items():
            info = tarfile.TarInfo(f"./{name}")
            info.size = len(inhalt)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(inhalt))
        if symlink is not None:
            info = tarfile.TarInfo(f"./{symlink}")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
    return _ar([("debian-binary", b"2.0\n"), ("control.tar.xz", b""),
                ("data.tar.xz", puffer.getvalue())])


def _lock(tmp_path: Path, deb: bytes, **mehr: object) -> Path:
    ordner = tmp_path / "deploy-probe"
    ordner.mkdir()
    daten = {"paket": "acl", "version": "1", "architektur": "amd64",
             "url": "https://example.invalid/acl.deb",
             "sha256": hashlib.sha256(deb).hexdigest(),
             "dateien": ["usr/bin/getfacl", "usr/bin/setfacl"], **mehr}
    (ordner / deploy_probe.LOCK).write_text(json.dumps(daten), encoding="utf-8")
    return ordner


GUT = {"usr/bin/getfacl": b"GET", "usr/bin/setfacl": b"SET", "usr/bin/chacl": b"CH"}


def test_echtes_lock_gueltig() -> None:
    lock = deploy_probe.lade_lock(ECHTES_LOCK.parent)
    assert lock.sha256 == "fa89b05576c590211c8e8965761fc76b830e3eeb3dd100ac59c8440f11ea18fd"
    assert lock.url.startswith("https://deb.debian.org/")


def test_bereitstellen_und_bereit(tmp_path: Path) -> None:
    deb = _deb(GUT)
    ordner = _lock(tmp_path, deb)
    (tmp_path / "acl.deb").write_bytes(deb)
    assert deploy_probe.bereit(ordner) is None
    bin_ = deploy_probe.bereitstellen(ordner, quelle=tmp_path / "acl.deb")
    assert sorted(p.name for p in bin_.iterdir() if not p.name.startswith(".")) == [
        "getfacl", "setfacl"]
    assert (bin_ / "setfacl").read_bytes() == b"SET"
    assert (bin_ / "setfacl").stat().st_mode & 0o777 == 0o755
    assert deploy_probe.bereit(ordner) == bin_
    # Zweiter Aufruf: nichts zu tun.
    assert deploy_probe.bereitstellen(ordner, quelle=tmp_path / "fehlt.deb") == bin_


def test_falsche_pruefsumme_bricht_ab(tmp_path: Path) -> None:
    deb = _deb(GUT)
    ordner = _lock(tmp_path, deb, sha256="0" * 64)
    (tmp_path / "acl.deb").write_bytes(deb)
    with pytest.raises(NachweisFehler, match="deploy-probe"):
        deploy_probe.bereitstellen(ordner, quelle=tmp_path / "acl.deb")
    assert deploy_probe.bereit(ordner) is None
    assert not (ordner / "bin").exists()


def test_datei_fehlt_im_paket(tmp_path: Path) -> None:
    deb = _deb({"usr/bin/getfacl": b"GET"})
    ordner = _lock(tmp_path, deb)
    (tmp_path / "acl.deb").write_bytes(deb)
    with pytest.raises(NachweisFehler, match="setfacl"):
        deploy_probe.bereitstellen(ordner, quelle=tmp_path / "acl.deb")


def test_symlink_statt_datei_abgelehnt(tmp_path: Path) -> None:
    deb = _deb({"usr/bin/getfacl": b"GET"}, symlink="usr/bin/setfacl")
    ordner = _lock(tmp_path, deb)
    (tmp_path / "acl.deb").write_bytes(deb)
    with pytest.raises(NachweisFehler, match="setfacl"):
        deploy_probe.bereitstellen(ordner, quelle=tmp_path / "acl.deb")


def test_veraltete_marke_nicht_bereit(tmp_path: Path) -> None:
    deb = _deb(GUT)
    ordner = _lock(tmp_path, deb)
    (tmp_path / "acl.deb").write_bytes(deb)
    deploy_probe.bereitstellen(ordner, quelle=tmp_path / "acl.deb")
    daten = json.loads((ordner / deploy_probe.LOCK).read_text(encoding="utf-8"))
    daten["version"] = "2"
    (ordner / deploy_probe.LOCK).write_text(json.dumps(daten), encoding="utf-8")
    assert deploy_probe.bereit(ordner) is None
    # Ein vorhandener, nicht passender bin/-Ordner wird nicht still überschrieben.
    with pytest.raises(NachweisFehler, match="Marke"):
        deploy_probe.bereitstellen(ordner, quelle=tmp_path / "acl.deb")


@pytest.mark.parametrize("feld", ["url", "sha256", "dateien"])
def test_lock_pflichtfelder(tmp_path: Path, feld: str) -> None:
    ordner = _lock(tmp_path, b"x")
    daten = json.loads((ordner / deploy_probe.LOCK).read_text(encoding="utf-8"))
    del daten[feld]
    (ordner / deploy_probe.LOCK).write_text(json.dumps(daten), encoding="utf-8")
    with pytest.raises(NachweisFehler, match=feld):
        deploy_probe.lade_lock(ordner)
