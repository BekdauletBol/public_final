"""``publicsafe certs``: wrap mkcert to issue the local development certificate.

The certificate covers ``localhost``, ``127.0.0.1``, ``::1``, every current
LAN IPv4 address of this Mac and ``<hostname>.local`` so that phones on the
same Wi-Fi get a trusted HTTPS connection once the mkcert root CA is installed
on them (see ``docs/PHONE_SETUP.md``).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

CERT_FILE = "dev.pem"
KEY_FILE = "dev-key.pem"
ROOT_CA_FILE = "rootCA.pem"
BREW_HINT = "brew install mkcert"


class CertsError(RuntimeError):
    """mkcert is unavailable or failed; the message explains what to do."""


@dataclass
class CertResult:
    cert: Path
    key: Path
    names: list[str]
    created: bool
    caroot: Path | None
    ca_present: bool
    messages: list[str] = field(default_factory=list)


def lan_ipv4_addresses(addrs: Mapping[str, Sequence[Any]] | None = None) -> list[str]:
    """All non-loopback, non-link-local IPv4 addresses, sorted for stable output."""
    if addrs is None:
        addrs = psutil.net_if_addrs()
    found: set[str] = set()
    for entries in addrs.values():
        for entry in entries:
            if entry.family != socket.AF_INET:
                continue
            address = str(entry.address)
            if address.startswith(("127.", "169.254.")):
                continue
            found.add(address)
    return sorted(found, key=lambda a: tuple(int(p) for p in a.split(".")))


def local_hostnames(hostname: str | None = None) -> list[str]:
    """``<hostname>.local`` for Bonjour resolution on the LAN."""
    name = (socket.gethostname() if hostname is None else hostname).strip()
    if name.endswith(".local"):
        name = name[: -len(".local")]
    return [f"{name}.local"] if name else []


def cert_names(ips: Sequence[str], hostnames: Sequence[str]) -> list[str]:
    """Subject alternative names, de-duplicated and in a stable order."""
    ordered = ["localhost", "127.0.0.1", "::1", *hostnames, *ips]
    seen: set[str] = set()
    out: list[str] = []
    for name in ordered:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def mkcert_command(mkcert: str, names: Sequence[str], cert: Path, key: Path) -> list[str]:
    return [mkcert, "-cert-file", str(cert), "-key-file", str(key), *names]


def mkcert_caroot(mkcert: str, run: Callable[..., Any] = subprocess.run) -> Path | None:
    try:
        proc = run([mkcert, "-CAROOT"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    text = str(proc.stdout).strip()
    return Path(text) if text else None


def phone_ca_instructions(caroot: Path | None) -> str:
    """Steps for trusting the mkcert root CA on iOS and Android (also in PHONE_SETUP.md)."""
    ca_path = (caroot / ROOT_CA_FILE) if caroot else Path("<mkcert -CAROOT>") / ROOT_CA_FILE
    return f"""Trust the development root CA on each phone (one-time):

  Root CA file: {ca_path}
  Transfer it to the phone (AirDrop, email, or from this Mac:
    cd "{caroot or '<mkcert -CAROOT>'}" && python3 -m http.server 8088
  then open http://<this-mac-lan-ip>:8088/{ROOT_CA_FILE} on the phone).

  iOS (Safari):
    1. Open the file; tap Allow when asked to download a configuration profile.
    2. Settings > General > VPN & Device Management > "mkcert ..." > Install.
    3. Settings > General > About > Certificate Trust Settings > enable full trust
       for the mkcert root. Without step 3 Safari still shows a warning.

  Android (Chrome):
    1. Settings > Security (or Security & privacy) > More security settings
       > Encryption & credentials > Install a certificate > CA certificate.
    2. Choose {ROOT_CA_FILE}. Chrome trusts user CAs; Firefox does not by default.

  Then open the https URL shown by `make dev` / `make run` on the phone and check
  that no certificate warning appears.
"""


def ensure_certs(
    certs_dir: Path,
    *,
    force: bool = False,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., Any] = subprocess.run,
    ips: Sequence[str] | None = None,
    hostnames: Sequence[str] | None = None,
) -> CertResult:
    """Create ``certs/dev.pem`` and ``certs/dev-key.pem`` with mkcert if missing.

    ``which``, ``run``, ``ips`` and ``hostnames`` are injectable for tests.
    """
    mkcert = which("mkcert")
    if mkcert is None:
        raise CertsError(
            "mkcert is not installed. Install it with Homebrew:\n"
            f"  {BREW_HINT} && mkcert -install\n"
            "then run `make certs` again."
        )

    cert = certs_dir / CERT_FILE
    key = certs_dir / KEY_FILE
    names = cert_names(
        ips if ips is not None else lan_ipv4_addresses(),
        hostnames if hostnames is not None else local_hostnames(),
    )
    caroot = mkcert_caroot(mkcert, run)
    ca_present = bool(caroot and (caroot / ROOT_CA_FILE).is_file())
    messages: list[str] = []
    if not ca_present:
        messages.append(
            "The mkcert root CA does not exist yet. Run `mkcert -install` (asks for your "
            "macOS password once) so this Mac trusts it, then re-run `make certs`."
        )

    created = False
    if force or not (cert.is_file() and key.is_file()):
        certs_dir.mkdir(parents=True, exist_ok=True)
        try:
            run(mkcert_command(mkcert, names, cert, key), check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CertsError(f"mkcert failed: {exc}") from exc
        created = True
    else:
        messages.append(
            f"{cert} already exists; use --force to re-issue (e.g. after an IP change)."
        )

    return CertResult(
        cert=cert,
        key=key,
        names=names,
        created=created,
        caroot=caroot,
        ca_present=ca_present,
        messages=messages,
    )
