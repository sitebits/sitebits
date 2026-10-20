"""Mobilstufe nachträglich: ein vorhandenes Paket ohne Originalquelle um ``mobile.glb`` ergänzen.

Abgeleitet wird aus der ausgelieferten Vollstufe ``model.glb`` (meshopt dekodiert, KTX2-Texturen
im KTX-Container nach RGBA entpackt), vereinfacht und neu kodiert wie beim Import. Das Ziel ist
eine Hardlink-Kopie des Quellpakets: die Dateien teilen sich die Inodes; alles, was geändert wird
oder später von Werkzeugen an Ort und Stelle beschrieben werden könnte (JSON, PNG), ist
eine echte Kopie. Geändertes wird nie überschrieben, sondern per ``os.replace`` ersetzt — so bleibt
das Quellpaket unberührt. Veröffentlicht wird erst nach bestandener Prüfung, per ``rename``.
"""

from __future__ import annotations

import copy
import errno
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from .manifest_io import origin_of
from .mesh_convert import (
    MOBIL_DREIECKE,
    MOBIL_KODIERUNG,
    MOBIL_MAX_PX,
    MOBIL_URL,
    mobil_felder,
    mobil_ziel,
    stage_mesh,
    write_stage,
)
from .mesh_source import read_mesh
from .mesh_stages import compact, remove_degenerate
from .mesh_verify import measure_stage, mobil_warnungen, texture_report

# JSON und PNG (Draufsicht) werden kopiert statt verlinkt: verify, start-sichtbar und overview
# schreiben sie an Ort und Stelle und träfen sonst das Quellpaket mit. Alles andere (auch die
# vielen kleinen Foto- und Panoramabilder, zusammen Gigabytes) wird verlinkt.
KOPIEREN_ENDUNGEN = (".json", ".png")


def hardlink_baum(quelle: Path, ziel: Path) -> dict[str, int]:
    """``quelle`` nach ``ziel`` (vorhandener, leerer Ordner) spiegeln: Hardlinks, nur JSON und
    PNG kopiert. Symlinks (z. B. ein Ordner aus Verweisen auf ein älteres Paket)
    werden aufgelöst; das Ziel enthält keine Symlinks."""
    stats = {"links": 0, "kopien": 0, "kopienAndereDateisysteme": 0}
    gesehen: set[Path] = set()

    def spiegeln(q: Path, z: Path) -> None:
        real = q.resolve()
        if real in gesehen:
            raise ValueError(f"Ordnerzyklus im Paket: {q}")
        gesehen.add(real)
        for eintrag in sorted(q.iterdir()):
            t = z / eintrag.name
            if eintrag.is_dir():
                t.mkdir()
                spiegeln(eintrag, t)
            elif eintrag.is_file():
                datei = eintrag.resolve()
                if datei.suffix.lower() in KOPIEREN_ENDUNGEN:
                    shutil.copy2(datei, t)
                    stats["kopien"] += 1
                    continue
                try:
                    os.link(datei, t)
                    stats["links"] += 1
                except OSError as exc:
                    if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EMLINK):
                        raise
                    shutil.copy2(datei, t)
                    stats["kopienAndereDateisysteme"] += 1
            else:
                raise ValueError(f"Unerwarteter Eintrag im Paket (defekter Symlink?): {eintrag}")

    spiegeln(quelle, ziel)
    if stats["kopienAndereDateisysteme"]:
        print(
            f"HINWEIS: {stats['kopienAndereDateisysteme']} Dateien kopiert statt verlinkt "
            "(anderes Dateisystem oder keine Linkrechte)",
            file=sys.stderr,
        )
    return stats


def _ersetzen(pfad: Path, daten: dict[str, Any]) -> None:
    """Neue Datei schreiben und per rename einsetzen — löst einen Hardlink, statt ihn zu
    beschreiben."""
    tmp = pfad.with_name(f".{pfad.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(daten, indent=2, ensure_ascii=False, allow_nan=False))
    os.replace(tmp, pfad)


def _mesh_ebene(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("schemaVersion") != 2:
        raise ValueError("mobilstufe benötigt ein Schema-2-Paket")
    ebenen: list[dict[str, Any]] = [
        lay
        for lay in manifest.get("layers", [])
        if isinstance(lay, dict) and lay.get("kind") == "mesh"
    ]
    if len(ebenen) != 1 or ebenen[0].get("format") != "gltf":
        raise ValueError("Paket braucht genau eine glTF-Mesh-Ebene")
    if ebenen[0].get("mobileUrl"):
        raise ValueError("Mesh-Ebene hat bereits eine Mobilstufe")
    return ebenen[0]


def _bericht(work: Path, layer: dict[str, Any], rep: dict[str, Any], dauer: float) -> None:
    """Mobilstufe in den vorhandenen Konvertierungsbericht der Ebene eintragen."""
    pfad = work / layer.get("verify", {}).get("report", "layers/mesh/conversion-report.json")
    bericht: dict[str, Any] = json.loads(pfad.read_text()) if pfad.is_file() else {"stages": {}}
    bericht.setdefault("stages", {})["mobile"] = rep
    bericht["warnungen"] = [*bericht.get("warnungen", []), *mobil_warnungen(bericht["stages"])]
    bericht["mobilstufe"] = {
        "referenz": layer["url"],
        "hinweis": "nachträglich aus der Vollstufe abgeleitet; Abstände gegen model.glb, "
        "nicht gegen die Originalquelle",
        "seconds": dauer,
        "hostPeakRssMb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    }
    bericht["ok"] = bool(bericht.get("ok", True) and rep["ok"])
    if "layerSizeBytes" in bericht:
        bericht["layerSizeBytes"] += rep["sizeBytes"]
    pfad.parent.mkdir(parents=True, exist_ok=True)
    _ersetzen(pfad, bericht)


def mobilstufe(quelle: Path, ziel: Path, mobil_dreiecke: int = MOBIL_DREIECKE) -> dict[str, Any]:
    """Neues Paket ``ziel`` = ``quelle`` + Mobilstufe, ``revision`` + 1. ``quelle`` bleibt
    unverändert; ``ziel`` darf nicht existieren."""
    if mobil_dreiecke <= 0:
        raise ValueError("--mobil-dreiecke muss größer als 0 sein")
    if ziel.exists() or ziel.is_symlink():
        raise FileExistsError(f"Zielpaket existiert bereits: {ziel}")
    roh = (quelle / "project.json").read_bytes()
    manifest: dict[str, Any] = json.loads(roh)
    layer = _mesh_ebene(manifest)
    if (quelle / MOBIL_URL).exists():
        raise ValueError(f"{MOBIL_URL} liegt schon im Quellpaket, ist aber nicht eingetragen")
    origin = np.asarray(origin_of(manifest), dtype=np.float64)
    opts = layer.get("sourceOptions", {})
    ziel.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{ziel.name}-mobil-", dir=ziel.parent))
    try:
        os.chmod(work, quelle.stat().st_mode & 0o777)
        verteilung = hardlink_baum(quelle, work)
        t0 = time.monotonic()
        model = read_mesh(work / layer["url"])
        model.positions += origin
        # Alle Primitive teilen sich in model.glb einen Vertexpuffer; der Leser legt ihn je
        # Material neu an. Unbenutzte Kopien entfernen, bevor vereinfacht wird.
        model = compact(model, model.triangles, model.materials)
        clean, source_removal = remove_degenerate(model)
        ziel_dreiecke = mobil_ziel(mobil_dreiecke, len(clean.triangles))
        mobile, info, removal = stage_mesh(clean, ziel_dreiecke, origin, lock_border=True)
        if not info["zielErreicht"]:
            print(f"WARNUNG Stufe mobile: {info['grund']} — kleinste nahterhaltende Stufe",
                  file=sys.stderr)
        reports, quant, step = write_stage(
            mobile, work / MOBIL_URL, origin, MOBIL_MAX_PX, MOBIL_KODIERUNG
        )
        info["erreicht"] = len(mobile.triangles) - quant["removed"]
        info["abgeleitetAus"] = layer["url"]
        stage_removal = {"simplification": removal, "quantization": quant}

        # Prüfung: unabhängig zurücklesen, gegen model.glb messen (Originalquelle fehlt).
        output = read_mesh(work / MOBIL_URL)
        output.positions += origin
        removed = source_removal["removed"] + removal["removed"] + quant["removed"]
        rep = measure_stage(
            model,
            output,
            opts["toleranzM"],
            False,
            ziel_dreiecke,
            info["erreicht"] if info["zielErreicht"] is False else None,
            removed,
            opts.get("ausdehnungToleranzM"),
        )
        rep["referenz"] = layer["url"]
        rep["degenerateRemoval"] = stage_removal
        rep["positionStepM"] = step
        rep["textures"] = texture_report(
            model, output, MOBIL_MAX_PX, f"ktx2-{MOBIL_KODIERUNG}"
        )
        rep["sizeBytes"] = (work / MOBIL_URL).stat().st_size
        rep["ok"] = bool(rep["ok"] and rep["textures"]["ok"])
        if not rep["ok"]:
            felder = ("counts", "stageToSource", "sourceToStage", "boundsMaxMm", "targetOk")
            raise ValueError(
                "Mobilstufe-Prüfung fehlgeschlagen: "
                + json.dumps({k: rep.get(k) for k in felder} | {"textures": rep["textures"]["ok"]})
            )

        neu = copy.deepcopy(manifest)
        neue_ebene = _mesh_ebene(neu)
        mobil_felder(
            neue_ebene, info, stage_removal, step, reports, ziel_dreiecke, rep["sizeBytes"]
        )
        dauer = time.monotonic() - t0
        neue_ebene.setdefault("conversion", {})["mobile"] = {
            "seconds": dauer,
            "abgeleitetAus": layer["url"],
            "containerPeakRssMb": max((r["peakRssMb"] for r in reports), default=0),
            "containerSeconds": sum(r["seconds"] for r in reports),
            "dateien": verteilung,
        }
        neu["revision"] = int(manifest.get("revision") or 0) + 1
        _bericht(work, layer, rep, dauer)
        _ersetzen(work / "project.json", neu)
        if (quelle / "project.json").read_bytes() != roh:
            raise ValueError("Quellmanifest während mobilstufe geändert; keine Veröffentlichung")
        if ziel.exists() or ziel.is_symlink():
            raise FileExistsError(f"Zielpaket inzwischen angelegt: {ziel}")
        os.rename(work, ziel)
        return neu
    finally:
        if work.exists():
            shutil.rmtree(work)
