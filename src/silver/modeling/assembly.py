"""Phase 5 모델 후보를 결정적인 최종 Silver 출력으로 조립한다."""

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import TypeVar

from ..contracts.phase5 import (
    JoinReferenceKey,
    ModelCounts,
    ModelKey,
    ModelMetadata,
    ModelRecord,
    Phase5Output,
)
from .phase4_binding import Phase4ContractViolation
from .projections import (
    Phase5ProjectionResult,
    ProjectionCandidate,
    ensure_no_projection_conflicts,
)

EMPLOYEE_MODEL_NAME = "employee"
AREA_MODEL_NAME = "area"
PARENT_AREA_MODEL_NAME = "parent_area"
JOIN_REFERENCE_MODEL_NAME = "join_reference"

MODEL_KEY_FIELD_NAMES = {
    EMPLOYEE_MODEL_NAME: ("employee_id",),
    AREA_MODEL_NAME: ("area_id",),
    PARENT_AREA_MODEL_NAME: ("top_area_id",),
    JOIN_REFERENCE_MODEL_NAME: ("area_id", "employee_id"),
}

AssemblyDataT = TypeVar("AssemblyDataT")


def assemble_phase5_output(result: Phase5ProjectionResult) -> Phase5Output:
    """내부 projection 결과를 중복 제거한 최종 Phase 5 출력으로 조립한다.

    Args:
        result: Phase 5 검증과 projection을 마친 내부 전달 결과.

    Returns:
        모델 키로 정렬하고 lineage와 지문을 확정한 Phase5Output.

    Raises:
        Phase4ContractViolation: context contract version이나 후보 계약이
            잘못됐거나 같은 모델 키에 서로 다른 데이터가 있는 경우.
    """

    contract_version = _context_contract_version(result.context)
    employees = _assemble_model_records(
        result.employees,
        EMPLOYEE_MODEL_NAME,
        contract_version,
    )
    areas = _assemble_model_records(
        result.areas,
        AREA_MODEL_NAME,
        contract_version,
    )
    parent_areas = _assemble_model_records(
        result.parent_areas,
        PARENT_AREA_MODEL_NAME,
        contract_version,
    )
    join_references = _assemble_model_records(
        result.join_references,
        JOIN_REFERENCE_MODEL_NAME,
        contract_version,
    )

    model_counts = ModelCounts(
        employee=len(employees),
        area=len(areas),
        parent_area=len(parent_areas),
        join_reference=len(join_references),
    )
    return Phase5Output(
        context=result.context,
        employees=employees,
        areas=areas,
        parent_areas=parent_areas,
        join_references=join_references,
        rejected=result.rejected,
        source_metrics=result.source_metrics,
        model_counts=model_counts,
    )


def compute_model_fingerprint(
    model_name: str,
    data: object,
    contract_version: str,
) -> str:
    """모델 이름·데이터·계약 버전만으로 canonical SHA-256 지문을 계산한다.

    Args:
        model_name: 승인된 Silver 모델 이름.
        data: 해당 모델의 불변 dataclass 데이터.
        contract_version: 원래 BatchContext가 제공한 계약 버전 문자열.

    Returns:
        canonical UTF-8 JSON 바이트의 소문자 SHA-256 16진수 문자열.

    Raises:
        Phase4ContractViolation: data가 dataclass 인스턴스가 아닌 경우.
    """

    if not is_dataclass(data) or isinstance(data, type):
        raise Phase4ContractViolation("model fingerprint data must be a dataclass")

    fingerprint_payload = {
        "model_name": model_name,
        "data": asdict(data),
        "contract_version": contract_version,
    }
    canonical_json = json.dumps(
        fingerprint_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _context_contract_version(context: object) -> str:
    """BatchContext에서 비어 있지 않은 원래 계약 버전을 읽는다.

    Args:
        context: Phase 4에서 전달받은 원래 BatchContext 객체.

    Returns:
        변형하지 않은 context.contract_version 문자열.

    Raises:
        Phase4ContractViolation: 속성이 없거나 비어 있지 않은 문자열이 아닌
            경우.
    """

    try:
        contract_version = context.contract_version
    except AttributeError as error:
        raise Phase4ContractViolation(
            "Phase5ProjectionResult.context is missing contract_version"
        ) from error

    if not isinstance(contract_version, str) or not contract_version:
        raise Phase4ContractViolation(
            "Phase5ProjectionResult.context.contract_version must be a non-empty str"
        )
    return contract_version


def _assemble_model_records(
    candidates: tuple[ProjectionCandidate[AssemblyDataT], ...],
    model_name: str,
    contract_version: str,
) -> tuple[ModelRecord[AssemblyDataT], ...]:
    """한 모델 후보를 키별로 병합하고 키 오름차순의 레코드로 확정한다.

    Args:
        candidates: 중복 제거 전 동일 모델의 projection 후보들.
        model_name: 지문 payload와 오류 메시지에 사용할 승인 모델 이름.
        contract_version: 원래 BatchContext의 계약 버전.

    Returns:
        같은 데이터의 lineage를 병합한 model_key 오름차순 레코드 tuple.

    Raises:
        Phase4ContractViolation: 후보 타입이나 같은 키의 데이터가 계약을
            위반한 경우.
    """

    if type(candidates) is not tuple:
        raise Phase4ContractViolation(f"{model_name} candidates must be tuple")

    for candidate in candidates:
        _validate_projection_candidate(candidate, model_name)
    ensure_no_projection_conflicts(candidates, model_name)

    data_by_key: dict[ModelKey, AssemblyDataT] = {}
    source_ids_by_key: dict[ModelKey, set[int]] = {}
    for candidate in candidates:
        if candidate.model_key not in data_by_key:
            data_by_key[candidate.model_key] = candidate.data
            source_ids_by_key[candidate.model_key] = set()
        source_ids_by_key[candidate.model_key].add(candidate.source_record_id)

    records = []
    for model_key in sorted(data_by_key):
        data = data_by_key[model_key]
        source_record_ids = tuple(sorted(source_ids_by_key[model_key]))
        metadata = ModelMetadata(
            model_key=model_key,
            model_fingerprint=compute_model_fingerprint(
                model_name,
                data,
                contract_version,
            ),
            source_record_ids=source_record_ids,
        )
        records.append(ModelRecord(data=data, metadata=metadata))
    return tuple(records)


def _validate_projection_candidate(
    candidate: ProjectionCandidate[AssemblyDataT],
    model_name: str,
) -> None:
    """Phase 6 병합 전에 후보 키와 단일 lineage ID 타입을 확인한다.

    Args:
        candidate: 검증할 단일 projection 후보.
        model_name: 위반 메시지에 사용할 승인 모델 이름.

    Raises:
        Phase4ContractViolation: model_key가 모델별 canonical 타입·값이 아니거나
            source record ID가 정확한 int 타입이 아닌 경우.
    """

    if not isinstance(candidate, ProjectionCandidate):
        raise Phase4ContractViolation(
            f"{model_name} candidate must be a ProjectionCandidate"
        )
    _validate_model_key(candidate.model_key, model_name)
    if type(candidate.source_record_id) is not int:
        raise Phase4ContractViolation(f"{model_name} source_record_id must be int")

    if not is_dataclass(candidate.data) or isinstance(candidate.data, type):
        raise Phase4ContractViolation(
            f"{model_name} candidate data must be a dataclass instance"
        )

    key_field_names = MODEL_KEY_FIELD_NAMES.get(model_name)
    if key_field_names is None:
        raise Phase4ContractViolation(f"{model_name} has no canonical key fields")
    canonical_key = _canonical_model_key(
        candidate.data,
        model_name,
        key_field_names,
    )
    if candidate.model_key != canonical_key:
        if len(key_field_names) == 1:
            key_description = f"data.{key_field_names[0]}"
        else:
            field_description = ", ".join(key_field_names)
            key_description = f"data key ({field_description})"
        raise Phase4ContractViolation(
            f"{model_name} model_key must equal {key_description}"
        )


def _validate_model_key(model_key: ModelKey, model_name: str) -> None:
    """모델별 metadata key 타입과 비어 있지 않은 구성요소를 확인한다.

    Args:
        model_key: 검증할 문자열 키 또는 조인 참조 복합키.
        model_name: 키 계약을 선택할 Silver 모델 이름.

    Raises:
        Phase4ContractViolation: 모델별 키 타입이 다르거나 구성요소가 빈 경우.
    """

    if model_name == JOIN_REFERENCE_MODEL_NAME:
        if not isinstance(model_key, JoinReferenceKey):
            raise Phase4ContractViolation(
                "join_reference model_key must be a JoinReferenceKey"
            )
        for field_name in MODEL_KEY_FIELD_NAMES[JOIN_REFERENCE_MODEL_NAME]:
            component = getattr(model_key, field_name)
            if not isinstance(component, str) or not component:
                raise Phase4ContractViolation(
                    f"join_reference model_key.{field_name} must be a non-empty str"
                )
        return

    if not isinstance(model_key, str) or not model_key:
        raise Phase4ContractViolation(f"{model_name} model_key must be a non-empty str")


def _canonical_model_key(
    data: object,
    model_name: str,
    key_field_names: tuple[str, ...],
) -> ModelKey:
    """모델 data의 canonical 필드에서 비교용 키를 생성한다.

    Args:
        data: canonical 키 필드를 가진 모델 data dataclass.
        model_name: 단일 키와 복합키 생성을 구분할 모델 이름.
        key_field_names: 순서가 고정된 canonical key 필드 이름들.

    Returns:
        원래 문자열 키 또는 구조화된 조인 참조 복합키.

    Raises:
        Phase4ContractViolation: 필드가 없거나 비어 있지 않은 문자열이 아닌 경우.
    """

    key_values = []
    for field_name in key_field_names:
        try:
            field_value = getattr(data, field_name)
        except AttributeError as error:
            raise Phase4ContractViolation(
                f"{model_name} candidate data is missing {field_name!r}"
            ) from error
        if not isinstance(field_value, str) or not field_value:
            raise Phase4ContractViolation(
                f"{model_name} candidate data.{field_name} must be a non-empty str"
            )
        key_values.append(field_value)

    if model_name == JOIN_REFERENCE_MODEL_NAME:
        return JoinReferenceKey(
            area_id=key_values[0],
            employee_id=key_values[1],
        )
    return key_values[0]
