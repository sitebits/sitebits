"""Lizenzbeilage der PDAL-Umgebung auf dem Host (`scanview-import pdal-lizenzen`, Weg 3).

Synthetischer Paket-Cache mit zwei Paketen und einer Ergänzung: die Datei trägt den Lock-SHA und
einen Abschnitt je Paket; alles Unpassende bricht ab (kein Worker ohne Beilage)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from scanview_import import cli
from scanview_import.pdal_lizenzen import LizenzFehler, erzeuge, schreibe

LOCK = b"version: 7\n# synthetisches Lock\n"
TEXT_A = "Permission is hereby granted, free of charge, to any person obtaining a copy.\n"
TEXT_GPL = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007 (synthetischer Auszug)\n"
KOPF_H = "/*\n** The author disclaims copyright to this source code. (synthetisch)\n*/\nint x;\n"


def _paket(name: str, version: str = "1.0", build: str = "h0_0") -> dict[str, Any]:
    return {"name": name, "version": version, "build": build, "subdir": "linux-64",
            "url": f"https://conda.anaconda.org/conda-forge/linux-64/{name}-{version}-{build}.conda",
            "sha256": hashlib.sha256(name.encode()).hexdigest(), "md5": "0" * 32,
            "lizenz": "MIT", "rezept": {"feedstock": f"https://github.com/conda-forge/{name}-feedstock",
                                       "commit": "a" * 40},
            "upstream": {"home": "https://example.org", "dev": "",
                         "url": "https://example.org/src.tar.gz", "sha256": "b" * 64},
            "lizenzdateien": [], "ergaenzungen": []}


def _cache(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    cache = tmp_path / "cache"
    a, b = _paket("liba"), _paket("libb", "2.0", "h1_1")
    a["lizenzdateien"] = ["info/licenses/LICENSE"]
    b["lizenzdateien"] = ["info/licenses/COPYING"]
    a["ergaenzungen"] = [{"paket": "libb", "datei": "include/b.h",
                          "erklaerung": "Segenstext (Kopf von b.h)"}]
    for p, dateien in ((a, {"info/licenses/LICENSE": TEXT_A}),
                       (b, {"info/licenses/COPYING": TEXT_GPL, "include/b.h": KOPF_H})):
        ordner = cache / "pkgs" / f"{p['name']}-{p['version']}-{p['build']}"
        (ordner / "info").mkdir(parents=True)
        (ordner / "info" / "index.json").write_text(json.dumps(
            {"name": p["name"], "version": p["version"], "build": p["build"]}))
        for rel, text in dateien.items():
            (ordner / rel).parent.mkdir(parents=True, exist_ok=True)
            (ordner / rel).write_text(text)
    quellen = {"lockSha256": hashlib.sha256(LOCK).hexdigest(), "pakete": [a, b]}
    return cache, quellen


def test_beilage_mit_lock_sha_und_abschnitt_je_paket(tmp_path: Path) -> None:
    cache, quellen = _cache(tmp_path)
    text = erzeuge(LOCK, quellen, cache)
    assert f"Lock SHA-256: {hashlib.sha256(LOCK).hexdigest()}\n" in text
    assert text.count("\n" + "=" * 78 + "\nliba 1.0 (Build h0_0)\nLizenz laut Paket: MIT\n") == 1
    assert text.count("\n" + "=" * 78 + "\nlibb 2.0 (Build h1_1)\n") == 1
    abschnitt_a = text[text.index("\nliba 1.0"):text.index("\nlibb 2.0")]
    assert TEXT_A.strip() in abschnitt_a
    # Ergänzung: nur der Kopfkommentar der Header-Datei, mit Herkunft und Erklärung.
    assert "--- Ergänzung: libb/include/b.h — Segenstext (Kopf von b.h) ---" in abschnitt_a
    assert "disclaims copyright" in abschnitt_a and "int x;" not in abschnitt_a
    assert f"SHA-256: {quellen['pakete'][1]['sha256']}\n" in text
    assert "Rezept: https://github.com/conda-forge/libb-feedstock" in text


def _ohne_text(q: dict[str, Any], c: Path) -> None:
    (c / "pkgs/libb-2.0-h1_1/info/licenses/COPYING").write_text("kurz\n")


def _fremdes_lock(q: dict[str, Any], c: Path) -> None:
    q["lockSha256"] = "c" * 64


def _index_falsch(q: dict[str, Any], c: Path) -> None:
    (c / "pkgs/liba-1.0-h0_0/info/index.json").write_text(
        json.dumps({"name": "liba", "version": "9.9", "build": "h0_0"}))


def _paket_fehlt(q: dict[str, Any], c: Path) -> None:
    (c / "pkgs/libb-2.0-h1_1").rename(c / "pkgs/weg")


def _datei_fehlt(q: dict[str, Any], c: Path) -> None:
    q["pakete"][0]["lizenzdateien"].append("info/licenses/FEHLT")


def _pfad_hinaus(q: dict[str, Any], c: Path) -> None:
    q["pakete"][0]["lizenzdateien"] = ["../libb-2.0-h1_1/info/licenses/COPYING"]


def _symlink(q: dict[str, Any], c: Path) -> None:
    ziel = c / "pkgs/liba-1.0-h0_0/info/licenses/LICENSE"
    ziel.unlink()
    os.symlink("/etc/hostname", ziel)


def _ergaenzung_fremd(q: dict[str, Any], c: Path) -> None:
    q["pakete"][0]["ergaenzungen"][0]["paket"] = "nicht-im-lock"


@pytest.mark.parametrize(("aendern", "meldung"), [
    (_ohne_text, "libb: kein Lizenztext"),
    (_fremdes_lock, "gehört nicht zu diesem Lock"),
    (_index_falsch, "index.json passt nicht"),
    (_paket_fehlt, "libb: Paket fehlt im Cache"),
    (_datei_fehlt, "FEHLT nicht lesbar"),
    (_pfad_hinaus, "verlässt den Paketordner"),
    (_symlink, "ist ein Symlink"),
    (_ergaenzung_fremd, "außerhalb des Locks"),
], ids=["ohne-text", "fremdes-lock", "index", "paket-fehlt", "datei-fehlt", "pfad-hinaus",
        "symlink", "ergaenzung-fremd"])
def test_fail_closed(tmp_path: Path, aendern: Any, meldung: str) -> None:
    cache, quellen = _cache(tmp_path)
    aendern(quellen, cache)
    with pytest.raises(LizenzFehler, match=meldung):
        erzeuge(LOCK, quellen, cache)


def _dateien(tmp_path: Path) -> tuple[Path, Path, Path]:
    cache, quellen = _cache(tmp_path)
    lock, qpfad = tmp_path / "pixi.lock", tmp_path / "quellen.json"
    lock.write_bytes(LOCK)
    qpfad.write_text(json.dumps(quellen))
    return lock, qpfad, cache


def test_schreibe_atomar_0644(tmp_path: Path) -> None:
    lock, qpfad, cache = _dateien(tmp_path)
    ziel = tmp_path / "LIZENZEN.txt"
    assert schreibe(lock, qpfad, cache, ziel) == 2
    assert ziel.stat().st_mode & 0o777 == 0o644
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".lizenzen-")] == []


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lock, qpfad, cache = _dateien(tmp_path)
    ziel = tmp_path / "LIZENZEN.txt"
    argv = ["pdal-lizenzen", "--lock", str(lock), "--quellen", str(qpfad), "--cache", str(cache)]
    assert cli.main([*argv, "--ausgabe", str(ziel)]) == 0
    assert ziel.read_text().startswith("Lizenztexte der PDAL-Umgebung")
    lock.write_bytes(LOCK + "# geändert\n".encode())
    ziel.unlink()
    assert cli.main([*argv, "--ausgabe", str(ziel)]) == 1
    assert "gehört nicht zu diesem Lock" in capsys.readouterr().err
    assert not ziel.exists()
