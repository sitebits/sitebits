"""Bildanhang am Befund-Faden bereinigen (F7): dekodieren und neu kodieren, isoliert.

Läuft nur im Auftrag des Import-Workers (kein Netz, eigene Speicher- und Zeitgrenze, nur die eine
Eingabedatei lesbar). Der Webdienst hat davor nur Kopfbytes und Größe geprüft.

Ein Foto vom Handy ist fremde Eingabe: Es kann eine Dekompressionsbombe sein, ein Polyglott
(gültiges Bild mit angehängtem HTML/ZIP) oder Personendaten in den Metadaten tragen (GPS,
Gerät, Name). Deshalb:
- Format nur JPEG oder PNG (aus den Kopfbytes, nicht aus Endung oder Content-Type);
- Pixelgrenze vor dem Dekodieren (`MAX_PIXEL`), Pillows eigene Bombenprüfung bleibt an;
- EXIF-Orientierung angewendet, dann aus den Pixeln ein **neues** JPEG ohne EXIF, XMP, IPTC,
  Kommentare und Vorschaubilder; angehängte Bytes gehen dabei verloren;
- die Aufnahmezeit (EXIF DateTimeOriginal) wird vor dem Entfernen als Feld gelesen (ohne Zeitzone,
  wie die Kamera sie schreibt); das Farbprofil (ICC) bleibt, sonst nichts.

Ausgabe in den Ordner `aus`: `bild.jpg` und `info.json` ({breite, hoehe, aufnahme?}). Exit 0 bei
Erfolg; 2 mit letzter Zeile „Abgelehnt: …“ (ohne Pfade), wenn das Bild nicht angenommen wird.
"""

from __future__ import annotations

import json
import re
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXEL = 40_000_000
MAX_KANTE = 8192
QUALITAET = 88
_EXIF_IFD = 0x8769
_DATUM_ORIGINAL = 0x9003
_DATUM_RE = re.compile(r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})")


class Abgelehnt(ValueError):
    """Nutzersichtbar, ohne Pfade."""


def _format(kopf: bytes) -> str:
    if kopf.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if kopf.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    raise Abgelehnt("Nur JPEG- oder PNG-Bilder.")


def _aufnahme(bild: Image.Image) -> str | None:
    try:
        roh = bild.getexif().get_ifd(_EXIF_IFD).get(_DATUM_ORIGINAL)
    except (ValueError, TypeError, OSError, KeyError):
        return None
    if not isinstance(roh, str):
        return None
    m = _DATUM_RE.fullmatch(roh.strip("\x00 "))
    if m is None:
        return None
    try:
        j, mo, t, st, mi, se = (int(x) for x in m.groups())
        return datetime(j, mo, t, st, mi, se).isoformat()  # Kamerazeit, ohne Zone
    except ValueError:
        return None


def _neu_kodieren(bild: Image.Image, ziel: Path, icc: Any) -> tuple[int, int]:
    """Pixel ohne Metadaten als neues JPEG speichern; liefert (Breite, Höhe).

    Speicher (Budget 512 MB, 40 MP ≈ 120 MB je RGB-Kopie): Drehen an Ort und Stelle, Umwandeln nur,
    wenn nötig. Keine EXIF-/XMP-/IPTC-Blöcke, keine Kommentare: Pillow schreibt beim Speichern nur,
    was in `info` steht oder übergeben wird — `info` wird geleert, übergeben wird höchstens das
    Farbprofil. Neu kodiert aus den Pixeln: angehängte Bytes der Quelle gehen nicht mit.
    """
    ImageOps.exif_transpose(bild, in_place=True)
    if bild.mode in ("RGBA", "LA", "P"):
        rgba = bild.convert("RGBA")
        pixel = Image.new("RGB", rgba.size, (255, 255, 255))
        pixel.paste(rgba, mask=rgba.getchannel("A"))
        del rgba
    elif bild.mode != "RGB":
        pixel = bild.convert("RGB")
    else:
        pixel = bild
    if max(pixel.size) > MAX_KANTE:
        pixel.thumbnail((MAX_KANTE, MAX_KANTE), Image.Resampling.LANCZOS)
    pixel.info = {}
    optionen: dict[str, Any] = {"quality": QUALITAET, "optimize": True}
    if isinstance(icc, bytes) and len(icc) <= 1024 * 1024:
        optionen["icc_profile"] = icc
    pixel.save(ziel, "JPEG", **optionen)
    return pixel.size


def bereinigen(ein: Path, aus: Path) -> dict[str, Any]:
    """Liest `ein`, schreibt `aus/bild.jpg` und `aus/info.json`; wirft `Abgelehnt`."""
    if ein.is_symlink() or not ein.is_file():
        raise Abgelehnt("Eingabe fehlt.")
    groesse = ein.stat().st_size
    if not 0 < groesse <= MAX_BYTES:
        raise Abgelehnt("Bild leer oder größer als 20 MB.")
    with ein.open("rb") as f:
        art = _format(f.read(16))
    Image.MAX_IMAGE_PIXELS = MAX_PIXEL
    aus.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        # Pillow warnt ab MAX_IMAGE_PIXELS und bricht erst beim Doppelten ab: hier gilt die
        # Grenze hart.
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(ein, formats=[art]) as bild:
                b, h = bild.size
                if b * h > MAX_PIXEL or b < 1 or h < 1:
                    raise Abgelehnt("Bild hat zu viele Pixel (höchstens 40 Megapixel).")
                aufnahme = _aufnahme(bild)
                icc = bild.info.get("icc_profile")
                if art == "JPEG" and max(b, h) > MAX_KANTE:
                    # Größer als die Ausgabe: gleich verkleinert dekodieren (DCT-Skalierung, spart
                    # Speicher), die Feinverkleinerung folgt unten.
                    bild.draft("RGB", (MAX_KANTE, MAX_KANTE))
                bild.load()
                neu = _neu_kodieren(bild, aus / "bild.jpg", icc)
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
            raise Abgelehnt("Bild hat zu viele Pixel (höchstens 40 Megapixel).") from e
        except (OSError, SyntaxError, ValueError) as e:
            if isinstance(e, Abgelehnt):
                raise
            raise Abgelehnt("Bild ist beschädigt oder kein gültiges JPEG/PNG.") from e
    info: dict[str, Any] = {"breite": neu[0], "hoehe": neu[1]}
    if aufnahme is not None:
        info["aufnahme"] = aufnahme
    (aus / "info.json").write_text(json.dumps(info), encoding="utf-8")
    return info
