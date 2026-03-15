"""
Central logging configuration for Sandhi AI backend.
Format: <datetime>.<level>.<message> e.g. 2025-03-10 12:00:00.INFO.message
"""
import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Configure root logger and all child loggers. Format: <datetime>.<type>.<message>"""
    fmt = "%(asctime)s.%(levelname)s.%(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Reduce noise from third-party libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
