"""HTTPS trust for the program's own downloads.

Frozen Python verifies TLS with OpenSSL and reads only the Windows root store.
A freshly installed Windows often lacks newer roots until Edge visits the site,
so the first-run GWS update check and the update-info fetch failed with a
certificate error in a clean VirtualBox install on 2026-09-04 while Google and
the central sender, whose chains end in roots every Windows has, worked.
Every download the program makes for itself goes through this module, which
adds the bundled Mozilla root list (certifi) on top of the Windows store.
"""
from __future__ import annotations

import ssl
import urllib.request


def https_context() -> ssl.SSLContext:
    """Windows root store plus the bundled root list; hostname checks stay on."""

    context = ssl.create_default_context()
    try:
        import certifi

        context.load_verify_locations(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - without the bundle the Windows store alone applies.
        pass
    return context


def open_https(url_or_request, timeout=None):
    """Drop-in for ``urllib.request.urlopen`` that verifies with ``https_context``."""

    return urllib.request.urlopen(url_or_request, timeout=timeout, context=https_context())
