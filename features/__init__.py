"""features — State representation and structural detection for the world model."""

from features.state import StateBuilder, build_state_from_parquet
from features.structures import (
    StructureInstance,
    SwingPoint,
    detect_structures,
    detect_swing_points,
    compute_atr,
    compute_premium_discount,
)

__all__ = [
    "StateBuilder",
    "build_state_from_parquet",
    "StructureInstance",
    "SwingPoint",
    "detect_structures",
    "detect_swing_points",
    "compute_atr",
    "compute_premium_discount",
]
