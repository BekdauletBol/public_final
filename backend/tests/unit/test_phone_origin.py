"""Pairing URLs must use an origin a phone can open (PUBLIC_URL, Host, or the LAN IP)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from publicsafe.api.pairing import phone_origin, split_host_port, ws_url


def req(host: str, scheme: str = "https"):
    return SimpleNamespace(url=SimpleNamespace(scheme=scheme, netloc=host), headers={"host": host})


@pytest.mark.parametrize(
    ("netloc", "expected"),
    [
        ("192.168.0.20:8443", ("192.168.0.20", "8443")),
        ("localhost", ("localhost", None)),
        ("[::1]:5173", ("[::1]", "5173")),
        ("[::]", ("[::]", None)),
        ("macbook.local:5173", ("macbook.local", "5173")),
    ],
)
def test_split_host_port(netloc: str, expected: tuple[str, str | None]) -> None:
    assert split_host_port(netloc) == expected


def test_public_url_wins(make_settings) -> None:
    s = make_settings(public_url="https://cam.example:9443/")
    assert phone_origin(req("0.0.0.0:8443"), s, ["10.0.0.5"]) == ("https://cam.example:9443", None)


def test_lan_host_is_kept(make_settings) -> None:
    s = make_settings()
    assert phone_origin(req("192.168.0.20:5173"), s, ["10.0.0.5"]) == (
        "https://192.168.0.20:5173",
        None,
    )
    assert phone_origin(req("macbook-air.local:8443"), s, []) == (
        "https://macbook-air.local:8443",
        None,
    )


@pytest.mark.parametrize("host", ["0.0.0.0:8443", "localhost:5173", "127.0.0.1:8443", "[::]:8443"])
def test_unreachable_host_falls_back_to_lan_ip_with_warning(make_settings, host: str) -> None:
    origin, warning = phone_origin(req(host), make_settings(), ["192.168.0.20", "10.0.0.5"])
    port = host.rsplit(":", 1)[1]
    assert origin == f"https://192.168.0.20:{port}"
    assert warning and "192.168.0.20" in warning and "PUBLIC_URL" in warning


def test_unreachable_host_without_lan_ip_warns_to_type_pin(make_settings) -> None:
    origin, warning = phone_origin(req("localhost"), make_settings(), [])
    assert origin == "https://localhost"
    assert warning and "PIN" in warning


def test_ws_url() -> None:
    assert ws_url("https://192.168.0.20:8443") == "wss://192.168.0.20:8443/ws/camera"
    assert ws_url("http://127.0.0.1:8000") == "ws://127.0.0.1:8000/ws/camera"
