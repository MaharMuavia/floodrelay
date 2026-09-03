"""Tool layer.

Every tool has a docstring the model actually reads, typed arguments, an
explicit timeout, and a typed failure return. None of them raise into the agent
loop: a failure is a value the model can reason about.
"""

from .gdacs import global_flood_alerts
from .geocode import GeocodeResult, parse_coordinates, resolve
from .imagery import score_photo, vision_available
from .imagery_layers import available_layers
from .ndma import situation as ndma_situation
from .places import find_places
from .reliefweb import situation_context
from .river import river_discharge
from .roster import roster_assign, roster_search
from .routing import compute_routes, heatmap
from .weather import rainfall

__all__ = [
    "GeocodeResult",
    "available_layers",
    "compute_routes",
    "find_places",
    "global_flood_alerts",
    "heatmap",
    "ndma_situation",
    "parse_coordinates",
    "rainfall",
    "resolve",
    "river_discharge",
    "roster_assign",
    "roster_search",
    "score_photo",
    "situation_context",
    "vision_available",
]
