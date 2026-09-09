"""Politeness and resilience when talking to government hosts.

These servers are slow, occasionally drop connections, and are public
infrastructure nobody here is entitled to hammer. The rules below are the
ones that keep a long harvest both survivable and well-behaved, and they are
tested against a fake transport so the suite stays offline and deterministic.
"""
import pytest

from ingest.http import Fetcher, PermanentError, TransientError
from ingest.tls import build_bundle

LEAF = b"-----BEGIN CERTIFICATE-----\nbase\n-----END CERTIFICATE-----\n"
INTERMEDIATE = b"-----BEGIN CERTIFICATE-----\nintermediate\n-----END CERTIFICATE-----\n"


class Transport:
    """A scripted stand-in for the network.

    Each entry is either an (status, body) pair to return or an exception to
    raise. Recording the calls is what lets the tests assert on real
    behaviour - how many requests were made and with what headers - rather
    than on a mock's own bookkeeping.
    """

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers=None, data=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "data": data})
        result = self._responses.pop(0) if self._responses else (200, b"ok", url)
        if isinstance(result, Exception):
            raise result
        status, body, *rest = result
        return status, body, (rest[0] if rest else url)


class Clock:
    def __init__(self):
        self.slept = []
        self.time = 0.0

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.time += seconds

    def now(self):
        return self.time


def fetcher(transport, clock=None, **kwargs):
    clock = clock or Clock()
    return Fetcher(transport, sleep=clock.sleep, now=clock.now, **kwargs)


class TestRateLimiting:
    def test_the_first_request_does_not_wait(self):
        clock = Clock()
        fetcher(Transport(), clock, min_interval=2.0).get("https://example.gov.in/a")
        assert clock.slept == []

    def test_a_second_request_waits_out_the_interval(self):
        clock = Clock()
        f = fetcher(Transport(), clock, min_interval=2.0)

        f.get("https://example.gov.in/a")
        f.get("https://example.gov.in/b")

        assert clock.slept == [2.0]

    def test_no_wait_when_the_interval_has_already_elapsed(self):
        # Time spent waiting for a slow server counts towards the interval;
        # sleeping again on top of it would double the harvest's runtime for
        # no politeness gain.
        clock = Clock()
        f = fetcher(Transport(), clock, min_interval=2.0)
        f.get("https://example.gov.in/a")
        clock.time += 5.0

        f.get("https://example.gov.in/b")

        assert clock.slept == []


class TestRetries:
    def test_a_dropped_connection_is_retried(self):
        transport = Transport(ConnectionError("reset by peer"), (200, b"recovered"))

        assert fetcher(transport, retries=3).get("https://example.gov.in/a").content == b"recovered"
        assert len(transport.calls) == 2

    def test_a_server_error_is_retried(self):
        transport = Transport((503, b"unavailable"), (200, b"recovered"))

        assert fetcher(transport, retries=3).get("https://example.gov.in/a").content == b"recovered"

    def test_it_gives_up_after_the_configured_attempts(self):
        transport = Transport(*[ConnectionError("reset")] * 5)

        with pytest.raises(TransientError):
            fetcher(transport, retries=3).get("https://example.gov.in/a")
        assert len(transport.calls) == 3

    def test_backoff_grows_between_attempts(self):
        # A fixed retry delay against a struggling server is just a slower
        # way of hammering it.
        clock = Clock()
        transport = Transport(ConnectionError("x"), ConnectionError("x"), (200, b"ok"))

        fetcher(transport, clock, retries=3, min_interval=0, backoff=1.0).get("https://x.gov.in/a")

        assert clock.slept == [1.0, 2.0]

    def test_a_missing_document_is_not_retried(self):
        # A 404 will still be 404 on the fourth attempt. Retrying it wastes
        # the harvest's budget on documents that do not exist.
        transport = Transport((404, b"not found"))

        with pytest.raises(PermanentError):
            fetcher(transport, retries=3).get("https://example.gov.in/missing")
        assert len(transport.calls) == 1


class TestRequestShape:
    def test_it_identifies_itself_with_a_user_agent(self):
        # e-Gazette serves an error page to requests with no User-Agent; the
        # search flow simply does not work without one.
        transport = Transport()
        fetcher(transport).get("https://example.gov.in/a")
        assert transport.calls[0]["headers"]["User-Agent"]

    def test_it_sends_a_referer_when_given_one(self):
        # The same ASP.NET flow rejects postbacks that arrive without the
        # page they claim to come from.
        transport = Transport()
        fetcher(transport).get("https://example.gov.in/b", referer="https://example.gov.in/a")
        assert transport.calls[0]["headers"]["Referer"] == "https://example.gov.in/a"

    def test_a_post_carries_its_form_body(self):
        transport = Transport()
        fetcher(transport).post("https://example.gov.in/a", {"k": "v"})
        assert transport.calls[0]["method"] == "POST"
        assert transport.calls[0]["data"] == {"k": "v"}

    def test_the_response_reports_the_url_it_ended_on(self):
        # e-Gazette redirects to a session-scoped path, and every subsequent
        # postback must target that path rather than the one requested. A
        # response that only reports what was asked for loses the session.
        landed = "https://example.gov.in/(S(abc123))/SearchMinistry.aspx?id=7"
        transport = Transport((200, b"ok", landed))

        assert fetcher(transport).get("https://example.gov.in/").url == landed


class TestCertificateBundle:
    """egazette.gov.in serves its leaf certificate without the Let's Encrypt
    intermediate that signs it, so every stock HTTP client fails with
    "unable to get local issuer certificate". The fix is to append the
    intermediate, fetched from the leaf's own Authority Information Access
    URL, to the trusted roots."""

    def test_the_bundle_contains_both_the_roots_and_the_intermediate(self):
        bundle = build_bundle(LEAF, INTERMEDIATE)
        assert LEAF in bundle
        assert INTERMEDIATE in bundle

    def test_building_twice_does_not_duplicate_the_intermediate(self):
        # The bundle is cached and rebuilt on refresh; duplicating a
        # certificate on every run grows the file without bound.
        bundle = build_bundle(build_bundle(LEAF, INTERMEDIATE), INTERMEDIATE)
        assert bundle.count(INTERMEDIATE) == 1

    def test_certificates_stay_separated_by_a_newline(self):
        # PEM files concatenated without a trailing newline produce a single
        # unparseable blob, and the failure surfaces as an opaque SSL error.
        bundle = build_bundle(b"-----BEGIN CERTIFICATE-----\nbase\n-----END CERTIFICATE-----",
                              INTERMEDIATE)
        assert b"-----END CERTIFICATE-----\n-----BEGIN CERTIFICATE-----" in bundle

    def test_a_whole_chain_can_be_appended_at_once(self):
        second = b"-----BEGIN CERTIFICATE-----\nsecond\n-----END CERTIFICATE-----\n"
        bundle = build_bundle(LEAF, INTERMEDIATE, second)
        assert INTERMEDIATE in bundle and second in bundle


class TestChasingTheChain:
    """One hop is not always enough.

    egazette.gov.in's leaf is signed by Let's Encrypt intermediate `YR2`,
    which is signed by `ISRG Root YR` - a 2025-era root that is in neither
    certifi nor the system trust store yet. Root YR is in turn cross-signed
    by `ISRG Root X1`, which *is* trusted, and it publishes that cross-signed
    certificate at its own Authority Information Access URL.

    So the chain has to be followed until it reaches something already
    trusted, not fetched once and hoped over. Stopping after one hop fails
    with `unable to get issuer certificate`, which is a different and much
    more confusing error than the `unable to get local issuer certificate`
    that started all this.
    """

    def chase(self, certs, issuers, **kwargs):
        """`certs` maps a URL to the certificate served there; `issuers` maps
        a certificate to the URL of the one that signed it."""
        from ingest.tls import chase_issuers

        return chase_issuers("http://yr2", fetch=certs.get,
                             issuer_url_of=issuers.get, **kwargs)

    def test_collects_every_certificate_up_to_a_trusted_one(self):
        certs = {"http://yr2": b"YR2", "http://yr": b"RootYR"}
        issuers = {b"YR2": "http://yr", b"RootYR": None}  # RootYR is cross-signed by a trusted root

        assert self.chase(certs, issuers) == [b"YR2", b"RootYR"]

    def test_stops_at_the_depth_limit(self):
        # A CA that points at itself must cost a few requests, not an
        # unbounded harvest.
        certs = {"http://yr2": b"YR2"}
        issuers = {b"YR2": "http://yr2"}

        assert self.chase(certs, issuers, max_depth=3) == [b"YR2", b"YR2", b"YR2"]

    def test_a_link_that_cannot_be_fetched_ends_the_chain(self):
        assert self.chase({"http://yr2": None}, {}) == []

    def test_no_starting_url_yields_nothing(self):
        from ingest.tls import chase_issuers

        assert chase_issuers(None, fetch=lambda u: b"x", issuer_url_of=lambda c: None) == []
