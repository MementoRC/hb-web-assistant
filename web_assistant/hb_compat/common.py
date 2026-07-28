"""Re-export of the hb-logger canonical logger used by web_assistant.

Isolating the ``logger`` sub-package import here keeps the rest of
web_assistant import-clean of the external dependency. See ADR 0001 Group D
(hb-web-assistant#17) — pilot migration for the logger-bypass cluster.
"""

from __future__ import annotations

import logging

from logger import HummingbotLogger  # type: ignore[import-untyped]

__all__ = ["HummingbotLogger", "get_logger"]


def get_logger(name: str) -> HummingbotLogger:
    """Return the named :class:`HummingbotLogger`, creating it on first access.

    Importing the ``logger`` sub-package registers ``HummingbotLogger`` as the
    process-wide ``logging.getLoggerClass()``, so ``logging.getLogger(name)``
    already returns a ``HummingbotLogger`` instance. This wrapper gives call
    sites an explicit, correctly-typed entry point instead of reaching into
    ``logging`` (and the external ``logger`` package) directly.
    """
    return logging.getLogger(name)
