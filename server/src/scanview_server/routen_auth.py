"""Auth-Routen: Abmelden, Einladung prüfen/Passwort oder Passkey setzen, Anmeldung mit Passwort
oder Passkey (Drosselung bei Passwort), weiterer Passkey für ein bestehendes Konto.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import db as dbmod
from . import konten, passkey, passwort
from .web import (
    COOKIE,
    UEBERLASTET,
    csrf,
    db,
    fehler,
    frisch_angemeldet,
    loesche_sitzung,
    neue_sitzung,
)

router = APIRouter()

EINLADUNG_UNGUELTIG = "Einladung ungültig, abgelaufen oder bereits eingelöst."
FALSCH = "E-Mail oder Passwort falsch."
GESPERRT = "Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen."  # noqa: RUF001
NICHT_ANGEMELDET = "nicht angemeldet"
# „Weiteres Gerät hinzufügen“ nur mit frischer Sitzung (web.frisch_angemeldet, FRISCH_S): ein
# liegengelassenes, noch gültiges Cookie allein soll keinen neuen Passkey anlegen dürfen.
NEU_ANMELDEN_WOZU = "ein weiteres Gerät hinzuzufügen"


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=100)


class PasswortEinladungIn(TokenIn):
    passwort: str = Field(max_length=256)


class AnmeldenIn(BaseModel):
    email: str = Field(max_length=320)
    passwort: str = Field(max_length=256)


class PasskeyAbschlussIn(BaseModel):
    challengeId: str = Field(max_length=100)
    credential: dict[str, Any]
    geraet: str = Field(default="", max_length=80)


class EinladungPasskeyIn(PasskeyAbschlussIn, TokenIn):
    pass


class PasskeyAnmeldenIn(BaseModel):
    challengeId: str = Field(max_length=100)
    credential: dict[str, Any]


def _sperre_loeschen(conn: sqlite3.Connection, email: str) -> None:
    """Nach erfolgreichem Einlösen einer Einladung (Passwort oder Passkey) eine eventuell noch
    bestehende Passwort-Sperre für dieselbe E-Mail aufheben: Ein Zurücksetzen
    ersetzt Passwort bzw. Passkey, die alte Sperre aus den Fehlversuchen davor soll dann nicht
    weiter greifen. Dieselbe Normalisierung wie in `passwort_anmelden`.
    """
    passwort.erfolg(conn, passwort.schluessel(email.strip().lower()))


@router.post("/auth/abmelden", dependencies=[Depends(csrf)])
def abmelden(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    t = request.cookies.get(COOKIE)
    if t:
        konten.sitzung_beenden(conn, t)
    r = JSONResponse({"ok": True})
    loesche_sitzung(r, request.app.state.settings)
    return r


@router.post("/auth/einladung/pruefen", dependencies=[Depends(csrf)])
def einladung_pruefen(body: TokenIn, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    e = konten.einladung_pruefen(conn, body.token)
    if e is None:
        return fehler(404, EINLADUNG_UNGUELTIG)
    return JSONResponse({"name": e.name, "email": e.email, "zweck": e.purpose})


@router.post("/auth/einladung/passwort", dependencies=[Depends(csrf)])
async def einladung_passwort(body: PasswortEinladungIn, request: Request) -> JSONResponse:
    # async + eigener Thread-Pool (WB-M3): argon2 wartet nie im gemeinsamen Pool von /api/authz.
    return await request.app.state.rechenplaetze.ausfuehren(
        lambda: _einladung_passwort(body, request),
    )


def _einladung_passwort(body: PasswortEinladungIn, request: Request) -> JSONResponse:
    if (msg := passwort.pruefe_regeln(body.passwort)) is not None:
        return fehler(400, msg)
    conn = dbmod.connect(request.app.state.settings.db_path)
    try:
        # Erst die Einladung prüfen, dann hashen: ungültige Tokens sollen keine argon2-Rechnung
        # (64 MiB, siehe passwort.ARGON2_PARALLEL) auslösen. Das Einlösen unten prüft atomar
        # noch einmal.
        if konten.einladung_pruefen(conn, body.token) is None:
            return fehler(404, EINLADUNG_UNGUELTIG)
        # Hashen VOR dem Einlösen: argon2 ist absichtlich langsam, das darf nicht innerhalb der
        # BEGIN-IMMEDIATE-Transaktion von einladung_einloesen laufen (hielte den DB-Schreiblock).
        h = passwort.hash_pw(body.passwort)

        def speichern(c: sqlite3.Connection, uid: int) -> None:
            c.execute(
                "INSERT OR REPLACE INTO passwords (user_id, hash, updated_at)"
                " VALUES (?,?,strftime('%s','now'))",
                (uid, h),
            )

        try:
            n = konten.einladung_einloesen(conn, body.token, speichern)
        except konten.EinladungUngueltig as e:
            # Meist der generische Text; bei einer bereits eingelösten Einladung für ein
            # inzwischen bestehendes Konto trägt die Ausnahme die spezifischere Meldung — die
            # geht nie als 500 durch, sondern immer als 404 JSON hier raus.
            return fehler(404, str(e) or EINLADUNG_UNGUELTIG)
        _sperre_loeschen(conn, n.email)
        r = JSONResponse({"ok": True})
        neue_sitzung(r, request, conn, n.id)
        return r
    finally:
        conn.close()


@router.post("/auth/passwort/anmelden", dependencies=[Depends(csrf)])
async def passwort_anmelden(body: AnmeldenIn, request: Request) -> JSONResponse:
    # Globale Bremse (WB-M2) vor allem anderen: kostet weder Thread noch DB-Zugriff.
    if request.app.state.anmeldebremse.gebremst():
        return fehler(429, UEBERLASTET)
    client_ip = request.client.host if request.client else None
    return await request.app.state.rechenplaetze.ausfuehren(
        lambda: _passwort_anmelden(body, request, client_ip),
    )


def _passwort_anmelden(
    body: AnmeldenIn, request: Request, client_ip: str | None,
) -> JSONResponse:
    email = body.email.strip().lower()
    key = passwort.schluessel(email)
    conn = dbmod.connect(request.app.state.settings.db_path)
    try:
        row = conn.execute(
            "SELECT u.id, p.hash FROM users u JOIN passwords p ON p.user_id=u.id"
            " WHERE u.email=? AND u.disabled=0",
            (body.email.strip(),),
        ).fetchone()
        # Prüfen und Zählen atomar (WB-M1); unbekannte, gesperrte und Passkey-Konten laufen
        # über den Dummy-Hash — gleiche Antwort, gleiche Laufzeit, keine Konto-Aufzählung.
        ergebnis = passwort.anmeldung_pruefen(
            conn, key, row["hash"] if row is not None else None, body.passwort,
        )
        if ergebnis == "gesperrt":
            return fehler(429, GESPERRT)
        if ergebnis != "ok" or row is None:
            passwort.fehlversuch_melden(key, client_ip)
            request.app.state.anmeldebremse.fehlschlag()
            return fehler(401, FALSCH)
        r = JSONResponse({"ok": True})
        neue_sitzung(r, request, conn, row["id"])
        return r
    finally:
        conn.close()


@router.post("/auth/einladung/passkey/optionen", dependencies=[Depends(csrf)])
def einladung_passkey_optionen(
    body: TokenIn, request: Request, conn: sqlite3.Connection = Depends(db),
) -> JSONResponse:
    e = konten.einladung_pruefen(conn, body.token)
    if e is None:
        return fehler(404, EINLADUNG_UNGUELTIG)
    cid, js = passkey.registrier_optionen(
        conn, request.app.state.settings, name=e.name, email=e.email,
        invite_hash=e.token_hash, user_id=None, ausschliessen=[],
    )
    return JSONResponse({"challengeId": cid, "optionen": json.loads(js)})


@router.post("/auth/einladung/passkey/abschluss", dependencies=[Depends(csrf)])
def einladung_passkey_abschluss(
    body: EinladungPasskeyIn, request: Request, conn: sqlite3.Connection = Depends(db),
) -> JSONResponse:
    s = request.app.state.settings
    e = konten.einladung_pruefen(conn, body.token)
    if e is None:
        return fehler(404, EINLADUNG_UNGUELTIG)
    try:
        v = passkey.registrierung_pruefen(
            conn, s, body.challengeId, body.credential, invite_hash=e.token_hash, user_id=None,
        )
    except passkey.PasskeyFehler as x:
        return fehler(400, str(x))
    try:
        n = konten.einladung_einloesen(
            conn, body.token, lambda c, uid: passkey.speichere(c, uid, v, body.geraet),
        )
    except konten.EinladungUngueltig as e2:
        return fehler(404, str(e2) or EINLADUNG_UNGUELTIG)
    except passkey.PasskeyFehler as x:
        return fehler(400, str(x))
    _sperre_loeschen(conn, n.email)
    r = JSONResponse({"ok": True})
    neue_sitzung(r, request, conn, n.id)
    return r


@router.post("/auth/passkey/optionen", dependencies=[Depends(csrf)])
def passkey_optionen(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    cid, js = passkey.anmelde_optionen(conn, request.app.state.settings)
    return JSONResponse({"challengeId": cid, "optionen": json.loads(js)})


@router.post("/auth/passkey/anmelden", dependencies=[Depends(csrf)])
def passkey_anmelden(
    body: PasskeyAnmeldenIn, request: Request, conn: sqlite3.Connection = Depends(db),
) -> JSONResponse:
    s = request.app.state.settings
    try:
        uid = passkey.anmeldung_pruefen(conn, s, body.challengeId, body.credential)
    except passkey.PasskeyFehler:
        return fehler(401, "Anmeldung mit Passkey fehlgeschlagen.")
    r = JSONResponse({"ok": True})
    neue_sitzung(r, request, conn, uid)
    return r


@router.post("/auth/passkey/hinzufuegen/optionen", dependencies=[Depends(csrf)])
def passkey_hinzufuegen_optionen(
    request: Request, conn: sqlite3.Connection = Depends(db),
) -> JSONResponse:
    n = frisch_angemeldet(request, conn, NEU_ANMELDEN_WOZU)
    cid, js = passkey.registrier_optionen(
        conn, request.app.state.settings, name=n.name, email=n.email,
        invite_hash=None, user_id=n.id, ausschliessen=passkey.bestehende(conn, n.id),
    )
    return JSONResponse({"challengeId": cid, "optionen": json.loads(js)})


@router.post("/auth/passkey/hinzufuegen/abschluss", dependencies=[Depends(csrf)])
def passkey_hinzufuegen_abschluss(
    body: PasskeyAbschlussIn, request: Request, conn: sqlite3.Connection = Depends(db),
) -> JSONResponse:
    # Vor registrierung_pruefen: eine 403 soll die Challenge nicht verbrauchen.
    n = frisch_angemeldet(request, conn, NEU_ANMELDEN_WOZU)
    s = request.app.state.settings
    try:
        v = passkey.registrierung_pruefen(
            conn, s, body.challengeId, body.credential, invite_hash=None, user_id=n.id,
        )
        passkey.speichere(conn, n.id, v, body.geraet)
    except passkey.PasskeyFehler as x:
        return fehler(400, str(x))
    return JSONResponse({"ok": True})
