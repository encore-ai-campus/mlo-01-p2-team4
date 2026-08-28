"""Lineage collection and duplicate-group helpers for Phase 3.

The implementation lives in :mod:`batch_validator` so structural partitioning
can use the same functions.  This module is the stable, focused import path
for callers that only need lineage handling.
"""

from .batch_validator import (
    collect_observed_lineage,
    detect_duplicate_lineage_groups,
)

__all__ = [
    "collect_observed_lineage",
    "detect_duplicate_lineage_groups",
]
