"""Simple logging utilities using Rich for beautiful CLI output."""

from rich.console import Console

console = Console()


def log(message: str, style: str = ""):
    """Log a message with optional style.

    Replaces print() for consistent output formatting.

    Args:
        message: The message to log
        style: Rich style string (e.g., "bold", "dim", "red")
    """
    console.print(message, style=style)


def log_success(message: str):
    """Log a success message with green checkmark."""
    console.print(f"✓ {message}", style="green")


def log_error(message: str):
    """Log an error message with red X."""
    console.print(f"✗ {message}", style="red")


def log_warning(message: str):
    """Log a warning message with yellow warning symbol."""
    console.print(f"⚠ {message}", style="yellow")

def log_section(title: str):
    """Log a section header with decorative border."""
    console.print(f"\n[bold]━━━ {title} ━━━[/bold]")