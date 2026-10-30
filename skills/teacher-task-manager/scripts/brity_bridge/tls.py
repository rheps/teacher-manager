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

import http.client
import socket
import ssl
import time
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


class FailoverHTTPSConnection(http.client.HTTPSConnection):
    """Try each resolved address until one completes TCP + TLS (2026-10-29).

    One of the eight IPv4 addresses behind the central server's *.a.run.app name
    always ended the TLS handshake (SSLEOFError); urllib only tried the first
    address, so every request failed while DNS put that address first. Failover
    happens only before any HTTP byte is written: a TCP connect failure or a
    handshake failure moves to the next address within the same timeout. A proxy
    tunnel keeps the standard path unchanged.
    """

    def connect(self):
        if self._tunnel_host:
            return super().connect()
        timeout = self.timeout
        limited = isinstance(timeout, (int, float))
        deadline = time.monotonic() + timeout if limited else None
        last_error = None
        for family, kind, proto, _name, address in socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM):
            remaining = None
            if limited:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
            sock = None
            try:
                sock = socket.socket(family, kind, proto)
                if limited:
                    sock.settimeout(remaining)
                if self.source_address:
                    sock.bind(self.source_address)
                sock.connect(address)
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass
                tls_sock = self._context.wrap_socket(sock, server_hostname=self.host)
            except ssl.SSLCertVerificationError:
                if sock is not None:
                    sock.close()
                raise
            except (ssl.SSLError, OSError) as error:
                # OSError covers TCP connect failures, ConnectionReset/Aborted and timeouts.
                if sock is not None:
                    sock.close()
                last_error = error
                continue
            if limited:
                tls_sock.settimeout(timeout)
            self.sock = tls_sock
            return
        if last_error is not None:
            raise last_error
        raise OSError("no address could be reached for " + str(self.host))


class FailoverHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(FailoverHTTPSConnection, req, context=self._context)


def urlopen_failover(url_or_request, timeout=None):
    """``urllib.request.urlopen`` with per-address failover; default trust unchanged."""

    return urllib.request.build_opener(FailoverHTTPSHandler()).open(url_or_request, timeout=timeout)


def open_https(url_or_request, timeout=None):
    """Drop-in for ``urllib.request.urlopen`` that verifies with ``https_context``."""

    return urllib.request.urlopen(url_or_request, timeout=timeout, context=https_context())
