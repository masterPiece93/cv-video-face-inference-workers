"""Verification strategies package and factory."""
from typing import Type

from workers.face_verification_worker.services.strategies.base import (
    BaseVerificationStrategy,
    VerificationResult,
)
from workers.face_verification_worker.services.strategies.common_faces import CommonFacesStrategy
from workers.face_verification_worker.services.strategies.with_profile import WithProfileStrategy

__all__ = [
    "BaseVerificationStrategy",
    "VerificationResult",
    "CommonFacesStrategy",
    "WithProfileStrategy",
    "get_strategy",
]

_STRATEGIES: dict[str, Type[BaseVerificationStrategy]] = {
    "common_faces": CommonFacesStrategy,
    "with_profile": WithProfileStrategy,
    "profile_match_all": WithProfileStrategy,   # alias
    "any_common": CommonFacesStrategy,          # alias
}


def get_strategy(name: str, tolerance: float = 0.6) -> BaseVerificationStrategy:
    """Factory to instantiate a verification strategy.

    Args:
        name: Strategy name.
        tolerance: Face match distance threshold.

    Returns:
        Configured strategy instance.
    """
    if name not in _STRATEGIES:
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {list(_STRATEGIES)}"
        )
    return _STRATEGIES[name](tolerance=tolerance)
