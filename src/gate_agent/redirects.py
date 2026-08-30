"""One opener, and it does not follow a redirect. Both directions use it.

**A 3xx is an ANSWER, and it is an answer this process does not accept.**

`urllib.request.urlopen` follows redirects by default, and CPython's
`HTTPRedirectHandler` rebuilds the new request from the old one's headers minus
only `content-length` and `content-type` -- so `Authorization` travels with it,
to whatever host the payload named. That is three failures from one line of a
target's response:

  * **the credential leaves.** A monitor holds an operator token for the
    platform and a bearer token for the paging endpoint, both read from files
    precisely so they are on no route and in no argument vector. A followed
    redirect puts one on the wire to a host of the target's choosing.
  * **a target substitutes somebody else's health as its own.** The monitor read
    `{"ok": true}` from a third host and published it as the lane's answer.
  * **a webhook that was never delivered reports SUCCESS.** `deliver()` returned
    without raising on a POST that went nowhere, so `sink_delivery_failed` --
    the machinery this module has precisely so that "never wrong silently"
    applies to the messenger too -- never fired.

Every URL this process opens is one a human declared in its configuration file.
There is no case in which the right thing to do with a `Location` header is to
follow it, and a monitor is pointed at a third party's lane BY DESIGN, so "the
target is trusted" is not available here.

With the handler below in the opener, a 3xx arrives as an `HTTPError` carrying
its status, exactly as a 4xx does. The client publishes it as a refusal with
that status beside it, and the webhook sink treats it as a delivery that did not
happen. Nothing is guessed and nothing is followed.
"""

from __future__ import annotations

import urllib.request


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuses to build the redirected request, which raises the 3xx instead.

    Returning `None` from `redirect_request` is urllib's own way of saying "do
    not follow this one": the handler chain falls through to the default error
    handler, which raises `HTTPError` with the 3xx status and headers intact. So
    the status reaches the caller rather than being swallowed, and there is one
    code path for "the target answered something we will not act on".

    Subclassing rather than removing the handler, because `build_opener`
    replaces a default handler with any subclass of it -- so this is the only
    redirect handler in the chain, and there is no second one to fall back to.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_opener(*handlers):
    """An opener that follows nothing, plus whatever handlers a caller needs.

    **Every opener in this package is built here.** A camera answers `401` with
    a `WWW-Authenticate` header and expects the credential on the retry, which
    is an authentication handler and therefore a second opener -- and a second
    opener built anywhere else would be a second opener with urllib's default
    redirect handler in it. That is precisely how the credential leaves: the
    retry that carries `Authorization` is the request a `Location` would take
    somewhere else.

    So the refusal is not a property of one opener. It is a property of the only
    function that makes them, and `tests/test_no_opening_authority.py` sweeps
    the source for a `build_opener` or an `urlopen` anywhere else.
    """
    return urllib.request.build_opener(_NoRedirects, *handlers)


_OPENER = build_opener()


def open_url(request, timeout: float):
    """Open `request`, following nothing. The unauthenticated opener."""
    return _OPENER.open(request, timeout=timeout)


__all__ = ["build_opener", "open_url"]
