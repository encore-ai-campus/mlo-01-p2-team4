"""Create stable rejected-row representations."""

from src.silver.contracts import RejectedRecord

from .record_validator import serialize_raw_record


def create_rejection(source_row_no, errors, cleaned_row):
    """Build a rejection row from ``(code, detail)`` errors."""
    error_codes = list(dict.fromkeys(code for code, _ in errors))
    return {
        "source_row_no": source_row_no,
        "error_codes": "|".join(error_codes),
        "error_details": " | ".join(detail for _, detail in errors),
        **cleaned_row,
    }


def make_rejected_record(observed_lineage, source_record, violations):
    """Build the immutable Phase 4 Reject value while retaining raw JSON."""
    return RejectedRecord(
        observed_lineage=observed_lineage,
        raw_json=serialize_raw_record(source_record),
        violations=tuple(violations),
    )


__all__ = ["create_rejection", "make_rejected_record"]
