"""Phase 4 계약부터 Phase 7 교차 검증까지 Silver 모델을 처리한다."""

import re
from dataclasses import fields, is_dataclass
from datetime import datetime

from ..contracts.phase5 import JoinReferenceKey, Phase5Output
from .assembly import assemble_phase5_output
from .model_validator import Phase5ModelValidator
from .phase4_binding import (
    Phase4ContractViolation,
    Phase4IntegrationBinding,
    Phase4IntegrationUnavailable,
    unavailable_phase4_binding,
)
from .projections import (
    Phase5ProjectionResult,
    ProjectionCandidate,
    ensure_no_projection_conflicts,
    project_area,
    project_employee,
    project_join_reference,
    project_parent_area,
)

BUSINESS_FIELD_NAMES = (
    "area_id",
    "area_name",
    "parent_area_id",
    "parent_area_name",
    "top_area_id",
    "top_area_name",
    "top_area_level_code",
    "employee_id",
    "employee_name",
    "employee_department_name",
    "employee_position_name",
    "employee_hire_datetime",
    "employee_status_code",
    "area_registration_date",
    "top_area_registration_date",
)

REQUIRED_STRING_FIELDS = (
    "area_id",
    "area_name",
    "top_area_level_code",
    "top_area_id",
    "top_area_name",
    "employee_id",
    "employee_name",
    "employee_department_name",
    "employee_position_name",
    "employee_hire_datetime",
    "employee_status_code",
    "area_registration_date",
    "top_area_registration_date",
)

NULLABLE_STRING_FIELDS = (
    "parent_area_id",
    "parent_area_name",
)

DATETIME_FIELDS = (
    "employee_hire_datetime",
    "area_registration_date",
    "top_area_registration_date",
)

IDENTIFIER_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,19}")
NAME_PATTERN = re.compile(r".{1,100}")
LEVEL_CODE_PATTERN = re.compile(r"[A-Z0-9_]{1,20}")
STATUS_CODE_PATTERN = re.compile(r"[A-Z0-9_]{1,20}")
CANONICAL_DATETIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class Phase5Processor:
    """Phase 4를 방어 검증하고 최종 Phase 5 Silver 출력을 생성한다."""

    def __init__(
        self,
        binding: Phase4IntegrationBinding | None = None,
    ) -> None:
        """Phase 4 공유 기능을 사용할 binding을 고정한다.

        Args:
            binding: 실제 Plan 1 contract lock·output·fingerprint 결합. 생략하면
                검증을 건너뛰지 않는 fail-closed binding을 사용한다.
        """

        if binding is None:
            binding = unavailable_phase4_binding()
        self._binding = binding

    def process(self, phase4_output: object) -> Phase5Output:
        """Phase 4 출력을 한 번 검증·투영하고 최종 Phase 5 출력을 조립한다.

        Args:
            phase4_output: 실제 Plan 1 ``Phase4Output`` 객체.

        Returns:
            context·reject·source metrics 원본과 결정적 모델 레코드를 담은 출력.

        Raises:
            Phase4IntegrationUnavailable: 공유 Plan 1 결합이 제공되지 않은 경우.
            Phase4ContractViolation: Phase 4 입력 계약이나 projection 일관성이
                깨진 경우.
            Phase5ModelValidationError: 조립된 네 모델의 Phase 7 교차 검증이
                실패한 경우.
        """

        projection_result = self._validate_and_project(phase4_output)
        phase5_output = assemble_phase5_output(projection_result)
        Phase5ModelValidator.validate(phase4_output, phase5_output)
        return phase5_output

    def _validate_and_project(
        self,
        phase4_output: object,
    ) -> Phase5ProjectionResult:
        """기존 Phase 5 방어 검증을 수행하고 내부 모델 후보를 한 번 생성한다.

        Args:
            phase4_output: 실제 Plan 1 ``Phase4Output`` 객체.

        Returns:
            입력 순서와 원래 context·reject·metrics를 보존한 projection 결과.

        Raises:
            Phase4IntegrationUnavailable: 공유 Plan 1 결합이 제공되지 않은 경우.
            Phase4ContractViolation: Phase 4 입력 계약이나 projection 일관성이
                깨진 경우.
        """

        self._binding.verify_lock(phase4_output)
        self._binding.verify_output(phase4_output)

        context = _required_attribute(phase4_output, "context", "Phase4Output")
        accepted = _required_tuple(phase4_output, "accepted", "Phase4Output")
        rejected = _required_tuple(phase4_output, "rejected", "Phase4Output")
        source_metrics = _required_attribute(
            phase4_output,
            "metrics",
            "Phase4Output",
        )

        accepted_record_ids = _accepted_record_ids(accepted)
        _validate_record_id_intersection(
            accepted_record_ids,
            rejected,
        )

        employee_candidates = []
        area_candidates = []
        parent_area_candidates = []
        join_reference_candidates = []

        for record_index, record in enumerate(accepted):
            location = f"accepted[{record_index}]"
            business = _required_attribute(record, "business", location)
            _validate_standardized_business(business, location)
            self._validate_record_fingerprint(record, location)

            source_record_id = accepted_record_ids[record_index]
            employee_data = project_employee(business)
            area_data = project_area(business)
            parent_area_data = project_parent_area(business)
            join_reference_data = project_join_reference(business)

            employee_candidates.append(
                ProjectionCandidate(
                    model_key=employee_data.employee_id,
                    data=employee_data,
                    source_record_id=source_record_id,
                )
            )
            area_candidates.append(
                ProjectionCandidate(
                    model_key=area_data.area_id,
                    data=area_data,
                    source_record_id=source_record_id,
                )
            )
            parent_area_candidates.append(
                ProjectionCandidate(
                    model_key=parent_area_data.top_area_id,
                    data=parent_area_data,
                    source_record_id=source_record_id,
                )
            )
            join_reference_candidates.append(
                ProjectionCandidate(
                    model_key=JoinReferenceKey(
                        area_id=join_reference_data.area_id,
                        employee_id=join_reference_data.employee_id,
                    ),
                    data=join_reference_data,
                    source_record_id=source_record_id,
                )
            )

        employees = tuple(employee_candidates)
        areas = tuple(area_candidates)
        parent_areas = tuple(parent_area_candidates)
        join_references = tuple(join_reference_candidates)

        ensure_no_projection_conflicts(employees, "employee")
        ensure_no_projection_conflicts(areas, "area")
        ensure_no_projection_conflicts(parent_areas, "parent_area")
        ensure_no_projection_conflicts(join_references, "join_reference")

        return Phase5ProjectionResult(
            context=context,
            employees=employees,
            areas=areas,
            parent_areas=parent_areas,
            join_references=join_references,
            rejected=rejected,
            source_metrics=source_metrics,
        )

    def _validate_record_fingerprint(
        self,
        record: object,
        location: str,
    ) -> None:
        """공유 알고리즘으로 record fingerprint를 다시 계산해 비교한다.

        Args:
            record: 실제 StandardizedRecord 객체.
            location: 위반 위치를 식별할 accepted 인덱스 문자열.

        Raises:
            Phase4IntegrationUnavailable: 공유 callback 결과가 문자열이 아닌 경우.
            Phase4ContractViolation: 저장된 fingerprint가 없거나 재계산 값과
                다른 경우.
        """

        stored_fingerprint = _required_attribute(
            record,
            "record_fingerprint",
            location,
        )
        if not isinstance(stored_fingerprint, str):
            raise Phase4ContractViolation(f"{location}.record_fingerprint must be str")

        recomputed_fingerprint = self._binding.recompute_record_fingerprint(record)
        if not isinstance(recomputed_fingerprint, str):
            raise Phase4IntegrationUnavailable(
                "Phase 4 fingerprint callback must return str"
            )
        if recomputed_fingerprint != stored_fingerprint:
            raise Phase4ContractViolation(
                f"{location}.record_fingerprint does not match the "
                "shared Phase 4 recomputation"
            )


def _required_attribute(
    value: object,
    attribute_name: str,
    location: str,
) -> object:
    """계약 객체에서 필수 속성을 읽고 누락을 계약 위반으로 바꾼다.

    Args:
        value: 속성을 읽을 계약 객체.
        attribute_name: 읽을 필수 속성 이름.
        location: 위반 위치를 설명할 문자열.

    Returns:
        입력 객체가 가진 속성 값을 변형 없이 반환한다.

    Raises:
        Phase4ContractViolation: 필수 속성이 없는 경우.
    """

    try:
        return getattr(value, attribute_name)
    except AttributeError as error:
        raise Phase4ContractViolation(
            f"{location} is missing required attribute {attribute_name!r}"
        ) from error


def _required_tuple(
    value: object,
    attribute_name: str,
    location: str,
) -> tuple[object, ...]:
    """필수 collection 속성이 tuple 계약을 지키는지 확인한다.

    Args:
        value: collection 속성을 가진 계약 객체.
        attribute_name: tuple이어야 하는 속성 이름.
        location: 위반 위치를 설명할 문자열.

    Returns:
        원래 입력 tuple 객체.

    Raises:
        Phase4ContractViolation: 속성이 없거나 tuple이 아닌 경우.
    """

    collection = _required_attribute(value, attribute_name, location)
    if not isinstance(collection, tuple):
        raise Phase4ContractViolation(f"{location}.{attribute_name} must be tuple")
    return collection


def _accepted_record_ids(accepted: tuple[object, ...]) -> tuple[int, ...]:
    """accepted lineage에서 필수 record ID를 입력 순서대로 읽는다.

    Args:
        accepted: 실제 StandardizedRecord tuple.

    Returns:
        accepted 순서를 보존한 record ID tuple.

    Raises:
        Phase4ContractViolation: lineage 또는 정수 record ID 계약이 깨진 경우.
    """

    record_ids = []
    for record_index, record in enumerate(accepted):
        location = f"accepted[{record_index}]"
        lineage = _required_attribute(record, "lineage", location)
        record_id = _required_attribute(lineage, "record_id", f"{location}.lineage")
        if type(record_id) is not int:
            raise Phase4ContractViolation(f"{location}.lineage.record_id must be int")
        record_ids.append(record_id)
    return tuple(record_ids)


def _validate_record_id_intersection(
    accepted_record_ids: tuple[int, ...],
    rejected: tuple[object, ...],
) -> None:
    """accepted와 rejected의 관찰 가능한 record ID가 겹치지 않는지 확인한다.

    ``RejectedRecord.observed_lineage.record_id``의 ``None``은 malformed 원천을
    보존하는 정상 표현이므로 집합에 넣거나 다른 값으로 추정하지 않는다.

    Args:
        accepted_record_ids: accepted lineage의 필수 record ID들.
        rejected: 실제 RejectedRecord tuple.

    Raises:
        Phase4ContractViolation: rejected lineage 계약이 깨지거나 ID가 겹치는
            경우.
    """

    rejected_record_ids: set[int] = set()
    for record_index, record in enumerate(rejected):
        location = f"rejected[{record_index}]"
        observed_lineage = _required_attribute(
            record,
            "observed_lineage",
            location,
        )
        record_id = _required_attribute(
            observed_lineage,
            "record_id",
            f"{location}.observed_lineage",
        )
        if record_id is None:
            continue
        if type(record_id) is not int:
            raise Phase4ContractViolation(
                f"{location}.observed_lineage.record_id must be int or None"
            )
        rejected_record_ids.add(record_id)

    overlapping_record_ids = set(accepted_record_ids).intersection(rejected_record_ids)
    if overlapping_record_ids:
        overlap_detail = sorted(overlapping_record_ids)
        raise Phase4ContractViolation(
            f"accepted/rejected record_id intersection is not empty: {overlap_detail}"
        )


def _validate_standardized_business(
    business: object,
    location: str,
) -> None:
    """표준 business의 15개 필드·타입·명시 domain을 방어 검증한다.

    값은 수정·정규화·Reject 전환하지 않는다. 허용되지 않은 모양이나 값은
    upstream 계약 위반으로 배치 전체를 중단한다.

    Args:
        business: 실제 StandardizedBusinessRecord 객체.
        location: 해당 business를 가진 accepted 위치.

    Raises:
        Phase4ContractViolation: 필드 순서·구성·타입·명시 domain이 다른 경우.
    """

    actual_field_names = _dataclass_field_names(business, location)
    if actual_field_names != BUSINESS_FIELD_NAMES:
        raise Phase4ContractViolation(
            f"{location}.business fields differ from the locked 15-field "
            f"contract: {actual_field_names!r}"
        )

    for field_name in REQUIRED_STRING_FIELDS:
        field_value = _required_attribute(
            business,
            field_name,
            f"{location}.business",
        )
        if not isinstance(field_value, str):
            raise Phase4ContractViolation(
                f"{location}.business.{field_name} must be str"
            )

    for field_name in NULLABLE_STRING_FIELDS:
        field_value = _required_attribute(
            business,
            field_name,
            f"{location}.business",
        )
        if field_value is not None and not isinstance(field_value, str):
            raise Phase4ContractViolation(
                f"{location}.business.{field_name} must be str or None"
            )

    _validate_pattern_field(
        business,
        "area_id",
        IDENTIFIER_PATTERN,
        location,
    )
    _validate_optional_pattern_field(
        business,
        "parent_area_id",
        IDENTIFIER_PATTERN,
        location,
    )
    _validate_pattern_field(
        business,
        "top_area_id",
        IDENTIFIER_PATTERN,
        location,
    )
    _validate_pattern_field(
        business,
        "employee_id",
        IDENTIFIER_PATTERN,
        location,
    )
    for field_name in (
        "area_name",
        "top_area_name",
        "employee_name",
        "employee_department_name",
        "employee_position_name",
    ):
        _validate_pattern_field(
            business,
            field_name,
            NAME_PATTERN,
            location,
        )
    _validate_optional_pattern_field(
        business,
        "parent_area_name",
        NAME_PATTERN,
        location,
    )
    _validate_pattern_field(
        business,
        "top_area_level_code",
        LEVEL_CODE_PATTERN,
        location,
    )
    _validate_employee_status(business, location)

    for field_name in DATETIME_FIELDS:
        _validate_canonical_datetime(business, field_name, location)


def _dataclass_field_names(
    value: object,
    location: str,
) -> tuple[str, ...]:
    """dataclass 계약의 선언 필드 이름을 순서대로 반환한다.

    Args:
        value: 실제 Plan 1 business 계약 객체.
        location: 위반 위치를 설명할 accepted 위치.

    Returns:
        dataclass 선언 순서의 필드 이름 tuple.

    Raises:
        Phase4ContractViolation: business가 dataclass가 아닌 경우.
    """

    if not is_dataclass(value) or isinstance(value, type):
        raise Phase4ContractViolation(
            f"{location}.business must be a dataclass instance"
        )

    field_names = []
    for field_definition in fields(value):
        field_names.append(field_definition.name)
    return tuple(field_names)


def _validate_pattern_field(
    business: object,
    field_name: str,
    pattern: re.Pattern[str],
    location: str,
) -> None:
    """필수 문자열 필드가 승인된 canonical 패턴과 정확히 맞는지 확인한다.

    Args:
        business: 검증할 StandardizedBusinessRecord.
        field_name: 패턴을 적용할 필드 이름.
        pattern: 전체 문자열에 적용할 canonical 정규식.
        location: 해당 business를 가진 accepted 위치.

    Raises:
        Phase4ContractViolation: 값이 문자열이 아니거나 패턴과 다른 경우.
    """

    field_value = _required_attribute(
        business,
        field_name,
        f"{location}.business",
    )
    if not isinstance(field_value, str) or pattern.fullmatch(field_value) is None:
        raise Phase4ContractViolation(
            f"{location}.business.{field_name} violates its canonical domain"
        )


def _validate_optional_pattern_field(
    business: object,
    field_name: str,
    pattern: re.Pattern[str],
    location: str,
) -> None:
    """nullable 문자열이 None이 아니면 canonical 패턴과 맞는지 확인한다.

    Args:
        business: 검증할 StandardizedBusinessRecord.
        field_name: nullable canonical 필드 이름.
        pattern: 전체 문자열에 적용할 canonical 정규식.
        location: 해당 business를 가진 accepted 위치.

    Raises:
        Phase4ContractViolation: non-None 값이 문자열이 아니거나 패턴과 다른
            경우.
    """

    field_value = _required_attribute(
        business,
        field_name,
        f"{location}.business",
    )
    if field_value is None:
        return
    if not isinstance(field_value, str) or pattern.fullmatch(field_value) is None:
        raise Phase4ContractViolation(
            f"{location}.business.{field_name} violates its canonical domain"
        )


def _validate_employee_status(business: object, location: str) -> None:
    """필수 직원 상태가 표준 상태 코드 형식인지 확인한다.

    Args:
        business: 검증할 StandardizedBusinessRecord.
        location: 해당 business를 가진 accepted 위치.

    Raises:
        Phase4ContractViolation: 상태가 없거나 표준 형식과 다른 경우.
    """

    status_code = _required_attribute(
        business,
        "employee_status_code",
        f"{location}.business",
    )
    if (
        not isinstance(status_code, str)
        or STATUS_CODE_PATTERN.fullmatch(status_code) is None
    ):
        raise Phase4ContractViolation(
            f"{location}.business.employee_status_code violates its canonical domain"
        )


def _validate_canonical_datetime(
    business: object,
    field_name: str,
    location: str,
) -> None:
    """필수 일시가 naive ISO seconds 문자열인지 확인한다.

    Args:
        business: 검증할 StandardizedBusinessRecord.
        field_name: 검증할 canonical datetime 필드 이름.
        location: 해당 business를 가진 accepted 위치.

    Raises:
        Phase4ContractViolation: 값이 없거나 형식·달력 값이 잘못된 경우.
    """

    field_value = _required_attribute(
        business,
        field_name,
        f"{location}.business",
    )
    if (
        not isinstance(field_value, str)
        or CANONICAL_DATETIME_PATTERN.fullmatch(field_value) is None
    ):
        raise Phase4ContractViolation(
            f"{location}.business.{field_name} must use YYYY-MM-DDTHH:MM:SS"
        )

    try:
        # 반환값은 사용하지 않고 naive 문자열의 달력·시각 범위만 확인한다.
        datetime.strptime(field_value, "%Y-%m-%dT%H:%M:%S")  # noqa: DTZ007
    except ValueError as error:
        raise Phase4ContractViolation(
            f"{location}.business.{field_name} is not a valid calendar datetime"
        ) from error
