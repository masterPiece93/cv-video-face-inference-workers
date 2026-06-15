"""Unit tests for face_verification_worker strategies __init__ (get_strategy factory)."""
import pytest

from workers.face_verification_worker.services.strategies import (
    CommonFacesStrategy,
    WithProfileStrategy,
    get_strategy,
)


class TestGetStrategy:
    def test_common_faces_by_name(self):
        s = get_strategy("common_faces")
        assert isinstance(s, CommonFacesStrategy)

    def test_with_profile_by_name(self):
        s = get_strategy("with_profile")
        assert isinstance(s, WithProfileStrategy)

    def test_alias_profile_match_all(self):
        s = get_strategy("profile_match_all")
        assert isinstance(s, WithProfileStrategy)

    def test_alias_any_common(self):
        s = get_strategy("any_common")
        assert isinstance(s, CommonFacesStrategy)

    def test_custom_tolerance_applied(self):
        s = get_strategy("common_faces", tolerance=0.42)
        assert s.tolerance == 0.42

    def test_unknown_name_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            get_strategy("nonexistent_strategy")

    def test_default_tolerance_is_0_6(self):
        s = get_strategy("with_profile")
        assert s.tolerance == 0.6
