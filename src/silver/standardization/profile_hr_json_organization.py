import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR.parents[2] / "test" / "data" / "records.json"
OUTPUT_FILE = BASE_DIR / "hr-organization-json-profile-report.json"

NULL_TOKENS = {
    "",
    "NULL",
    "N/A",
    "NONE",
    "UNKNOWN",
    "없음",
    "미상",
    "오류값",
    "-",
}

EXPECTED_COLUMNS = {
    "area_no",
    "area_nm",
    "p_area_no",
    "p_area_nm",
    "top_area_no",
    "top_area_nm",
    "top_area_lvl",
    "mgr_no",
    "mgr_nm",
    "mgr_dept_nm",
    "mgr_pos_nm",
    "mgr_hire_dtm",
    "mgr_act_yn",
    "area_reg_dtm",
    "top_area_reg_dtm",
}

DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S",
    "%Y.%m.%d %H:%M:%S",
    "%Y%m%d%H%M%S",
)


def raw_text(value):
    if value is None:
        return ""

    return str(value)


def clean_text(value):
    value = unicodedata.normalize(
        "NFKC",
        raw_text(value),
    ).strip()

    if value.upper() in NULL_TOKENS:
        return None

    return value


def normalize_identifier(value):
    value = clean_text(value)

    if value is None:
        return None

    upper_value = value.upper()
    compact_value = re.sub(
        r"[\s_-]+",
        "",
        upper_value,
    )

    if match := re.fullmatch(
        r"BIZ(\d+)",
        compact_value,
    ):
        return f"BIZ_{match.group(1)}"

    if match := re.fullmatch(
        r"EMP(\d+)",
        compact_value,
    ):
        return f"EMP{match.group(1)}"

    return re.sub(
        r"_+",
        "_",
        re.sub(
            r"[\s-]+",
            "_",
            upper_value,
        ),
    )


def normalize_name_candidate(value):
    value = clean_text(value)

    if value is None:
        return None

    # 공백을 제거하지 않고 하나로만 축약한다.
    # 내부 공백 패턴은 별도로 프로파일링한다.
    return re.sub(r"\s+", " ", value)


def map_status(value):
    value = clean_text(value)

    if value is None:
        return None

    value = value.upper()

    if value in {"Y", "YES", "1", "사용", "재직"}:
        return "ACTIVE"

    if value in {"N", "NO", "0", "미사용", "퇴직"}:
        return "INACTIVE"

    return None


def map_level(value):
    value = clean_text(value)

    if value is None:
        return None

    if value.upper() in {
        "1",
        "L1",
        "TOP LEVEL",
        "TOP_LEVEL",
        "최상위",
    }:
        return "TOP_LEVEL"

    return None


def parse_datetime_candidate(value):
    value = clean_text(value)

    if value is None:
        return None

    korean_time = re.fullmatch(
        r"(\d{4}[-/.]\d{2}[-/.]\d{2})\s+"
        r"(오전|오후)\s+"
        r"(\d{1,2}):(\d{2}):(\d{2})",
        value,
    )

    if korean_time:
        date_text, ampm, hour, minute, second = (
            korean_time.groups()
        )

        year, month, day = map(
            int,
            re.split(r"[-/.]", date_text),
        )

        hour = int(hour)

        if ampm == "오후" and hour < 12:
            hour += 12

        if ampm == "오전" and hour == 12:
            hour = 0

        try:
            return datetime(
                year,
                month,
                day,
                hour,
                int(minute),
                int(second),
            ).isoformat(timespec="seconds")
        except ValueError:
            return None

    for date_format in DATETIME_FORMATS:
        try:
            return datetime.strptime(
                value,
                date_format,
            ).isoformat(timespec="seconds")
        except ValueError:
            continue

    return None


def normalize_for_profile(field, value):
    if field.endswith("_no"):
        return normalize_identifier(value)

    if field.endswith("_nm"):
        return normalize_name_candidate(value)

    if field.endswith("_dtm"):
        return parse_datetime_candidate(value)

    if field == "mgr_act_yn":
        return map_status(value)

    if field == "top_area_lvl":
        return map_level(value)

    return clean_text(value)


def pattern_of(field, value):
    raw = raw_text(value)
    cleaned = clean_text(value)

    if cleaned is None:
        return "NULL_OR_SENTINEL"

    if field.endswith("_no"):
        upper_value = cleaned.upper()

        if re.fullmatch(r"BIZ_\d+", upper_value):
            return "BIZ_UNDERSCORE"

        if re.fullmatch(r"BIZ-\d+", upper_value):
            return "BIZ_HYPHEN"

        if re.fullmatch(r"BIZ\s+\d+", upper_value):
            return "BIZ_SPACE"

        if re.fullmatch(r"BIZ\d+", upper_value):
            return "BIZ_COMPACT"

        if re.fullmatch(r"EMP-\d+", upper_value):
            return "EMP_HYPHEN"

        if re.fullmatch(r"EMP\s+\d+", upper_value):
            return "EMP_SPACE"

        if re.fullmatch(r"EMP\d+", upper_value):
            return "EMP_COMPACT"

        return "OTHER_IDENTIFIER"

    if field.endswith("_dtm"):
        if re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
            cleaned,
        ):
            return "ISO_SECOND"

        if re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+",
            cleaned,
        ):
            return "ISO_FRACTION"

        if re.fullmatch(
            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?",
            cleaned,
        ):
            return "SPACE"

        if re.fullmatch(
            r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}",
            cleaned,
        ):
            return "SLASH"

        if re.fullmatch(
            r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}",
            cleaned,
        ):
            return "DOT"

        if re.fullmatch(r"\d{14}", cleaned):
            return "COMPACT"

        if re.fullmatch(
            r"\d{4}[-/.]\d{2}[-/.]\d{2}\s+"
            r"(오전|오후)\s+\d{1,2}:\d{2}:\d{2}",
            cleaned,
        ):
            return "KOREAN_AMPM"

        return "INVALID_OR_OTHER"

    if field == "mgr_act_yn":
        if map_status(cleaned) == "ACTIVE":
            return "ACTIVE"

        if map_status(cleaned) == "INACTIVE":
            return "INACTIVE"

        return "OTHER_STATUS"

    if field == "top_area_lvl":
        if map_level(cleaned) == "TOP_LEVEL":
            return "TOP_LEVEL"

        return "OTHER_LEVEL"

    if "\t" in raw:
        return "TAB"

    if "　" in raw:
        return "FULLWIDTH_SPACE"

    if raw != raw.strip():
        return "LEADING_OR_TRAILING_SPACE"

    if re.search(r"\S\s+\S", cleaned):
        return "INTERNAL_SPACE"

    return "PLAIN"


def column_profile(rows, field):
    values = [
        row.get(field)
        for row in rows
    ]

    raw_values = [
        raw_text(value)
        for value in values
    ]

    candidate_values = [
        normalize_for_profile(field, value)
        for value in values
    ]

    pattern_counts = Counter(
        pattern_of(field, value)
        for value in values
    )

    pattern_samples = defaultdict(list)

    for value in values:
        pattern = pattern_of(field, value)
        text = raw_text(value)

        if (
            text not in pattern_samples[pattern]
            and len(pattern_samples[pattern]) < 5
        ):
            pattern_samples[pattern].append(text)

    non_null_values = [
        value
        for value in values
        if clean_text(value) is not None
    ]

    conversion_failures = sum(
        clean_text(value) is not None
        and normalize_for_profile(field, value) is None
        for value in values
    )

    return {
        "row_count": len(values),
        "null_or_sentinel_count": sum(
            clean_text(value) is None
            for value in values
        ),
        "distinct_raw_count": len(set(raw_values)),
        "distinct_trimmed_count": len({
            value.strip()
            for value in raw_values
        }),
        "distinct_candidate_count": len({
            value
            for value in candidate_values
            if value is not None
        }),
        "conversion_failure_count": conversion_failures,
        "leading_or_trailing_space_count": sum(
            raw_text(value) != raw_text(value).strip()
            for value in values
        ),
        "tab_count": sum(
            "\t" in raw_text(value)
            for value in values
        ),
        "fullwidth_space_count": sum(
            "　" in raw_text(value)
            for value in values
        ),
        "internal_space_candidate_count": sum(
            bool(re.search(
                r"\S\s+\S",
                raw_text(value),
            ))
            for value in non_null_values
        ),
        "pattern_counts": dict(pattern_counts),
        "pattern_samples": dict(pattern_samples),
    }


def key_profile(rows, field):
    keys = [
        normalize_for_profile(field, row.get(field))
        for row in rows
    ]

    keys = [
        key
        for key in keys
        if key is not None
    ]

    counts = Counter(keys)
    repeated = [
        count
        for count in counts.values()
        if count > 1
    ]

    return {
        "distinct_count": len(counts),
        "null_or_invalid_count": len(rows) - len(keys),
        "repeated_key_groups": len(repeated),
        "duplicate_extra_rows": sum(
            count - 1
            for count in repeated
        ),
        "maximum_rows_per_key": max(
            counts.values(),
            default=0,
        ),
    }


def dependency_profile(
    rows,
    key_field,
    dependent_fields,
):
    groups = defaultdict(list)

    for row in rows:
        key = normalize_for_profile(
            key_field,
            row.get(key_field),
        )

        if key is not None:
            groups[key].append(row)

    result = {}

    for field in dependent_fields:
        conflicting_groups = 0
        invalid_groups = 0
        missing_rows = 0
        conflict_samples = []

        for key, group in groups.items():
            values = []
            invalid_count = 0

            for row in group:
                raw_value = row.get(field)
                candidate = normalize_for_profile(
                    field,
                    raw_value,
                )

                if (
                    clean_text(raw_value) is not None
                    and candidate is None
                ):
                    invalid_count += 1
                elif candidate is not None:
                    values.append(candidate)

            unique_values = sorted(set(values))

            if invalid_count:
                invalid_groups += 1

            missing_rows += sum(
                normalize_for_profile(
                    field,
                    row.get(field),
                ) is None
                for row in group
            )

            if len(unique_values) > 1:
                conflicting_groups += 1

                if len(conflict_samples) < 5:
                    conflict_samples.append({
                        "key": key,
                        "values": unique_values[:10],
                    })

        result[field] = {
            "conflicting_key_groups": conflicting_groups,
            "invalid_value_groups": invalid_groups,
            "missing_value_rows": missing_rows,
            "conflict_samples": conflict_samples,
        }

    return result


def conditional_completeness(rows):
    rules = {
        "mgr_no": [
            "mgr_nm",
            "mgr_dept_nm",
            "mgr_pos_nm",
            "mgr_hire_dtm",
            "mgr_act_yn",
        ],
        "p_area_no": [
            "p_area_nm",
        ],
        "top_area_no": [
            "top_area_nm",
            "top_area_lvl",
            "top_area_reg_dtm",
        ],
        "area_no": [
            "area_nm",
            "area_reg_dtm",
        ],
    }

    result = {}

    for key_field, dependent_fields in rules.items():
        key_present = 0
        dependent_missing = Counter()

        for row in rows:
            key_value = normalize_for_profile(
                key_field,
                row.get(key_field),
            )

            if key_value is None:
                continue

            key_present += 1

            for field in dependent_fields:
                if normalize_for_profile(
                    field,
                    row.get(field),
                ) is None:
                    dependent_missing[field] += 1

        result[key_field] = {
            "key_present_rows": key_present,
            "dependent_missing_counts": dict(
                dependent_missing
            ),
        }

    return result


def main():
    with INPUT_FILE.open(
        encoding="utf-8",
    ) as file:
        records = json.load(file)

    rows = [
        record.get("payload", {})
        for record in records
    ]

    payload_key_sets = {
        frozenset(row.keys())
        for row in rows
    }

    payload_columns = set().union(
        *payload_key_sets
    )

    columns = sorted(payload_columns)

    exact_row_counts = Counter(
        tuple(
            raw_text(row.get(column))
            for column in columns
        )
        for row in rows
    )

    duplicate_exact_rows = sum(
        count - 1
        for count in exact_row_counts.values()
        if count > 1
    )

    missing_expected_columns = sorted(
        EXPECTED_COLUMNS - payload_columns
    )

    unexpected_columns = sorted(
        payload_columns - EXPECTED_COLUMNS
    )

    report = {
        "status": (
            "review-required"
            if not missing_expected_columns
            else "error"
        ),
        "scope": {
            "source": "records.json",
            "source_type": "supplemental_legacy_input",
            "official_csv_available": False,
            "official_unique_field_count": 16,
            "observed_payload_field_count": len(
                payload_columns
            ),
        },
        "grain": {
            "row_meaning": (
                "하나의 area 원본 레코드에 "
                "상위 조직과 관리자 정보가 결합된 행"
            ),
            "business_key": None,
            "candidate_source_identifier": [
                "area_no"
            ],
            "reason": (
                "공식 기준 파일이 없고 "
                "area_no의 업무 의미가 미확정임"
            ),
        },
        "row_count": len(rows),
        "column_count": len(payload_columns),
        "columns": columns,
        "missing_expected_columns": (
            missing_expected_columns
        ),
        "unexpected_columns": unexpected_columns,
        "payload_key_sets_equal": (
            len(payload_key_sets) == 1
        ),
        "duplicate_exact_rows": duplicate_exact_rows,
        "column_profiles": {
            column: column_profile(rows, column)
            for column in columns
        },
        "key_profiles": {
            "mgr_no": key_profile(rows, "mgr_no"),
            "p_area_no": key_profile(
                rows,
                "p_area_no",
            ),
            "top_area_no": key_profile(
                rows,
                "top_area_no",
            ),
            "area_no": key_profile(rows, "area_no"),
        },
        "dependency_profiles": {
            "employee": dependency_profile(
                rows,
                "mgr_no",
                [
                    "mgr_nm",
                    "mgr_act_yn",
                    "mgr_pos_nm",
                    "mgr_hire_dtm",
                    "mgr_dept_nm",
                ],
            ),
            "parent_organization": dependency_profile(
                rows,
                "p_area_no",
                [
                    "p_area_nm",
                    "top_area_no",
                    "top_area_nm",
                    "top_area_reg_dtm",
                    "top_area_lvl",
                ],
            ),
            "area_record": dependency_profile(
                rows,
                "area_no",
                [
                    "area_nm",
                    "area_reg_dtm",
                ],
            ),
        },
        "conditional_completeness": (
            conditional_completeness(rows)
        ),
        "checks": {
            "payload_key_sets_equal": (
                len(payload_key_sets) == 1
            ),
            "expected_columns_present": (
                not missing_expected_columns
            ),
            "official_baseline_available": False,
            "business_key_confirmed": False,
        },
        "warnings": [
            "공식 CSV 4종을 사용할 수 없어 records.json만 분석함",
            "가이드의 16개 고유 필드명은 실제 헤더로 검증되지 않음",
            "이름 내부 공백은 제거하지 않고 이상 패턴으로 집계함",
            "변환 실패 값은 별도 건수로 기록함",
            "area_no는 업무키로 확정하지 않음",
        ],
    }

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
