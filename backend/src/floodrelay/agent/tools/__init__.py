"""Tool layer.

Every tool has a docstring the model actually reads, typed arguments, an
explicit timeout, and a typed failure return. None of them raise into the agent
loop: a failure is a value the model can reason about.
"""

from .geocode import GeocodeResult, parse_coordinates, resolve
from .imagery import score_photo, vision_available
from .places import find_places
from .reliefweb import situation_context
from .roster import roster_assign, roster_search
from .routing import compute_routes, heatmap
from .weather import rainfall

__all__ = [
    "GeocodeResult",
    "compute_routes",
    "find_places",
    "heatmap",
    "parse_coordinates",
    "rainfall",
    "resolve",
    "roster_assign",
    "roster_search",
    "score_photo",
    "situation_context",
    "vision_available",
]
