"""Tests for core/logging_config."""
from core.logging_config import configure_logging


def test_configure_logging_default():
    """configure_logging runs without error with default level."""
    configure_logging()


def test_configure_logging_custom_level():
    """configure_logging accepts level string."""
    configure_logging(level="DEBUG")
    configure_logging(level="INFO")
