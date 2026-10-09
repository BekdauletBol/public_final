"""PIN generation, normalisation and hashing (Section G.1). Pure functions, no I/O."""

from __future__ import annotations

import hashlib
import hmac
import secrets

ALPHABETS: dict[str, str] = {
    # RFC 8628 section 6.1: no vowels, no 0/O or 1/I confusion (20 symbols, ~25.9 bits).
    "letters": "BCDFGHJKLMNPQRSTVWXZ",
    # ~19.9 bits; acceptable only with the attempt limits of G.2.
    "digits": "0123456789",
}
PIN_LENGTH = 6
SALT_BYTES = 16


class PinFormatError(ValueError):
    """The typed PIN cannot be valid (wrong length or characters outside the alphabet)."""


def generate_pin(alphabet: str = "letters") -> str:
    symbols = ALPHABETS[alphabet]
    return "".join(secrets.choice(symbols) for _ in range(PIN_LENGTH))


def format_pin(pin: str) -> str:
    """Display form: two groups of three, e.g. ``XKD-MPT``."""
    return f"{pin[:3]}-{pin[3:]}"


def normalise_pin(raw: str, alphabet: str = "letters") -> str:
    """Uppercase, strip spaces and hyphens; reject anything outside the alphabet.

    Raises :class:`PinFormatError` so that typos fail fast without a database
    lookup or an attempt being counted (G.2 step 2).
    """
    separators = {" ", "-", "\t"} | {chr(c) for c in range(0x2010, 0x2015)}  # unicode dashes
    cleaned = "".join(ch for ch in raw.upper() if ch not in separators)
    symbols = ALPHABETS[alphabet]
    if len(cleaned) != PIN_LENGTH or any(ch not in symbols for ch in cleaned):
        raise PinFormatError("PIN has the wrong length or contains invalid characters")
    return cleaned


def new_salt() -> str:
    return secrets.token_hex(SALT_BYTES)  # 32 hex chars


def hash_pin(salt_hex: str, pin: str) -> str:
    """``sha256(salt || pin)`` as hex (64 chars)."""
    return hashlib.sha256(bytes.fromhex(salt_hex) + pin.encode("ascii")).hexdigest()


def pin_matches(salt_hex: str, pin: str, pin_hash: str) -> bool:
    return hmac.compare_digest(hash_pin(salt_hex, pin), pin_hash)


def new_device_token() -> str:
    """256-bit opaque device token (G.3)."""
    return secrets.token_urlsafe(32)


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()
