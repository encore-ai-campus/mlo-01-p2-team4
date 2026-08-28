"""Publish the official Plan 1 validation and reverse-engineering artifacts."""

from __future__ import annotations

import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parents[2]
DEFAULT_REPORT_DIR = BASE_DIR
DEFAULT_DATA_DIR = PROJECT_DIR / "docs" / "Data"

REPORT_FILES = {
    "standard_metadata_validation": "hr-organization-standard-validation.json",
    "cleansing_validation": "hr-organization-cleansing-validation.json",
    "profile": "hr-organization-profile-report.json",
}


def _read_json(path):
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _combined_status(*documents):
    statuses = {document.get("status") for document in documents}
    if "error" in statuses:
        return "error"
    return "ready" if statuses == {"ready"} else "review-required"


def build_validation_document(standard_metadata_validation, cleansing_validation, profile):
    """Combine independent validator results without hiding their individual status."""
    return {
        "artifact": "plan1-validation",
        "status": _combined_status(
            standard_metadata_validation, cleansing_validation, profile
        ),
        "scope": {
            "plan": "Plan 1 — Phase 1~4",
            "source": profile.get("scope", {}).get("source"),
        },
        "profile_summary": {
            "status": profile.get("status"),
            "row_count": profile.get("row_count"),
            "column_count": profile.get("column_count"),
            "payload_key_sets_equal": profile.get("payload_key_sets_equal"),
            "duplicate_exact_rows": profile.get("duplicate_exact_rows"),
            "warning_count": len(profile.get("warnings", [])),
        },
        "standard_metadata_validation": standard_metadata_validation,
        "cleansing_validation": cleansing_validation,
    }


def _markdown_value(value):
    if value is True:
        return "통과"
    if value is False:
        return "실패"
    if value is None:
        return "-"
    return str(value).replace("|", "\\|")


def build_reverse_engineering_report(profile):
    """Render the profile JSON as the human-readable Plan 1 report."""
    scope = profile.get("scope", {})
    grain = profile.get("grain", {})
    lines = [
        "# 레거시 데이터 역공학 보고서",
        "",
        "## 범위",
        "",
        f"- 원천: `{scope.get('source', '-')}`",
        f"- 원천 성격: `{scope.get('source_type', '-')}`",
        f"- 프로파일 상태: `{profile.get('status', '-')}`",
        "",
        "## Grain",
        "",
        f"- 행 의미: {grain.get('row_meaning', '-')}",
        f"- 업무 키: {grain.get('business_key') or '미확정'}",
        f"- 후보 원천 식별자: {', '.join(grain.get('candidate_source_identifier', [])) or '-'}",
        f"- 판단 근거: {grain.get('reason', '-')}",
        "",
        "## 구조 요약",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 행 수 | {_markdown_value(profile.get('row_count'))} |",
        f"| Payload 컬럼 수 | {_markdown_value(profile.get('column_count'))} |",
        f"| Payload 키 구성 일치 | {_markdown_value(profile.get('payload_key_sets_equal'))} |",
        f"| 정확 중복 행 수 | {_markdown_value(profile.get('duplicate_exact_rows'))} |",
        f"| 예상 컬럼 누락 | {', '.join(profile.get('missing_expected_columns', [])) or '없음'} |",
        f"| 예상 외 컬럼 | {', '.join(profile.get('unexpected_columns', [])) or '없음'} |",
        "",
        "## 컬럼 프로파일",
        "",
        "| 컬럼 | NULL·sentinel | 원천 고유값 | 후보 고유값 | 앞뒤 공백 | 전각 공백 | 내부 공백 후보 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in profile.get("columns", []):
        column = profile.get("column_profiles", {}).get(name, {})
        lines.append(
            "| {name} | {nulls} | {raw} | {candidate} | {trim} | {fullwidth} | {internal} |".format(
                name=name,
                nulls=_markdown_value(column.get("null_or_sentinel_count")),
                raw=_markdown_value(column.get("distinct_raw_count")),
                candidate=_markdown_value(column.get("distinct_candidate_count")),
                trim=_markdown_value(column.get("leading_or_trailing_space_count")),
                fullwidth=_markdown_value(column.get("fullwidth_space_count")),
                internal=_markdown_value(column.get("internal_space_candidate_count")),
            )
        )

    lines.extend(["", "## 검증 결과", "", "| 검증 | 결과 |", "| --- | --- |"])
    for name, value in profile.get("checks", {}).items():
        lines.append(f"| {name} | {_markdown_value(value)} |")

    lines.extend(["", "## 검토 필요 사항", ""])
    warnings = profile.get("warnings", [])
    if warnings:
        for warning in warnings:
            text = warning if isinstance(warning, str) else json.dumps(
                warning, ensure_ascii=False, sort_keys=True
            )
            lines.append(f"- {text}")
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


def publish_plan1_artifacts(report_dir=DEFAULT_REPORT_DIR, data_dir=DEFAULT_DATA_DIR):
    """Write the two official Plan 1 artifacts from the three detailed reports."""
    report_dir = Path(report_dir)
    data_dir = Path(data_dir)
    standard = _read_json(report_dir / REPORT_FILES["standard_metadata_validation"])
    cleansing = _read_json(report_dir / REPORT_FILES["cleansing_validation"])
    profile = _read_json(report_dir / REPORT_FILES["profile"])

    validation = build_validation_document(standard, cleansing, profile)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (data_dir / "legacy_data_reverse_engineering_report.md").write_text(
        build_reverse_engineering_report(profile), encoding="utf-8"
    )
    return validation


def main():
    validation = publish_plan1_artifacts()
    print(f"Published docs/Data/validation.json ({validation['status']})")
    return validation


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_DATA_DIR",
    "DEFAULT_REPORT_DIR",
    "build_reverse_engineering_report",
    "build_validation_document",
    "publish_plan1_artifacts",
]
