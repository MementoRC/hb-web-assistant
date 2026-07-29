"""hb_compat — adapters isolating external sub-package imports for web_assistant.

Contains thin wrappers so internal web_assistant modules never import
external sub-packages directly, keeping the import-linter boundary clean.
"""

from web_assistant.hb_compat.common import HummingbotLogger, get_logger

__all__ = ["HummingbotLogger", "get_logger"]
