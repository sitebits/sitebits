"""Synthetische Testkirche für die E2E-Suite (nur erfundene Geometrie, keine Kundendaten).

Ersatz für die echten Pakete (Pilotkirche, Pilothalle) in den Browser-Tests, damit die
Suite auch auf Rechnern ohne Projektdaten läuft. **Kein Genauigkeitsnachweis**: Die Flächen sind
eben (mit 2 mm Rauschen), Referenzstrecken auf diesem Paket belegen den Ablauf, nicht die
Messgenauigkeit. Die Nachweise (Referenzstrecken, A4) bleiben auf echten Daten.

Welt-Koordinaten (m), lokales System:

- Kirchenschiff x ∈ [0, 30], y ∈ [-6, 6], Boden z = 0, flache Decke z = 6 (in Scanreichweite);
- Westwand x = 0 mit Portal y ∈ [-1, 1], z ∈ [0, 3], darin die Tür um ``TUER_TIEFE`` zurückgesetzt
  (Laibung und Sturz); Ostwand x = 30;
- Südwand y = -6 geschlossen (Referenzwand für S005/S010), Nordwand y = 6 mit Fenstern
  z ∈ [2,5; 4,5], je 1,5 m breit, Mitte bei x = 2,5 + 5k;
- acht Pfeiler (Zylinder r = 0,4 m) bei x = 5, 10, 20, 25 und y = ±3,5 (bei x = 15 keiner);
- Bodenöffnung (Krypta-Treppe) Radius 1,8 m um den Standpunkt ``OHNE_BODEN``.

45 Standpunkte S001 bis S045 auf 3 Reihen (y = -3, 0, 3) zu je 15 (x = 1, 3, … 29), Höhe 1,5 m.
Sonderfälle wie im echten Paket, damit die Tests ihre Bedeutung behalten:

- ``AUSSERHALB`` (S017) steht vor dem Portal bei x = -4: außerhalb der Wolke, auf der
  Übersicht trotzdem;
- ``OHNE_BODEN`` (S023, Schiffmitte, fern von Wand und Pfeilern) steht über der Bodenöffnung:
  ``groundZ`` ist ``null``;
- Jeder Scan enthält die Punkte im Umkreis ``--radius`` (überlappend, also fremde Kandidaten im
  Scanfilter).

Dazu ``e2e-halle`` (Gegenstück zur Pilothalle): 3 Scans ohne ``colorLimits``, S001 ohne Panorama;
``e2e-kirche-alt``: Kopie ohne ``pointSourceIsScan`` (Altpaket im Scanfilter-Test);
``e2e-kirche-ref.json``: Referenzpaare aus Scan 4 (S005) und Scan 9 (S010) für ``SCANVIEW_REF``;
``erwartung-e2e-kirche.json``: die Werte, die die Tests sonst fest eintragen.

Aufruf (aus ``import/``, braucht Docker mit PDAL)::

    uv run python scripts/synthetisch_testkirche.py --ziel ~/scanview-e2e-daten

Ein vorhandenes Ziel wird nie überschrieben. Nach dem Bau prüft das Skript die Sonderfälle am
fertigen Paket und bricht ab, wenn einer nicht stimmt.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np

KIRCHE = "e2e-kirche"
KIRCHE_ALT = "e2e-kirche-alt"
HALLE = "e2e-halle"
TITEL = "Synthetisch: Testkirche"
TITEL_HALLE = "Synthetisch: Halle"

LAENGE, HALBBREITE, HOEHE = 30.0, 6.0, 6.0
PORTAL_Y, PORTAL_Z = (-1.0, 1.0), (0.0, 3.0)
FENSTER_Z, FENSTER_BREITE = (2.5, 4.5), 1.5
PFEILER_X, PFEILER_Y, PFEILER_R = (5.0, 10.0, 20.0, 25.0), (-3.5, 3.5), 0.4
SENSOR_Z = 1.5
TUER_TIEFE = 0.2
REIHEN_Y, SPALTEN_X = (-3.0, 0.0, 3.0), tuple(1.0 + 2.0 * i for i in range(15))
AUSSERHALB, AUSSERHALB_POS = "S017", (-4.0, 0.0, SENSOR_Z)
OHNE_BODEN, BODENLOCH_R = "S023", 1.8
REF_SCANS = (4, 9)  # S005, S010 — wie im echten Paket
RAUSCHEN_M = 0.002
LEVELS = "512,1024"


def stationen() -> list[tuple[str, tuple[float, float, float]]]:
    """(ID, Position) in Bau-Reihenfolge; Scan-Index = Listenindex, ID = S{Index+1:03d}."""
    out: list[tuple[str, tuple[float, float, float]]] = []
    for y in REIHEN_Y:
        for x in SPALTEN_X:
            sid = f"S{len(out) + 1:03d}"
            out.append((sid, AUSSERHALB_POS if sid == AUSSERHALB else (x, y, SENSOR_Z)))
    return out


def _raster(
    a: tuple[float, float], b: tuple[float, float], abstand: float, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    u = np.arange(a[0], a[1] + 1e-9, abstand)
    v = np.arange(b[0], b[1] + 1e-9, abstand)
    uu, vv = np.meshgrid(u, v, indexing="ij")
    uu = uu.ravel() + rng.uniform(-0.2, 0.2, uu.size) * abstand
    vv = vv.ravel() + rng.uniform(-0.2, 0.2, vv.size) * abstand
    return uu, vv


def _im_fenster(x: np.ndarray, z: np.ndarray) -> np.ndarray:
    mitte = 2.5 + 5.0 * np.round((x - 2.5) / 5.0)
    return (np.abs(x - mitte) < FENSTER_BREITE / 2) & (z > FENSTER_Z[0]) & (z < FENSTER_Z[1])


def erzeuge_kirche(abstand: float, seed: int = 20261009) -> np.ndarray:
    """Alle Flächen der Kirche in Welt-Koordinaten (N, 3), mit Rauschen.

    Gleicher Seed, gleiche Punkte."""
    rng = np.random.default_rng(seed)
    teile: list[np.ndarray] = []

    loch = dict(stationen())[OHNE_BODEN]
    x, y = _raster((0, LAENGE), (-HALBBREITE, HALBBREITE), abstand, rng)
    keep = np.hypot(x - loch[0], y - loch[1]) > BODENLOCH_R
    teile.append(np.column_stack([x[keep], y[keep], np.zeros(keep.sum())]))
    teile.append(np.column_stack([x, y, np.full(x.size, HOEHE)]))

    for wand_y in (-HALBBREITE, HALBBREITE):
        x, z = _raster((0, LAENGE), (0, HOEHE), abstand, rng)
        keep = ~_im_fenster(x, z) if wand_y > 0 else np.ones(x.size, dtype=bool)
        teile.append(np.column_stack([x[keep], np.full(keep.sum(), wand_y), z[keep]]))

    for wand_x in (0.0, LAENGE):
        y, z = _raster((-HALBBREITE, HALBBREITE), (0, HOEHE), abstand, rng)
        keep = np.ones(y.size, dtype=bool)
        if wand_x == 0.0:
            keep = ~((y > PORTAL_Y[0]) & (y < PORTAL_Y[1]) & (z < PORTAL_Z[1]))
        teile.append(np.column_stack([np.full(keep.sum(), wand_x), y[keep], z[keep]]))

    for px in PFEILER_X:
        for py in PFEILER_Y:
            w, z = _raster((0, 2 * np.pi * PFEILER_R), (0, HOEHE), abstand, rng)
            phi = w / PFEILER_R
            teile.append(
                np.column_stack([px + PFEILER_R * np.cos(phi), py + PFEILER_R * np.sin(phi), z])
            )

    welt = np.vstack(teile)
    return welt + rng.normal(0.0, RAUSCHEN_M, welt.shape)


def erzeuge_portal(abstand: float, seed: int = 20261017) -> np.ndarray:
    """Tür, Laibung und Sturz des Westportals (N, 3), mit Rauschen.

    Eigener Zufallsstrom, hinten angehängt: Die Punkte von ``erzeuge_kirche`` bleiben bitgleich."""
    rng = np.random.default_rng(seed)
    teile: list[np.ndarray] = []
    y, z = _raster(PORTAL_Y, PORTAL_Z, abstand, rng)
    teile.append(np.column_stack([np.full(y.size, TUER_TIEFE), y, z]))
    for wand_y in PORTAL_Y:
        x, z = _raster((0.0, TUER_TIEFE), PORTAL_Z, abstand, rng)
        teile.append(np.column_stack([x, np.full(x.size, wand_y), z]))
    x, y = _raster((0.0, TUER_TIEFE), PORTAL_Y, abstand, rng)
    teile.append(np.column_stack([x, y, np.full(x.size, PORTAL_Z[1])]))
    welt = np.vstack(teile)
    return welt + rng.normal(0.0, RAUSCHEN_M, welt.shape)


def erzeuge_suedwand_dicht(abstand: float, seed: int = 20261011) -> np.ndarray:
    """Südwand (Referenzwand) dicht, nur für die Referenz-Scans S005/S010.

    Die Feinsuche beim Messen im Panorama sucht nur ~1 cm um den Klick (rayPick.PICK_RADIUS bei
    3 m Tiefe); echte Scans sind dort millimeterdicht. Nur zwei Scans tragen die dichte Wand, sonst
    wird die Wolke für Läufe ohne GPU zu schwer."""
    rng = np.random.default_rng(seed)
    x, z = _raster((0, LAENGE), (0, HOEHE), abstand, rng)
    welt = np.column_stack([x, np.full(x.size, -HALBBREITE), z])
    return welt + rng.normal(0.0, RAUSCHEN_M, welt.shape)


def erzeuge_halle(abstand: float, seed: int = 20261010) -> np.ndarray:
    """Kleine Halle 12 m lang, 8 m breit, 5 m hoch: Boden und vier Wände."""
    rng = np.random.default_rng(seed)
    teile = []
    x, y = _raster((0, 12), (-4, 4), abstand, rng)
    teile.append(np.column_stack([x, y, np.zeros(x.size)]))
    for wy in (-4.0, 4.0):
        x, z = _raster((0, 12), (0, 5), abstand, rng)
        teile.append(np.column_stack([x, np.full(x.size, wy), z]))
    for wx in (0.0, 12.0):
        y, z = _raster((-4, 4), (0, 5), abstand, rng)
        teile.append(np.column_stack([np.full(y.size, wx), y, z]))
    welt = np.vstack(teile)
    return welt + rng.normal(0.0, RAUSCHEN_M, welt.shape)


def scans_aus(
    welt: np.ndarray, posen: list[tuple[float, float, float]], radius: float
) -> list[np.ndarray]:
    """Je Standpunkt die Punkte im Umkreis, im Scan-System (Welt minus Position, keine Drehung)."""
    out = []
    for p in posen:
        pos = np.array(p)
        sel = np.linalg.norm(welt - pos, axis=1) <= radius
        out.append(welt[sel] - pos)
    return out


def _fixture_writer() -> Callable[..., Any]:
    tests = Path(__file__).resolve().parent.parent / "tests"
    if str(tests) not in sys.path:
        sys.path.append(str(tests))
    from e57_fixture import write_fixture

    return cast(Callable[..., Any], write_fixture)


FACES = ("px", "nx", "py", "ny", "pz", "nz")


def _schreibe_e57(
    e57: Path,
    posen: list[tuple[float, float, float]],
    lokal: list[np.ndarray],
    ohne_panorama: set[int],
    color_limits: bool,
    image_size: int,
) -> None:
    from scanview_import.model import Pose

    scans = [
        (Pose(p, (1.0, 0.0, 0.0, 0.0)), pts, np.zeros(len(pts), dtype=np.int64))
        for p, pts in zip(posen, lokal, strict=True)
    ]
    images = [(f, i) for i in range(len(posen)) if i not in ohne_panorama for f in FACES]
    _fixture_writer()(e57, scans, images=images, image_size=image_size, color_limits=color_limits)


def _bauen(e57: Path, ziel: Path, titel: str) -> None:
    from scanview_import.cli import main as import_main

    try:
        rc = import_main(["build", str(e57), str(ziel), "--levels", LEVELS])
    except BaseException:
        shutil.rmtree(ziel, ignore_errors=True)
        raise
    if rc:
        shutil.rmtree(ziel, ignore_errors=True)
        raise SystemExit(f"Import für {ziel.name} gescheitert ({rc})")
    pj = ziel / "project.json"
    d = json.loads(pj.read_text(encoding="utf-8"))
    d["title"] = titel
    pj.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Leeres POI-Dokument wie in den übrigen Prüfpaketen: statische Testserver liefern sonst HTML.
    leer = {"schemaVersion": 1, "projectId": d["projectId"], "pois": []}
    (ziel / "pois.json").write_text(json.dumps(leer) + "\n", encoding="utf-8")


def _punktebene(manifest: dict[str, Any]) -> dict[str, Any]:
    return cast(
        dict[str, Any], next(lay for lay in manifest["layers"] if lay.get("kind") == "pointcloud")
    )


def altpaket(quelle: Path, ziel: Path) -> None:
    """Kopie ohne ``pointSourceIsScan`` (wie ein Paket aus der Zeit vor der Scan-Zuordnung)."""
    shutil.copytree(quelle, ziel)
    pj = ziel / "project.json"
    d = json.loads(pj.read_text(encoding="utf-8"))
    d["projectId"] = ziel.name
    _punktebene(d).pop("pointSourceIsScan", None)
    d["title"] = f"{d['title']} (Altpaket)"
    pj.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    leer = {"schemaVersion": 1, "projectId": ziel.name, "pois": []}
    (ziel / "pois.json").write_text(json.dumps(leer) + "\n", encoding="utf-8")


def pruefe(ziel: Path) -> dict[str, Any]:
    """Sonderfälle am fertigen Paket prüfen; gibt die Erwartungswerte für die Tests zurück."""
    manifest = json.loads((ziel / KIRCHE / "project.json").read_text(encoding="utf-8"))
    st = json.loads((ziel / KIRCHE / "stations.json").read_text(encoding="utf-8"))["stations"]
    halle = json.loads((ziel / HALLE / "stations.json").read_text(encoding="utf-8"))["stations"]
    alt = _punktebene(json.loads((ziel / KIRCHE_ALT / "project.json").read_text(encoding="utf-8")))
    cloud = _punktebene(manifest)
    fehler: list[str] = []

    ids = [s["id"] for s in st]
    if ids != [sid for sid, _ in stationen()]:
        fehler.append(f"Standpunkte {ids[:3]}... statt S001 bis S045")
    by_id = {s["id"]: s for s in st}
    lo, hi = cloud["boundsWorld"]  # [[min x, y, z], [max x, y, z]]
    t = by_id[AUSSERHALB]["pose"]["translation"]
    if all(lo[i] <= t[i] <= hi[i] for i in range(3)):
        fehler.append(f"{AUSSERHALB} liegt innerhalb der Wolke")
    if by_id[OHNE_BODEN].get("groundZ") is not None:
        fehler.append(f"{OHNE_BODEN} hat groundZ {by_id[OHNE_BODEN]['groundZ']}")
    for s in st:
        pano = s.get("panorama") or {}
        if sorted(pano.get("levels", [])) != [512, 1024]:
            fehler.append(f"{s['id']}: Panorama-Stufen {pano.get('levels')}")
            break
    if halle[0].get("panorama"):
        fehler.append("e2e-halle S001 hat ein Panorama")
    if not all(s.get("panorama") for s in halle[1:]):
        fehler.append("e2e-halle: S002/S003 ohne Panorama")
    if cloud.get("pointSourceIsScan") is not True:
        fehler.append("e2e-kirche ohne pointSourceIsScan (verify gescheitert?)")
    if "pointSourceIsScan" in alt:
        fehler.append("e2e-kirche-alt trägt noch pointSourceIsScan")
    if fehler:
        raise SystemExit("Sonderfälle stimmen nicht:\n  " + "\n  ".join(fehler))

    return {
        "hinweis": (
            "Erzeugt von import/scripts/synthetisch_testkirche.py; "
            "synthetisch, kein Genauigkeitsnachweis"
        ),
        "pakete": {"v2": KIRCHE, "alt": KIRCHE_ALT, "ohnePanoramaErste": HALLE},
        "standpunkte": len(ids),
        "ersterStandpunkt": ids[0],
        "ausserhalbDerWolke": AUSSERHALB,
        "ohneBoden": OHNE_BODEN,
        "panoramaStufen": [512, 1024],
        "referenzScans": {"paar0": f"S{REF_SCANS[0] + 1:03d}", "paar1": f"S{REF_SCANS[1] + 1:03d}"},
        "projektId": KIRCHE,
    }


def referenz(e57: Path, ziel: Path) -> None:
    """Referenzpaare aus Scan 4 und 9 wählen (``reference_distances.py select``)."""
    skripte = Path(__file__).resolve().parent
    if str(skripte) not in sys.path:
        sys.path.append(str(skripte))
    from reference_distances import main as ref_main

    rc = ref_main(
        [
            "select",
            str(e57),
            "--out",
            str(ziel / f"{KIRCHE}-ref.json"),
            "--scans",
            ",".join(str(i) for i in REF_SCANS),
            "--per-scan",
            "1",
            "--count",
            "2",
            # Grobprüfung nimmt jeden 20. Punkt; bei 3 cm Abstand braucht sie einen größeren Radius,
            # um auf MIN_NEIGHBOURS zu kommen (echte Scans sind dichter).
            "--radius-coarse",
            "0.6",
        ]
    )
    if rc:
        raise SystemExit(f"Referenzpaare gescheitert ({rc})")
    pairs = json.loads((ziel / f"{KIRCHE}-ref.json").read_text())["pairs"]
    if [p["scan"] for p in pairs] != list(REF_SCANS):
        gefunden = [p["scan"] for p in pairs]
        raise SystemExit(f"Referenzpaare aus Scans {gefunden} statt {list(REF_SCANS)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ziel", type=Path, required=True, help="Datenordner (darf nicht existieren)")
    ap.add_argument("--abstand", type=float, default=0.03, help="Punktabstand in m (Standard 3 cm)")
    ap.add_argument(
        "--abstand-sued", type=float, default=0.008, help="Punktabstand der Südwand in m"
    )
    ap.add_argument("--radius", type=float, default=6.0, help="Reichweite je Scan in m")
    ap.add_argument("--bildgroesse", type=int, default=1024, help="Kantenlänge der Würfelseiten")
    args = ap.parse_args(argv)
    ziel: Path = args.ziel.expanduser()
    if ziel.exists():
        print(f"Ziel existiert bereits, nichts überschrieben: {ziel}", file=sys.stderr)
        return 2
    ziel.mkdir(parents=True)

    posen = [p for _, p in stationen()]
    welt = np.vstack([erzeuge_kirche(args.abstand), erzeuge_portal(args.abstand)])
    kirche = scans_aus(welt, posen, args.radius)
    dicht = scans_aus(erzeuge_suedwand_dicht(args.abstand_sued), posen, args.radius)
    for i in REF_SCANS:
        kirche[i] = np.vstack([kirche[i], dicht[i]])
    print(f"Kirche: {len(posen)} Scans, {sum(len(s) for s in kirche)} Punkte")
    halle_posen = [(3.0, 0.0, SENSOR_Z), (6.0, 0.0, SENSOR_Z), (9.0, 0.0, SENSOR_Z)]
    halle = scans_aus(erzeuge_halle(args.abstand), halle_posen, args.radius)
    print(f"Halle: 3 Scans, {sum(len(s) for s in halle)} Punkte")

    with tempfile.TemporaryDirectory(prefix="testkirche-") as tmp:
        e57_k, e57_h = Path(tmp) / "kirche.e57", Path(tmp) / "halle.e57"
        _schreibe_e57(e57_k, posen, kirche, set(), color_limits=True, image_size=args.bildgroesse)
        _schreibe_e57(
            e57_h, halle_posen, halle, {0}, color_limits=False, image_size=args.bildgroesse
        )
        _bauen(e57_k, ziel / KIRCHE, TITEL)
        _bauen(e57_h, ziel / HALLE, TITEL_HALLE)
        altpaket(ziel / KIRCHE, ziel / KIRCHE_ALT)
        referenz(e57_k, ziel)
    erwartung = pruefe(ziel)
    (ziel / f"erwartung-{KIRCHE}.json").write_text(
        json.dumps(erwartung, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Fertig: {ziel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
