"""Passkeys über py_webauthn. Challenges liegen in der DB (einmalig, 5 min), gebunden an Einladung
bzw. Nutzer; jede Prüfung löscht die Challenge zuerst — auch wenn sie danach scheitert.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from typing import Any

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.registration.verify_registration_response import VerifiedRegistration

from .config import Settings
from .tokens import new_token

CHALLENGE_S = 300
# WB-N1/BB-I5: höchstens so viele offene Challenges OHNE Anmeldung (`/auth/passkey/optionen`);
# darüber 429. Reguläre Nutzung braucht eine Handvoll gleichzeitig (5 min gültig).
MAX_ANONYM = 1000


class ZuVieleChallenges(Exception):
    """Obergrenze offener anonymer Challenges erreicht — die Route antwortet mit 429."""


class PasskeyFehler(Exception):
    """Nutzersichtbare Meldung (Deutsch); enthält nie Eingabewerte."""


def user_handle(email: str) -> bytes:
    """Stabiler WebAuthn-`user_id`-Handle aus der E-Mail — keine eigene Spalte nötig, verrät die
    E-Mail nicht direkt (SHA-256, gekürzt auf 16 Byte)."""
    return hashlib.sha256(("scanview-user:" + email.strip().lower()).encode()).digest()[:16]


def bestehende(conn: sqlite3.Connection, user_id: int) -> list[bytes]:
    """Credential-IDs vorhandener Passkeys eines Nutzers, für `exclude_credentials`."""
    rows = conn.execute("SELECT id FROM passkeys WHERE user_id=?", (user_id,)).fetchall()
    return [base64url_to_bytes(r["id"]) for r in rows]


def _challenge_ablegen(
    conn: sqlite3.Connection, challenge: bytes, purpose: str,
    invite_hash: str | None, user_id: int | None, now: int | None = None,
) -> str:
    cid = new_token()
    n = int(time.time()) if now is None else now
    # Index challenges_exp (Migration 0002): kein voller Tabellenscan mehr je Aufruf.
    conn.execute("DELETE FROM challenges WHERE expires_at < ?", (n,))
    anonym = invite_hash is None and user_id is None
    # Einfügen und Obergrenze in EINER Anweisung — parallele Aufrufe können die Grenze nicht
    # gemeinsam überschreiten. Nur anonyme Challenges zählen: die übrigen verlangen eine gültige
    # Einladung oder eine frische Sitzung.
    cur = conn.execute(
        "INSERT INTO challenges (id, challenge, purpose, invite_hash, user_id, expires_at)"
        " SELECT ?,?,?,?,?,? WHERE NOT ? OR (SELECT COUNT(*) FROM challenges"
        " WHERE invite_hash IS NULL AND user_id IS NULL AND expires_at >= ?) < ?",
        (cid, challenge, purpose, invite_hash, user_id, n + CHALLENGE_S, anonym, n, MAX_ANONYM),
    )
    if cur.rowcount != 1:
        raise ZuVieleChallenges
    return cid


def _challenge_holen(
    conn: sqlite3.Connection, cid: str, purpose: str,
    invite_hash: str | None, user_id: int | None,
) -> bytes:
    # DELETE ... RETURNING holt und löscht in einem Schritt (SQLite >= 3.35, hier 3.53/3.40):
    # Jede Verbindung ist autocommit (siehe db.connect), ein separates SELECT+DELETE ließe zwei
    # parallele Anfragen mit derselben challengeId beide dieselbe Zeile lesen, bevor eine der
    # beiden sie löscht — beide würden dann als gültig durchgehen (zwei Sitzungen/Credentials aus
    # einer Challenge). Mit RETURNING bekommt höchstens eine Anfrage die Zeile.
    row = conn.execute("DELETE FROM challenges WHERE id=? RETURNING *", (cid,)).fetchone()
    if (
        row is None or row["purpose"] != purpose or row["expires_at"] < int(time.time())
        or row["invite_hash"] != invite_hash or row["user_id"] != user_id
    ):
        raise PasskeyFehler("Anfrage abgelaufen oder ungültig — bitte erneut versuchen.")
    return bytes(row["challenge"])


def registrier_optionen(
    conn: sqlite3.Connection, s: Settings, *, name: str, email: str,
    invite_hash: str | None, user_id: int | None, ausschliessen: list[bytes],
) -> tuple[str, str]:
    o = generate_registration_options(
        rp_id=s.rp_id, rp_name=s.rp_name, user_name=email, user_display_name=name,
        user_id=user_handle(email), timeout=CHALLENGE_S * 1000,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in ausschliessen],
    )
    cid = _challenge_ablegen(conn, o.challenge, "registrieren", invite_hash, user_id)
    return cid, options_to_json(o)


def registrierung_pruefen(
    conn: sqlite3.Connection, s: Settings, cid: str, credential: dict[str, Any], *,
    invite_hash: str | None, user_id: int | None,
) -> VerifiedRegistration:
    ch = _challenge_holen(conn, cid, "registrieren", invite_hash, user_id)
    try:
        return verify_registration_response(
            credential=credential, expected_challenge=ch, expected_rp_id=s.rp_id,
            expected_origin=s.origin, require_user_verification=True,
        )
    except Exception as e:  # py_webauthn wirft verschiedene Fehlerklassen
        raise PasskeyFehler("Passkey konnte nicht geprüft werden.") from e


def speichere(conn: sqlite3.Connection, user_id: int, v: VerifiedRegistration, label: str) -> None:
    try:
        conn.execute(
            "INSERT INTO passkeys (id, user_id, public_key, sign_count, label, created_at)"
            " VALUES (?,?,?,?,?,strftime('%s','now'))",
            (bytes_to_base64url(v.credential_id), user_id, v.credential_public_key,
             v.sign_count, label[:80]),
        )
    except sqlite3.IntegrityError as e:
        # Gleicher Credential zweimal registriert (Doppelklick, manipulierter Client, o. Ä.) —
        # kein 500er, sondern eine gewöhnliche, für den Nutzer sichtbare Fehlermeldung.
        raise PasskeyFehler("Dieser Passkey ist schon registriert.") from e


def anmelde_optionen(conn: sqlite3.Connection, s: Settings) -> tuple[str, str]:
    o = generate_authentication_options(
        rp_id=s.rp_id, timeout=CHALLENGE_S * 1000,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return _challenge_ablegen(conn, o.challenge, "anmelden", None, None), options_to_json(o)


def anmeldung_pruefen(
    conn: sqlite3.Connection, s: Settings, cid: str, credential: dict[str, Any],
) -> int:
    ch = _challenge_holen(conn, cid, "anmelden", None, None)
    raw = credential.get("rawId") or credential.get("id")
    if not isinstance(raw, str):
        raise PasskeyFehler("Passkey unbekannt.")
    try:
        cred_id = bytes_to_base64url(base64url_to_bytes(raw))
    except Exception as e:
        raise PasskeyFehler("Passkey unbekannt.") from e
    row = conn.execute(
        "SELECT p.*, u.disabled FROM passkeys p JOIN users u ON u.id=p.user_id WHERE p.id=?",
        (cred_id,),
    ).fetchone()
    if row is None or row["disabled"]:
        raise PasskeyFehler("Passkey unbekannt.")
    try:
        v = verify_authentication_response(
            credential=credential, expected_challenge=ch, expected_rp_id=s.rp_id,
            expected_origin=s.origin, credential_public_key=bytes(row["public_key"]),
            credential_current_sign_count=row["sign_count"], require_user_verification=True,
        )
    except Exception as e:
        raise PasskeyFehler("Passkey konnte nicht geprüft werden.") from e
    # Nur vorwärts aktualisieren: ein Klon des Authenticators (Replay eines alten Zustands)
    # würde sonst den Zähler zurücksetzen können. `<=` statt `<` lässt 0/0-Authenticatoren
    # (die den Zähler nie erhöhen, z. B. manche Plattform-Authenticatoren) weiter zu.
    conn.execute(
        "UPDATE passkeys SET sign_count=?, last_used_at=strftime('%s','now')"
        " WHERE id=? AND sign_count<=?",
        (v.new_sign_count, cred_id, v.new_sign_count),
    )
    return int(row["user_id"])
