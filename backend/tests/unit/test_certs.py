"""M0: the mkcert wrapper (pure parts and injected subprocess)."""

from __future__ import annotations

import socket
from collections import namedtuple
from pathlib import Path

import pytest

from publicsafe.certs import (
    BREW_HINT,
    CertsError,
    cert_names,
    ensure_certs,
    lan_ipv4_addresses,
    local_hostnames,
    mkcert_command,
    phone_ca_instructions,
)

Addr = namedtuple("Addr", "family address")


def test_lan_ipv4_filters_loopback_and_link_local() -> None:
    addrs = {
        "lo0": [Addr(socket.AF_INET, "127.0.0.1"), Addr(socket.AF_INET6, "::1")],
        "en0": [Addr(socket.AF_INET, "192.168.0.20"), Addr(socket.AF_INET6, "fe80::1")],
        "en1": [Addr(socket.AF_INET, "10.0.0.5"), Addr(socket.AF_INET, "169.254.1.1")],
        "en2": [Addr(socket.AF_INET, "192.168.0.20")],  # duplicate
    }
    assert lan_ipv4_addresses(addrs) == ["10.0.0.5", "192.168.0.20"]


def test_local_hostnames() -> None:
    assert local_hostnames("MacBook-Air.local") == ["MacBook-Air.local"]
    assert local_hostnames("MacBook-Air") == ["MacBook-Air.local"]
    assert local_hostnames("") == []


def test_cert_names_order_and_dedup() -> None:
    names = cert_names(["192.168.0.20", "127.0.0.1"], ["mac.local", "mac.local"])
    assert names == ["localhost", "127.0.0.1", "::1", "mac.local", "192.168.0.20"]


def test_mkcert_command() -> None:
    cmd = mkcert_command(
        "/opt/homebrew/bin/mkcert", ["localhost", "1.2.3.4"], Path("c.pem"), Path("k.pem")
    )
    assert cmd == [
        "/opt/homebrew/bin/mkcert",
        "-cert-file",
        "c.pem",
        "-key-file",
        "k.pem",
        "localhost",
        "1.2.3.4",
    ]


def test_missing_mkcert_gives_brew_hint(tmp_path: Path) -> None:
    with pytest.raises(CertsError) as excinfo:
        ensure_certs(tmp_path, which=lambda _: None)
    assert BREW_HINT in str(excinfo.value)


def test_ensure_certs_runs_mkcert_once_and_is_idempotent(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    caroot = tmp_path / "caroot"
    caroot.mkdir()
    (caroot / "rootCA.pem").write_text("ca")

    class Proc:
        stdout = str(caroot)

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[1] == "-CAROOT":
            return Proc()
        Path(cmd[2]).write_text("cert")
        Path(cmd[4]).write_text("key")
        return Proc()

    certs_dir = tmp_path / "certs"
    first = ensure_certs(
        certs_dir,
        which=lambda _: "mkcert",
        run=fake_run,
        ips=["192.168.0.20"],
        hostnames=["mac.local"],
    )
    assert first.created and first.ca_present and first.caroot == caroot
    assert first.names == ["localhost", "127.0.0.1", "::1", "mac.local", "192.168.0.20"]
    assert (certs_dir / "dev.pem").read_text() == "cert"
    assert [c[1] for c in calls] == ["-CAROOT", "-cert-file"]

    second = ensure_certs(certs_dir, which=lambda _: "mkcert", run=fake_run, ips=[], hostnames=[])
    assert not second.created
    assert any("already exists" in m for m in second.messages)
    assert [c[1] for c in calls] == ["-CAROOT", "-cert-file", "-CAROOT"]


def test_missing_root_ca_is_reported(tmp_path: Path) -> None:
    class Proc:
        stdout = str(tmp_path / "empty-caroot")

    def fake_run(cmd, **kwargs):
        if cmd[1] != "-CAROOT":
            Path(cmd[2]).write_text("cert")
            Path(cmd[4]).write_text("key")
        return Proc()

    result = ensure_certs(
        tmp_path / "certs", which=lambda _: "mkcert", run=fake_run, ips=[], hostnames=[]
    )
    assert result.ca_present is False
    assert any("mkcert -install" in m for m in result.messages)


def test_phone_instructions_mention_both_platforms(tmp_path: Path) -> None:
    text = phone_ca_instructions(tmp_path)
    assert "iOS" in text and "Android" in text and str(tmp_path / "rootCA.pem") in text
    assert "rootCA.pem" in phone_ca_instructions(None)
