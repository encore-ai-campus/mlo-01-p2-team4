# DRD - 레거시 인사 데이터 통합 및 사내 규정 기반 연차 관리 시스템

## 1. 목적과 기준

### 1.1 목적

본 문서는 레거시 인사 Flat 데이터를 보존하고 표준화하여 직원·업무 영역 데이터와 연차 산정에 필요한 Gold 입력을 구성하기 위한 데이터 요구사항을 정의한다. 논리 데이터 모델과 표준화 계약은 정의하지만, 원본 DDL이 없는 항목의 물리 PK·FK 제약조건은 확정하지 않는다.

## 2. 범위와 데이터 계층

### 2.1 포함 범위

- 레거시 Flat/API 원천 데이터의 Raw 보존과 처리 lineage
- 원천 컬럼을 표준 용어·도메인으로 매핑하는 Silver 표준화
- Accepted와 Reject의 행 단위 분리 및 accounting
- 직원, 업무 영역, 최상위 영역 lookup, 조인 참조의 네 Silver 모델
- 연차 규정별 Gold 산정 입력과 승인 fixture
- 담당자가 선택한 규정의 계산·중복 확인·부여 이력 저장에 필요한 데이터

### 2.2 제외 범위

- 원천 시스템의 직접 등록·수정
- 연차 신청·승인·취소·사용 처리 전체
- 사내 복지 규정 자체의 등록·변경
- 급여·근태·평가 시스템 자체의 구축
- 규정 충족 여부의 무인 자동 판정 또는 담당자가 선택하지 않은 규정의 자동 부여
- 우수부서와 다태아 출산처럼 결정되지 않은 규정의 운영 적용

### 2.3 계층별 책임

| 계층 | 데이터 책임 | 보존·변경 원칙 | 주요 산출물 |
|---|---|---|---|
| Bronze | API/Flat 원천과 수신 상태 보존 | 원천 items·원본 행은 변경하지 않음. 저장 성공 후에만 cursor를 확정 | 원천 JSON, batch, cursor/checkpoint, 실행 기록 |
| Silver | 표준화·품질 검증·관계 투영 | 승인된 매핑만 적용하고 위반 행은 Reject로 분리 | silver_employee, silver_area, silver_parent_area, silver_area_join_reference |
| Gold | 연차 산정 목적별 입력 구성 | 누락·미결정 값을 임의 보정하지 않음 | 규정별 입력, 기대 결과 fixture |
| Service | 담당자 선택·계산·이력 관리 | 선택되지 않은 규정은 계산·부여하지 않음. 과거 이력은 규정 변경으로 변경하지 않음 | 계산 결과, 중복 확인, grant history |

## 3. 원천 데이터와 grain

### 3.1 원천 컬럼 집합

통합 Flat에서 확인한 원천 컬럼은 다음 15개다.

~~~text
area_no, area_nm, p_area_no, p_area_nm, top_area_no,
top_area_nm, top_area_lvl, mgr_no, mgr_nm, mgr_dept_nm,
mgr_pos_nm, mgr_hire_dtm, mgr_act_yn, area_reg_dtm,
top_area_reg_dtm
~~~

## 4. 표준 단어와 데이터 도메인

### 4.1 표준 단어

표준 물리명은 다음 단어를 조합한 lower_snake_case를 사용한다. ID, NAME, CODE는 각각 식별자·이름·코드의 의미를 명시하는 접미어 또는 구성 단어로 사용한다.

| word_id | 논리 단어 | 영어 단어 | 주요 사용 |
|---|---|---|---|
| AREA | 업무영역 | area | 업무 영역 |
| PARENT | 상위 | parent | 직접 상위 영역 |
| TOP | 최상위 | top | 최상위 영역 역할 |
| EMPLOYEE | 직원 | employee | 관리자 역할로 연결된 직원 |
| DEPARTMENT | 부서 | department | 직원 소속 부서명 |
| POSITION | 직위 | position | 직원 직위명 |
| HIRE | 입사 | hire | 직원 입사 일시 |
| IS | 여부 | is | 논리 상태 접두어의 표준 단어 |
| ACTIVE | 활성 | active | 직원 활성 상태 |
| REGISTRATION | 등록 | registration | 영역 등록 일시 |
| LEVEL | 단계 | level | 계층 단계 코드 |
| DATE | 일자 | date | 날짜·일시 구성 단어 |
| ID | ID | identifier | 유일 식별 값 |
| NAME | 명 | name | 이름 값 |
| CODE | 코드 | code | 상태·단계 구분 값 |

### 4.2 도메인

아래 도메인은 요청 제공본 data-domains.yaml의 version 1을 그대로 요약한 것이다. 도메인 자체는 nullable을 허용하지만, 실제 필드의 필수 여부는 standard-terms.csv의 필드 계약과 모델 계약으로 제한한다.

| domain_id | 논리형 / Python | DB 타입 | 형식·길이 | 허용값 | 도메인 nullable |
|---|---|---|---|---|:---:|
| IDENTIFIER_20 | identifier / str | VARCHAR(20) | ^[A-Z][A-Z0-9_]{0,19}$ | 없음 | Y |
| NAME_100 | name / str | VARCHAR(100) | ^.{1,100}$ | 없음 | Y |
| LEVEL_CODE_20 | code / str | VARCHAR(20) | ^[A-Z][A-Z0-9_]{0,19}$ | TOP_LEVEL | Y |
| DATETIME_ISO | datetime / datetime | DATETIME | ^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$ | 없음 | Y |
| STATUS_CODE_20 | code / str | VARCHAR(20) | ^[A-Z][A-Z0-9_]{0,19}$ | ACTIVE, INACTIVE | Y |

DATETIME_ISO의 정규 표현식에는 timezone offset이 포함되지 않는다. 원천 offset의 해석·UTC 변환·local time 보존 중 어느 방식을 적용할지는 아직 확정되지 않았으므로 Raw 값을 삭제하거나 시간대 의미를 임의로 바꾸지 않는다.

## 5. 표준 용어·원천 매핑

| term_id | 표준 물리명 | 원천 컬럼 | domain_id | 필드 null | 의미 |
|---|---|---|---|:---:|---|
| AREA_ID | area_id | area_no | IDENTIFIER_20 | N | 업무 영역 식별자 |
| AREA_NAME | area_name | area_nm | NAME_100 | N | 표준 업무 영역 이름 |
| PARENT_AREA_ID | parent_area_id | p_area_no | IDENTIFIER_20 | Y | 직접 상위 업무 영역 식별자 |
| PARENT_AREA_NAME | parent_area_name | p_area_nm | NAME_100 | Y | 직접 상위 업무 영역 이름 |
| TOP_AREA_ID | top_area_id | top_area_no | IDENTIFIER_20 | N | 최상위 업무 영역 식별자 |
| TOP_AREA_NAME | top_area_name | top_area_nm | NAME_100 | N | 최상위 업무 영역 이름 |
| TOP_AREA_LEVEL_CODE | top_area_level_code | top_area_lvl | LEVEL_CODE_20 | N | 최상위 업무 영역 단계 코드 |
| EMPLOYEE_ID | employee_id | mgr_no | IDENTIFIER_20 | N | 현재 레코드에서 관리자 역할로 연결된 직원 식별자 |
| EMPLOYEE_NAME | employee_name | mgr_nm | NAME_100 | N | 연결된 직원 이름 |
| EMPLOYEE_DEPARTMENT_NAME | employee_department_name | mgr_dept_nm | NAME_100 | N | 연결된 직원 부서명 |
| EMPLOYEE_POSITION_NAME | employee_position_name | mgr_pos_nm | NAME_100 | N | 연결된 직원 직위명 |
| EMPLOYEE_HIRE_DATETIME | employee_hire_datetime | mgr_hire_dtm | DATETIME_ISO | N | 연결된 직원 입사 일시 |
| EMPLOYEE_STATUS_CODE | employee_status_code | mgr_act_yn | STATUS_CODE_20 | N | 직원 활성·비활성 상태 코드 |
| AREA_REGISTRATION_DATE | area_registration_date | area_reg_dtm | DATETIME_ISO | N | 업무 영역 등록 일시 |
| TOP_AREA_REGISTRATION_DATE | top_area_registration_date | top_area_reg_dtm | DATETIME_ISO | N | 최상위 업무 영역 등록 일시 |

PRD의 Silver 필드 표에 사용된 division_* 표기는 본 DRD의 표준 용어인 area_*에 대한 추적용 별칭으로 취급한다. 실행·저장 계약의 canonical physical name은 요청 제공 표준자료와 위 표의 area_*를 사용한다.

## 6. 표준화·검증·Reject 요구사항

### 6.1 원천 보존과 lineage

1. Bronze의 원천 JSON items와 Flat 원본 행은 변경 없이 보존한다.
2. 표준화 결과는 원천 값을 덮어쓰지 않고 별도 Silver 결과로 생성한다.
3. Reject에는 raw_json, 가능한 lineage, batch_record_index, violation code·rule·field·detail을 보존한다.
4. 정상 Silver 모델에는 raw_json, 원천 행 번호, lineage 및 처리 추적 필드를 넣지 않는다.
5. input_count = accepted_count + rejected_count + excluded_count를 항상 만족해야 하며, PRD의 Silver 계약에서는 excluded_count = 0이다.

### 6.2 구조와 필수값

- wrapper, payload, 원천 JSON 구조와 관찰 lineage를 먼저 검증하고, 구조 오류는 컬럼 매핑 전에 Reject한다.
- standard-terms.csv에서 필드 null이 N인 값이 없거나 빈 문자열이면 해당 행을 Accepted로 만들지 않는다.
- parent_area_id, parent_area_name은 null을 허용한다. 두 값의 동시 존재·참조 이름 일치·최상위 행 표현 규칙은 별도 업무 결정 또는 품질 계약으로 확정해야 한다.
- 구조 오류, 도메인 위반, unresolved mapping, 동일 key의 상충 값은 관련 행과 원천 사유를 함께 Reject한다.

### 6.3 식별자

표준 식별자는 IDENTIFIER_20 형식과 길이를 만족해야 한다. 역공학 보고서에 관찰된 예시는 다음과 같다.

| 원천 예시 | 표준화 관찰 예시 | 주의 |
|---|---|---|
| BIZ11608 | BIZ_11608 | 접두어·숫자 구분 규칙을 계약으로 lock해야 함 |
| BIZ-31536 | BIZ_31536 | 하이픈을 표준 구분자로 바꾸는 승인 mapping 필요 |
| BIZ_00379 | BIZ_00379 | 이미 표준 형식인 값 |
| emp000038 | EMP000038 | 대소문자 표준화 관찰 |

구분자·대소문자 차이를 동일 식별자로 병합하기 전에 유일성·동일성·충돌을 검사한다. 표준화 후 같은 key에 서로 다른 속성이 남으면 first/last wins로 선택하지 않고 fail closed 한다.

DEC-001과 DEC-002를 함께 적용할 때 영역·부모·최상위 영역 계열은 BIZ_* 형식으로 표준화 후 비교하고, 직원 계열은 DEC-002에 따라 공식 employee_id인 EMP* 형식으로 취급한다. BIZ_* 표준화와 비교 처리는 Phase 1~4에서 수행할 대상이며, 비교 결과가 충돌하면 자동 병합하지 않는다.

### 6.4 이름·코드·일시

- 이름은 앞뒤 공백을 제거한 뒤 NAME_100을 검사한다. 분 석팀 → 분석팀은 역공학 보고서의 관찰 예시이며, 내부 공백 제거를 모든 이름에 일반화하기 전 승인 mapping이 필요하다.
- top_area_level_code는 TOP_LEVEL만 허용한다. 최상위 → TOP_LEVEL은 보고서의 관찰 표준화 예시다. 다른 단계 코드를 운영에 허용하려면 도메인 버전을 먼저 변경한다.
- employee_status_code는 ACTIVE 또는 INACTIVE만 허용한다. 사용, 재직 → ACTIVE는 보고서에 기록된 mapping 예시다. 원천 y 등 제공 mapping에 없는 값은 자동으로 Accepted 처리하지 않는다.
- 일시는 DATETIME_ISO에 맞춰야 한다. 2021-12-01T05:30:46+09:00 → 2021-12-01T05:30:46은 보고서에 기록된 관찰 결과지만, offset을 제거해도 되는지 또는 UTC로 변환해야 하는지는 미결정이다. Raw 원문과 변환 근거를 함께 보존한다.

### 6.5 배치·결정성

1. SourceBatch + ReferenceSnapshot → Phase4Request → Phase4Output → Phase5Output 흐름을 사용한다.
2. ReferenceSnapshot.complete=false, context·version·snapshot 불일치는 batch 실패로 처리한다.
3. Phase4Output을 Phase5Processor에 직접 전달하고 중간 파일·DTO·재표준화를 삽입하지 않는다.
4. 같은 model key와 같은 data는 병합할 수 있지만, 같은 key와 다른 data는 계약 위반으로 중단한다.
5. 동일한 유효 입력은 입력 순서·배치 크기를 바꿔도 같은 업무 데이터 payload를 만들어야 한다.

## 7. 논리 데이터 모델

### 7.1 복원 ERD와 해석 경계

아래는 역공학 보고서의 관계를 표준 모델명으로 옮긴 논리 후보다. 선으로 표현된 관계는 물리 FK나 최종 카디널리티의 승인이 아니다.

~~~mermaid
erDiagram
    SILVER_EMPLOYEE ||--o{ SILVER_AREA : "employee_id / mgr_no candidate"
    SILVER_AREA ||--o{ SILVER_AREA : "parent_area_id self-reference candidate"
    SILVER_PARENT_AREA ||--o{ SILVER_AREA : "top_area_id lookup candidate"
    SILVER_AREA ||--|| SILVER_AREA_JOIN_REFERENCE : "area_id derived candidate"
    SILVER_EMPLOYEE ||--o{ SILVER_AREA_JOIN_REFERENCE : "employee snapshot candidate"
~~~

역공학 보고서에는 Top Area를 업무 개체처럼 요약한 부분과 Parent Lookup으로 분류한 부분이 함께 있다. 1차 결정에 따라 Top Area는 독립 Master로 승격하지 않고 lookup/reference로만 운영한다. 본 DRD는 PRD의 네 모델 요구사항을 충족하기 위해 silver_parent_area를 lookup/reference projection으로 관리한다. silver_area_join_reference도 새로운 업무 사실을 관리하지 않는 비정규화 파생·snapshot이다.

### 7.2 모델 계약

| 모델 | 원천 역할 | grain | 업무 key | 권위 수준 | nullable 필드 |
|---|---|---|---|---|---|
| silver_employee | BIZ_EMPLOYEE_MASTER | 직원 속성 1건 | employee_id | Master 후보 | 없음 |
| silver_area | BIZ_META_AREA | 업무 영역 1건 | area_id | Master 후보 | parent_area_id |
| silver_parent_area | BIZ_META_AREA_PARENT_LOOKUP | 최상위 영역 lookup 1건 | top_area_id | Lookup/Reference | 없음 |
| silver_area_join_reference | BIZ_META_AREA_JOIN_READY | 영역 기준 조인 snapshot 1건 | area_id | Derived/Reference | parent_area_id, parent_area_name |

업무 key는 Silver 업무 데이터 payload의 식별 기준이며, 데이터베이스의 물리 PK 확정과 동일하지 않다. 특히 silver_area_join_reference의 area_id 유일성은 보고서의 1:1 추정이므로 전체 데이터 검증 전에는 물리 unique 제약으로 승격하지 않는다.

### 7.3 모델 필드 사전

| 모델 | 순번 | 표준 필드 | 원천 컬럼 | domain_id | null | key·역할 |
|---|---:|---|---|---|:---:|---|
| silver_employee | 1 | employee_id | mgr_no | IDENTIFIER_20 | N | 공식 직원 key, EMP* |
| silver_employee | 2 | employee_name | mgr_nm | NAME_100 | N | 직원명 |
| silver_employee | 3 | employee_department_name | mgr_dept_nm | NAME_100 | N | 직원 부서명 |
| silver_employee | 4 | employee_position_name | mgr_pos_nm | NAME_100 | N | 직원 직위명 |
| silver_employee | 5 | employee_hire_datetime | mgr_hire_dtm | DATETIME_ISO | N | 직원 입사 일시 |
| silver_employee | 6 | employee_status_code | mgr_act_yn | STATUS_CODE_20 | N | 직원 상태 코드 |
| silver_area | 1 | area_id | area_no | IDENTIFIER_20 | N | 영역 key 후보 |
| silver_area | 2 | area_name | area_nm | NAME_100 | N | 영역명 |
| silver_area | 3 | parent_area_id | p_area_no | IDENTIFIER_20 | Y | 부모 영역 참조 후보 |
| silver_area | 4 | employee_id | mgr_no | IDENTIFIER_20 | N | 관리자 역할 직원 참조 후보 |
| silver_area | 5 | area_registration_date | area_reg_dtm | DATETIME_ISO | N | 영역 등록 일시 |
| silver_parent_area | 1 | top_area_id | top_area_no | IDENTIFIER_20 | N | 최상위 lookup key |
| silver_parent_area | 2 | top_area_name | top_area_nm | NAME_100 | N | 최상위 영역명 |
| silver_parent_area | 3 | top_area_level_code | top_area_lvl | LEVEL_CODE_20 | N | 최상위 단계 코드 |
| silver_parent_area | 4 | top_area_registration_date | top_area_reg_dtm | DATETIME_ISO | N | 최상위 등록 일시 |
| silver_area_join_reference | 1 | area_id | area_no | IDENTIFIER_20 | N | 조인 기준 영역 key |
| silver_area_join_reference | 2 | parent_area_id | p_area_no | IDENTIFIER_20 | Y | 부모 영역 참조 snapshot |
| silver_area_join_reference | 3 | parent_area_name | p_area_nm | NAME_100 | Y | 부모 영역명 snapshot |
| silver_area_join_reference | 4 | employee_id | mgr_no | IDENTIFIER_20 | N | 관리자 직원 snapshot key |
| silver_area_join_reference | 5 | employee_name | mgr_nm | NAME_100 | N | 관리자 직원명 snapshot |
| silver_area_join_reference | 6 | employee_department_name | mgr_dept_nm | NAME_100 | N | 관리자 부서명 snapshot |
| silver_area_join_reference | 7 | employee_position_name | mgr_pos_nm | NAME_100 | N | 관리자 직위명 snapshot |
| silver_area_join_reference | 8 | employee_hire_datetime | mgr_hire_dtm | DATETIME_ISO | N | 관리자 입사 일시 snapshot |
| silver_area_join_reference | 9 | employee_status_code | mgr_act_yn | STATUS_CODE_20 | N | 관리자 상태 snapshot |

### 7.4 업무 데이터 payload 예시

아래 예시는 제공된 Phase5 출력 예시를 기준으로 업무 데이터만 표현한 것이다. 각 모델 레코드에는 data 객체만 포함하며, 추적용 객체나 필드는 포함하지 않는다.

~~~json
{
  "employees": [
    {
      "data": {
        "employee_id": "EMP002583",
        "employee_name": "박주원",
        "employee_department_name": "IT운영팀",
        "employee_position_name": "실장",
        "employee_hire_datetime": "2013-02-18T00:00:00",
        "employee_status_code": "ACTIVE"
      }
    }
  ],
  "areas": [
    {
      "data": {
        "area_id": "BIZ_00019",
        "area_name": "IT",
        "parent_area_id": "BIZ_00324",
        "employee_id": "EMP002583",
        "area_registration_date": "2017-04-26T00:00:00"
      }
    }
  ],
  "parent_areas": [
    {
      "data": {
        "top_area_id": "BIZ_00324",
        "top_area_name": "교육",
        "top_area_level_code": "TOP_LEVEL",
        "top_area_registration_date": "2016-02-17T00:00:00"
      }
    }
  ],
  "join_references": [
    {
      "data": {
        "area_id": "BIZ_00019",
        "parent_area_id": "BIZ_00324",
        "parent_area_name": "교육",
        "employee_id": "EMP002583",
        "employee_name": "박주원",
        "employee_department_name": "IT운영팀",
        "employee_position_name": "실장",
        "employee_hire_datetime": "2013-02-18T00:00:00",
        "employee_status_code": "ACTIVE"
      }
    }
  ]
}
~~~

### 7.5 관계 요구사항

| 관계 | 데이터 표현 | 근거·상태 | 검증/결정 필요 |
|---|---|---|---|
| 직원 → 영역 | silver_area.employee_id → silver_employee.employee_id | 역공학 보고서의 EMPLOYEE 1:N AREA 추정, DEC-002로 공식 직원 ID 확정 | 실제 FK·필수성·카디널리티 |
| 영역 → 영역 | silver_area.parent_area_id → silver_area.area_id | area_no → p_area_no 자기참조 후보 | root·고아·순환·부모 null 규칙 |
| 최상위 lookup → 영역 | top_area_id와 영역의 최상위 역할 연결 | DEC-003·DEC-006으로 최상위 lookup/reference 의미와 lookup 운영 확정 | 실제 연결 key·참조 정합성 |
| 영역 → 조인 참조 | join_reference.area_id 기준 파생 | 보고서의 1:1 파생 관계 추정 | 실제 유일성·snapshot 기준시점 |
| 직원 → 조인 참조 | join_reference.employee_id에 직원 속성 반복 | 관리자 snapshot 파생 추정 | 보존 주기·변경 시점·참조 정합성 |

정상 모델 payload에는 위 업무 필드 외의 raw·lineage·source 추적 필드를 추가하지 않는다. 원천 증거와 Reject 사유는 별도 산출물로 보존한다.

## 8. Gold·Service 데이터 요구사항

### 8.1 Silver만으로 충족되지 않는 추가 입력

역공학된 직원·영역 데이터는 연차 계산의 공통 직원 식별·입사 일시·상태·조직 조회를 지원하지만, Appendix의 모든 규정을 계산하기에는 다음 자료가 추가로 필요하다. 이 자료들의 원천 시스템·물리 컬럼·갱신 주기는 제공 문서에서 확정되지 않았다.

| 데이터 그룹 | 최소 논리 속성 | 사용 규정 | 현재 상태 |
|---|---|---|---|
| 직원 기준 | employee_id, 부여일 현재 상태, employee_hire_datetime, 기준일 | 전체 규정 | Silver에서 일부 제공. 인정 근속기간·재직 snapshot 기준은 확정 필요 |
| 휴직·재직 이력 | 직원, 휴직 시작·종료, 인정 근속기간 산입 여부 | 장기근속·우수 근태·직원평가 | 필요하지만 레거시 ERD에 없음 |
| 근태 | 직원, 반기, 최종 지각 건수, 승인 외근·출장 제외 여부, 기록 확정 시각 | 우수 근태 | 필요. 반기 확정 자료의 원천·schema 미정 |
| 성과평가 | 직원, 전년도 평가 등급, 평가 확정 여부·일시 | 성과평가 | 필요. 평가 등급 체계·원천 미정 |
| 직원평가 | 직원, 반기, 반기 종료일 현재 부서, 우수자 선정 결과 | 직원평가 | 필요. 부서별 1명 선정 결과의 원천 미정 |
| 가족·출산 사건 | 직원 또는 배우자, 출산일, 자녀 순서·수, 사건 식별자 | 출산장려·다자녀 | 필요. 증빙·다태아 처리 미정 |
| 현재 잔여 연차 | 직원, 현재 잔여 일수, 기준 시각 | 직원 조회·부여 | 반영 시점과 방식이 OI-008 미결정 |

Gold는 위 자료의 누락·미결정 값을 임의 보정하지 않는다. 자료가 없거나 확정 상태가 아니면 해당 규정의 계산·부여를 진행하지 않고 담당자 확인 대상으로 남긴다.

### 8.2 규정별 산정 입력과 상태

| 규정 | 기준·부여 | 주기·중복 key | 필수 데이터 | 상태 |
|---|---|---|---|---|
| 장기근속 3년 | 인정 근속 3년 도달 시 2일, 1회 | 직원 + 규정 | 입사일, 휴직 이력, 기존 이력, 부여일 재직 | 적용 기준 확정 |
| 장기근속 5년 | 인정 근속 5년 도달 시 4일, 3년분 재합산 없음 | 직원 + 규정 | 입사일, 휴직 이력, 기존 이력, 부여일 재직 | 적용 기준 확정 |
| 장기근속 7년 이상 | 7년 도달 후 입사기념일마다 5일 | 직원 + 규정 + 입사기념일 cycle | 입사일, 휴직 이력, cycle 이력, 부여일 재직 | 적용 기준 확정 |
| 우수 근태 | 반기 최종 지각 0~2회이고 조건 충족 시 0.5일 | 직원 + 규정 + 연도 + 반기 | 반기 근태 확정, 입사·휴직 이력, 부여일 재직 | 적용 기준 확정 |
| 성과평가 | 부여일 재직 1년 이상·직전년도 B 이상이면 연 0.5일 | 직원 + 규정 + 연도 | 입사일, 직전년도 평가 확정 결과, 기존 이력 | 평가 없음은 해당 규정만 제외 |
| 직원평가 | 반기·부서별 우수자 1명에게 1일 | 직원 + 규정 + 연도 + 반기 | 반기 종료일 소속 부서, 선정 결과, 입사·휴직 이력, 부여일 재직 | 적용 기준 확정 |
| 우수부서 | 기준·일수 미정 | TBD | 승인된 부서평가 기준 | 도입 보류, 운영 금지 |
| 출산장려 | 직원·배우자의 첫째·둘째 자녀별 1일, 최대 2일 | 직원 + 규정 + 사건 | 출산 사건, 자녀 순서, 입사 3개월 기준, 기존 이력, 부여일 재직 | 다태아 기준 미결정 |
| 다자녀 | 자녀 3명 이상, 최초 요건 충족 연도와 이후 매년 1월 1일에 2일 | 직원 + 규정 + 연도 | 가족관계, 셋째 출산일, 입사 3개월 기준, 기존 이력, 부여일 재직 | 적용 기준 확정 |

모든 규정은 담당자가 적용 규정을 직접 선택한 경우에만 계산·저장한다. 선택되지 않은 규정은 자동 계산·부여하지 않는다. 모든 부여는 실제 부여일 현재 재직자만 대상이며, 규정 변경은 과거 이력에 소급하지 않는다.

### 8.3 부여 이력 데이터

Appendix에서 정의한 필수 이력 필드는 다음과 같다.

| 필드 | 필수 | 의미 |
|---|:---:|---|
| employee_identifier | Y | 직원 식별자 |
| rule_identifier | Y | 적용 규정 식별자 |
| rule_name | Y | 적용 당시 규정명 |
| grant_days | Y | 해당 규정으로 부여한 일수 |
| application_period_or_event | Y | 연도·반기·cycle 또는 출산 사건 |
| operator | Y | 규정을 선택하고 저장한 담당자 |
| saved_at | Y | 저장 시각 |
| rule_version | Y | 적용 당시 규정 버전 |
| evidence_or_memo | N | 확인 근거·메모 |
| history_status | TBD | 정상·취소·정정 등 상태 |

rule_version의 값 형식, 잘못 저장된 이력의 보정·취소 권한과 방식은 운영 전 승인해야 한다. 현재 잔여 연차에 추가 부여분을 반영하는 시점·방법도 OI-008이 결정될 때까지 확정하지 않는다.

## 9. 품질 지표와 수용 기준

### 9.1 Silver

| 검사 | 산식·기준 | 목표·게이트 |
|---|---|---|
| 원천 보존 | 저장된 원천 items / API 수신 items × 100 | 100% |
| cursor 순서 | 저장 성공 후 cursor 갱신 / 전체 cursor 갱신 | 100%. 저장 전 갱신 0건 |
| 필드 매핑 | 유효한 15개 표준 필드를 가진 Accepted 행 / Accepted 행 | 100% |
| 행 accounting | input = accepted + rejected + excluded | 항상 성립, excluded = 0 |
| 데이터 복원율 | accepted_count / input_count × 100 | ≥ 90% |
| Reject 비율 | rejected_count / input_count × 100 | ≤ 10% |
| 핵심 품질 오류 | 필수값·도메인·key conflict 등 | 0건 |
| 모델 결정성 | 입력 순서·배치 크기 변경 후 결과 비교 | 동일 결과, first/last wins 0건 |

90% 복원율과 10% Reject 비율은 PRD의 릴리스 목표값이다. 초기 실행에서 실제 count와 Reject 사유 분포를 별도로 기록하며, 목표 달성을 관측 사실처럼 쓰지 않는다.

### 9.2 Gold·Service

| 검사 | 목표·게이트 |
|---|---|
| 승인 규정 fixture 일치율 | 100% |
| 선택 규정별·총합 계산 불일치 | 0건 |
| grant history 저장·재조회 | 검수 대상 성공률 100% |
| 동일 규정·동일 적용 기간 중복 | 0건 |
| 선택하지 않은 규정의 자동 부여 | 0건 |

### 9.3 실행·복구

- 원천 JSON 저장 실패 시 마지막 성공 cursor를 유지하고 재시작 시 같은 cursor에서 재개한다.
- next_cursor는 opaque 값으로 취급하고 의미를 해석·변형하지 않는다.
- 동일 batch 재실행은 idempotent해야 하며, stale fencing token의 commit은 거부한다.
- rollback·checkpoint recovery에서 Bronze 원본은 변경하지 않고 Silver disposition·ledger만 재분류한다.

## 10. BRD·PRD 추적성

| BRD | DRD 데이터 요구사항 | PRD 실행 연결 | 수용 근거 |
|---|---|---|---|
| BR-001 | 원천 보존·lineage·cursor 순서 | B-01~B-06, I1~I3 | 보존율 100%, 저장 전 cursor 갱신 0건 |
| BR-002 | 15개 표준 필드·도메인 매핑 | S1~S4 | Accepted 대상 필드 매핑 100% |
| BR-003 | Accepted/Reject 분리와 행 accounting | S2~S4 | 모든 입력 행 1회 배정, excluded=0 |
| BR-004 | 규정별 Gold 입력·fixture | G-01~G-02 | 승인 fixture 일치율 100% |
| BR-005 | 직원·잔여 연차·기존 이력 조회 입력 | V-01 | 대상 정보·이력 조회 성공률 100% |
| BR-006 | 선택 규정별 계산·총합 | V-02~V-03 | 계산 불일치 0건 |
| BR-007 | 규정·근거·운영자·기간·버전 이력 | V-04 | 저장·재조회 성공률 100% |
| BR-008 | 규정·기간별 중복 key와 사전 경고 | V-03~V-04 | 중복 부여 0건 |

## 11. 미결정 사항과 출시 차단

다음 항목은 제공 자료만으로 확정하지 않는다. 결정되기 전에는 자동 보정·자동 계산·운영 적재를 확장하지 않는다.

| ID | 항목 | 영향 | 결정 여부 및 처리 |
|---|---|---|---|
| DEC-001 | 하이픈·밑줄·대소문자 차이가 같은 ID인지, 충돌 시 우선 원본은 무엇인지 | 직원·영역·부모·최상위 key | 영역 계열을 BIZ_* 형식으로 표준화 후 비교. Phase 1~4 처리 예정 |
| DEC-002 | mgr_no가 공식 직원 ID인지 관리자 전용 번호인지 | employee key·관리자 관계 | 공식 직원 ID로 확정. canonical employee_id는 EMP* |
| DEC-003 | p_area_no·top_area_no의 정확한 부모·최상위 의미와 root null 규칙 | self-reference·lookup 관계 | p_area_no는 area_no의 상위, top_area_no는 최상위. root null·물리 FK는 검증 필요 |
| DEC-004 | 관리자 지정의 유효기간·이력·역할 코드 존재 여부 | assignment 모델 분리 여부 | 미결정(-). 현재 snapshot 후보로만 유지 |
| DEC-005 | 상태 mapping, Boolean 여부, timezone과 등록 일시의 사건 의미 | 도메인·연차 기준일 | 미결정(-). 승인 mapping·시간대 정책 전 Reject 또는 확인 |
| DEC-006 | Top Area를 독립 Master로 승격할지 lookup으로 둘지 | silver_parent_area 경계 | lookup/reference로만 운영 확정 |
| DEC-007 | 보고서 1:N·1:1 추정을 실제 제약으로 승인할 수 있는지 | PK·FK·unique·카디널리티 | 전체 데이터 검증 전 후보 유지 |
| OI-005 | 우수부서 평가 기준 | 규정 계산·부여 | 도입 보류 |
| OI-006 | 다태아 출산장려 적용 방식 | 사건별 grant days | 담당자 확인, 자동 계산 금지 |
| OI-007 | 이력 보정·취소 권한과 처리 방식 | history status·audit | 운영 저장 전 승인 |
| OI-008 | 추가 연차의 현재 잔여 연차 반영 시점·방법 | 현재 잔여 연차 조회 | Service 출시 전 확정 |

## 12. 구현 전 완료 기준

다음 조건을 모두 충족해야 본 DRD의 데이터 계약을 운영 적용 대상으로 승격한다.

1. 요청 제공본 표준자료의 version·checksum이 실행 환경에 lock되어 있다.
2. 15개 원천 컬럼의 mapping, 도메인, nullability, 승인 status mapping이 확정되어 있다.
3. employee·area·parent lookup·join reference의 key·grain·관계가 전체 원천 데이터로 검증되어 있다.
4. timezone, root·cycle·reference name consistency 및 conflict 처리 규칙이 결정되어 있다.
5. Bronze 보존·cursor·Silver accounting·Reject lineage·모델 결정성 테스트가 통과했다.
6. 연차 계산에 필요한 휴직·근태·평가·가족 사건·기존 이력의 원천과 snapshot 기준이 지정되었다.
7. Gold fixture, Service 이력 필드·규정 버전·중복 key·미결정 OI 처리가 승인되었다.
