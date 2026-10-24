"""`scanview-pruefen`: Lieferung gegen Anforderungskatalog prüfen, Prüfbericht schreiben.

Exit-Code: 0 keine Zeile „nicht erfüllt“, 1 mindestens eine, 2 Katalog- oder Eingabefehler.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import resource
import sys
import time
from importlib import metadata
from pathlib import Path
from typing import Any

from ..berichtsatz import git_stand, pdf_drucken
from . import bericht, pruefarten
from .dgm import lade as dgm_laden
from .flaeche import lade as flaechen_laden
from .katalog import KatalogFehler, lade
from .lieferung import erfasse

PROFILE = Path(__file__).parent / "profile"


def versionen() -> dict[str, str]:
    v = {p: metadata.version(p) for p in ("scanview-import", "laspy", "pye57", "pyproj", "Pillow")}
    v["python"] = platform.python_version()
    v["git"] = git_stand(Path(__file__).resolve().parent)
    return v


def pruefen(
    katalog: Path,
    lieferung: Path,
    paket: Path | None = None,
    flaeche: Path | None = None,
    flaeche_epsg: int | None = None,
    kontrollpunkte: Path | None = None,
    kontrollpunkte_crs: str | None = None,
    dgm: list[Path] | None = None,
    dgm_crs: str | None = None,
) -> dict[str, Any]:
    """Alle Zeilen prüfen; liefert das deterministische Ergebnis (ohne Zeitstempel)."""
    return pruefen_mit_karten(
        katalog,
        lieferung,
        paket,
        flaeche,
        flaeche_epsg,
        kontrollpunkte,
        kontrollpunkte_crs,
        dgm,
        dgm_crs,
    )[0]


def pruefen_mit_karten(
    katalog: Path,
    lieferung: Path,
    paket: Path | None = None,
    flaeche: Path | None = None,
    flaeche_epsg: int | None = None,
    kontrollpunkte: Path | None = None,
    kontrollpunkte_crs: str | None = None,
    dgm: list[Path] | None = None,
    dgm_crs: str | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Wie ``pruefen``, dazu die PNG-Karten je Dateiname (nicht im Ergebnis-Hash)."""
    kat = lade(katalog)
    lf = erfasse(lieferung, paket)
    if flaeche is not None:
        lf.flaeche = flaechen_laden(flaeche, flaeche_epsg)
        lf.extern[f"Auftragsfläche {flaeche.name}"] = lf.flaeche.sha256
    if kontrollpunkte is not None:
        lf.kontrollpunkte = (kontrollpunkte, kontrollpunkte_crs)
    if dgm:
        lf.dgm = dgm_laden(dgm, dgm_crs)
        for name, sha in lf.dgm.dateien:
            lf.extern[f"Referenz-DGM {name}"] = sha
    roh: list[tuple[Any, pruefarten.Ergebnis | None]] = [
        (z, None if z.pruefart == pruefarten.BERICHT else pruefarten.pruefe(z, lf))
        for z in kat.zeilen
    ]
    e: dict[str, Any] = {
        "katalog": {"titel": kat.titel, "datei": kat.datei, "sha256": kat.sha256},
        "lieferung": {"name": lf.wurzel.name, "dateien": len(lf.dateien), "links": lf.links},
        "paket": {"projectId": lf.paket.get("projectId", "")} if lf.paket else None,
        "versionen": versionen(),
        "eingaenge_sha256": {r: lf.sha256(r) for r in sorted(lf.gelesen)},
        "eingaenge_extern_sha256": dict(sorted(lf.extern.items())),
    }
    # „bericht“ prüft die Pflichtteile, die vor dem Urteil feststehen (Kette, Grenzen, …).
    e["zeilen"] = []
    karten: dict[str, bytes] = {}
    fehlend = bericht.pflichtteile(bericht.feste_teile(e))
    for z, erg in roh:
        if erg is None:
            erg = pruefarten.Ergebnis(
                pruefarten.NICHT_ERFUELLT if fehlend else pruefarten.ERFUELLT,
                f"Pflichtteile fehlen: {', '.join(fehlend)}"
                if fehlend
                else "dieser Bericht enthält alle Pflichtteile der Berichts-Norm "
                "(Frage und Antwort, Kette mit Prüfsummen, Schwellen, Grenzen, Datenschutz, "
                "Anhang); Aufnahme entfällt bei fremder Lieferung",
            )
        namen = {f"{z.id}-{n}.png": b for n, b in sorted(erg.bilder.items())}
        karten |= namen
        e["zeilen"].append(
            {
                "id": z.id,
                "anforderung": z.anforderung,
                "pruefart": z.pruefart,
                "parameter": z.parameter,
                "karten": sorted(namen),
                **erg.als_dict(),
            }
        )
    e["gesamt"] = bericht.gesamt(e["zeilen"])
    return e, karten


def _csv(zeilen: list[dict[str, Any]]) -> str:
    spalten = list(dict.fromkeys(k for z in zeilen for k in z))
    return "".join(
        ";".join(_feld(z.get(k)) for k in spalten) + "\n"
        for z in [dict(zip(spalten, spalten, strict=True)), *zeilen]
    )


def _feld(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return repr(v).replace(".", ",")
    return str(v).replace(";", ",")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scanview-pruefen",
        description="Lieferung gegen Anforderungskatalog prüfen (Stufen P1 bis P3, B1).",
        epilog="Exit 0: nichts „nicht erfüllt“; 1: mindestens eine Zeile nicht erfüllt; "
        "2: Katalog- oder Eingabefehler. Beispielprofil: --profil bestandserfassung",
    )
    p.add_argument("lieferung", type=Path, help="Ordner mit den gelieferten Dateien")
    quelle = p.add_mutually_exclusive_group(required=True)
    quelle.add_argument("--katalog", type=Path, help="Anforderungskatalog (TOML, schema = 1)")
    quelle.add_argument("--profil", help="mitgeliefertes Profil, z. B. bestandserfassung")
    p.add_argument("--paket", type=Path, help="siteBits-Paket als weitere Belegquelle")
    p.add_argument("--flaeche", type=Path, help="Auftragsfläche (GeoJSON) für Dichte, Lücken, GSD")
    p.add_argument(
        "--flaeche-epsg", type=int, help="EPSG der Fläche, wenn das GeoJSON nicht WGS84 ist"
    )
    p.add_argument(
        "--kontrollpunkte", type=Path, help="eigene Kontrollpunktliste des Auftraggebers (CSV)"
    )
    p.add_argument("--kontrollpunkte-crs", help="Bezugssystem der Liste, z. B. EPSG:25833+7837")
    p.add_argument(
        "--dgm",
        type=Path,
        action="append",
        help="Referenz-DGM des Auftraggebers (GeoTIFF), mehrfach für Kacheln",
    )
    p.add_argument("--dgm-crs", help="Bezugssystem des DGM, wenn die Datei es nicht nennt")
    p.add_argument("--aus", type=Path, required=True, help="Ausgabeordner")
    p.add_argument("--pdf", action="store_true", help="zusätzlich bericht.pdf (Chromium)")
    return p


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    katalog = a.katalog or PROFILE / f"{a.profil}.toml"
    t0 = time.monotonic()
    try:
        if not katalog.is_file():
            raise KatalogFehler(f"Katalog fehlt: {katalog}")
        e, karten = pruefen_mit_karten(
            katalog,
            a.lieferung,
            a.paket,
            a.flaeche,
            a.flaeche_epsg,
            a.kontrollpunkte,
            a.kontrollpunkte_crs,
            a.dgm,
            a.dgm_crs,
        )
    except (KatalogFehler, FileNotFoundError) as fehler:
        print(f"Fehler: {fehler}", file=sys.stderr)
        return 2
    aus: Path = a.aus
    aus.mkdir(parents=True, exist_ok=True)
    sha = bericht.ergebnis_hash(e)
    (aus / "ergebnis.json").write_text(
        json.dumps(e, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    (aus / "pruefsummen.sha256").write_text(
        "".join(f"{h}  {r}\n" for r, h in e["eingaenge_sha256"].items()), encoding="utf-8"
    )
    for name, png in karten.items():
        (aus / name).write_bytes(png)
    for z in e["zeilen"]:
        if z.get("tabelle"):
            (aus / f"{z['id']}-messwerte.csv").write_text(_csv(z["tabelle"]), encoding="utf-8")
    erstellt = dt.datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")
    html_datei = aus / "bericht.html"
    html_datei.write_text(
        bericht.html_bericht(e, bericht.teile(e), sha, erstellt, karten), encoding="utf-8"
    )
    if a.pdf:
        pdf_drucken(html_datei, aus / "bericht.pdf")
    lauf = {
        "erstellt": erstellt,
        "gesamt_s": round(time.monotonic() - t0, 2),
        "ram_spitze_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        "ergebnis_sha256": sha,
    }
    (aus / "lauf.json").write_text(json.dumps(lauf, ensure_ascii=False, indent=1) + "\n")
    g = e["gesamt"]
    print(
        json.dumps(
            {"urteil": g["urteil"], "zaehlung": g["zaehlung"], "aus": str(aus)},
            ensure_ascii=False,
        )
    )
    return 1 if g["zaehlung"][pruefarten.NICHT_ERFUELLT] else 0


if __name__ == "__main__":
    sys.exit(main())
