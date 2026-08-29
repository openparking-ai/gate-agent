"""A lane that is not ours, implementing the lane contract and nothing else.

Written as an implementer would write it: from `lane-controller/docs/CONTRACT.md`,
with its own vocabulary, on the standard library. No loops, no confirmation, no
identity service, no platform, and a fallback reason from its own vendor's words.

**It imports NOTHING from `lane_controller`.** It used to import two things --
the malfunction codes and the never-alarm set -- because those were the two the
document withheld, which made "written from the document" false for exactly the
part that mattered. The document publishes them now, under **The closed sets**,
and `lane.py` carries a literal copy with its source named beside it.

`tests/test_targets.py` reads the imports out of this package's source and
requires there to be none of ours, and requires the literal to equal
`MalfunctionCode`'s values in the suite that has our lane installed. A stub built
on our machinery would prove nothing about a foreign lane; a stub whose copy of a
published set has quietly drifted would prove something false.
"""

from .lane import (
    MALFUNCTION_CODES,
    NEVER_ALARM_CODES,
    VENDOR_CAVEAT,
    VENDOR_REASON,
    ForeignLane,
    make_server,
)

__all__ = [
    "MALFUNCTION_CODES",
    "NEVER_ALARM_CODES",
    "VENDOR_CAVEAT",
    "VENDOR_REASON",
    "ForeignLane",
    "make_server",
]
