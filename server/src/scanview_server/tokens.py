"""Zufallstokens für Einladungen und Sitzungen; gespeichert wird nur der SHA-256."""

import hashlib
import secrets


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "strict")).hexdigest()
