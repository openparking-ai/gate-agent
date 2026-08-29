"""Open Parking AI — the gate agent.

The intercom module. Its first process is the MALFUNCTION MONITOR: it watches
whatever a site declares -- a lane, an identification service, a platform -- and
tells a human what changed. The agent itself, the SIP endpoint that answers a
driver at the barrier, joins it in this package later.

**Nothing here has opening authority.** The monitor reads GETs and sends
messages: no vend, no resolve, no write to a lane. There is no client in this
package capable of another method, and that is swept rather than promised.

**Nothing here imports `lane_controller`.** This is a CONSUMER of the lane
contract, which is exactly the seat a third party takes, and it speaks HTTP to
that contract and nothing else. A shortcut into our own lane would make "our
software is an ordinary client of the contract" a sentence instead of a
property.
"""

from .client import ReadOnlyClient, TargetUnreachable
from .config import ConfigError, MonitorConfig
from .contract import CONTRACT_VERSION, MonitorCode, Notification
from .monitor import Monitor, UnsupportedContract
from .service import MonitorService, make_server

__all__ = [
    "CONTRACT_VERSION",
    "ConfigError",
    "Monitor",
    "MonitorCode",
    "MonitorConfig",
    "MonitorService",
    "Notification",
    "ReadOnlyClient",
    "TargetUnreachable",
    "UnsupportedContract",
    "make_server",
]
