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
        connect=1,                     # but an unreachable host retries the CONNECT only once
                                       # (2 x CONNECT_TIMEOUT), so a dead source fails in ~20s,
                                       # not 3 x 10s -- it won't come back within seconds anyway.
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


# Fail FAST on an unreachable host. A dead/slow source (e.g. BC's env.gov.bc.ca
# has timed out from the CI runner) must not stall the daily build: a scalar
# `timeout` sets both connect AND read, so a station whose host won't answer used
# to burn the full read budget (120s) x retries x stations -- that turned one flaky
# source into a 1-hour run (2026-08-23) and once a 6-hour hang that GitHub cancelled
# (2026-07-31, no deploy that day). Splitting the timeout caps the CONNECT phase
# hard (the common failure) while keeping a generous READ for big archive CSVs.
#
# A caller's bare int is treated as the READ budget and paired with this connect
# cap; an explicit (connect, read) tuple is passed through untouched; a missing
# timeout gets a sane default instead of blocking forever.
CONNECT_TIMEOUT = 10          # seconds to establish TCP+TLS before giving up
DEFAULT_READ_TIMEOUT = 90     # seconds to wait for the response body


def _cap_connect(kwargs: dict) -> dict:
    t = kwargs.get("timeout")
    if t is None:
        kwargs["timeout"] = (CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)
    elif isinstance(t, (int, float)):
        kwargs["timeout"] = (CONNECT_TIMEOUT, t)
    return kwargs


def get(url: str, **kwargs) -> requests.Response:
    return SESSION.get(url, **_cap_connect(kwargs))


def post(url: str, **kwargs) -> requests.Response:
    return SESSION.post(url, **_cap_connect(kwargs))
