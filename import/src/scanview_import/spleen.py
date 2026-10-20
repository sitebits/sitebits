"""Kleine Beigaben für neugierige Terminalnutzer; Daten reisen im Paket mit."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Sequence
from importlib.resources import files
from typing import Any, TypedDict, cast

from . import __version__


class EigenerSpruch(TypedDict):
    de: str
    en: str


class Zitat(TypedDict):
    text: str
    von: str
    quelle: str


class Sprueche(TypedDict):
    eigen: list[EigenerSpruch]
    zitate: list[Zitat]


def lade_sprueche() -> Sprueche:
    return cast(Sprueche, json.loads(files("scanview_import").joinpath("spleen_daten/sprueche.json")
                      .read_text(encoding="utf-8")))


def waehle_spruch(sprache: str = "de") -> str:
    daten = lade_sprueche()
    index = random.randrange(len(daten["eigen"]) + len(daten["zitate"]))
    if index < len(daten["eigen"]):
        return daten["eigen"][index]["de" if sprache.lower().startswith("de") else "en"]
    zitat = daten["zitate"][index - len(daten["eigen"])]
    return f"{zitat['text']} — {zitat['von']}, {zitat['quelle']}"


class VersionMitSpleen(argparse.Action):
    """Versionsausgabe beendet argparse vor der Prüfung des Unterkommandos."""

    def __call__(
        self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
        values: str | Sequence[Any] | None, option_string: str | None = None,
    ) -> None:
        print(f"scanview-import {__version__}")
        if sys.stdout.isatty():
            bilder = sorted(files("scanview_import").joinpath("spleen_daten/bilder").iterdir(),
                            key=lambda p: p.name)
            print(random.choice(bilder).read_text(encoding="utf-8").rstrip())
            print("© 2026 André Stiebitz · CC BY 4.0")
            print(waehle_spruch())
        parser.exit()
