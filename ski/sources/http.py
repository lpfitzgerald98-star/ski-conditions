"""One pooled HTTP session for every source client.

Each source used to call `requests.get` directly, which opens a fresh TCP+TLS
connection per request and throws it away. Measured against api.weather.gov:

    bare requests.get      0.493s
    session.get (reused)   0.095s

That ~0.4s is handshake, not data. A US mountain makes two calls (NWS gridpoints
+ Open-Meteo trailing actuals), so the live stream was paying ~0.8s of pure
handshake per mountain -- about a minute across the 79-mountain roster, for
nothing.

A `requests.Session` keeps a urllib3 connection pool per host and reuses warm
connections across threads. The pool is sized for the stream's concurrency; if it
were smaller, threads would silently queue on connections instead of running.

Retries are here too, rather than in each client. NRCS in particular resolves
flakily, and a bare `requests.get` turns a transient DNS blip into a dead
mountain on the map.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Must be >= api.STREAM_CONCURRENCY, or threads block waiting for a free
# connection and the "concurrency" is a lie.
POOL_MAXSIZE = 32

USER_AGENT = "ski-conditions-app (contact: set-your-email@example.com)"


def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=2,                       # 3 attempts, then give up: one mountain, not the stream
        backoff_factor=0.4,            # 0.0s, 0.4s, 0.8s
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(pool_connections=16, pool_maxsize=POOL_MAXSIZE,
                          max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": USER_AGENT})
    return s


# Module-level and shared across threads. requests.Session is not documented as
# thread-safe for mutation, but concurrent .get()/.post() against a mounted
# HTTPAdapter is -- the underlying urllib3 pool is. We never mutate it after
# build, so don't start doing so from a worker thread.
SESSION = _build_session()


def get(url: str, **kwargs) -> requests.Response:
    return SESSION.get(url, **kwargs)


def post(url: str, **kwargs) -> requests.Response:
    return SESSION.post(url, **kwargs)
