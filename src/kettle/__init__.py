"""Kettle - Attestable builds for TEE environments."""

__version__ = "0.1.0"

# Import toolchains to auto-register them
from . import toolchains  # noqa: F401

__all__ = [
    "__version__",
    "core",
    "provenance",
    "verification",
    "build",
    "toolchains",
]
