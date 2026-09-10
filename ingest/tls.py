"""Repairing egazette.gov.in's incomplete certificate chain.

The host serves only its leaf certificate. The Let's Encrypt intermediate
that signs it is never sent, so every stock client - curl, requests, httpx,
urllib - fails with `unable to get local issuer certificate` and the whole
source is unreachable. This is a current misconfiguration of the server, not
a local trust-store problem.

The repair is the one TLS already provides for it. A certificate carries an
Authority Information Access extension naming a URL for its issuer, so the
missing links can be fetched and supplied.

**One hop is not enough here.** The leaf is signed by intermediate `YR2`,
which is signed by `ISRG Root YR` - a 2025-era root present in neither
certifi nor the system trust store. Root YR is cross-signed by `ISRG Root
X1`, which *is* trusted, and publishes that cross-signed certificate at its
own AIA URL. So the chain is followed until it reaches something already
trusted. Stopping after one hop fails with `unable to get issuer
certificate`, a confusingly similar but different error.

Chasing at runtime rather than committing a copy of the intermediate means
the fix survives renewal under a different intermediate, which it will need
to: this chain has already rotated once.

Nothing here weakens verification. The chain is still verified to a root the
system already trusts; the only change is supplying links the server should
have sent itself.
"""
import os
import re
import socket
import ssl
import subprocess

from .store import write_atomic

_PEM_BLOCK = re.compile(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)
_AIA_CA_ISSUERS = re.compile(r"CA Issuers\s*-\s*URI:\s*(\S+)")

# The observed chain needs two hops. Four leaves room for a longer one while
# still bounding a CA that points at itself.
MAX_CHAIN_DEPTH = 4


def build_bundle(base_pem, *certificates):
    """Append certificates to a PEM bundle, skipping any already present.

    Pure, and separated from the fetching for that reason: it holds the two
    rules worth stating - certificates must be newline-separated, or the file
    parses as one unreadable blob, and the bundle is rebuilt on every refresh,
    so re-appending would grow it without bound.
    """
    bundle = base_pem.rstrip() + b"\n"
    for certificate in certificates:
        if certificate and certificate.strip() not in bundle:
            bundle += certificate.rstrip() + b"\n"
    return bundle


def chase_issuers(start_url, fetch, issuer_url_of, max_depth=MAX_CHAIN_DEPTH):
    """Follow AIA links from `start_url`, returning the certificates found.

    Stops at the first link that cannot be fetched, at a certificate naming
    no issuer, or at `max_depth` - the last of which is what keeps a
    misconfigured CA pointing at itself from looping forever.
    """
    chain, url = [], start_url
    for _ in range(max_depth):
        if not url:
            break
        certificate = fetch(url)
        if not certificate:
            break
        chain.append(certificate)
        url = issuer_url_of(certificate)
    return chain


def ca_bundle(host, cache_dir, port=443, fetch=None, validate=None):
    """Path to a CA bundle that can verify `host`, cached under `cache_dir`.

    Returns None if the chain cannot be completed, which the caller should
    treat as "this source is unreachable right now" rather than as a reason
    to stop verifying certificates.

    The cache is *checked*, not merely found. This chain has already rotated
    once and will again, and a bundle that no longer verifies is worse than
    no bundle: every fetch then fails deep inside the transport as an
    ordinary connection error, and the harvest reports a wall of failed
    documents with nothing anywhere pointing at one stale file as the cause.
    One handshake is a negligible cost next to a harvest measured in hours.
    """
    path = os.path.join(cache_dir, f"{host}-ca.pem")
    verifies = validate or _verifies
    if os.path.exists(path) and verifies(host, port, path):
        return path

    roots = _system_roots()
    if roots is None:
        return None
    leaf = _leaf_certificate(host, port)
    if leaf is None:
        return None

    chain = chase_issuers(_aia_url(leaf), fetch=fetch or _http_get, issuer_url_of=_issuer_url_of)
    if not chain:
        return None

    # Replaced atomically, and only once a complete replacement exists: a
    # bundle half-overwritten by an interrupted refresh would verify nothing
    # at all, and the stale one it replaced at least verified something.
    write_atomic(path, build_bundle(roots, *(_as_pem(c) for c in chain)))
    return path


def _verifies(host, port, bundle):
    """Whether `bundle` actually completes this host's chain today.

    A full verifying handshake, which is the only question that matters and
    the only one the existence of a file cannot answer.
    """
    context = ssl.create_default_context(cafile=bundle)
    try:
        with socket.create_connection((host, port), timeout=30) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                return True
    except (OSError, ValueError):
        return False


def _system_roots():
    """The trust store to build on. certifi is preferred because it is what
    httpx verifies against by default, so the two cannot disagree."""
    try:
        import certifi

        with open(certifi.where(), "rb") as fh:
            return fh.read()
    except (ImportError, OSError):
        return None


def _leaf_certificate(host, port):
    """The certificate the server presents, fetched without verifying it.

    Verification is precisely what is broken here, and the certificate is
    public information either way. Nothing is trusted on the strength of this
    connection: it is read only to find out where the missing links live, and
    the resulting chain is then verified normally.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=30) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return tls.getpeercert(binary_form=True)
    except OSError:
        return None


def _aia_url(certificate):
    """The CA Issuers URL a certificate names, or None."""
    text = _openssl(["x509", "-inform", _form(certificate), "-noout", "-text"], certificate)
    if text is None:
        return None
    m = _AIA_CA_ISSUERS.search(text.decode("utf-8", "replace"))
    return m.group(1) if m else None


def _issuer_url_of(certificate):
    return _aia_url(certificate)


def _as_pem(certificate):
    """Intermediates are served as DER; convert unless already PEM."""
    found = _PEM_BLOCK.search(certificate)
    if found:
        return found.group(0) + b"\n"
    return _openssl(["x509", "-inform", "DER", "-outform", "PEM"], certificate)


def _form(certificate):
    return "PEM" if _PEM_BLOCK.search(certificate) else "DER"


def _openssl(args, stdin):
    try:
        done = subprocess.run(["openssl", *args], input=stdin,
                              capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 and done.stdout else None


def _http_get(url):
    """Plain HTTP, by design: AIA URLs are http:// precisely so that fetching
    them cannot depend on the TLS chain being valid."""
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read()
    except OSError:
        return None
