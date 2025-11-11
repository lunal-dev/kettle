"""
Training module for attestable ML training.

This module provides deterministic, cryptographically verifiable training
with complete provenance tracking.
"""

from .orchestrator import train, verify_training_passport

__all__ = ["train", "verify_training_passport"]
