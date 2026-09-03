"""Open Parking AI — the gate agent.

The intercom module. It ships THREE processes and one of them can ask a barrier
to move.

The AGENT is the intercom's first line: it answers the call, works out which
lane the call belongs to, reads that lane's last decision through the contract,
says what happened in every language the site declared, and when the case needs a
person it calls one, stays in both calls, and records the authorisation they key.
**From round 7 it can also command a vend** at a lane a site gave it an act
token for, and pulse a standalone intercom's own relay on a human's word -- the
LANE applies its own refusals, and what a given configuration can ask for is
`AgentConfig.act_surface`, printed at every start. `docs/CONTRACT.md`, "IT CAN
NOW COMMAND A VEND", is where that is described in full.

The MALFUNCTION MONITOR watches whatever a site declares -- a lane, an
identification service, a platform, a capture process -- and tells a human what
changed. The CAPTURE PROCESS photographs the declared cameras on a timer and on
a lane's arrivals and vends, keeps what a per-site retention rule allows, and
deletes the rest.

**The MONITOR and the CAPTURE process have no opening authority.** Both read
GETs and neither writes to a lane; neither holds a client capable of another
method, and that is swept rather than promised. **The agent is the exception and
it is a narrow one:** exactly one module, `act.py`, may build a non-`GET` AT A
LANE, to exactly one path held in a constant, and it cannot be constructed
without an act token -- so a lane a site declared none for has no client at all
rather than a client the lane would refuse. The sweep exempts that file and the
webhook sink by name and nothing else, which is what keeps the exception
bounded. SIP itself is an external user agent's job, driven over a local control
socket by the one module allowed to open one -- which cannot see a lane's
address or its credential, and is held to that by the same sweep.

**Nothing here imports `lane_controller`.** This is a CONSUMER of the lane
contract, which is exactly the seat a third party takes, and it speaks HTTP to
that contract and nothing else. A shortcut into our own lane would make "our
software is an ordinary client of the contract" a sentence instead of a
property.
"""

from .agent import Agent, AudioMissing
from .agent_service import AgentService
from .camera import CameraUnreachable, SnapshotCamera
from .capture import CaptureProcess, UnsupportedLaneContract
from .capture_service import CaptureService
from .cases import LaneReading, derive
from .client import ReadOnlyClient, TargetUnreachable
from .config import AgentConfig, CaptureConfig, ConfigError, MonitorConfig
from .contract import (
    CONTRACT_VERSION,
    AgentCase,
    AgentCode,
    Authorisation,
    CaptureCode,
    MonitorCode,
    Notification,
)
from .monitor import Monitor, UnsupportedContract
from .service import MonitorService, make_server
from .store import CaptureStore
from .ua import UaEvent, UaEventKind, UaLeg, UaUnreachable, UaUnsupportedVersion
from .ua_baresip import BaresipUa

__all__ = [
    "CONTRACT_VERSION",
    "Agent",
    "AgentCase",
    "AgentCode",
    "AgentConfig",
    "AgentService",
    "AudioMissing",
    "Authorisation",
    "BaresipUa",
    "LaneReading",
    "UaEvent",
    "UaEventKind",
    "UaLeg",
    "UaUnreachable",
    "UaUnsupportedVersion",
    "derive",
    "CameraUnreachable",
    "CaptureCode",
    "CaptureConfig",
    "CaptureProcess",
    "CaptureService",
    "CaptureStore",
    "ConfigError",
    "Monitor",
    "MonitorCode",
    "MonitorConfig",
    "MonitorService",
    "Notification",
    "ReadOnlyClient",
    "SnapshotCamera",
    "TargetUnreachable",
    "UnsupportedContract",
    "UnsupportedLaneContract",
    "make_server",
]
