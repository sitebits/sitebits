"""Begrenzte Kachelaufträge; spawn erbt weder Quellmesh noch offene Bildcaches."""

from __future__ import annotations

import atexit
import multiprocessing as mp
import os
import resource
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from ..mesh_source import Mesh, read_mesh
from .backen import backen
from .glb import schreiben
from .texturen import BildCache, Insel, zuschneiden

STANDARD_JOBS = min(8, os.cpu_count() or 1)
_cache: BildCache | None = None


class Nachvereinfachung(Exception):
    """Texturteilung verlangt Neuaufbau mit korrigierten Kindgeometrien."""


def _initialisieren(budget: int) -> None:
    global _cache
    _cache = BildCache(budget)
    atexit.register(_cache.schliessen)


def texturauftrag(
    submitted: float, reduced: Mesh, combined: Mesh | None, error: float,
    maximum: int, plan: tuple[list[Insel], list[Mesh]] | None,
    path: Path, origin: np.ndarray, root: bool, budget: int, expected_rounding: float,
) -> dict[str, Any]:
    started = time.monotonic()
    assert _cache is not None
    stats: dict[str, Any] = {}
    if combined is not None:
        textured, textures = backen(reduced, combined, error, cache=_cache, messung=stats)
    else:
        textured, textures = zuschneiden(
            reduced, cache=_cache, maximum=maximum, messung=stats, plan=plan,
        )
    if len(textured.triangles) > budget:
        raise Nachvereinfachung(path.name)
    rounding = schreiben(textured, path, origin, textures, node_origin=root)
    if rounding != expected_rounding:
        raise Nachvereinfachung(path.name + ": geänderte Ausgabepositionen")
    stats.update(triangles=len(textured.triangles), bytes=path.stat().st_size,
                 worker=os.getpid(), queueSeconds=started - submitted,
                 completedMonotonic=time.monotonic(), texturCacheBytesBudget=_cache.grenze)
    return stats


def packauftrag(
    submitted: float, path: Path, dest: Path, origin: np.ndarray, root: bool,
) -> tuple[str, float, int, dict[str, Any]]:
    from .paket import Packschritt

    reference = read_mesh(path)
    if not root:
        reference.positions += origin
    packer = Packschritt(dest.parent.parent, None, None, vorhanden=True)
    packer.kachel(reference, path.name, origin, root)
    return path.name, packer.steps[0], packer.counts[path.name], packer.reports[path.name]


class Auftraege:
    """Hauptprozess hält höchstens 2N Futures; die RSS-Summe wird abgetastet.

    RSS zählt gemeinsam genutzte Bibliotheksseiten mehrfach. Linux-Abtastung
    alle 20 ms kann kurze Spitzen verpassen; kein cgroup-Höchststand.
    """

    def __init__(self, jobs: int, budget: int, previous_peak: float = 0.0):
        self.parallel = jobs > 1
        self.pool = (ProcessPoolExecutor(
            max_workers=jobs, mp_context=mp.get_context("spawn"),
            initializer=_initialisieren, initargs=(budget,),
        ) if self.parallel else None)
        self._before = {p.pid for p in mp.active_children()}
        self._peak = previous_peak
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._messen, name="kachel-rss", daemon=True)
        self._thread.start()

    def _probe(self) -> None:
        pids = [os.getpid()] + [p.pid for p in mp.active_children()
                               if p.pid is not None and p.pid not in self._before]
        total = 0
        for pid in pids:
            try:
                for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1])
                        break
            except (FileNotFoundError, ProcessLookupError):
                pass  # Prozess ist zwischen PID-Abfrage und RSS-Lesen beendet worden.
        self._peak = max(self._peak, total / 1024)

    def _messen(self) -> None:
        while not self._stop.wait(0.02):
            self._probe()

    def peak_mib(self) -> float:
        return max(self._peak, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)

    def abgeben(self, fn: Callable[..., Any], *args: Any) -> Future[Any]:
        assert self.pool is not None
        return self.pool.submit(fn, time.monotonic(), *args)

    def schliessen(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=True, cancel_futures=True)
        self._stop.set()
        self._thread.join()
