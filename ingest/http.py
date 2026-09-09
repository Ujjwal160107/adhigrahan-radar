"""Getting bytes from a government host, politely and reliably.

Every source in this package goes through here, so the rules about how to
behave on someone else's public infrastructure are stated once:

* **Rate limit.** A minimum interval between requests, measured from the last
  request rather than added to it, so time already spent waiting on a slow
  server counts.
* **Retry what is worth retrying.** Dropped connections and 5xx are
  transient; a 404 is not, and retrying it just spends the harvest's budget
  on documents that do not exist.
* **Back off.** A constant retry delay against a struggling server is a
  slower way of hammering it.

The transport is injected rather than imported, which is what lets all of the
above be tested offline and deterministically. `httpx` is one adapter behind
that seam, not a dependency of the logic.
"""
import time

USER_AGENT = (
    "adhigrahan-radar/0.1 (SIH26017 research; +https://github.com/upayanmazumder/adhigrahan-radar)"
)

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class TransientError(RuntimeError):
    """The request failed in a way that might succeed later."""


class PermanentError(RuntimeError):
    """The request failed in a way that will not."""


class Response:
    def __init__(self, status, content, url=None):
        self.status = status
        self.content = content
        self.url = url

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")


class Fetcher:
    """A rate-limited, retrying client over an injected transport.

    `transport(method, url, headers, data) -> (status, body, final_url)`,
    raising `OSError` for anything that never reached the server. That is the
    whole contract, deliberately: it is small enough to implement in a test
    without reproducing a real HTTP library's surface.

    `final_url` is not decoration. e-Gazette redirects to a session-scoped
    path and every subsequent postback must target *that* path, so a response
    that reported only the requested URL would drop the session.
    """

    def __init__(self, transport, *, min_interval=1.0, retries=3, backoff=1.0,
                 sleep=time.sleep, now=time.monotonic):
        self._transport = transport
        self._min_interval = min_interval
        self._retries = max(1, retries)
        self._backoff = backoff
        self._sleep = sleep
        self._now = now
        self._last_request_at = None

    def get(self, url, referer=None):
        return self._request("GET", url, referer=referer)

    def post(self, url, data, referer=None):
        return self._request("POST", url, referer=referer, data=data)

    def _request(self, method, url, referer=None, data=None):
        headers = {"User-Agent": USER_AGENT}
        if referer:
            headers["Referer"] = referer

        last = None
        for attempt in range(self._retries):
            if attempt:
                self._sleep(self._backoff * (2 ** (attempt - 1)))
            self._wait_turn()
            try:
                status, body, final_url = self._transport(
                    method, url, headers=headers, data=data)
            except OSError as exc:
                last = exc
                continue
            if status in _RETRYABLE_STATUS:
                last = TransientError(f"{status} from {url}")
                continue
            if status >= 400:
                raise PermanentError(f"{status} from {url}")
            return Response(status, body, final_url)

        raise TransientError(f"giving up on {url} after {self._retries} attempts: {last}")

    def _wait_turn(self):
        now = self._now()
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            if elapsed < self._min_interval:
                self._sleep(self._min_interval - elapsed)
        self._last_request_at = self._now()


def httpx_transport(verify=True, timeout=90.0):
    """The real transport: an `httpx` client behind the contract above.

    Cookies persist on the client because the e-Gazette search is an ASP.NET
    session, and its postbacks are rejected without them.
    """
    import httpx

    client = httpx.Client(verify=verify, timeout=timeout, follow_redirects=True)

    def transport(method, url, headers=None, data=None):
        try:
            response = client.request(method, url, headers=headers, data=data)
        except httpx.HTTPError as exc:
            raise OSError(str(exc)) from exc
        return response.status_code, response.content, str(response.url)

    transport.client = client
    return transport
