"""F7: Bildanhang bereinigen — keine Metadaten, keine angehängten Bytes, Grenzen (synthetisch)."""

import io
import json
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image

from scanview_import.anhang import MAX_PIXEL, Abgelehnt, bereinigen
from scanview_import.cli import main

GPS_IFD = 0x8825
EXIF_IFD = 0x8769


def _jpeg_mit_metadaten(pfad: Path, *, orientierung: int = 1) -> None:
    bild = Image.new("RGB", (60, 30), (200, 10, 10))
    exif = Image.Exif()
    exif[0x010F] = "Kamerahersteller"
    exif[0x0110] = "Modell X"
    exif[0x013B] = "Erika Mustermann"
    exif[0x0112] = orientierung
    exif.get_ifd(EXIF_IFD)[0x9003] = "2026:10:21 14:05:09"
    exif.get_ifd(GPS_IFD).update({1: "N", 2: (52.0, 24.0, 0.0), 3: "E", 4: (13.0, 3.0, 0.0)})
    xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/">Erika</x:xmpmeta>'
    puffer = io.BytesIO()
    bild.save(puffer, "JPEG", exif=exif, xmp=xmp, comment=b"Kommentar mit Namen",
              icc_profile=b"\x00" * 128)
    # Polyglott: angehängtes HTML hinter dem JPEG-Ende.
    pfad.write_bytes(puffer.getvalue() + b"<html><script>alert(1)</script></html>")


def test_entfernt_exif_gps_xmp_kommentar_und_anhang(tmp_path: Path) -> None:
    ein = tmp_path / "ein"
    _jpeg_mit_metadaten(ein)
    info = bereinigen(ein, tmp_path / "aus")
    roh = (tmp_path / "aus" / "bild.jpg").read_bytes()
    assert info == {"breite": 60, "hoehe": 30, "aufnahme": "2026-10-21T14:05:09"}
    assert roh.endswith(b"\xff\xd9")
    for verboten in (b"Exif\x00\x00", b"xmpmeta", b"Kommentar", b"Erika", b"Modell", b"<html"):
        assert verboten not in roh
    with Image.open(tmp_path / "aus" / "bild.jpg") as b:
        assert len(b.getexif()) == 0
        assert "comment" not in b.info and "xmp" not in b.info
        # Nur das Farbprofil bleibt (zulässig).
        assert set(b.info) - {"jfif", "jfif_version", "jfif_unit", "jfif_density", "dpi",
                              "progressive", "progression", "icc_profile"} == set()


def test_orientierung_angewendet(tmp_path: Path) -> None:
    ein = tmp_path / "ein"
    _jpeg_mit_metadaten(ein, orientierung=6)  # 90° gedreht
    info = bereinigen(ein, tmp_path / "aus")
    assert (info["breite"], info["hoehe"]) == (30, 60)


def test_png_mit_transparenz_wird_jpeg(tmp_path: Path) -> None:
    ein = tmp_path / "ein"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(ein, "PNG")
    info = bereinigen(ein, tmp_path / "aus")
    assert info == {"breite": 10, "hoehe": 10}
    with Image.open(tmp_path / "aus" / "bild.jpg") as b:
        assert b.format == "JPEG" and b.getpixel((5, 5)) == (255, 255, 255)


def _png_kopf(breite: int, hoehe: int) -> bytes:
    """Kleine PNG-Datei, die riesige Maße behauptet (Dekompressionsbombe im Kopf)."""
    ihdr = struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0)
    def chunk(typ: bytes, daten: bytes) -> bytes:
        return struct.pack(">I", len(daten)) + typ + daten + struct.pack(
            ">I", zlib.crc32(typ + daten) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00" * 100)) + chunk(b"IEND", b""))


@pytest.mark.parametrize("inhalt,meldung", [
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', "Nur JPEG"),
    (b"<!DOCTYPE html><html></html>", "Nur JPEG"),
    (b"%PDF-1.7\n", "Nur JPEG"),
    (b"\x00\x00\x00\x18ftypheic", "Nur JPEG"),
    (b"\xff\xd8\xff\xe0kaputt", "beschädigt"),
    (b"", "leer"),
])
def test_abgelehnt(tmp_path: Path, inhalt: bytes, meldung: str) -> None:
    ein = tmp_path / "ein"
    ein.write_bytes(inhalt)
    with pytest.raises(Abgelehnt, match=meldung):
        bereinigen(ein, tmp_path / "aus")
    assert not (tmp_path / "aus" / "bild.jpg").exists()


def test_dekompressionsbombe(tmp_path: Path) -> None:
    ein = tmp_path / "ein"
    ein.write_bytes(_png_kopf(20_000, 20_000))
    assert ein.stat().st_size < 1000 and 20_000 * 20_000 > MAX_PIXEL
    with pytest.raises(Abgelehnt, match="Pixel"):
        bereinigen(ein, tmp_path / "aus")


def test_uebergross(tmp_path: Path) -> None:
    ein = tmp_path / "ein"
    ein.write_bytes(b"\xff\xd8\xff" + b"\x00" * (20 * 1024 * 1024))
    with pytest.raises(Abgelehnt, match="20 MB"):
        bereinigen(ein, tmp_path / "aus")


def test_symlink_als_eingabe(tmp_path: Path) -> None:
    echt = tmp_path / "echt"
    _jpeg_mit_metadaten(echt)
    (tmp_path / "ein").symlink_to(echt)
    with pytest.raises(Abgelehnt):
        bereinigen(tmp_path / "ein", tmp_path / "aus")


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ein = tmp_path / "ein"
    _jpeg_mit_metadaten(ein)
    assert main(["anhang-bereinigen", str(ein), str(tmp_path / "aus")]) == 0
    assert json.loads(capsys.readouterr().out)["breite"] == 60
    ein.write_bytes(b"<svg/>")
    assert main(["anhang-bereinigen", str(ein), str(tmp_path / "aus2")]) == 2
    fehler = capsys.readouterr().err.strip().splitlines()[-1]
    assert fehler == "Abgelehnt: Nur JPEG- oder PNG-Bilder." and str(tmp_path) not in fehler
