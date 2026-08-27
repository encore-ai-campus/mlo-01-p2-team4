"""Phase 5 공개 dataclass와 Silver v1 표준 산출물의 일치를 검증한다."""

import csv
import json
from copy import deepcopy
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from src.silver.contracts.phase5 import (
    AreaData,
    EmployeeData,
    JoinReferenceData,
    JoinReferenceKey,
    ModelCounts,
    ModelMetadata,
    ModelRecord,
    ParentAreaData,
    Phase5Output,
)
from src.silver.modeling.assembly import compute_model_fingerprint
from src.silver.modeling.phase4_binding import Phase4ContractViolation
from src.silver.modeling.projections import (
    ProjectionCandidate,
    ensure_no_projection_conflicts,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
STANDARDS_ROOT = REPOSITORY_ROOT / "standards" / "silver" / "v1"

MODEL_TYPES = {
    "employee": EmployeeData,
    "area": AreaData,
    "parent_area": ParentAreaData,
    "join_reference": JoinReferenceData,
}

EXPECTED_MODEL_FIELDS = {
    "employee": (
        "employee_id",
        "employee_name",
        "employee_department_name",
        "employee_position_name",
        "employee_hire_datetime",
        "employee_status_code",
    ),
    "area": (
        "area_id",
        "area_name",
        "parent_area_id",
        "employee_id",
        "area_registration_date",
    ),
    "parent_area": (
        "top_area_id",
        "top_area_name",
        "top_area_level_code",
        "top_area_registration_date",
    ),
    "join_reference": (
        "area_id",
        "parent_area_id",
        "parent_area_name",
        "employee_id",
        "employee_name",
        "employee_department_name",
        "employee_position_name",
        "employee_hire_datetime",
        "employee_status_code",
    ),
}

EXPECTED_MODEL_KEYS = {
    "employee": ("employee_id",),
    "area": ("area_id",),
    "parent_area": ("top_area_id",),
    "join_reference": ("area_id", "employee_id"),
}


def _field_names(contract_type: type[object]) -> tuple[str, ...]:
    """dataclass 선언 순서의 필드 이름을 반환한다.

    Args:
        contract_type: 확인할 frozen dataclass 타입.

    Returns:
        선언 순서를 보존한 필드 이름 tuple.
    """

    names = []
    for field_definition in fields(contract_type):
        names.append(field_definition.name)
    return tuple(names)


def _load_json(path: Path) -> dict[str, Any]:
    """UTF-8 JSON object를 파싱한다.

    Args:
        path: 읽을 JSON 파일 경로.

    Returns:
        최상위 JSON object.
    """

    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _load_yaml(path: Path) -> dict[str, Any]:
    """UTF-8 YAML mapping을 safe loader로 파싱한다.

    Args:
        path: 읽을 YAML 파일 경로.

    Returns:
        최상위 YAML mapping.
    """

    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _load_catalog_rows(path: Path) -> list[dict[str, str]]:
    """Silver column catalog를 행 dictionary 목록으로 읽는다.

    Args:
        path: 읽을 UTF-8 CSV 파일 경로.

    Returns:
        header 이름을 키로 사용하는 catalog 행 목록.
    """

    with path.open(encoding="utf-8", newline="") as catalog_file:
        return list(csv.DictReader(catalog_file))


def test_phase5_contract_fields_are_exact_and_ordered() -> None:
    """네 data 계약과 최종 wrapper의 필드 구성·순서를 고정한다."""

    for model_name, contract_type in MODEL_TYPES.items():
        assert _field_names(contract_type) == EXPECTED_MODEL_FIELDS[model_name]

    assert _field_names(JoinReferenceKey) == ("area_id", "employee_id")
    assert _field_names(ModelMetadata) == (
        "model_key",
        "model_fingerprint",
        "source_record_ids",
    )
    assert _field_names(ModelRecord) == ("data", "metadata")
    assert _field_names(ModelCounts) == (
        "employee",
        "area",
        "parent_area",
        "join_reference",
    )
    assert _field_names(Phase5Output) == (
        "context",
        "employees",
        "areas",
        "parent_areas",
        "join_references",
        "rejected",
        "source_metrics",
        "model_counts",
    )


def test_phase5_contract_types_are_frozen() -> None:
    """모든 Plan 2 공개 dataclass가 frozen 계약을 유지하는지 확인한다."""

    contract_types = (
        EmployeeData,
        AreaData,
        ParentAreaData,
        JoinReferenceData,
        JoinReferenceKey,
        ModelMetadata,
        ModelRecord,
        ModelCounts,
        Phase5Output,
    )
    for contract_type in contract_types:
        assert contract_type.__dataclass_params__.frozen is True


def test_modeling_rules_and_catalog_match_dataclasses() -> None:
    """modeling YAML과 column catalog가 코드 필드 순서를 그대로 고정한다."""

    rules = _load_yaml(STANDARDS_ROOT / "rules" / "modeling.yaml")
    standardization = _load_yaml(
        STANDARDS_ROOT / "rules" / "standardization.yaml"
    )
    catalog_rows = _load_catalog_rows(
        STANDARDS_ROOT / "catalogs" / "silver-model-columns.csv"
    )

    assert rules["schema_version"] == 1
    assert rules["contract"]["output_type"] == "Phase5Output"
    assert rules["fingerprint"]["payload_fields"] == [
        "model_name",
        "data",
        "contract_version",
    ]

    for model_name, expected_fields in EXPECTED_MODEL_FIELDS.items():
        assert tuple(rules["models"][model_name]["data_fields"]) == expected_fields

        declared_model_key = rules["models"][model_name]["model_key"]
        if isinstance(declared_model_key, str):
            model_key_fields = (declared_model_key,)
        else:
            model_key_fields = tuple(declared_model_key)
        assert model_key_fields == EXPECTED_MODEL_KEYS[model_name]
        assert tuple(
            standardization["conflict_resolution"]["key_groups"][model_name][
                "key_fields"
            ]
        ) == EXPECTED_MODEL_KEYS[model_name]

        catalog_fields = []
        catalog_orders = []
        for row in catalog_rows:
            if row["model_name"] != model_name:
                continue
            catalog_fields.append(row["column_name"])
            catalog_orders.append(int(row["column_order"]))
        assert tuple(catalog_fields) == expected_fields
        assert catalog_orders == list(range(1, len(expected_fields) + 1))

        catalog_nullable = []
        for row in catalog_rows:
            if row["model_name"] != model_name:
                continue
            if row["nullable"] == "true":
                catalog_nullable.append(row["column_name"])
        assert tuple(catalog_nullable) == tuple(
            rules["models"][model_name]["nullable_fields"]
        )

        catalog_key_fields = []
        for row in catalog_rows:
            if row["model_name"] != model_name:
                continue
            if row["key_role"] == "model_key":
                catalog_key_fields.append(row["column_name"])
        assert tuple(catalog_key_fields) == EXPECTED_MODEL_KEYS[model_name]


def test_phase5_nullable_fields_are_exactly_the_two_parent_fields() -> None:
    """계약상 nullable 필드는 parent area ID와 이름 두 개뿐인지 고정한다."""

    rules = _load_yaml(STANDARDS_ROOT / "rules" / "modeling.yaml")
    assert rules["models"]["employee"]["nullable_fields"] == []
    assert rules["models"]["area"]["nullable_fields"] == ["parent_area_id"]
    assert rules["models"]["parent_area"]["nullable_fields"] == []
    assert rules["models"]["join_reference"]["nullable_fields"] == [
        "parent_area_id",
        "parent_area_name",
    ]


def test_schema_and_example_validate_as_draft_2020_12() -> None:
    """Phase5 schema 자체와 example instance를 Draft 2020-12로 검증한다."""

    schema = _load_json(STANDARDS_ROOT / "schemas" / "phase5-output.schema.json")
    example = _load_json(STANDARDS_ROOT / "examples" / "phase5-output.json")

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert example["context"]["contract_version"] == "1"
    assert example["join_references"][0]["metadata"]["model_key"] == {
        "area_id": "BIZ_00001",
        "employee_id": "EMP000001",
    }
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(example)


def test_schema_rejects_non_nullable_and_noncanonical_values() -> None:
    """v1 schema가 필수값 누락과 canonical domain 위반을 거부한다."""

    schema = _load_json(STANDARDS_ROOT / "schemas" / "phase5-output.schema.json")
    example = _load_json(STANDARDS_ROOT / "examples" / "phase5-output.json")
    validator = Draft202012Validator(schema)

    invalid_examples = []
    missing_employee_name = deepcopy(example)
    missing_employee_name["employees"][0]["data"]["employee_name"] = None
    invalid_examples.append(missing_employee_name)

    missing_parent_level = deepcopy(example)
    missing_parent_level["parent_areas"][0]["data"]["top_area_level_code"] = None
    invalid_examples.append(missing_parent_level)

    invalid_area_id = deepcopy(example)
    invalid_area_id["areas"][0]["data"]["area_id"] = "biz_00001"
    invalid_examples.append(invalid_area_id)

    for invalid_example in invalid_examples:
        assert validator.is_valid(invalid_example) is False


def test_schema_rejects_invalid_model_keys_and_source_record_ids() -> None:
    """v1 schema가 빈·누락·비구조화 키와 빈 lineage 배열을 거부한다."""

    schema = _load_json(STANDARDS_ROOT / "schemas" / "phase5-output.schema.json")
    example = _load_json(STANDARDS_ROOT / "examples" / "phase5-output.json")
    validator = Draft202012Validator(schema)

    empty_model_key = deepcopy(example)
    empty_model_key["employees"][0]["metadata"]["model_key"] = ""

    empty_source_record_ids = deepcopy(example)
    empty_source_record_ids["employees"][0]["metadata"]["source_record_ids"] = []

    empty_join_area_id = deepcopy(example)
    empty_join_area_id["join_references"][0]["metadata"]["model_key"][
        "area_id"
    ] = ""

    empty_join_employee_id = deepcopy(example)
    empty_join_employee_id["join_references"][0]["metadata"]["model_key"][
        "employee_id"
    ] = ""

    missing_join_area_id = deepcopy(example)
    del missing_join_area_id["join_references"][0]["metadata"]["model_key"][
        "area_id"
    ]

    missing_join_employee_id = deepcopy(example)
    del missing_join_employee_id["join_references"][0]["metadata"]["model_key"][
        "employee_id"
    ]

    string_join_model_key = deepcopy(example)
    string_join_model_key["join_references"][0]["metadata"]["model_key"] = (
        "BIZ_00001|EMP000001"
    )

    assert validator.is_valid(empty_model_key) is False
    assert validator.is_valid(empty_source_record_ids) is False
    assert validator.is_valid(empty_join_area_id) is False
    assert validator.is_valid(empty_join_employee_id) is False
    assert validator.is_valid(missing_join_area_id) is False
    assert validator.is_valid(missing_join_employee_id) is False
    assert validator.is_valid(string_join_model_key) is False


def test_schema_rejects_non_string_join_key_components() -> None:
    """v1 schema가 두 조인 키 구성요소의 integer와 null을 모두 거부한다."""

    schema = _load_json(STANDARDS_ROOT / "schemas" / "phase5-output.schema.json")
    example = _load_json(STANDARDS_ROOT / "examples" / "phase5-output.json")
    validator = Draft202012Validator(schema)
    invalid_components = (
        ("area_id", 1),
        ("area_id", None),
        ("employee_id", 1),
        ("employee_id", None),
    )

    assert validator.is_valid(example) is True
    for field_name, invalid_value in invalid_components:
        malformed = deepcopy(example)
        malformed["join_references"][0]["metadata"]["model_key"][field_name] = (
            invalid_value
        )
        assert validator.is_valid(malformed) is False


def test_modeling_rules_assign_semantic_invariants_to_runtime_validator() -> None:
    """Draft 2020-12로 표현할 수 없는 관계 제약의 runtime 책임을 고정한다."""

    rules = _load_yaml(STANDARDS_ROOT / "rules" / "modeling.yaml")
    runtime_validation = rules["validation"]["runtime"]

    assert runtime_validation["authority"] == "Phase5ModelValidator"
    assert runtime_validation["error_type"] == "Phase5ModelValidationError"
    assert {
        invariant["invariant_id"]
        for invariant in runtime_validation["semantic_invariants"]
    } == {
        "metadata_model_key_equals_data_key",
        "metadata_source_record_ids_ascending",
        "metadata_source_record_ids_in_phase4_accepted_lineage",
        "model_counts_equal_collection_lengths",
    }


def test_conflict_policy_matches_batch_fail_closed_runtime_contract() -> None:
    """V1 conflict 선언이 modeling 계약과 실제 batch 예외 경로와 일치한다."""

    standardization = _load_yaml(
        STANDARDS_ROOT / "rules" / "standardization.yaml"
    )
    modeling = _load_yaml(STANDARDS_ROOT / "rules" / "modeling.yaml")
    conflict_policy = standardization["conflict_resolution"]
    different_data_policy = conflict_policy["same_key_different_projected_data"]
    assembly_policy = modeling["assembly"]

    assert different_data_policy == {
        "action": "fail_closed",
        "failure_scope": "batch",
        "exception": "Phase4ContractViolation",
    }
    assert assembly_policy["same_key_same_data"] == "merge"
    assert assembly_policy["same_key_different_data"] == "fail_closed"
    assert assembly_policy["conflict_exception"] == (
        Phase4ContractViolation.__name__
    )
    assert different_data_policy["exception"] == Phase4ContractViolation.__name__
    assert conflict_policy["same_key_same_projected_data"] == {
        "action": "deduplicate",
        "lineage_action": "aggregate_unique_source_record_ids_ascending",
    }
    assert conflict_policy["first_wins"] == "prohibited"
    assert conflict_policy["last_wins"] == "prohibited"
    assert conflict_policy["input_order_must_not_change_result"] is True
    assert "records_in_multiple_conflicts" not in conflict_policy

    employee = EmployeeData(
        employee_id="EMP000001",
        employee_name="첫 값",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
    )
    conflicting_employee = EmployeeData(
        employee_id="EMP000001",
        employee_name="다른 값",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
    )
    candidates = (
        ProjectionCandidate("EMP000001", employee, 1),
        ProjectionCandidate("EMP000001", conflicting_employee, 2),
    )

    with pytest.raises(Phase4ContractViolation):
        ensure_no_projection_conflicts(candidates, "employee")


def test_example_fingerprints_match_canonical_model_contract() -> None:
    """표준 example의 네 model fingerprint가 실제 canonical 계산과 일치한다."""

    example = _load_json(STANDARDS_ROOT / "examples" / "phase5-output.json")
    contract_version = example["context"]["contract_version"]
    collections = (
        ("employees", "employee", EmployeeData),
        ("areas", "area", AreaData),
        ("parent_areas", "parent_area", ParentAreaData),
        ("join_references", "join_reference", JoinReferenceData),
    )

    for collection_name, model_name, data_type in collections:
        for record in example[collection_name]:
            data = data_type(**record["data"])
            expected_fingerprint = compute_model_fingerprint(
                model_name,
                data,
                contract_version,
            )
            assert record["metadata"]["model_fingerprint"] == expected_fingerprint
