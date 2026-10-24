"""/api/v1/projekte/{pid}/auswertungen: Auswertungsjobs einreihen, lesen, freigeben.

Der Webdienst startet nie einen Prozess (R-E-F-1): Er schreibt den Auftrag in `jobs.db`, der
Import-Worker rechnet ihn isoliert und legt den Bericht unter `<berichte>/<projekt>/<auftrag>/`
ab. Hier wird nur gelesen; auch Löschen ist eine Anforderung, die der Worker ausführt (er besitzt
die Berichtsordner, der Webdienst braucht dort keine Schreibrechte).

Rechte:
- Starten, alle Berichte sehen: Projektleitung und Admin, auch per Token — ein Token für einen
  KI-Agenten darf starten (der Job rechnet nur und ändert nichts am Projekt).
- Bearbeiter sehen nur Berichte, die die Projektleitung freigegeben hat; Betrachter und
  Rückmelder sehen keine.
- Freigeben und Löschen: Projektleitung und Admin, nicht über einen Token für KI-Agenten.
- Fremdes Projekt → 404, Rolle zu klein → 403 (Rangfolge wie zugriff.projekt).

Was der Worker schreibt, ist für den Webdienst fremd (der Job hat fremde Dateien gelesen):
Dateien werden ohne Symlinks und mit Größengrenze gelesen, der Bericht nur als Download mit
einer CSP ohne Skripte ausgeliefert.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auftraege, konten, lesezugriff, protokoll
from .api_modelle import (
    FEHLER_ALS_JSON,
    Auswertung,
    AuswertungMitWorker,
    Auswertungsliste,
    Ergebnis,
    antwort,
)
from .db import transaktion
from .web import Ungueltig, csrf, db, fehler, kanal, ki, token_name
from .zugriff import NICHT_GEFUNDEN, Zugriff, projekt

router = APIRouter(prefix="/api/v1/projekte/{pid}/auswertungen")

NUR_LEITUNG = "Auswertungen starten und verwalten nur Projektleitung und Admins."
NUR_SICHT = ("Auswertungen sehen Projektleitung und Admins; Bearbeiter nur freigegebene "
             "Berichte.")
NICHT_KI = ("Freigeben und Löschen entscheidet ein Mensch – nicht über einen Token für "  # noqa: RUF001
            "KI-Agenten.")
NICHT_FERTIG = "Die Auswertung ist noch nicht fertig."
LAEUFT = "Die Auswertung läuft gerade; nach dem Ende löschen."
ZU_VIELE = (f"Für dieses Projekt sind schon {auftraege.OFFEN_JE_PROJEKT_MAX} Auswertungen offen "
            "– bitte warten, bis eine fertig ist.")  # noqa: RUF001
ERGEBNIS_MAX = 32 * 1024 * 1024
BERICHT_MAX = 128 * 1024 * 1024
# Bericht = eigenständiges HTML mit Inline-Stil und eingebetteten PNG-Karten (data:), ohne Skript.
BERICHT_CSP = ("sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class AuswertungIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    typ: str = Field(max_length=40)
    optionen: dict[str, Any] | None = None


class FreigabeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    freigegeben: StrictBool


def jobs(request: Request) -> Iterator[sqlite3.Connection | None]:
    pfad: Path | None = request.app.state.settings.jobs_db
    if pfad is None:
        yield None
        return
    conn = auftraege.verbinden(pfad)
    try:
        yield conn
    finally:
        conn.close()


def _an(jc: sqlite3.Connection | None) -> sqlite3.Connection:
    """Ohne SCANVIEW_JOBS_DB gibt es keine Auswertungen (404 wie eine unbekannte Route)."""
    if jc is None:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return jc


def _leitung(request: Request, conn: sqlite3.Connection, pid: str, *, mensch: bool = False,
             ) -> Zugriff:
    z = projekt(request, conn, pid, mindestens="projektleitung", text=NUR_LEITUNG)
    if mensch and ki(request):
        raise StarletteHTTPException(status_code=403, detail=NICHT_KI)
    return z


def _sicht(request: Request, conn: sqlite3.Connection, pid: str) -> tuple[Zugriff, bool]:
    """(Zugriff, alle sichtbar?) — Bearbeiter sehen nur freigegebene, darunter 403."""
    z = projekt(request, conn, pid, mindestens="bearbeiter", text=NUR_SICHT)
    return z, konten.mindestens(z.rolle, "projektleitung")


def _freigaben(conn: sqlite3.Connection, pid: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT auftrag_id FROM auswertung_freigaben WHERE project_id=?", (pid,))}


_NAMEN_SQL = "SELECT id, name FROM users WHERE id IN (SELECT value FROM json_each(?))"


def _namen(conn: sqlite3.Connection, ids: set[int]) -> dict[int, str]:
    """Anzeigenamen zu Konto-IDs. Eine feste Anfrage, die IDs als ein JSON-Parameter — kein SQL
    aus Zeichenketten zusammengesetzt, keine Grenze für die Zahl der Platzhalter."""
    if not ids:
        return {}
    return {int(r[0]): r[1] for r in conn.execute(_NAMEN_SQL, (json.dumps(sorted(ids)),))}


def _json(a: auftraege.Auftrag, pid: str, *, freigegeben: bool, namen: dict[int, str],
          warteplatz: int | None) -> dict[str, Any]:
    basis = f"/api/v1/projekte/{pid}/auswertungen/{a.id}"
    fertig = a.status == auftraege.FERTIG
    return {
        "id": a.id, "typ": a.typ, "optionen": a.optionen, "status": a.status,
        "erstelltAm": a.erstellt_am, "erstelltVon": a.erstellt_von,
        "erstelltVonName": namen.get(a.erstellt_von) if a.erstellt_von is not None else None,
        "kanal": a.kanal, "begonnenAm": a.begonnen_am, "beendetAm": a.beendet_am,
        "warteplatz": warteplatz, "urteil": a.urteil, "zaehlung": a.zaehlung,
        "fehler": a.fehler, "freigegeben": freigegeben,
        "ergebnis": f"{basis}/ergebnis" if fertig else None,
        "bericht": f"{basis}/bericht" if fertig else None,
    }


def _einer(request: Request, conn: sqlite3.Connection, jc: sqlite3.Connection, pid: str,
           aid: str) -> tuple[Zugriff, auftraege.Auftrag, bool]:
    z, alle = _sicht(request, conn, pid)
    a = auftraege.lesen(jc, pid, aid)
    frei = aid in _freigaben(conn, pid) if a is not None else False
    if a is None or not (alle or frei):
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return z, a, frei


def _lies(request: Request, pid: str, aid: str, name: str, grenze: int) -> bytes:
    """Datei aus dem Berichtsordner, Größe begrenzt. Jeder Pfadteil wird relativ zum vorigen mit
    O_NOFOLLOW geöffnet: Der Worker darf die Ablage beschreiben und könnte zwischen einer
    resolve()-Prüfung und dem Öffnen einen Ordner gegen einen Symlink tauschen (TOCTOU). Eine Datei
    mit mehreren harten Links wird nicht ausgeliefert (verlinkte fremde Datei)."""
    wurzel: Path = request.app.state.settings.berichte_dir
    auftraege.berichtsordner(wurzel, pid, aid)  # prüft die IDs
    ordner_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    offen: list[int] = []
    try:
        offen.append(os.open(wurzel, ordner_flags))
        for teil in (pid, aid):
            offen.append(os.open(teil, ordner_flags, dir_fd=offen[-1]))
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=offen[-1])
    except OSError:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN) from None
    finally:
        for d in offen:
            os.close(d)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > grenze:
            raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
        with os.fdopen(fd, "rb", closefd=False) as f:
            daten = f.read(grenze + 1)
    finally:
        os.close(fd)
    if len(daten) > grenze:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return daten


@router.post("", dependencies=[Depends(csrf)], status_code=202,
             summary="Auswertung einreihen (Projektleitung; Typ und Optionen aus fester Liste)",
             responses=antwort(AuswertungMitWorker, 202))
def starten(pid: str, body: AuswertungIn, request: Request,
            conn: sqlite3.Connection = Depends(db),
            jc: sqlite3.Connection | None = Depends(jobs)) -> JSONResponse:
    z = _leitung(request, conn, pid)
    j = _an(jc)
    try:
        optionen = auftraege.pruefe(body.typ, body.optionen)
    except auftraege.AuftragUngueltig as e:
        raise Ungueltig(str(e)) from None
    weg = "agent" if ki(request) else kanal(request)
    try:
        aid = auftraege.einreihen(j, projekt=pid, typ=body.typ, optionen=optionen,
                                  nutzer_id=z.nutzer.id, kanal=weg)
    except auftraege.ZuViele:
        return fehler(409, ZU_VIELE)
    with transaktion(conn):
        protokoll.ereignis(
            conn, kanal=kanal(request), nutzer_id=z.nutzer.id, project_id=pid,
            art="auswertung.gestartet", objekt_id=aid,
            nachher={"typ": body.typ, "optionen": optionen}, token_name=token_name(request),
        )
    a = auftraege.lesen(j, pid, aid)
    if a is None:
        return fehler(500, "Auftrag verschwunden")
    return JSONResponse(
        {**_json(a, pid, freigegeben=False, namen={z.nutzer.id: z.nutzer.name},
                 warteplatz=auftraege.position(j, a)),
         "worker": auftraege.worker_zustand(j)},
        status_code=202,
    )


@router.get("", summary="Auswertungen des Projekts (neueste zuerst) und Zustand des Workers",
            responses=antwort(Auswertungsliste))
def auflisten(pid: str, request: Request, conn: sqlite3.Connection = Depends(db),
              jc: sqlite3.Connection | None = Depends(jobs)) -> JSONResponse:
    _z, alle = _sicht(request, conn, pid)
    j = _an(jc)
    frei = _freigaben(conn, pid)
    liste = [a for a in auftraege.liste(j, pid) if alle or a.id in frei]
    namen = _namen(conn, {a.erstellt_von for a in liste if a.erstellt_von is not None})
    return JSONResponse({
        "auswertungen": [_json(a, pid, freigegeben=a.id in frei, namen=namen,
                               warteplatz=auftraege.position(j, a)) for a in liste],
        "worker": auftraege.worker_zustand(j),
    })


@router.get("/{aid}", summary="Eine Auswertung: Status, Urteil, Zählung, Freigabe",
            responses=antwort(AuswertungMitWorker))
def lesen(pid: str, aid: str, request: Request, conn: sqlite3.Connection = Depends(db),
          jc: sqlite3.Connection | None = Depends(jobs)) -> JSONResponse:
    j = _an(jc)
    _z, a, frei = _einer(request, conn, j, pid, aid)
    namen = _namen(conn, {a.erstellt_von} if a.erstellt_von is not None else set())
    return JSONResponse({**_json(a, pid, freigegeben=frei, namen=namen,
                                 warteplatz=auftraege.position(j, a)),
                         "worker": auftraege.worker_zustand(j)})


@router.get("/{aid}/ergebnis", summary="ergebnis.json der fertigen Auswertung",
            responses=antwort(Ergebnis))
def ergebnis(pid: str, aid: str, request: Request, conn: sqlite3.Connection = Depends(db),
             jc: sqlite3.Connection | None = Depends(jobs)) -> JSONResponse:
    z, a, _frei = _einer(request, conn, _an(jc), pid, aid)
    if a.status != auftraege.FERTIG:
        return fehler(409, NICHT_FERTIG)
    roh = _lies(request, pid, aid, "ergebnis.json", ERGEBNIS_MAX)
    try:
        daten = json.loads(roh)
    except (ValueError, RecursionError):
        return fehler(404, NICHT_GEFUNDEN)
    lesezugriff.erfasse_anfrage(request, conn, z, "api", f"auswertung/{aid}")
    return JSONResponse(daten)


@router.get("/{aid}/bericht", summary="Prüfbericht als HTML-Datei (Download, ohne Skripte)",
            response_class=HTMLResponse, responses=FEHLER_ALS_JSON)
def bericht(pid: str, aid: str, request: Request, conn: sqlite3.Connection = Depends(db),
            jc: sqlite3.Connection | None = Depends(jobs)) -> Response:
    z, a, _frei = _einer(request, conn, _an(jc), pid, aid)
    if a.status != auftraege.FERTIG:
        return fehler(409, NICHT_FERTIG)
    roh = _lies(request, pid, aid, "bericht.html", BERICHT_MAX)
    lesezugriff.erfasse_anfrage(request, conn, z, "api", f"auswertung/{aid}")
    return Response(roh, media_type="text/html; charset=utf-8", headers={
        "Content-Security-Policy": BERICHT_CSP,
        "Content-Disposition": f'attachment; filename="pruefbericht-{pid}-{aid}.html"',
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Referrer-Policy": "no-referrer",
    })


@router.post("/{aid}/freigabe", dependencies=[Depends(csrf)],
             summary="Bericht für Bearbeiter freigeben oder Freigabe entziehen (Mensch)",
             responses=antwort(Auswertung))
def freigeben(pid: str, aid: str, body: FreigabeIn, request: Request,
              conn: sqlite3.Connection = Depends(db),
              jc: sqlite3.Connection | None = Depends(jobs)) -> JSONResponse:
    z = _leitung(request, conn, pid, mensch=True)
    j = _an(jc)
    a = auftraege.lesen(j, pid, aid)
    if a is None:
        return fehler(404, NICHT_GEFUNDEN)
    if a.status != auftraege.FERTIG:
        return fehler(409, NICHT_FERTIG)
    jetzt = int(time.time())
    with transaktion(conn):
        vorher = aid in _freigaben(conn, pid)
        if body.freigegeben and not vorher:
            conn.execute(
                "INSERT INTO auswertung_freigaben (project_id, auftrag_id, freigegeben_von,"
                " freigegeben_am) VALUES (?,?,?,?)", (pid, aid, z.nutzer.id, jetzt))
        elif not body.freigegeben and vorher:
            conn.execute("DELETE FROM auswertung_freigaben WHERE project_id=? AND auftrag_id=?",
                         (pid, aid))
        if vorher != body.freigegeben:
            protokoll.ereignis(
                conn, kanal=kanal(request), nutzer_id=z.nutzer.id, project_id=pid,
                art="auswertung.freigabe", objekt_id=aid, vorher={"freigegeben": vorher},
                nachher={"freigegeben": body.freigegeben}, token_name=token_name(request),
                now=jetzt,
            )
    namen = _namen(conn, {a.erstellt_von} if a.erstellt_von is not None else set())
    return JSONResponse(_json(a, pid, freigegeben=body.freigegeben, namen=namen, warteplatz=None))


@router.delete("/{aid}", dependencies=[Depends(csrf)], status_code=204,
               summary="Auswertung löschen (Projektleitung; der Worker entfernt den Bericht)")
def loeschen(pid: str, aid: str, request: Request, conn: sqlite3.Connection = Depends(db),
             jc: sqlite3.Connection | None = Depends(jobs)) -> Response:
    z = _leitung(request, conn, pid, mensch=True)
    j = _an(jc)
    a = auftraege.lesen(j, pid, aid)
    if a is None:
        return fehler(404, NICHT_GEFUNDEN)
    if not auftraege.loeschen_anfordern(j, pid, aid):
        return fehler(409, LAEUFT)
    with transaktion(conn):
        conn.execute("DELETE FROM auswertung_freigaben WHERE project_id=? AND auftrag_id=?",
                     (pid, aid))
        # Ereignis ohne Inhalt (Datenschutz): nur dass und wer gelöscht hat.
        protokoll.ereignis(
            conn, kanal=kanal(request), nutzer_id=z.nutzer.id, project_id=pid,
            art="auswertung.geloescht", objekt_id=aid, token_name=token_name(request),
        )
    return Response(status_code=204)
