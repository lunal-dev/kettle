"""Result data structures for verification checks."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class CheckResult:
    """Standard result type for all verification checks.

    Attributes:
        verified: Whether the check passed
        message: Human-readable message describing the result
        details: Optional additional data (report info, dependency data, etc.)
    """

    verified: bool
    message: str
    details: Optional[dict] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "verified": self.verified,
            "message": self.message,
        }
        if self.details:
            result.update(self.details)
        return result
