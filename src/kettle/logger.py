"""Simple logging utilities using Rich for beautiful CLI output."""

import contextvars
from typing import Callable, Optional

from rich.console import Console

console = Console()

# Progress callback for streaming build updates
_progress_callback: contextvars.ContextVar[Optional[Callable[[str, str], None]]] = (
    contextvars.ContextVar("progress_callback", default=None)
)


def set_progress_callback(
    callback: Optional[Callable[[str, str], None]]
) -> contextvars.Token:
    """Set a progress callback for the current context.

    Args:
        callback: Function that receives (event_type, message) pairs.
                  Pass None to clear the callback.

    Returns:
        Context token that can be used to reset the callback.
    """
    return _progress_callback.set(callback)


def _emit(event_type: str, message: str):
    """Emit progress event if callback is set."""
    cb = _progress_callback.get()
    if cb:
        cb(event_type, message)


def log(message: str, style: str = ""):
    """Log a message with optional style.

    Replaces print() for consistent output formatting.

    Args:
        message: The message to log
        style: Rich style string (e.g., "bold", "dim", "red")
    """
    _emit("log", message)
    console.print(message, style=style)


def log_success(message: str):
    """Log a success message with green checkmark."""
    _emit("success", message)
    console.print(f"✓ {message}", style="green")


def log_error(message: str):
    """Log an error message with red X."""
    _emit("error", message)
    console.print(f"✗ {message}", style="red")


def log_warning(message: str):
    """Log a warning message with yellow warning symbol."""
    _emit("warning", message)
    console.print(f"⚠ {message}", style="yellow")


def log_section(title: str):
    """Log a section header with decorative border."""
    _emit("section", title)
    console.print(f"\n[bold]━━━ {title} ━━━[/bold]")