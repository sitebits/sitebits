"""Kommandozeile ``scanview-import``.

Unterkommandos: ``inventory``, ``convert``, ``panos``, ``overview``, ``verify``, ``build`` (=
``convert`` → ``panos`` → ``overview`` → ``verify``), ``migrate`` (project.json Schema 1 → 2,
sichert ``project.v1.json``). ``--max-points`` begrenzt (keine
Scan-Auswahl, nur die ersten N Punkte der Quelle insgesamt) — wird es ohne
``--mode`` angegeben, gilt ``mode="staged"``: Proben mit ``--max-points`` sollen denselben
Pfad nehmen wie die großen Quellen, für die sie stehen. (Ursprünglich, weil der Direktmodus
``count`` ignorierte; inzwischen liest Stufe 1 in beiden Modi im Stream, ``count`` greift
immer — siehe ``pdal_runner.build_extract_pipeline``.)
"""

from __future__ import annotations

import argparse
import json
import math
import re
import resource
import struct
import sys
import time
import zlib
from pathlib import Path
from typing import Any

from . import hoehen, lage
from .convert import convert
from .fotos_import import import_fotos, importiere_export
from .fotos_pruefung import GRENZE_PX, pruefe_fotos_paket
from .inventory import inventory_to_json, read_inventory
from .kameras_import import import_kameras
from .manifest_io import cloud_of, migrate, read_manifest, start_sichtbar, write_manifest
from .overview import DEFAULT_MAX_PX, DEFAULT_PIXEL_M, build_overview
from .panos import build_panos
from .spleen import VersionMitSpleen, waehle_spruch
from .verify import verify

_REPORT_KEYS = (
    "ok",
    "pointBalance",
    "samples",
    "scansRepresented",
    "attributes",
    "pointSource",
    "host",
)
REPORT_NAME = "conversion-report.json"


def _host_peak_rss_mb() -> dict[str, float]:
    """Host-seitiger Speicher-Spitzenwert (``ru_maxrss``, unter Linux in KiB → MiB).

    ``self`` ist dieser Python-Prozess (u. a. ``verify``, das ganze Scans liest), ``children``
    der größte bereits beendete Kindprozess (der ``docker``-Client, nicht der Container: der
    läuft als Kind von ``dockerd`` — dessen Peak steht in ``conversion.peakRssMb``).
    ``RUSAGE_CHILDREN`` ist das Maximum eines einzelnen Nachfahren, keine Summe.
    """
    self_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    children_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    return {"self": self_mb, "children": children_mb, "max": max(self_mb, children_mb)}


def _host_block(phases: dict[str, float]) -> dict[str, Any]:
    """Wanduhrzeit je Phase dieses Aufrufs + Host-Peak-RSS (Ergänzung zu ``conversion``,
    das nur die Docker-Laufzeit und den Container-RSS von ``convert`` enthält)."""
    peak = _host_peak_rss_mb()
    return {
        "phases": {name: {"seconds": sec} for name, sec in phases.items()},
        "totalSeconds": sum(phases.values()),
        "peakRssMb": peak,
        "hostPeakRssMb": peak["max"],
        "note": (
            "Wanduhr und ru_maxrss dieses scanview-import-Aufrufs; Container-RSS siehe "
            "conversion.peakRssMb. Phasen aus getrennten Aufrufen fehlen hier."
        ),
    }


def _add_convert_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("e57", type=Path, metavar="quelle")
    sp.add_argument("pkg", type=Path)
    sp.add_argument(
        "--crs", default=None, help="WKT oder EPSG:xxxx — nur wenn die Quelle keins hat"
    )
    sp.add_argument(
        "--max-points",
        type=int,
        default=None,
        help="nur die ersten N Punkte der Quelle insgesamt (keine Scan-Auswahl)",
    )
    sp.add_argument(
        "--mode",
        choices=("auto", "direct", "staged"),
        default=None,
        help="Standard: auto; mit --max-points ohne --mode: staged (siehe Modul-Docstring)",
    )

    sp.add_argument(
        "--unit",
        choices=("m", "cm", "mm", "ft"),
        help="Quelleneinheit (PLY/PTS/Text: Pflicht oder Sidecar)",
    )
    sp.add_argument(
        "--up",
        choices=("z", "y"),
        default=None,
        help="Dateiachsen beibehalten; y dreht Y-oben nach Z-oben",
    )
    sp.add_argument("--float32-zulassen", action="store_true",
                    help="Grobe Float32-E57 ausdrücklich zulassen (Warnung im Report)")
    sp.add_argument("--columns", help="Pflicht für XYZ/TXT/CSV, z. B. x,y,z,r,g,b")
    sp.add_argument(
        "--rgb-8bit",
        action="store_true",
        help="LAS/LAZ mit Farbe 0 bis 255 in den 16-bit-Feldern (z. B. OpenDroneMap) auf 16 bit "
        "skalieren; bricht ab, wenn ein Wert über 255 liegt",
    )
    sp.add_argument(
        "--ausduennen",
        type=float,
        metavar="METER",
        help="Voxel-Nachbarfilter, gekachelt; Containerlimit 40 GiB; Messwerte siehe README",
    )
    sp.add_argument(
        "--quell-hoehensystem",
        help="Höhensystem der Quelle (dhhn2016, ellipsoidisch, dhhn92); Pflicht zum Umrechnen",
    )
    sp.add_argument(
        "--ziel-hoehensystem",
        help="Höhensystem des Pakets; Standard dhhn2016, wenn die Quelle angegeben ist",
    )
    lage_gruppe = sp.add_mutually_exclusive_group()
    lage_gruppe.add_argument(
        "--verschiebung",
        metavar="DX,DY|DATEI.json",
        help="Lage um dx/dy Meter verschieben (z. B. Anpassung an LoD2); "
        "Kameras und Fotos übernehmen den Wert aus dem Paket",
    )
    lage_gruppe.add_argument(
        "--transformation",
        metavar="DATEI.json",
        help="Starre 3D-Bewegung (matrix_4x4: Drehung + Verschiebung) nach der Höhenumrechnung; "
        "Kameras und Fotos übernehmen sie aus dem Paket",
    )



def _add_mesh_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--dreiecke", type=int, default=1000000,
                    help="Vollstufe; 0 erhält alle Dreiecke")
    sp.add_argument("--vorschau-dreiecke", type=int, default=200000)
    sp.add_argument("--mobil-dreiecke", type=int, default=400000,
                    help="Mobilstufe für Handys (ETC1S, höchstens 2048 px); 0 = keine")
    sp.add_argument("--toleranz", type=float, default=10, help="Oberflächentoleranz in mm")

def _add_overview_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--pixel-m", type=float, default=DEFAULT_PIXEL_M, help="Pixelgröße der Draufsicht in m"
    )
    sp.add_argument(
        "--max-px", type=int, default=DEFAULT_MAX_PX, help="größte Kantenlänge in Pixeln"
    )
    sp.add_argument(
        "--z-max",
        type=float,
        default=None,
        help="nur Punkte bis zu dieser Welt-Höhe (Innenräume: Decke ausblenden)",
    )


def _sfm_optionen(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("pkg", type=Path)
    sp.add_argument(
        "--hoehensystem",
        required=True,
        help="Höhensystem der Rekonstruktion (z. B. ellipsoidisch, DHHN2016) — wird nicht geraten",
    )
    sp.add_argument("--max-kante", default="4096", help="lange Kante in px oder 'voll' (Kacheln)")
    sp.add_argument("--gruppe", action="append", default=None)
    sp.add_argument("--rahmen-toleranz", type=float, default=None)
    sp.add_argument("--prozesse", type=int, default=1)
    sp.add_argument(
        "--rueckprojektion-grenze",
        type=float,
        default=GRENZE_PX,
        help="Median je Foto in px (Standard 2, höchstens 10); Fotos über 2 px werden vermerkt",
    )


def _rahmen_grenze(angabe: str | None) -> tuple[float, float] | None:
    """``"P50_MM,P95_MM"`` → (p50, p95) in Metern."""
    if angabe is None:
        return None
    teile = angabe.split(",")
    try:
        p50, p95 = (float(t) / 1000 for t in teile)
    except ValueError as exc:
        raise ValueError("--rahmen-grenze als P50_MM,P95_MM angeben, z. B. 20,150") from exc
    return p50, p95


def _klassen(angabe: str | None) -> tuple[int, ...]:
    """``"5,7"`` → (5, 7)."""
    if not angabe:
        return ()
    try:
        return tuple(int(t) for t in angabe.split(","))
    except ValueError as exc:
        raise ValueError("--rahmen-ohne-klassen als Klassennummern angeben, z. B. 5,7") from exc


def _lage(a: argparse.Namespace) -> lage.Lage | None:
    if a.verschiebung is not None:
        return lage.lies_verschiebung(a.verschiebung)
    if a.transformation is not None:
        return lage.lies_transformation(a.transformation)
    return None


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scanview-import", epilog=waehle_spruch(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action=VersionMitSpleen, nargs=0, help="Version anzeigen")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inventory", help="Metadaten einer E57 als JSON auf stdout")
    s.add_argument("e57", type=Path)

    s = sub.add_parser("convert", help="E57/LAS/LAZ/COPC/PLY/PTS/XYZ/TXT/CSV → COPC-Paket")
    _add_convert_args(s)
    _add_mesh_args(s)

    s = sub.add_parser("add", help="Mesh in vorhandenes Schema-2-Paket hinzufügen")
    _add_convert_args(s)
    _add_mesh_args(s)
    s.add_argument("--same-frame", action="store_true")
    s.add_argument(
        "--rahmen-grenze",
        default=None,
        metavar="P50_MM,P95_MM",
        help="frameCheck-Grenzen ausdrücklich anheben (Standard 5,25; höchstens 50,300); vermerkt",
    )
    s.add_argument(
        "--ausdehnung-toleranz",
        type=float,
        default=None,
        metavar="MM",
        help="eigene Toleranz für die Ausdehnung der Stufen (Standard = --toleranz; höchstens 500)",
    )
    s.add_argument(
        "--rahmen-ohne-klassen",
        default=None,
        metavar="K1,K2",
        help="frameCheck ohne diese Wolkenklassen (z. B. 5,7: Vegetation aus dem Mesh entfernt)",
    )

    s = sub.add_parser(
        "mobilstufe",
        help="Vorhandenes Mesh-Paket ohne Originalquelle um mobile.glb ergänzen (neues Ziel, "
        "Hardlink-Kopie der Quelle)",
    )
    s.add_argument("quelle", type=Path)
    s.add_argument("ziel", type=Path)
    s.add_argument("--mobil-dreiecke", type=int, default=400000)

    s = sub.add_parser(
        "neu-ohne-wolke",
        help="Neues Paket nur aus Mesh (GLB/PLY ohne Textur) oder Splats, ohne Punktwolke",
    )
    s.add_argument("quelle", type=Path)
    s.add_argument("pkg", type=Path)
    s.add_argument("--art", choices=("mesh", "splats"), required=True)
    s.add_argument("--titel")
    s.add_argument("--crs", help="EPSG:xxxx oder WKT; ohne Angabe lokal (Modellmaß)")
    s.add_argument("--unit", choices=("m", "cm", "mm", "ft"),
                   help="nur Mesh-PLY (Standard m); GLB ist immer Meter")
    s.add_argument("--quell-hoehensystem")
    s.add_argument("--ziel-hoehensystem")

    s = sub.add_parser("add-splats", help="Sichtschicht mit Rahmenbeleg ergänzen")
    s.add_argument("source", type=Path)
    s.add_argument("pkg", type=Path)
    s.add_argument("--same-frame", action="store_true")
    s.add_argument("--colmap", type=Path)
    s.add_argument("--kameras", type=Path)
    s.add_argument("--crs")
    s.add_argument("--quell-hoehensystem")
    s.add_argument("--ziel-hoehensystem")
    s.add_argument("--rahmen-grenze", type=float, nargs=2, default=(5, 25),
                   metavar=("P50_MM", "P95_MM"))
    s.add_argument("--kamera-grenze", type=float, default=25)
    s.add_argument("--startebene", action="store_true")
    s.add_argument("--ausgabeformat", choices=("auto", "spz", "ply", "rad"), default="auto")
    s.add_argument("--build-lod", type=Path, help="Freigegebenes Spark v2.3.0 build-lod-Binary")
    s.add_argument("--startkameras", type=Path,
                   help="Weltzentren CSV/XML für fehlende Startansicht")

    s = sub.add_parser("panos", help="Würfelpanoramen + stations.json (braucht project.json)")
    s.add_argument("e57", type=Path)
    s.add_argument("pkg", type=Path)
    s.add_argument("--levels", default="512,1024,2048,4096")

    s = sub.add_parser("panos-kameras", help="Kugelpanoramen (kameras.json) in vorhandenes Paket")
    s.add_argument("--kameras", type=Path, required=True)
    s.add_argument("pkg", type=Path)
    s.add_argument("--levels", default="512,1024,2048,4096")
    s.add_argument("--hoehensystem", help="Höhensystem bestätigen, falls im Paket unbekannt")

    s = sub.add_parser("fotos-kameras", help="Rahmenkameras (kameras.json) als orientierte Fotos")
    s.add_argument("--kameras", type=Path, required=True)
    s.add_argument("pkg", type=Path)
    s.add_argument("--max-kante", default="4096", help="lange Kante in px oder 'voll' (Kacheln)")
    s.add_argument("--gruppe", action="append", default=None)
    s.add_argument("--rahmen-toleranz", type=float, default=None)
    s.add_argument("--prozesse", type=int, default=1)
    s.add_argument("--hoehensystem", help="Höhensystem bestätigen, falls im Paket unbekannt")
    s.add_argument(
        "--exif-drehung-ignorieren",
        action="store_true",
        help="EXIF-Orientation 5 bis 8 übergehen, wenn die Rohpixelmaße der exportierten Kamera "
        "entsprechen (die Ausrichtung rechnet in Sensorpixeln)",
    )
    s.add_argument(
        "--rueckprojektion-grenze",
        type=float,
        default=GRENZE_PX,
        help="Median je Foto in px (Standard 2, höchstens 10); Fotos über 2 px werden vermerkt",
    )

    s = sub.add_parser(
        "fotos-colmap",
        help="COLMAP-Modell (schon im Paket-CRS, z. B. per model_aligner) als orientierte Fotos",
    )
    s.add_argument("--modell", type=Path, required=True, help="Ordner mit cameras/images/points3D")
    s.add_argument("--bilder", type=Path, required=True, help="Bildordner (Namen relativ dazu)")
    s.add_argument("--epsg", type=int, required=True, help="CRS des Modells (wie das Paket)")
    s.add_argument("--versatz", default="0,0,0", help="E,N,H, addiert zu Modellkoordinaten")
    _sfm_optionen(s)

    s = sub.add_parser("fotos-odm", help="OpenDroneMap-Lauf (georeferenziert) als Fotos")
    s.add_argument("--projekt", type=Path, required=True, help="ODM-Projektordner")
    _sfm_optionen(s)

    s = sub.add_parser("genauigkeit", help="Genauigkeitsnachweis (genauigkeit.json) ins Paket")
    s.add_argument("--datei", type=Path, required=True)
    s.add_argument("pkg", type=Path)
    s.add_argument(
        "--bericht", type=Path, help="Bericht der Photogrammetrie-Software (PDF), nur Prüfsumme"
    )
    s.add_argument("--ebene", action="append", default=None, help="Ebenen-ID, mehrfach")

    s = sub.add_parser("fotos-pruefen", help="Fotos-Ebene eines Pakets prüfen (Dateien, Größen)")
    s.add_argument("pkg", type=Path)

    s = sub.add_parser("overview", help="topview.png + Bodenhöhe je Standpunkt (braucht Paket)")
    s.add_argument("pkg", type=Path)
    _add_overview_args(s)

    s = sub.add_parser(
        "verify", help="Originalquelle gegen bestehendes COPC-Paket prüfen, Report schreiben"
    )
    s.add_argument("e57", type=Path)
    s.add_argument("pkg", type=Path)
    s.add_argument("--samples", type=int, default=10000)
    s.add_argument("--float32-zulassen", action="store_true")

    s = sub.add_parser("build", help="convert -> panos -> overview -> verify")
    _add_convert_args(s)
    s.add_argument("--levels", default="512,1024,2048,4096")
    _add_overview_args(s)
    s.add_argument("--samples", type=int, default=10000)

    s = sub.add_parser(
        "migrate", help="project.json Schema 1 -> 2 migrieren (sichert project.v1.json)"
    )
    s.add_argument("pkg", type=Path)

    s = sub.add_parser(
        "start-sichtbar", help="Ebene beim Start im Viewer zeigen (an) oder ausblenden (aus)"
    )
    s.add_argument("pkg", type=Path)
    s.add_argument("ebene")
    s.add_argument("wert", choices=("an", "aus"))

    return p


def _withdraw_unverified_point_source(pkg: Path, rep: dict[str, Any]) -> None:
    """``cloud.pointSourceIsScan`` gilt nur mit bestandenem verify.

    Schlägt der ``pointSource``-Block fehl, setzt build das Flag auf ``false`` — sonst
    behauptete das Paket eine ungeprüfte Scan-Zuordnung, und der Viewer filtert im Panorama
    womöglich falsch. Pakete ohne das Flag bleiben unverändert."""
    if rep.get("pointSource", {}).get("ok") is True:
        return
    path = pkg / "project.json"
    if not path.exists():
        return
    manifest = read_manifest(pkg)
    try:
        cloud = cloud_of(manifest)
    except KeyError:
        return
    if cloud.get("pointSourceIsScan") is not True:
        return
    cloud["pointSourceIsScan"] = False
    write_manifest(pkg, manifest)
    print(
        "[verify] Scan-Zuordnung nicht bestätigt — cloud.pointSourceIsScan auf false gesetzt.",
        file=sys.stderr,
        flush=True,
    )


def _record_verify_report(pkg: Path, rep: dict[str, Any]) -> None:
    """Bei Schema 2 verweist die Punktwolken-Ebene mit ``verify: {ok, report}`` auf
    ``conversion-report.json`` im Paketwurzelverzeichnis. Schema-1-Pakete bleiben unangetastet
    (kein Ebenenkonzept, in das das passen würde)."""
    path = pkg / "project.json"
    if not path.exists():
        return
    manifest = read_manifest(pkg)
    if manifest.get("schemaVersion") != 2:
        return
    try:
        cloud = cloud_of(manifest)
    except KeyError:
        return
    cloud["verify"] = {"ok": rep.get("ok"), "report": REPORT_NAME}
    write_manifest(pkg, manifest)


def _resolve_mode(a: argparse.Namespace) -> str:
    if a.mode is not None:
        return str(a.mode)
    return "staged" if a.max_points is not None else "auto"


def _start_sichtbar(a: argparse.Namespace) -> int:
    try:
        ergebnis = start_sichtbar(a.pkg, a.ebene, a.wert == "an")
    except KeyError:
        print(f"Ebene {a.ebene} gibt es im Paket nicht", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    json.dump(ergebnis, sys.stdout, ensure_ascii=False)
    print()
    return 0


def _mobilstufe(a: argparse.Namespace) -> int:
    from .mesh_mobil import mobilstufe

    try:
        manifest = mobilstufe(a.quelle, a.ziel, a.mobil_dreiecke)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"Mobilstufe abgebrochen: {exc}", file=sys.stderr)
        return 1
    layer = next(lay for lay in manifest["layers"] if lay["kind"] == "mesh")
    felder = ("mobileUrl", "mobileSizeBytes", "mobileTriangleCount")
    ergebnis = {k: layer[k] for k in felder} | {"revision": manifest["revision"],
                                                "ziel": str(a.ziel)}
    print(json.dumps(ergebnis, ensure_ascii=False, indent=2))
    return 0


_PFAD_RE = re.compile(r"(?<![\w.])/[^\s'\"]+")


def _ohne_pfade(text: str) -> str:
    """Meldung ohne absolute Pfade (geht an Mensch und Agent): jeder Pfad wird zu seinem Namen."""
    return _PFAD_RE.sub(lambda t: Path(t.group(0)).name or "<pfad>", text).strip()


def _neu_ohne_wolke(a: argparse.Namespace) -> int:
    """Exit 0 = Paket geschrieben; 2 = Eingabe abgelehnt (letzte Zeile = Meldung ohne Pfade)."""
    from .neu_ohne_wolke import neu_mesh, neu_splats

    hoehe = (a.quell_hoehensystem, a.ziel_hoehensystem)
    try:
        if a.art == "mesh":
            m = neu_mesh(a.quelle, a.pkg, titel=a.titel, crs=a.crs, unit=a.unit, hoehe=hoehe)
        else:
            if a.unit is not None:
                raise ValueError("--unit gilt nur für Meshes")
            m = neu_splats(a.quelle, a.pkg, titel=a.titel, crs=a.crs, hoehe=hoehe)
    except (ValueError, KeyError, FileExistsError, FileNotFoundError) as exc:
        text = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"Abgelehnt: {_ohne_pfade(str(text))}", file=sys.stderr)
        return 2
    except (EOFError, OSError, struct.error, zlib.error):
        # Abgeschnittene oder kaputte Datei (gzip/SPZ, Binär-PLY): fremde Eingabe, kein Absturz.
        print("Abgelehnt: Quelldatei beschädigt oder unvollständig", file=sys.stderr)
        return 2
    print(json.dumps(m, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    a = _build_parser().parse_args(argv)

    if a.cmd == "add-splats":
        from .splat_convert import add_splats
        try:
            splat_result = add_splats(a.source, a.pkg, same_frame=a.same_frame, colmap=a.colmap,
                                cameras=a.kameras, crs=a.crs,
                                hoehe=(a.quell_hoehensystem, a.ziel_hoehensystem),
                                limits_mm=tuple(a.rahmen_grenze), camera_limit_mm=a.kamera_grenze,
                                initial=a.startebene, output_format=a.ausgabeformat,
                                start_cameras=a.startkameras, build_lod=a.build_lod)
            print(json.dumps(splat_result, ensure_ascii=False, indent=2))
            return 0
        except (ValueError, OSError, KeyError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if a.cmd == "mobilstufe":
        return _mobilstufe(a)

    if a.cmd == "neu-ohne-wolke":
        return _neu_ohne_wolke(a)

    if a.cmd == "verify":
        from .neu_ohne_wolke import ist_splat_paket_ohne_wolke, verify_splats
        if ist_splat_paket_ohne_wolke(a.pkg):
            try:
                splat_rep = verify_splats(a.e57, a.pkg)
            except (OSError, ValueError, KeyError, StopIteration, EOFError, struct.error,
                    zlib.error) as exc:
                print(f"Splat-Prüfung abgebrochen: {_ohne_pfade(str(exc))}", file=sys.stderr)
                return 2
            print(json.dumps(splat_rep, ensure_ascii=False, indent=2))
            return 0 if splat_rep["ok"] else 1

    if a.cmd in ("convert", "add", "verify"):
        from .mesh_source import is_mesh
        try:
            mesh_input = is_mesh(a.e57)
            if a.cmd == "add" and not mesh_input:
                raise ValueError("add unterstützt in dieser Scheibe nur Meshes")
            if mesh_input:
                from .mesh_convert import add_mesh, convert_mesh
                from .mesh_verify import verify_mesh
                if a.cmd == "verify":
                    report = verify_mesh(a.e57, a.pkg)
                    manifest = read_manifest(a.pkg)
                    layer = next(lay for lay in manifest["layers"] if lay["kind"] == "mesh")
                    layer["verify"]["ok"] = report["ok"]
                    write_manifest(a.pkg, manifest)
                    print(json.dumps(report, ensure_ascii=False, indent=2))
                    return 0 if report["ok"] else 2
                if any(getattr(a, k, None) is not None for k in
                       ("max_points", "columns", "ausduennen", "mode", "verschiebung",
                        "transformation")):
                    raise ValueError("Punktoptionen sind bei Meshes nicht zulässig")
                options = dict(crs=a.crs, unit=a.unit, up=a.up,
                               dreiecke=a.dreiecke, vorschau_dreiecke=a.vorschau_dreiecke,
                               mobil_dreiecke=a.mobil_dreiecke, toleranz=a.toleranz,
                               hoehe=(a.quell_hoehensystem, a.ziel_hoehensystem))
                mesh_result = (add_mesh(a.e57, a.pkg, same_frame=a.same_frame,
                                        rahmen_grenzen=_rahmen_grenze(a.rahmen_grenze),
                                        rahmen_ohne_klassen=_klassen(a.rahmen_ohne_klassen),
                                        ausdehnung_toleranz=a.ausdehnung_toleranz,
                                        **options)
                          if a.cmd == "add" else convert_mesh(a.e57, a.pkg, **options))
                print(json.dumps(mesh_result, ensure_ascii=False, indent=2))
                return 0
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            print(f"Mesh-Import/Prüfung abgebrochen: {exc}", file=sys.stderr)
            return 1

    if a.cmd == "inventory":
        json.dump(inventory_to_json(read_inventory(a.e57)), sys.stdout, indent=2)
        return 0

    if a.cmd == "panos-kameras":
        try:
            levels = tuple(int(x) for x in a.levels.split(","))
            report = import_kameras(a.kameras, a.pkg, levels, a.hoehensystem)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"Panorama-Import abgebrochen: {exc}", file=sys.stderr)
            return 1
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    if a.cmd == "fotos-kameras":
        try:
            max_kante = None if a.max_kante == "voll" else int(a.max_kante)
            if max_kante is not None and max_kante < 64:
                raise ValueError("--max-kante muss ≥ 64 oder 'voll' sein")
            if a.prozesse < 1:
                raise ValueError("--prozesse muss ≥ 1 sein")
            report = import_fotos(
                a.kameras,
                a.pkg,
                max_kante=max_kante,
                gruppen=a.gruppe,
                rahmen_toleranz=a.rahmen_toleranz,
                prozesse=a.prozesse,
                height_system=a.hoehensystem,
                exif_drehung_ignorieren=a.exif_drehung_ignorieren,
                rueckprojektion_grenze=a.rueckprojektion_grenze,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"Foto-Import abgebrochen: {exc}", file=sys.stderr)
            return 1
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    if a.cmd in ("fotos-colmap", "fotos-odm"):
        from .fotos_sfm import read_colmap, read_odm

        try:
            max_kante = None if a.max_kante == "voll" else int(a.max_kante)
            if max_kante is not None and max_kante < 64:
                raise ValueError("--max-kante muss ≥ 64 oder 'voll' sein")
            if a.prozesse < 1:
                raise ValueError("--prozesse muss ≥ 1 sein")
            if a.cmd == "fotos-colmap":
                versatz = [float(x) for x in a.versatz.split(",")]
                if len(versatz) != 3 or not all(math.isfinite(x) for x in versatz):
                    raise ValueError("--versatz braucht drei Zahlen E,N,H")
                export = read_colmap(
                    a.modell,
                    a.bilder,
                    epsg=a.epsg,
                    vertical_datum=a.hoehensystem,
                    versatz=(versatz[0], versatz[1], versatz[2]),
                )
                quelle = {"source": "colmap", "model": a.modell.name}
            else:
                export = read_odm(a.projekt, vertical_datum=a.hoehensystem)
                quelle = {"source": "opendronemap"}
            report = importiere_export(
                export,
                a.pkg,
                quelle=quelle,
                max_kante=max_kante,
                gruppen=a.gruppe,
                rahmen_toleranz=a.rahmen_toleranz,
                prozesse=a.prozesse,
                height_system=a.hoehensystem,
                rueckprojektion_grenze=a.rueckprojektion_grenze,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"Foto-Import abgebrochen: {exc}", file=sys.stderr)
            return 1
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    if a.cmd == "genauigkeit":
        from .genauigkeit import importiere

        try:
            acc = importiere(a.pkg, a.datei, bericht=a.bericht, ebenen=a.ebene)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"Genauigkeit-Import abgebrochen: {exc}", file=sys.stderr)
            return 1
        json.dump(acc, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    if a.cmd == "fotos-pruefen":
        try:
            pruefung = pruefe_fotos_paket(a.pkg)
        except ValueError as exc:
            print(f"Fotos-Prüfung abgebrochen: {exc}", file=sys.stderr)
            return 1
        json.dump(pruefung, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0 if pruefung["ok"] else 1

    if a.cmd == "start-sichtbar":
        return _start_sichtbar(a)

    if a.cmd == "migrate":
        try:
            result = migrate(a.pkg)
        except FileExistsError:
            print(
                "project.v1.json existiert schon – Migration abgebrochen, nichts verändert",  # noqa: RUF001
                file=sys.stderr,
            )
            return 1
        except (ValueError, KeyError) as exc:
            print(f"Migration abgebrochen: {exc}", file=sys.stderr)
            return 1
        print(result)
        return 0

    # Fortschritt geht bewusst nach stderr — stdout bleibt für den JSON-Report
    # (verify/build) maschinenlesbar (`scanview-import build ... | jq .ok`). Jede Phase
    # bekommt eine eigene Wanduhrzeit (nicht nur `conversion.seconds`, das nur die
    # Docker-Laufzeit misst und z. B. den SHA-256 der Quelle danach — Teil des
    # project.json-Manifestaufbaus in convert() — nicht einschließt).
    phases: dict[str, float] = {}

    if a.cmd in ("convert", "build"):
        mode = _resolve_mode(a)
        print(f"[convert] Start ({mode}) …", file=sys.stderr, flush=True)
        t0 = time.monotonic()
        extra: dict[str, Any] = {}
        if a.float32_zulassen:
            extra["float32_zulassen"] = True
        if a.rgb_8bit:
            extra["rgb_8bit"] = True
        if a.unit is not None:
            extra["unit"] = a.unit
        if a.up is not None and a.up != "z":
            extra["up"] = a.up
        if a.columns is not None:
            extra["columns"] = a.columns
        if a.ausduennen is not None:
            extra["ausduennen"] = a.ausduennen
        try:
            hoehe = hoehen.festlegen(a.quell_hoehensystem, a.ziel_hoehensystem)
            if hoehe[0] is not None:
                extra["hoehe"] = hoehe
            if (lage_angabe := _lage(a)) is not None:
                extra["lage"] = lage_angabe
            if a.cmd == "build" and a.e57.suffix.lower() != ".e57":
                raise ValueError("build benötigt E57-Scans; Punktformate mit convert importieren")
            manifest = convert(a.e57, a.pkg, a.crs, a.max_points, mode, **extra)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"Punktimport abgebrochen: {exc}", file=sys.stderr)
            return 1
        wall = time.monotonic() - t0
        phases["convert"] = wall
        conv = manifest.get("conversion", {})
        print(
            f"[convert] fertig nach {wall:.1f}s (Wanduhr) — mode={conv.get('mode')} "
            f"dockerSeconds={conv.get('seconds')} containerPeakRssMb={conv.get('peakRssMb')}",
            file=sys.stderr,
            flush=True,
        )

    if a.cmd in ("panos", "build"):
        levels = tuple(int(x) for x in a.levels.split(","))
        print("[panos] Start …", file=sys.stderr, flush=True)
        t0 = time.monotonic()
        build_panos(a.e57, a.pkg, levels)
        phases["panos"] = time.monotonic() - t0
        print(f"[panos] fertig nach {phases['panos']:.1f}s", file=sys.stderr, flush=True)

    if a.cmd in ("overview", "build"):
        print("[overview] Start …", file=sys.stderr, flush=True)
        t0 = time.monotonic()
        res = build_overview(a.pkg, a.pixel_m, a.max_px, a.z_max)
        phases["overview"] = time.monotonic() - t0
        with_ground = sum(v is not None for v in res["groundZ"].values())
        print(
            f"[overview] fertig nach {phases['overview']:.1f}s — topview "
            f"{res['topview']['widthPx']}x{res['topview']['heightPx']} px, "
            f"groundZ {with_ground}/{len(res['groundZ'])}",
            file=sys.stderr,
            flush=True,
        )

    if a.cmd in ("verify", "build"):
        print("[verify] Start …", file=sys.stderr, flush=True)
        t0 = time.monotonic()
        try:
            rep: dict[str, Any] = verify(a.e57, a.pkg, a.samples, **(
                {"float32_zulassen": True} if a.float32_zulassen else {}))
        except BaseException:
            # Abgebrochenes verify bestätigt die Scan-Zuordnung ebenso wenig (Review 12c).
            if a.cmd == "build":
                _withdraw_unverified_point_source(a.pkg, {})
            raise
        phases["verify"] = time.monotonic() - t0
        print(f"[verify] fertig nach {phases['verify']:.1f}s", file=sys.stderr, flush=True)
        # verify() schreibt den Report, bevor seine eigene Dauer feststeht — Zeiten und
        # Host-Peak werden deshalb hier ergänzt und der Report neu geschrieben (I1).
        rep["host"] = _host_block(phases)
        (a.pkg / REPORT_NAME).write_text(json.dumps(rep, indent=2, allow_nan=False))
        _record_verify_report(a.pkg, rep)
        if a.cmd == "build":
            _withdraw_unverified_point_source(a.pkg, rep)
        print(json.dumps({k: rep[k] for k in _REPORT_KEYS}, indent=2))
        truncated = a.cmd == "build" and getattr(a, "max_points", None) is not None
        if not rep["ok"] and truncated:
            print(
                "Hinweis: --max-points liefert ein unvollständiges Paket; verify vergleicht "
                "gegen die volle Quelle und schlägt bei einer Kürzung erwartungsgemäß fehl "
                "(ok=False ist hier erwartet) — Exit-Code trotzdem 0.",
                file=sys.stderr,
            )
            return 0
        return 0 if rep["ok"] else 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
