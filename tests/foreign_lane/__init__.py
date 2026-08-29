"""A lane that is not ours, implementing the lane contract and nothing else.

Written as an implementer would write it: from `lane-controller/docs/CONTRACT.md`,
with its own vocabulary, on the standard library. No loops, no confirmation, no
identity service, no platform, and a fallback reason from its own vendor's words.

**It imports exactly two things from `lane_controller`**, and they are the sets
that contract PUBLISHES as closed: the malfunction codes, and which of them may
never be alarmed on. Everything else about this lane is its own. A stub built on
our machinery would prove nothing about a foreign lane, so
`tests/test_targets.py` reads the imports out of this package's source and
requires them to be those two and no others.

That mirrors the same rule in `lane-controller/tests/test_third_party_seat.py`.
It is a second stub rather than that one because `tests/` is not part of the
installed package -- and a second implementer writing from the document is
closer to the thing being measured than a copy would be.
"""

from .lane import VENDOR_REASON, ForeignLane, make_server

__all__ = ["VENDOR_REASON", "ForeignLane", "make_server"]
