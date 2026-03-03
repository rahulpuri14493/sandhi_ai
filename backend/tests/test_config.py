"""Unit tests for configuration."""
import pytest

from core.config import settings


def test_settings_has_required_attributes():
    """Settings object has all required configuration attributes."""
    assert hasattr(settings, "DATABASE_URL")
    assert hasattr(settings, "SECRET_KEY")
    assert hasattr(settings, "ALGORITHM")
    assert hasattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES")
    assert hasattr(settings, "PLATFORM_COMMISSION_RATE")


def test_settings_sensible_defaults():
    """Settings have sensible default values."""
    assert settings.ALGORITHM == "HS256"
    assert settings.PLATFORM_COMMISSION_RATE >= 0
    assert settings.PLATFORM_COMMISSION_RATE <= 1
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES > 0
    assert len(settings.SECRET_KEY) > 0
