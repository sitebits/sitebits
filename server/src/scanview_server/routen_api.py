"""Öffentliche API: Gesundheit, Sitzung, Projekte-Liste, forward_auth-Prüfung (Caddy)."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from . import faden, fotos_tor, konten, lesezugriff, projektverwaltung, teilen
from .katalog import PROJECT_ID_RE, Karte
from .web import DIENST, aktueller_nutzer, db, fehler
from .zugriff import angemeldet

router = APIRouter()
log = logging.getLogger("scanview.api")

# Dieselbe projectId-Form wie im Katalog — aus PROJECT_ID_RE abgeleitet statt erneut
# ausgeschrieben, damit beide Stellen nicht auseinanderlaufen können.
_PID_MUSTER = PROJECT_ID_RE.pattern.removeprefix("^").removesuffix("$")
_DATA_RE = re.compile(rf"^/data/(?P<pid>{_PID_MUSTER})/(?P<rest>[A-Za-z0-9._/-]+)$")
_VERBOTEN_KODIERT = re.compile(r"%(2e|2f|5c)", re.IGNORECASE)
# Nur Dateiarten, die ein Paket tatsächlich enthält oder künftig enthalten wird
# (docs/architektur.md, Abschnitte „Projektpaket (Schema 2)“ und „Geplant“) — Manifeste
# (.json, auch tileset.json/.gltf), Punktwolke (.laz/.copc.laz), Bilder (Draufsicht, Panoramen),
# Meshes (.glb/.bin/.ktx2), 3D Tiles (.b3dm/.pnts/.i3dm/.cmpt/.subtree),
# Splats (.ply/.splat/.spz/.rad/.radc).
# Alles andere (.html, .svg, .js, …) → 404: aktive Inhalte eines Pakets liefen sonst im Origin
# der Plattform (gespeichertes XSS). Caddy setzt auf /data zusätzlich CSP `sandbox`.
PAKET_ENDUNGEN = frozenset({
    "json", "laz", "png", "jpg", "jpeg", "webp", "bin", "glb", "gltf", "ktx2",
    "b3dm", "pnts", "i3dm", "cmpt", "subtree", "ply", "splat", "spz", "rad", "radc",
})


def karte_json(
    k: Karte, meta: sqlite3.Row | None, rolle: str,
    einst: projektverwaltung.Einstellungen = projektverwaltung.VORGABE,
) -> dict[str, Any]:
    """Projektkarte für Portal und Viewer. project_meta überlagert Titel/Beschreibung des Pakets
    nur in der Anzeige; `version` ist die optimistische Sperre der Metadaten (0 = nie geändert)."""
    return {
        "id": k.id,
        "titel": (meta["title"] if meta and meta["title"] else k.titel),
        "beschreibung": (meta["description"] if meta and meta["description"] else ""),
        "punkte": k.punkte, "standpunkte": k.standpunkte,
        "ausdehnung": list(k.ausdehnung) if k.ausdehnung else None,
        "crs": k.crs, "scandatum": k.scandatum, "groesseBytes": k.groesse_bytes,
        # Koordinaten- und Höhensystem wie im CSV-Kopf (F23), für Agenten (KI-Parität).
        "einheit": k.einheit,
        "draufsicht": k.draufsicht, "rolle": rolle,
        "genauigkeit": [g.als_json() for g in k.genauigkeit],
        "version": int(meta["version"]) if meta else 0,
        "einstellungen": einst.als_json(),
        "befund_faeden": einst.befund_faeden,
    }


@router.get("/api/gesundheit", include_in_schema=False)
async def gesundheit() -> dict[str, bool]:
    # async: kein Thread aus dem gemeinsamen Pool nötig (WB-M3).
    return {"ok": True}


@router.get("/api/sitzung", include_in_schema=False)
@router.get("/api/v1/sitzung", summary="Angemeldeter Nutzer (401 ohne Sitzung)")
def sitzung(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    # Gast eines Nur-Lese-Links (F28): nur auf dem Browser-Weg /api/sitzung und nur ohne Konto-
    # Sitzung; der Viewer erkennt daran die Gast-Ansicht. Mit Konto zusätzlich `gast`, damit ein
    # angemeldeter Nutzer ohne Rolle im Projekt den Link trotzdem öffnen kann (?gast=1).
    g = (teilen.gast(conn, request.cookies.get(teilen.GAST_COOKIE))
         if request.url.path == "/api/sitzung" else None)
    gast_json = ({"projekt": g.project_id, "name": g.name, "ablauf": g.ablauf}
                 if g is not None else None)
    if g is not None and aktueller_nutzer(request, conn) is None:
        return JSONResponse({"dienst": DIENST, "nutzer": None, "gast": gast_json,
                             "quelltext_url": request.app.state.settings.quelltext_url})
    # Über zugriff.angemeldet — Bearer (nur /api/v1/sitzung, bearer_pfad), Drossel, 401.
    n = angemeldet(request, conn)
    # quelltext_url: Quelltext-Link (EUPL-1.2) der Instanz (SCANVIEW_QUELLTEXT_URL),
    # null = kein Link.
    return JSONResponse(
        {"dienst": DIENST,
         "nutzer": {"id": n.id, "name": n.name, "email": n.email, "admin": n.is_admin},
         **({"gast": gast_json} if gast_json is not None else {}),
         "quelltext_url": request.app.state.settings.quelltext_url},
    )


def _ziel_aus_uri(uri: str) -> tuple[str, str] | None:
    """Leitet aus der von Caddy weitergereichten Original-URI die projectId ab.

    Gibt bei jedem Pfadtrick None zurück: kodierte Trennzeichen (%2e/%2f/%5c, case-insensitiv)
    im ungequoteten Rohpfad, Backslashes, falsches Präfix, Groß-/Kleinschreibung, leere oder
    mit Punkt beginnende Segmente, Dateiendung nicht in PAKET_ENDUNGEN. Query-String wird vor
    jeder Prüfung abgeschnitten, `unquote` läuft genau einmal.
    """
    pfad = uri.split("?", 1)[0]
    if _VERBOTEN_KODIERT.search(pfad) or "\\" in pfad:
        return None
    pfad = unquote(pfad)
    # .fullmatch, nicht .match: .match akzeptiert einen Zeilenumbruch am Ende,
    # weil `$` davor statt am echten Stringende greift (z. B. ".../cloud.copc.laz%0a").
    m = _DATA_RE.fullmatch(pfad)
    if not m:
        return None
    segmente = m["rest"].split("/")
    for seg in segmente:
        if not seg or seg.startswith("."):
            return None
    name = segmente[-1]
    if "." not in name or name.rsplit(".", 1)[1].lower() not in PAKET_ENDUNGEN:
        return None
    return m["pid"], m["rest"]


@router.get("/api/authz", include_in_schema=False)
def authz(request: Request, conn: sqlite3.Connection = Depends(db)) -> Response:
    """`forward_auth`-Ziel für Caddy: Header `X-Forwarded-Uri`/`X-Forwarded-Method`.

    Leistung: kein Cache, kein argon2 — höchstens die Sitzungs- und die Rollenabfrage
    (je ein indizierter SELECT) plus ein gedrosseltes last_seen-UPDATE (`konten.sitzung_nutzer`).
    Bei SCANVIEW_LOG_LEVEL=DEBUG genau eine Protokollzeile je Aufruf: Status, projectId
    (oder „-“) und Dauer — nie IP, Cookie, Token, E-Mail oder die volle angefragte URI.
    """
    begonnen = time.perf_counter()
    # Fail-closed: fehlt der Header komplett, ist das kein regulärer
    # forward_auth-Aufruf von Caddy (das schickt ihn immer) — nicht stillschweigend GET
    # unterstellen, sondern ablehnen.
    verfahren = request.headers.get("x-forwarded-method")
    if verfahren is None or verfahren.upper() not in ("GET", "HEAD"):
        _log_authz(begonnen, 404, None)
        return fehler(404, "nicht gefunden")
    n = aktueller_nutzer(request, conn)
    ziel = _ziel_aus_uri(request.headers.get("x-forwarded-uri", ""))
    rolle = konten.rolle(conn, n, ziel[0]) if n is not None and ziel is not None else None
    if rolle is None:
        # Kein Konto oder keine Rolle in diesem Projekt: Gast eines Nur-Lese-Links (F28)?
        g = teilen.gast(conn, request.cookies.get(teilen.GAST_COOKIE))
        if g is not None:
            status = _gast_pruefen(request, g, ziel)
            _log_authz(begonnen, status, ziel[0] if ziel else None)
            if status == 200:
                return Response(status_code=200)
            return fehler(status, "nicht gefunden" if status == 404 else "zu viele Anfragen")
    if n is None:
        _log_authz(begonnen, 401, None)
        return fehler(401, "nicht angemeldet")
    if ziel is None:
        _log_authz(begonnen, 404, None)
        return fehler(404, "nicht gefunden")
    pid, rest = ziel
    if request.app.state.katalog.get(pid) is None or rolle is None:
        _log_authz(begonnen, 404, pid)
        return fehler(404, "nicht gefunden")
    # Orientierte Fotos (Personen): ohne freigegebene Anonymisierung nur Bearbeiter+.
    if (
        fotos_tor.ist_fotopfad(rest)
        and not konten.mindestens(rolle, fotos_tor.MINDESTROLLE)
        and not fotos_tor.freigegeben(request.app.state.katalog.data_dir / pid)
    ):
        _log_authz(begonnen, 404, pid)
        return fehler(404, "nicht gefunden")
    # Erst nach bestandener Rechteprüfung: der Viewer lädt sein Projekt über diesen Weg
    # (project.json), nicht über `projekt_lesen`. Der Merker hält den heißen Pfad schreibfrei.
    lesezugriff.erfasse_nutzer(request, conn, n.id, pid, "paket")
    _log_authz(begonnen, 200, pid)
    return Response(status_code=200)


def _gast_pruefen(request: Request, g: teilen.Gast, ziel: tuple[str, str] | None) -> int:
    """Status für eine Gast-Anfrage an /data: 200 nur für eine Datei der Positivliste im Projekt
    des Links (exakter Pfadvergleich nach `_ziel_aus_uri`), keine Mehrfach-Range, innerhalb der
    Abrufgrenze je Link. Alles andere 404 (verrät nichts über das Paket), über der Grenze 429."""
    if ziel is None or ziel[0] != g.project_id:
        return 404
    katalog = request.app.state.katalog
    gp = teilen.gast_paket(katalog.data_dir / g.project_id) if katalog.get(g.project_id) else None
    if gp is None or ziel[1] not in gp.dateien or fotos_tor.ist_fotopfad(ziel[1]):
        return 404
    if "," in request.headers.get("range", ""):
        return 404
    if not request.app.state.teilen_drosseln.abrufe.erlaubt(g.link_id):
        return 429
    return 200


def _log_authz(begonnen: float, status: int, pid: str | None) -> None:
    if not log.isEnabledFor(logging.DEBUG):
        return
    dauer_ms = (time.perf_counter() - begonnen) * 1000
    log.debug("authz %s %s %.1f ms", status, pid or "-", dauer_ms)


@router.get("/api/projekte", include_in_schema=False)
@router.get("/api/v1/projekte", summary="Projekte, auf die der Nutzer ein Recht hat")
def projekte(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    n = angemeldet(request, conn)
    meta = {r["project_id"]: r for r in conn.execute("SELECT * FROM project_meta")}
    out: list[dict[str, Any]] = []
    einst = projektverwaltung.alle_einstellungen(conn)
    tag = faden.heute()
    for k in request.app.state.katalog.alle():
        e = einst.get(k.id, projektverwaltung.VORGABE)
        r = projektverwaltung.wirksame_rolle(konten.rolle(conn, n, k.id), e)
        if r is None:
            continue
        karte = karte_json(k, meta.get(k.id), r, e)
        # Befund-Fäden: „3 überfällig“ auf der Projektkarte, wenn ich (oder mein Gewerk) zuständig.
        karte["ueberfaellig_meine"] = (
            faden.ueberfaellig_meine(conn, k.id, faden.sicht(conn, k.id, n.id, r), tag)
            if e.befund_faeden else 0)
        out.append(karte)
    out.sort(key=lambda x: str(x["titel"]).lower())
    return JSONResponse({"projekte": out})
