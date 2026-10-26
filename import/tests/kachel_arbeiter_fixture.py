"""Importierbare Initialisierer für spawn-Prozesstests."""

from dataclasses import replace

import numpy as np
from test_kacheln import fake_ktx

from scanview_import.kacheln import parallel
from scanview_import.kacheln.backen import backen as echtes_backen
from scanview_import.kacheln.parallel import _initialisieren as echter_start


def start_mit_encoder(budget):
    echter_start(budget)
    from scanview_import.kacheln import paket

    paket.encode_texture = fake_ktx


def mehr_flaechen(mesh, source, error, **kwargs):
    result, textures = echtes_backen(mesh, source, error, **kwargs)
    # Synthetische Seitenteilung erzwingt die sonst seltene Nachvereinfachung.
    if len(mesh.triangles) > 3:
        result = replace(result, triangles=np.tile(result.triangles, (4, 1)),
                         materials=np.tile(result.materials, 4))
    return result, textures


def start_mit_teilung(budget):
    echter_start(budget)
    parallel.backen = mehr_flaechen


def defekter_auftrag(*args, **kwargs):
    raise ValueError("Synthetischer Arbeiterfehler")


def start_mit_fehler(budget):
    echter_start(budget)
    parallel.schreiben = defekter_auftrag


def start_mit_packfehler(budget):
    echter_start(budget)
    from scanview_import.kacheln import paket

    paket.encode_texture = defekter_auftrag


