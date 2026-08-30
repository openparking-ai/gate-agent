"""Open Parking AI — the gate agent.

The intercom module. It ships TWO processes and neither can open a barrier.

The MALFUNCTION MONITOR watches whatever a site declares -- a lane, an
identification service, a platform, a capture process -- and tells a human what
changed. The CAPTURE PROCESS photographs the declared cameras on a timer and on
a lane's arrivals and vends, keeps what a per-site retention rule allows, and
deletes the rest. The agent itself, the SIP endpoint that answers a driver at
the barrier, joins them in this package later.

**Nothing here has opening authority.** The monitor reads GETs and sends
messages: no vend, no resolve, no write to a lane. There is no client in this
package capable of another method, and that is swept rather than promised.

**Nothing here imports `lane_controller`.** This is a CONSUMER of the lane
contract, which is exactly the seat a third party takes, and it speaks HTTP to
that contract and nothing else. A shortcut into our own lane would make "our
software is an ordinary client of the contract" a sentence instead of a
property.
"""

from .camera import CameraUnreachable, SnapshotCamera
from .capture import CaptureProcess, UnsupportedLaneContract
from .capture_service import CaptureService
from .client import ReadOnlyClient, TargetUnreachable
from .config import CaptureConfig, ConfigError, MonitorConfig
from .contract import CONTRACT_VERSION, CaptureCode, MonitorCode, Notification
from .monitor import Monitor, UnsupportedContract
from .service import MonitorService, make_server
from .store import CaptureStore

__all__ = [
    "CONTRACT_VERSION",
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
