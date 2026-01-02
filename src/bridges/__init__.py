"""
Matrix-synapse-deployment bridges module.

Contains bridge services for connecting external platforms to Matrix.
"""

from src.bridges.letta_matrix_bridge import (
    LettaMatrixBridge,
    BridgeConfig,
    initialize_bridge,
    get_bridge,
)

__all__ = [
    "LettaMatrixBridge",
    "BridgeConfig",
    "initialize_bridge",
    "get_bridge",
]
