"""최종 Phase 5 모델 사이의 읽기 전용 일관성을 검증한다."""

import re
from dataclasses import fields, is_dataclass

from ..contracts.phase5 import JoinReferenceKey, ModelKey, Phase5Output
from .assembly import compute_model_fingerprint

EMPLOYEE_FIELD_NAMES = (
    "employee_id",
    "employee_name",
    "employee_department_name",
    "employee_position_name",
    "employee_hire_datetime",
    "employee_status_code",
)

AREA_FIELD_NAMES = (
    "area_id",
    "area_name",
    "parent_area_id",
    "employee_id",
    "area_registration_date",
)

PARENT_AREA_FIELD_NAMES = (
    "top_area_id",
    "top_area_name",
    "top_area_level_code",
    "top_area_registration_date",
)

JOIN_REFERENCE_FIELD_NAMES = (
    "area_id",
    "parent_area_id",
    "parent_area_name",
    "employee_id",
    "employee_name",
    "employee_department_name",
    "employee_position_name",
    "employee_hire_datetime",
    "employee_status_code",
)

MODEL_SPECS = (
    ("employee", "employees", EMPLOYEE_FIELD_NAMES, ("employee_id",)),
    ("area", "areas", AREA_FIELD_NAMES, ("area_id",)),
    (
        "parent_area",
        "parent_areas",
        PARENT_AREA_FIELD_NAMES,
        ("top_area_id",),
    ),
    (
        "join_reference",
        "join_references",
        JOIN_REFERENCE_FIELD_NAMES,
        ("area_id", "employee_id"),
    ),
)

EMPLOYEE_SHARED_FIELD_NAMES = EMPLOYEE_FIELD_NAMES
AREA_SHARED_FIELD_NAMES = (
    "area_id",
    "parent_area_id",
    "employee_id",
)

FORBIDDEN_DATA_FIELD_TOKENS = frozenset(("raw", "lineage", "source"))
CAMEL_CASE_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
NON_NAME_CHARACTER_PATTERN = re.compile(r"[^a-z0-9]+")
ATOMIC_DATA_TYPES = (str, bytes, int, float, complex, bool, type(None))
PLAIN_CONTAINER_TYPES = (tuple, list, set, frozenset)


class Phase5ModelValidationError(ValueError):
    """Phase 7 최종 모델 교차 검증 실패를 나타낸다."""


class Phase5ModelValidator:
    """Phase 4 원본과 최종 Phase 5 모델을 변경 없이 교차 검증한다."""

    @staticmethod
    def validate(
        phase4_output: object,
        phase5_output: Phase5Output,
    ) -> None:
        """최종 네 모델의 구조·lineage·공유 값을 fail closed로 확인한다.

        Args:
            phase4_output: 원래 accepted lineage와 business 값을 가진 Phase 4 출력.
            phase5_output: Phase 6 조립이 끝난 최종 Phase 5 출력.

        Raises:
            Phase5ModelValidationError: Phase 7 구조·lineage·모델 간 일관성 중
                하나라도 깨졌거나 검증 대상 구조를 안전하게 읽을 수 없는 경우.
        """

        try:
            _validate_phase5_models(phase4_output, phase5_output)
        except Phase5ModelValidationError:
            raise
        except Exception as error:
            raise Phase5ModelValidationError(
                "Phase 7 validation failed closed while reading model structures"
            ) from error


def _validate_phase5_models(
    phase4_output: object,
    phase5_output: Phase5Output,
) -> None:
    """Phase 7의 순수 검증 단계를 정해진 순서로 실행한다.

    Args:
        phase4_output: 원래 Phase 4 출력.
        phase5_output: 최종 Phase 5 출력.

    Raises:
        Phase5ModelValidationError: 최종 모델 불변식이 하나라도 깨진 경우.
    """

    accepted_business_by_record_id = _accepted_business_by_record_id(phase4_output)
    contract_version = _contract_version(phase5_output)
    records_by_model: dict[str, dict[ModelKey, object]] = {}

    for model_name, collection_name, expected_fields, key_field_names in MODEL_SPECS:
        collection = _required_tuple(
            phase5_output,
            collection_name,
            "Phase5Output",
        )
        records_by_model[model_name] = _validate_model_collection(
            model_name=model_name,
            collection=collection,
            expected_fields=expected_fields,
            key_field_names=key_field_names,
            accepted_record_ids=frozenset(accepted_business_by_record_id),
            contract_version=contract_version,
        )

    _validate_model_counts(phase5_output, records_by_model)
    _validate_join_employee_portion(
        records_by_model["join_reference"],
        records_by_model["employee"],
    )
    _validate_join_area_portion(
        records_by_model["join_reference"],
        records_by_model["area"],
    )
    _validate_parent_area_sources(
        records_by_model["parent_area"],
        accepted_business_by_record_id,
    )


def _accepted_business_by_record_id(
    phase4_output: object,
) -> dict[int, object]:
    """원래 accepted lineage ID별 business를 중복 없이 읽는다.

    Args:
        phase4_output: accepted tuple을 가진 원래 Phase 4 출력.

    Returns:
        정확한 int record ID를 키로 하는 원래 business 객체 mapping.

    Raises:
        Phase5ModelValidationError: accepted·lineage·record ID·business 구조가
            없거나 모호한 경우.
    """

    accepted = _required_tuple(phase4_output, "accepted", "Phase4Output")
    business_by_record_id: dict[int, object] = {}

    for record_index, record in enumerate(accepted):
        location = f"Phase4Output.accepted[{record_index}]"
        lineage = _required_attribute(record, "lineage", location)
        record_id = _required_attribute(lineage, "record_id", f"{location}.lineage")
        if type(record_id) is not int:
            raise Phase5ModelValidationError(
                f"{location}.lineage.record_id must be an exact int"
            )
        if record_id in business_by_record_id:
            raise Phase5ModelValidationError(
                f"Phase4Output.accepted has duplicate lineage record_id={record_id}"
            )

        business_by_record_id[record_id] = _required_attribute(
            record,
            "business",
            location,
        )

    return business_by_record_id


def _contract_version(phase5_output: Phase5Output) -> str:
    """모델 지문 재계산에 사용할 계약 버전을 안전하게 읽는다.

    Args:
        phase5_output: context를 가진 최종 Phase 5 출력.

    Returns:
        비어 있지 않은 context.contract_version 문자열.

    Raises:
        Phase5ModelValidationError: context 또는 계약 버전이 잘못된 경우.
    """

    context = _required_attribute(phase5_output, "context", "Phase5Output")
    contract_version = _required_attribute(
        context,
        "contract_version",
        "Phase5Output.context",
    )
    if not isinstance(contract_version, str) or not contract_version:
        raise Phase5ModelValidationError(
            "Phase5Output.context.contract_version must be a non-empty str"
        )
    return contract_version


def _validate_model_collection(
    *,
    model_name: str,
    collection: tuple[object, ...],
    expected_fields: tuple[str, ...],
    key_field_names: tuple[str, ...],
    accepted_record_ids: frozenset[int],
    contract_version: str,
) -> dict[ModelKey, object]:
    """단일 모델 collection의 키·구조·lineage·지문을 검증한다.

    Args:
        model_name: 공개 Silver 모델 이름.
        collection: 검증할 최종 ModelRecord tuple.
        expected_fields: 고정 catalog의 정확한 data 필드 순서.
        key_field_names: metadata.model_key를 구성하는 순서가 고정된 data 필드들.
        accepted_record_ids: 원래 accepted에 존재하는 lineage ID 집합.
        contract_version: 모델 지문에 사용한 원래 계약 버전.

    Returns:
        검증된 model_key별 원래 ModelRecord mapping.

    Raises:
        Phase5ModelValidationError: 단일 모델 불변식이 깨진 경우.
    """

    records_by_key: dict[ModelKey, object] = {}
    observed_keys = []

    for record_index, record in enumerate(collection):
        location = f"{model_name}[{record_index}]"
        data = _required_attribute(record, "data", location)
        metadata = _required_attribute(record, "metadata", location)
        model_key = _validated_model_key(
            model_name,
            _required_attribute(metadata, "model_key", f"{location}.metadata"),
            location,
        )
        if model_key in records_by_key:
            raise Phase5ModelValidationError(
                f"{model_name} metadata.model_key must be unique: {model_key!r}"
            )

        actual_fields = _dataclass_field_names(data, f"{location}.data")
        if actual_fields != expected_fields:
            raise Phase5ModelValidationError(
                f"{location}.data fields must exactly match the fixed catalog: "
                f"expected={expected_fields!r}, actual={actual_fields!r}"
            )
        _validate_no_forbidden_data_fields(data, f"{location}.data")

        canonical_key = _canonical_model_key(
            model_name,
            data,
            key_field_names,
            location,
        )
        if model_key != canonical_key:
            if len(key_field_names) == 1:
                key_description = f"data.{key_field_names[0]}"
            else:
                field_description = ", ".join(key_field_names)
                key_description = f"data key ({field_description})"
            raise Phase5ModelValidationError(
                f"{location}.metadata.model_key must equal {key_description}"
            )

        _validate_source_record_ids(
            metadata,
            location,
            accepted_record_ids,
        )
        _validate_model_fingerprint(
            model_name,
            data,
            metadata,
            contract_version,
            location,
        )

        records_by_key[model_key] = record
        observed_keys.append(model_key)

    if observed_keys != sorted(observed_keys):
        raise Phase5ModelValidationError(
            f"{model_name} records must be ordered by metadata.model_key"
        )
    return records_by_key


def _validated_model_key(
    model_name: str,
    model_key: object,
    location: str,
) -> ModelKey:
    """모델별 metadata key 타입과 필수 구성요소를 검증한다.

    Args:
        model_name: 공개 Silver 모델 이름.
        model_key: metadata에서 읽은 검증 전 키 값.
        location: 해당 ModelRecord 위치.

    Returns:
        타입과 비어 있지 않은 값을 확인한 문자열 또는 조인 복합키.

    Raises:
        Phase5ModelValidationError: 모델별 키 타입이 다르거나 값이 빈 경우.
    """

    if model_name == "join_reference":
        if not isinstance(model_key, JoinReferenceKey):
            raise Phase5ModelValidationError(
                f"{location}.metadata.model_key must be a JoinReferenceKey"
            )
        for field_name in ("area_id", "employee_id"):
            component = getattr(model_key, field_name)
            if not isinstance(component, str) or not component:
                raise Phase5ModelValidationError(
                    f"{location}.metadata.model_key.{field_name} "
                    "must be a non-empty str"
                )
        return model_key

    if not isinstance(model_key, str) or not model_key:
        raise Phase5ModelValidationError(
            f"{location}.metadata.model_key must be a non-empty str"
        )
    return model_key


def _canonical_model_key(
    model_name: str,
    data: object,
    key_field_names: tuple[str, ...],
    location: str,
) -> ModelKey:
    """모델 data의 canonical key 필드에서 비교용 키를 생성한다.

    Args:
        model_name: 단일 키와 조인 복합키를 구분할 모델 이름.
        data: canonical key 필드를 가진 모델 data 객체.
        key_field_names: 키 구성요소의 고정 순서를 나타내는 필드 이름들.
        location: 해당 ModelRecord 위치.

    Returns:
        검증된 문자열 키 또는 구조화된 조인 참조 복합키.

    Raises:
        Phase5ModelValidationError: key 필드가 없거나 비어 있지 않은 문자열이
            아닌 경우.
    """

    key_values = []
    for field_name in key_field_names:
        field_value = _required_attribute(data, field_name, f"{location}.data")
        if not isinstance(field_value, str) or not field_value:
            raise Phase5ModelValidationError(
                f"{location}.data.{field_name} must be a non-empty str"
            )
        key_values.append(field_value)

    if model_name == "join_reference":
        return JoinReferenceKey(
            area_id=key_values[0],
            employee_id=key_values[1],
        )
    return key_values[0]


def _dataclass_field_names(value: object, location: str) -> tuple[str, ...]:
    """dataclass 인스턴스의 선언 필드 이름을 순서대로 읽는다.

    Args:
        value: 필드 구조를 확인할 모델 data 객체.
        location: 오류 위치를 나타내는 문자열.

    Returns:
        선언 순서의 필드 이름 tuple.

    Raises:
        Phase5ModelValidationError: 모델 data가 dataclass 인스턴스가 아닌 경우.
    """

    if not is_dataclass(value) or isinstance(value, type):
        raise Phase5ModelValidationError(f"{location} must be a dataclass instance")
    return tuple(field_definition.name for field_definition in fields(value))


def _validate_no_forbidden_data_fields(value: object, location: str) -> None:
    """normal data 내부를 재귀 순회해 raw·lineage·source 필드를 거부한다.

    metadata는 호출 대상에 포함하지 않는다. dataclass·namedtuple 필드와 built-in
    dict의 문자열 키를 필드 이름으로 취급한다. 속성을 숨길 수 있는 임의 객체나
    container subclass는 반사적으로 탐색하지 않고 지원하지 않는 구조로 거부한다.

    Args:
        value: 정상 모델의 record.data 객체.
        location: 오류 위치를 나타내는 문자열.

    Raises:
        Phase5ModelValidationError: 금지된 필드 이름이 data 내부에 있는 경우.
    """

    pending = [(value, location)]
    visited_container_ids: set[int] = set()

    while pending:
        current, current_location = pending.pop()
        if type(current) in ATOMIC_DATA_TYPES:
            continue

        current_id = id(current)
        if current_id in visited_container_ids:
            continue
        visited_container_ids.add(current_id)

        if is_dataclass(current) and not isinstance(current, type):
            dataclass_fields = fields(current)
            declared_field_names = tuple(
                field_definition.name for field_definition in dataclass_fields
            )
            for field_definition in dataclass_fields:
                field_name = field_definition.name
                _reject_forbidden_field_name(field_name, current_location)
                nested = _required_attribute(current, field_name, current_location)
                pending.append((nested, f"{current_location}.{field_name}"))
            _append_extra_named_attributes(
                current,
                declared_field_names,
                current_location,
                pending,
            )
            continue

        namedtuple_fields = _namedtuple_field_names(current, current_location)
        if namedtuple_fields is not None:
            for field_index, field_name in enumerate(namedtuple_fields):
                _reject_forbidden_field_name(field_name, current_location)
                nested = current[field_index]
                pending.append((nested, f"{current_location}.{field_name}"))
            _append_extra_named_attributes(
                current,
                namedtuple_fields,
                current_location,
                pending,
            )
            continue

        if type(current) is dict:
            for field_name, nested in current.items():
                if type(field_name) is not str:
                    raise Phase5ModelValidationError(
                        f"{current_location} has an unsupported non-string "
                        "normal-data mapping key"
                    )
                _reject_forbidden_field_name(field_name, current_location)
                nested_location = f"{current_location}[{field_name!r}]"
                pending.append((nested, nested_location))
            continue

        if type(current) in PLAIN_CONTAINER_TYPES:
            for item_index, nested in enumerate(current):
                pending.append((nested, f"{current_location}[{item_index}]"))
            continue

        raise Phase5ModelValidationError(
            f"{current_location} contains unsupported nested normal-data object "
            f"type {type(current).__name__!r}"
        )


def _append_extra_named_attributes(
    value: object,
    known_field_names: tuple[str, ...],
    location: str,
    pending: list[tuple[object, str]],
) -> None:
    """지원 객체의 ``__dict__``와 선언 slot에 숨은 추가 속성을 순회에 넣는다.

    ``dir``이나 임의 property를 호출하지 않고 실제 instance dictionary와 class가
    선언한 slot 이름만 확인한다. dataclass·namedtuple의 이미 처리한 계약 필드는
    중복으로 넣지 않는다.

    Args:
        value: 추가 named attribute를 확인할 dataclass 또는 namedtuple 인스턴스.
        known_field_names: 이미 순회에 넣은 선언 계약 필드 이름.
        location: 오류 위치를 나타내는 문자열.
        pending: 추가 속성 값을 이어서 검사할 순회 stack.

    Raises:
        Phase5ModelValidationError: attribute 이름·저장 구조가 안전하게 읽을 수
            없는 모양이거나 금지 필드 이름이 발견된 경우.
    """

    known_names = set(known_field_names)
    appended_names: set[str] = set()
    instance_attributes = getattr(value, "__dict__", None)
    if instance_attributes is not None:
        if type(instance_attributes) is not dict:
            raise Phase5ModelValidationError(
                f"{location} has an unsupported instance attribute mapping"
            )
        for attribute_name, nested in instance_attributes.items():
            if type(attribute_name) is not str:
                raise Phase5ModelValidationError(
                    f"{location} has a non-string instance attribute name"
                )
            if attribute_name in known_names:
                continue
            _reject_forbidden_field_name(attribute_name, location)
            pending.append((nested, f"{location}.{attribute_name}"))
            appended_names.add(attribute_name)

    for slot_name in _declared_slot_names(type(value), location):
        if (
            slot_name in known_names
            or slot_name in appended_names
            or slot_name in ("__dict__", "__weakref__")
        ):
            continue
        _reject_forbidden_field_name(slot_name, location)
        try:
            nested = getattr(value, slot_name)
        except AttributeError as error:
            raise Phase5ModelValidationError(
                f"{location} has an unreadable declared slot {slot_name!r}"
            ) from error
        pending.append((nested, f"{location}.{slot_name}"))


def _declared_slot_names(
    value_type: type[object],
    location: str,
) -> tuple[str, ...]:
    """class MRO가 명시적으로 선언한 ``__slots__`` 이름만 수집한다.

    Args:
        value_type: dataclass 또는 namedtuple 인스턴스의 실제 class.
        location: 잘못된 slot 선언을 보고할 data 위치.

    Returns:
        MRO 선언 순서대로 모은 slot 이름 tuple.

    Raises:
        Phase5ModelValidationError: slot 선언이 문자열 또는 문자열 iterable이
            아닌 경우.
    """

    slot_names = []
    for declared_type in value_type.__mro__:
        declared_slots = vars(declared_type).get("__slots__", ())
        if isinstance(declared_slots, str):
            declared_slots = (declared_slots,)
        try:
            normalized_slots = tuple(declared_slots)
        except TypeError as error:
            raise Phase5ModelValidationError(
                f"{location} has an invalid __slots__ declaration"
            ) from error
        if any(type(slot_name) is not str for slot_name in normalized_slots):
            raise Phase5ModelValidationError(
                f"{location} has a non-string __slots__ declaration"
            )
        slot_names.extend(normalized_slots)
    return tuple(slot_names)


def _namedtuple_field_names(
    value: object,
    location: str,
) -> tuple[str, ...] | None:
    """tuple subclass가 선언한 namedtuple 필드 이름을 안전하게 읽는다.

    인스턴스의 임의 속성을 탐색하지 않고 namedtuple class의 ``_fields``만 읽는다.
    ``_fields``가 없는 일반 tuple 또는 tuple이 아닌 값에는 ``None``을 반환한다.

    Args:
        value: namedtuple 여부를 판별할 중첩 data 값.
        location: 오류 위치를 나타내는 문자열.

    Returns:
        선언 순서의 namedtuple 필드 이름 또는 namedtuple이 아니면 None.

    Raises:
        Phase5ModelValidationError: ``_fields`` 모양이나 필드 수가 tuple 값과
            일치하지 않는 경우.
    """

    if not isinstance(value, tuple) or type(value) is tuple:
        return None

    field_names = getattr(type(value), "_fields", None)
    if field_names is None:
        return None
    if (
        not isinstance(field_names, tuple)
        or len(field_names) != len(value)
        or any(type(field_name) is not str for field_name in field_names)
    ):
        raise Phase5ModelValidationError(
            f"{location} has an invalid namedtuple field declaration"
        )
    return field_names


def _reject_forbidden_field_name(field_name: str, location: str) -> None:
    """필드 이름에 금지된 raw·lineage·source 의미 토큰이 있는지 확인한다.

    Args:
        field_name: dataclass 필드 또는 mapping 문자열 키.
        location: 해당 필드를 포함한 data 위치.

    Raises:
        Phase5ModelValidationError: 금지 토큰을 포함한 필드 이름인 경우.
    """

    snake_like_name = CAMEL_CASE_BOUNDARY_PATTERN.sub("_", field_name).lower()
    tokens = NON_NAME_CHARACTER_PATTERN.split(snake_like_name)
    if FORBIDDEN_DATA_FIELD_TOKENS.intersection(tokens):
        raise Phase5ModelValidationError(
            f"{location} contains forbidden normal-data field {field_name!r}"
        )


def _validate_source_record_ids(
    metadata: object,
    location: str,
    accepted_record_ids: frozenset[int],
) -> None:
    """metadata source ID가 정렬·고유하고 원래 accepted에 존재하는지 확인한다.

    Args:
        metadata: 검증할 단일 ModelMetadata 객체.
        location: 해당 ModelRecord 위치.
        accepted_record_ids: 원래 Phase 4 accepted record ID 집합.

    Raises:
        Phase5ModelValidationError: source ID 구조·순서·존재성이 잘못된 경우.
    """

    source_record_ids = _required_attribute(
        metadata,
        "source_record_ids",
        f"{location}.metadata",
    )
    if not isinstance(source_record_ids, tuple) or not source_record_ids:
        raise Phase5ModelValidationError(
            f"{location}.metadata.source_record_ids must be a non-empty tuple"
        )
    for source_id in source_record_ids:
        if type(source_id) is not int:
            raise Phase5ModelValidationError(
                f"{location}.metadata.source_record_ids must contain exact ints"
            )

    if source_record_ids != tuple(sorted(set(source_record_ids))):
        raise Phase5ModelValidationError(
            f"{location}.metadata.source_record_ids must be sorted and unique"
        )

    missing_source_ids = [
        source_id
        for source_id in source_record_ids
        if source_id not in accepted_record_ids
    ]
    if missing_source_ids:
        raise Phase5ModelValidationError(
            f"{location}.metadata.source_record_ids are absent from Phase 4 "
            f"accepted lineage: {missing_source_ids}"
        )


def _validate_model_fingerprint(
    model_name: str,
    data: object,
    metadata: object,
    contract_version: str,
    location: str,
) -> None:
    """기존 canonical 함수로 모델 지문을 재계산해 저장값과 비교한다.

    Args:
        model_name: 지문 payload의 Silver 모델 이름.
        data: 모델의 normal data dataclass.
        metadata: 저장 지문을 가진 ModelMetadata.
        contract_version: context가 제공한 원래 계약 버전.
        location: 해당 ModelRecord 위치.

    Raises:
        Phase5ModelValidationError: 저장 지문이 문자열이 아니거나 재계산과
            다른 경우.
    """

    stored_fingerprint = _required_attribute(
        metadata,
        "model_fingerprint",
        f"{location}.metadata",
    )
    if not isinstance(stored_fingerprint, str):
        raise Phase5ModelValidationError(
            f"{location}.metadata.model_fingerprint must be str"
        )

    expected_fingerprint = compute_model_fingerprint(
        model_name,
        data,
        contract_version,
    )
    if stored_fingerprint != expected_fingerprint:
        raise Phase5ModelValidationError(
            f"{location}.metadata.model_fingerprint does not match recomputation"
        )


def _validate_model_counts(
    phase5_output: Phase5Output,
    records_by_model: dict[str, dict[ModelKey, object]],
) -> None:
    """최종 model_counts가 실제 네 collection 길이와 같은지 확인한다.

    Args:
        phase5_output: model_counts를 가진 최종 출력.
        records_by_model: 중복 검증을 통과한 모델별 record mapping.

    Raises:
        Phase5ModelValidationError: count 타입 또는 실제 길이가 다른 경우.
    """

    model_counts = _required_attribute(
        phase5_output,
        "model_counts",
        "Phase5Output",
    )
    for model_name, count_field_name in (
        ("employee", "employee"),
        ("area", "area"),
        ("parent_area", "parent_area"),
        ("join_reference", "join_reference"),
    ):
        stored_count = _required_attribute(
            model_counts,
            count_field_name,
            "Phase5Output.model_counts",
        )
        actual_count = len(records_by_model[model_name])
        if type(stored_count) is not int or stored_count != actual_count:
            raise Phase5ModelValidationError(
                f"Phase5Output.model_counts.{count_field_name} must equal "
                f"the {model_name} collection length {actual_count}"
            )


def _validate_join_employee_portion(
    join_records: dict[ModelKey, object],
    employee_records: dict[ModelKey, object],
) -> None:
    """각 join의 직원 부분이 참조 employee 레코드와 같은지 확인한다.

    Args:
        join_records: area·employee 복합키별 join-reference ModelRecord.
        employee_records: employee ID별 employee ModelRecord.

    Raises:
        Phase5ModelValidationError: 참조 employee가 없거나 공유 값이 다른 경우.
    """

    for join_key, join_record in join_records.items():
        join_data = _required_attribute(
            join_record, "data", f"join_reference[{join_key}]"
        )
        employee_id = _required_attribute(
            join_data,
            "employee_id",
            f"join_reference[{join_key}].data",
        )
        if employee_id not in employee_records:
            raise Phase5ModelValidationError(
                f"join_reference[{join_key!r}] references missing employee "
                f"{employee_id!r}"
            )

        employee_record = employee_records[employee_id]
        employee_data = _required_attribute(
            employee_record,
            "data",
            f"employee[{employee_id}]",
        )
        _validate_shared_fields_equal(
            left=join_data,
            right=employee_data,
            field_names=EMPLOYEE_SHARED_FIELD_NAMES,
            location=(
                f"join_reference[{join_key!r}] employee portion for {employee_id!r}"
            ),
        )


def _validate_join_area_portion(
    join_records: dict[ModelKey, object],
    area_records: dict[ModelKey, object],
) -> None:
    """각 join의 조직 공유 부분이 참조 area 레코드와 같은지 확인한다.

    Args:
        join_records: area·employee 복합키별 join-reference ModelRecord.
        area_records: area ID별 area ModelRecord.

    Raises:
        Phase5ModelValidationError: 참조 area가 없거나 세 공유 값이 다른 경우.
    """

    for join_key, join_record in join_records.items():
        join_data = _required_attribute(
            join_record, "data", f"join_reference[{join_key}]"
        )
        area_id = _required_attribute(
            join_data,
            "area_id",
            f"join_reference[{join_key}].data",
        )
        if area_id not in area_records:
            raise Phase5ModelValidationError(
                f"join_reference[{join_key!r}] references missing area {area_id!r}"
            )

        area_record = area_records[area_id]
        area_data = _required_attribute(
            area_record,
            "data",
            f"area[{area_id}]",
        )
        _validate_shared_fields_equal(
            left=join_data,
            right=area_data,
            field_names=AREA_SHARED_FIELD_NAMES,
            location=f"join_reference[{join_key!r}] area portion for {area_id!r}",
        )


def _validate_parent_area_sources(
    parent_area_records: dict[ModelKey, object],
    accepted_business_by_record_id: dict[int, object],
) -> None:
    """parent-area 네 필드를 각 lineage 원본의 top 필드와 비교한다.

    같은 배치의 area collection에 해당 top ID가 존재하는지는 확인하지 않는다.
    parent-area는 원래 accepted top 필드의 lookup projection이기 때문이다.

    Args:
        parent_area_records: top area ID별 parent-area ModelRecord.
        accepted_business_by_record_id: accepted lineage ID별 원래 business.

    Raises:
        Phase5ModelValidationError: 원래 top 필드와 parent-area 값이 다른 경우.
    """

    for parent_key, parent_record in parent_area_records.items():
        location = f"parent_area[{parent_key!r}]"
        parent_data = _required_attribute(parent_record, "data", location)
        metadata = _required_attribute(parent_record, "metadata", location)
        source_record_ids = _required_attribute(
            metadata,
            "source_record_ids",
            f"{location}.metadata",
        )

        for source_record_id in source_record_ids:
            source_business = accepted_business_by_record_id[source_record_id]
            _validate_shared_fields_equal(
                left=parent_data,
                right=source_business,
                field_names=PARENT_AREA_FIELD_NAMES,
                location=(
                    f"{location} projection from Phase 4 accepted "
                    f"record_id={source_record_id}"
                ),
            )


def _validate_shared_fields_equal(
    *,
    left: object,
    right: object,
    field_names: tuple[str, ...],
    location: str,
) -> None:
    """두 계약 객체의 지정된 공유 필드가 모두 같은지 확인한다.

    Args:
        left: 최종 projection data 객체.
        right: 비교 기준 모델 또는 원래 business 객체.
        field_names: 양쪽에서 같은 이름으로 읽을 공유 필드들.
        location: 비교 목적과 모델 키를 설명하는 문자열.

    Raises:
        Phase5ModelValidationError: 공유 필드 중 하나라도 다른 경우.
    """

    for field_name in field_names:
        left_value = _required_attribute(left, field_name, location)
        right_value = _required_attribute(right, field_name, location)
        if left_value != right_value:
            raise Phase5ModelValidationError(
                f"{location} differs at shared field {field_name!r}"
            )


def _required_attribute(
    value: object,
    attribute_name: str,
    location: str,
) -> object:
    """검증 대상의 필수 속성을 읽고 누락을 Phase 7 오류로 바꾼다.

    Args:
        value: 속성을 읽을 검증 대상 객체.
        attribute_name: 읽어야 하는 필수 속성 이름.
        location: 오류 위치를 나타내는 문자열.

    Returns:
        입력 객체가 가진 속성 값을 변경 없이 반환한다.

    Raises:
        Phase5ModelValidationError: 필수 속성이 없는 경우.
    """

    try:
        return getattr(value, attribute_name)
    except AttributeError as error:
        raise Phase5ModelValidationError(
            f"{location} is missing required attribute {attribute_name!r}"
        ) from error


def _required_tuple(
    value: object,
    attribute_name: str,
    location: str,
) -> tuple[object, ...]:
    """필수 collection 속성이 결정적인 tuple 계약인지 확인한다.

    Args:
        value: collection 속성을 가진 검증 대상 객체.
        attribute_name: tuple이어야 하는 속성 이름.
        location: 오류 위치를 나타내는 문자열.

    Returns:
        입력 객체의 원래 tuple.

    Raises:
        Phase5ModelValidationError: 속성이 없거나 tuple이 아닌 경우.
    """

    collection = _required_attribute(value, attribute_name, location)
    if not isinstance(collection, tuple):
        raise Phase5ModelValidationError(f"{location}.{attribute_name} must be tuple")
    return collection
