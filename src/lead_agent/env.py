from __future__ import annotations

import os
import ssl
from pathlib import Path


def _patch_ssl_context() -> None:
    """Patch the default SSL context with a non-system CA bundle.

    Python's ``urllib`` module creates HTTPS SSL contexts via
    :func:`ssl._create_default_https_context`, which by default depends on
    the macOS framework keychain (``/etc/ssl/cert.pem``).  On some macOS /
    sandbox environments that keychain CA bundle causes every HTTPS request to
    fail with ``CERTIFICATE_VERIFY_FAILED``.

    When ``/etc/ssl/cert.pem`` (a system CA bundle provided by Homebrew /
    macOS) is available we replace ``ssl._create_default_https_context`` so
    that all ``urllib.request.urlopen`` calls succeed without individual
    callers having to pass an explicit ``context``.
    """
    _system_cafile = "/etc/ssl/cert.pem"
    if not Path(_system_cafile).exists():
        return

    def _create_default_https_context():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cafile=_system_cafile)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    ssl._create_default_https_context = _create_default_https_context


_patch_ssl_context()


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists() or not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue

        os.environ.setdefault(key, value)
