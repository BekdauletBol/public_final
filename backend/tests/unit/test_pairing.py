"""M2: PIN generation, normalisation and hashing (G.1)."""

from __future__ import annotations

import re

import pytest

from publicsafe.pairing import (
    ALPHABETS,
    PinFormatError,
    format_pin,
    generate_pin,
    hash_device_token,
    hash_pin,
    new_device_token,
    new_salt,
    normalise_pin,
    pin_matches,
)


def test_generate_letters_and_digits() -> None:
    for _ in range(200):
        pin = generate_pin("letters")
        assert len(pin) == 6 and all(c in ALPHABETS["letters"] for c in pin)
        pin = generate_pin("digits")
        assert re.fullmatch(r"\d{6}", pin)
    assert "A" not in ALPHABETS["letters"] and "O" not in ALPHABETS["letters"]
    assert "0" not in ALPHABETS["letters"] and "1" not in ALPHABETS["letters"]
    assert len(set(generate_pin() for _ in range(50))) > 45


def test_format_pin() -> None:
    assert format_pin("XKDMPT") == "XKD-MPT"


@pytest.mark.parametrize(
    "raw",
    ["XKDMPT", "xkd-mpt", " xkd mpt ", "XKD\u2013MPT", "xKd-MpT", "XKD - MPT", "X K D M P T"],
)
def test_normalise_accepts_variants(raw: str) -> None:
    assert normalise_pin(raw) == "XKDMPT"


@pytest.mark.parametrize(
    "raw",
    ["AKDMPT", "XKDMP0", "XKDMP1", "XKDMPO", "XKDMPI", "XKDMP", "XKDMPTT", "", "123456", "XKD_MPT"],
)
def test_normalise_rejects_outside_alphabet(raw: str) -> None:
    with pytest.raises(PinFormatError):
        normalise_pin(raw)


def test_normalise_digits_alphabet() -> None:
    assert normalise_pin("123-456", "digits") == "123456"
    with pytest.raises(PinFormatError):
        normalise_pin("XKDMPT", "digits")


def test_hashing_is_salted_and_constant_time_compare() -> None:
    s1, s2 = new_salt(), new_salt()
    assert len(s1) == 32 and s1 != s2 and int(s1, 16) >= 0
    h1 = hash_pin(s1, "XKDMPT")
    assert len(h1) == 64 and h1 == hash_pin(s1, "XKDMPT")
    assert h1 != hash_pin(s2, "XKDMPT") and h1 != hash_pin(s1, "XKDMPX")
    assert pin_matches(s1, "XKDMPT", h1) and not pin_matches(s1, "XKDMPX", h1)


def test_device_tokens() -> None:
    token = new_device_token()
    assert len(token) >= 43 and token != new_device_token()
    assert len(hash_device_token(token)) == 64
    assert hash_device_token(token) == hash_device_token(token)
