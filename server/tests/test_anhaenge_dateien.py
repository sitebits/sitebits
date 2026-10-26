"""F7: Dateiseite der Anhänge im Webdienst — Signatur, Grenze beim Empfang, Übernahme der
Worker-Ausgabe als fremde Eingabe, Ablage nur unter dem eigenen Hash."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from scanview_server import anhaenge as A

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
PDF = b"%PDF-1.7\n" + b"x" * 20


@pytest.mark.parametrize("ct,kopf,art", [
    ("image/jpeg", JPEG, "bild"), ("image/png", PNG, "bild"),
    ("application/pdf; charset=binary", PDF, "pdf"),
])
def test_signatur_passt(ct: str, kopf: bytes, art: str) -> None:
    assert A.typ_pruefen(ct, kopf) == art


@pytest.mark.parametrize("ct,kopf,text", [
    ("image/svg+xml", b"<svg xmlns=", A.NUR_TYPEN),
    ("text/html", b"<!DOCTYPE html>", A.NUR_TYPEN),
    ("image/jpeg", b"<svg xmlns=", A.SIGNATUR_FALSCH),
    ("image/png", JPEG, A.SIGNATUR_FALSCH),
    ("application/pdf", b"<html>%PDF-", A.SIGNATUR_FALSCH),
    ("image/jpeg", b"\x00\x00\x00\x18ftypheic", A.HEIC_TEXT),
    ("image/heic", b"\x00\x00\x00\x18ftypheic", A.HEIC_TEXT),
    ("application/octet-stream", JPEG, A.NUR_TYPEN),
])
def test_signatur_abgelehnt(ct: str, kopf: bytes, text: str) -> None:
    with pytest.raises(A.AnhangFehler, match=re.escape(text[:20])) as e:
        A.typ_pruefen(ct, kopf)
    assert e.value.status == 415


async def _strom(*teile: bytes) -> AsyncIterator[bytes]:
    for t in teile:
        yield t


def test_empfang_mit_grenze(tmp_path: Path) -> None:
    n, sha, kopf = asyncio.run(A.empfangen(_strom(JPEG[:3], JPEG[3:]), tmp_path / "a"))
    assert (n, sha, kopf) == (len(JPEG), hashlib.sha256(JPEG).hexdigest(), JPEG[:16])
    gross = b"x" * (1024 * 1024)
    with pytest.raises(A.AnhangFehler) as e:
        asyncio.run(A.empfangen(_strom(*([gross] * 21)), tmp_path / "b"))
    assert e.value.status == 413 and not (tmp_path / "b").exists()
    with pytest.raises(A.AnhangFehler, match="leer"):
        asyncio.run(A.empfangen(_strom(), tmp_path / "c"))


def test_empfang_folgt_keinem_symlink(tmp_path: Path) -> None:
    (tmp_path / "ziel").write_bytes(b"alt")
    (tmp_path / "a").symlink_to(tmp_path / "ziel")
    with pytest.raises(FileExistsError):
        asyncio.run(A.empfangen(_strom(JPEG), tmp_path / "a"))
    assert (tmp_path / "ziel").read_bytes() == b"alt"


def _ausgabe(wurzel: Path, aid: str, bild: bytes = JPEG, info: object = None) -> Path:
    d = wurzel / "geprueft" / aid
    d.mkdir(parents=True)
    (d / "bild.jpg").write_bytes(bild)
    (d / "info.json").write_text(json.dumps(info if info is not None
                                            else {"breite": 3, "hoehe": 2,
                                                  "aufnahme": "2026-10-21T14:05:09"}))
    (wurzel / "eingang").mkdir(exist_ok=True)
    (wurzel / "eingang" / aid).write_bytes(b"roh")
    return d


def test_bild_uebernehmen(tmp_path: Path) -> None:
    aid = A.neue_id()
    _ausgabe(tmp_path, aid)
    ab = A.bild_uebernehmen(tmp_path, "ost", aid)
    assert (ab.breite, ab.hoehe, ab.aufnahme) == (3, 2, "2026-10-21T14:05:09")
    assert ab.sha256 == hashlib.sha256(JPEG).hexdigest()
    assert A.ablage_pfad(tmp_path, "ost", ab.sha256, "bild") is not None
    assert not (tmp_path / "eingang" / aid).exists()
    assert A.belegt(tmp_path, "ost") == len(JPEG)


@pytest.mark.parametrize("bild,info", [
    (b"<svg/>", None),
    (JPEG, {"breite": 0, "hoehe": 2}),
    (JPEG, {"breite": 3, "hoehe": 2, "name": "x"}),
    (JPEG, {"breite": 3, "hoehe": 2, "aufnahme": "../.."}),
])
def test_bild_uebernehmen_lehnt_fremdes_ab(tmp_path: Path, bild: bytes, info: object) -> None:
    aid = A.neue_id()
    _ausgabe(tmp_path, aid, bild, info)
    with pytest.raises(ValueError):
        A.bild_uebernehmen(tmp_path, "ost", aid)
    assert A.belegt(tmp_path, "ost") == 0


def test_bild_uebernehmen_symlink_und_hardlink(tmp_path: Path) -> None:
    aid = A.neue_id()
    d = _ausgabe(tmp_path, aid)
    (d / "bild.jpg").unlink()
    (tmp_path / "geheim").write_bytes(JPEG)
    (d / "bild.jpg").symlink_to(tmp_path / "geheim")
    with pytest.raises(OSError):
        A.bild_uebernehmen(tmp_path, "ost", aid)
    (d / "bild.jpg").unlink()
    os.link(tmp_path / "geheim", d / "bild.jpg")
    with pytest.raises(ValueError):
        A.bild_uebernehmen(tmp_path, "ost", aid)


def test_pdf_ablegen_unveraendert(tmp_path: Path) -> None:
    aid = A.neue_id()
    (tmp_path / "eingang").mkdir()
    (tmp_path / "eingang" / aid).write_bytes(PDF)
    ab = A.pdf_ablegen(tmp_path, "ost", aid)
    p = A.ablage_pfad(tmp_path, "ost", ab.sha256, "pdf")
    assert p is not None and p.read_bytes() == PDF


@pytest.mark.parametrize("roh,erwartet", [
    ("C:\\Users\\x\\Foto 1.jpg", "Foto 1.jpg"), ("../../etc/passwd", "passwd"),
    ("<script>.jpg", "_script_.jpg"), ("", None), ("...", None), ("a" * 300, "a" * 120),
])
def test_dateiname(roh: str, erwartet: str | None) -> None:
    assert A.dateiname(roh) == erwartet


def test_ablage_pfad_nur_mit_hash(tmp_path: Path) -> None:
    assert A.ablage_pfad(tmp_path, "ost", "../x", "bild") is None
    assert A.ablage_pfad(tmp_path, "ost", "a" * 64, "html") is None


def test_rechte_trotz_umask_077(tmp_path: Path) -> None:
    alt = os.umask(0o077)  # wie die Unit des Webdienstes
    try:
        d = A.ordner(tmp_path, "eingang")
        asyncio.run(A.empfangen(_strom(JPEG), d / "a"))
    finally:
        os.umask(alt)
    assert oct(d.stat().st_mode & 0o777) == "0o750"
    assert oct((d / "a").stat().st_mode & 0o777) == "0o640"
