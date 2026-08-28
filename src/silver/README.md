# Silver 증분 Flat·정규화 레이어

`src/silver/`는 Bronze가 오래된 순서로 제공하는 Atlas 미처리 원본을 최소
전처리하고, 표준 flat 행으로 변환한 뒤 전체 표준값 중복을 제거하여 임시 CSV에
증분 반영합니다. 누적 Flat accept 전체에서는 직원·업무 영역·최상위 영역 lookup·
영역-직원 조인 참조의 네 Silver 모델 snapshot을 다시 생성합니다. 통합 진입점
`src/main.py`는 이 Silver 처리가 성공하면 생성된 네 모델 CSV로 MySQL 적재기를
실행합니다.

## 파일과 책임

```text
src/silver/
├── preprocessor.py   # 의미를 추정하지 않는 문자열 최소 정리
├── rules.py          # 표준 용어와 YAML/CSV mapping 로딩·검증
├── normalizer.py     # 표준 flat 변환과 1차 Reject 판정
├── deduplicator.py   # 전체 표준 필드값 중복과 2차 Reject 판정
├── csv_output.py     # 기존 Flat 행 보존, staging, CSV pair publication
├── modeling/
│   ├── contracts.py  # 네 모델·정규화 Reject의 컬럼과 key 계약
│   ├── projections.py # 누적 Flat의 모델 투영·충돌 source 라우팅
│   ├── materializer.py # accept.csv 전체 snapshot 재생성 조립
│   └── model_output.py # 정규화 5개 파일의 staging·publication·복원
├── mysql_settings.py # src/.env 기반 MySQL 연결 설정 검증
├── mysql_csv_reader.py # 네 모델 CSV의 header·key·datetime 최소 검증
├── mysql_schema.py   # 네 고정 테이블의 CREATE·스키마 검증
├── mysql_loader.py   # 독립 dry-run·스키마 초기화·트랜잭션 적재 CLI
├── flat_pipeline.py  # Flat·정규화 조립, Bronze 연동, checkpoint 확정, CLI
└── README.md
```

## 고정 Bronze 연동 계약

`flat_pipeline.py`는 다음 공개 경로만 사용합니다.

```python
from src.bronze.atlas_pipeline import AtlasIncrementalPipeline

pipeline = AtlasIncrementalPipeline(
    settings,
    processed_ids_path=temp_dir / "processed_ids.json",
)
batches = pipeline.iter_batches(limit=n)
pipeline.mark_processed(source_ids)
pipeline.close()
```

- 각 batch는 `.records`를 제공합니다.
- 각 raw record에는 MongoDB `_id`가 있습니다.
- `iter_batches(limit=n)`은 처리하지 않은 원본을 오래된 순서로 제공합니다.
- Silver는 Flat CSV pair와 정규화 5개 파일의 publication 및 source accounting이
  모두 성공한 뒤에만 이번 실행에서 관찰한 `_id` 문자열 전체를
  `mark_processed()`에 전달합니다.
- 조회·Flat 처리·정규화·publication 중 실패하면 `mark_processed()`를 호출하지
  않습니다.

## 정확한 조립 순서

```text
기존 accept/reject source_id 로드 + 이전 accept 중복 key 등록
    ↓
AtlasIncrementalPipeline.iter_batches(limit=n)
    ↓ 오래된 미처리 raw record
source_id 재반영 방지
    ├─ 기존 CSV에 있음 → replayed, CSV 재기록 없음
    └─ 신규 source
           ↓
BasicPreprocessor.preprocess()
    ↓ 의미를 바꾸지 않은 최소 정리
FlatNormalizer.normalize()
    ├─ 실패 → FIRST_STAGE Reject
    └─ 성공 → 표준 flat 행
                 ↓
FullRowDeduplicator.check_and_add()
    ├─ 중복 → SECOND_STAGE Reject
    └─ 최초 → accept
                 ↓
CsvOutputTransaction staging 작성 및 pair publication
                 ↓
누적 accept.csv 전체 로드
                 ↓
build_normalization_projection()
    ├─ 같은 model key·같은 data → 모델별 한 행으로 축약
    ├─ model key 누락·같은 key의 상이 data → normalization Reject
    └─ Reject source → 네 모델 모두에서 제외
                 ↓
publish_normalization_outputs()
    ├─ normalization_reject.csv
    └─ models/ 아래 네 모델 CSV
                 ↓ 두 publication과 source accounting이 성공한 뒤에만
AtlasIncrementalPipeline.mark_processed(source_ids)
```

`flat_pipeline.write_clean_flat_data()`가 전처리기, 규칙, 정규화기, 중복 검사기,
Flat CSV transaction을 조립합니다. `modeling.materialize_normalized_outputs()`는
게시된 누적 `accept.csv` 전체에서 네 모델과 정규화 Reject snapshot을 다시 만들고,
`run_atlas_cleaning()`은 이 두 단계 앞뒤에 Bronze 조회와 processed-ID 확정을
연결합니다.

## 1. 기본 전처리

`preprocessor.BasicPreprocessor`는 mapping·list·tuple 안의 모든 문자열 값에 다음
처리만 재귀적으로 적용하며 입력 객체를 직접 수정하지 않습니다.

1. Unicode NFKC 정규화
2. 탭·줄바꿈을 포함한 공백 문자를 ASCII 공백으로 변환
3. 연속 공백을 한 칸으로 축소하고 앞뒤 공백 제거
4. `Cc`, `Cf` 범주의 비정상 제어문자 제거

`Unknown`, `없음`, `미상`처럼 의미상 이상한 토큰은 전처리에서 삭제하거나 빈 값으로
바꾸지 않습니다. 그대로 다음 단계에 넘기며, Flat 표준화에서 nullable 부모 필드인
`p_area_no`, `p_area_nm`에만 `None`을 적용하고 나머지 필드에서는 1차 Reject로
기록합니다.

## 2. 표준화와 1차 Reject

`rules.SilverRules`는 다음 파일만 읽습니다.

| 파일 | 목적 |
|---|---|
| `data-contracts/standard-term.csv` | source/표준 필드, 출력 순서, nullability |
| `standards/code-normalization.yaml` | 상태·레벨 코드 변환 |
| `standards/area-name-normalization.csv` | 업무영역명 변환 |

`normalizer.FlatNormalizer`는 정리된 payload를 표준 필드로 flat하게 변환합니다.
이 단계에서는 서로 같은 표준 행이 여러 개 나와도 허용합니다. 타입, 필수값,
결측 대체 토큰, 식별자, 이름 mapping, 코드, 날짜 패턴 또는 미래 시각 검증에
실패한 레코드는 `FIRST_STAGE` Reject입니다.

- `area_nm`, `p_area_nm`, `top_area_nm`은 내부 공백을 모두 제거한 뒤 기존 20개
  승인 영역 mapping을 적용합니다.
- `mgr_nm`, `mgr_dept_nm`, `mgr_pos_nm`은 내부 공백을 모두 제거합니다.
- 결측 대체 토큰은 nullable `p_area_no`, `p_area_nm`에서만 `None`으로 허용하며,
  다른 필드에서는 기존 `NULL_LIKE_VALUE` Reject를 유지합니다.

코드 YAML을 읽을 때는 각 `source_values`의 변환 결과가 해당
`allowed_values`에 포함되는지 확인합니다. 비어 있는 허용 목록, 허용 목록 밖의
표준값, 정규화 후 서로 다른 표준값으로 충돌하는 원천값은 실행 전에
`SilverRuleError`로 중단합니다. 대소문자만 다른 별칭이 같은 표준값으로 수렴하는
경우는 하나의 lookup으로 허용합니다.

주요 1차 Reject 코드는 다음과 같습니다.

| 코드 | 의미 |
|---|---|
| `TYPE_INVALID` | 레코드나 필드 타입을 처리할 수 없음 |
| `RECORD_ID_INVALID` | `record_id`가 1 이상의 정수가 아님 |
| `PAYLOAD_INVALID` | payload가 객체가 아님 |
| `REQUIRED_VALUE_MISSING` | 필수값이 없음 |
| `NULL_LIKE_VALUE` | nullable 부모 필드 외에 들어온 `Unknown`, `없음`, `미상` 등의 이상 토큰 |
| `IDENTIFIER_INVALID` | 업무영역·직원 ID 패턴 불일치 |
| `AREA_NAME_UNMAPPED` | 승인된 업무영역명으로 변환 불가 |
| `STATUS_CODE_INVALID` | 상태 코드 변환 불가 |
| `LEVEL_CODE_INVALID` | 최상위 단계 코드 변환 불가 |
| `DATETIME_INVALID` | 날짜 형식·달력값·소수 초 규칙 불일치 |
| `FUTURE_DATETIME` | 실행 기준시각보다 미래인 날짜 |
| `TEXT_INVALID` | 일반 문자열 길이 제한 초과 |

1차 Reject는 중복 검사기에 전달하지 않으므로 중복 key 저장소를 오염시키지
않습니다. Reject의 `raw_json`은 전처리 결과가 아니라 전처리 전 Atlas 원본을 BSON
Extended JSON으로 직렬화한 값입니다.

## 3. 전체값 중복과 2차 Reject

`deduplicator.FullRowDeduplicator`는 `standard-term.csv` 순서의 모든 표준 필드값을
중복 key로 사용합니다. lineage인 `source_id`(Mongo `_id`)와 `record_id`는 key에서
제외합니다.

중복 비교 범위는 다음 세 가지입니다.

- 같은 batch에서 앞서 표준화에 성공한 행
- 현재 실행의 이전 batch에서 표준화에 성공한 행
- 이전 실행의 `accept.csv`에 이미 존재하는 행

Bronze 입력 순서를 그대로 사용하므로 가장 오래된 최초 행만 accept되고 이후 같은
표준값은 `SECOND_STAGE` 및 `DUPLICATE_NORMALIZED_ROW`로 Reject됩니다. 1차 Reject는
중복 key에 등록되지 않으므로 같은 표준 후보가 뒤에 정상적으로 들어오면 accept될 수
있습니다.

## 4. Flat 증분 CSV 출력

### `temp/accept.csv`

```text
source_id,record_id,area_id,area_name,parent_area_id,parent_area_name,
top_area_id,top_area_name,top_area_level_code,employee_id,employee_name,
employee_department_name,employee_position_name,employee_hire_datetime,
employee_status_code,area_registration_date,top_area_registration_date
```

실제 header는 한 줄입니다. `source_id`와 `record_id`는 lineage이며 중복 key에는
포함되지 않습니다.

### `temp/reject.csv`

| 컬럼 | 내용 |
|---|---|
| `source_id` | MongoDB `_id` 문자열 |
| `record_id` | 원본 record ID |
| `reject_stage` | `FIRST_STAGE` 또는 `SECOND_STAGE` |
| `reason_codes` | `|`로 연결한 Reject 코드 |
| `reason_details` | 코드·필드·설명을 담은 JSON 배열 |
| `raw_json` | 전처리 전 BSON Extended JSON 원본 |

`CsvOutputTransaction`은 기존 accept/reject 행을 staging 파일에 먼저 복사하고 새
행만 추가합니다. 두 파일이 모두 완성된 뒤 최종 경로로 교체하며, 두 번째 파일 교체
실패 시 이전 pair를 복원합니다.

기존 상태를 열 때 accept/reject 중 한 파일만 있거나, 두 파일에서 동일한 nonempty
`source_id`가 두 번 나타나거나, `reject_stage`가 허용값과 다르면 fail-closed로
중단합니다. 손상 상태를 빈 파일이나 임의 disposition으로 자동 보정하지 않습니다.

## 5. 누적 Flat 기반 정규화 출력

`modeling.materializer`는 매 실행마다 게시된 `temp/accept.csv` 전체를 읽어 정규화
결과를 전체 snapshot으로 다시 생성합니다. 모델 CSV에 신규 행만 append하지 않으므로,
이번 실행에서 과거 key와의 충돌이 확인되면 그 key에 연결된 과거 source도 현재
snapshot의 네 모델에서 함께 제외됩니다. 기존 Flat pair만 있고 정규화 출력이 없는
상태도 같은 방식으로 bootstrap할 수 있습니다.

정규화 투영 규칙은 다음과 같습니다.

- 같은 model key와 같은 data는 모델별 한 행으로 중복 제거합니다.
- model key가 비었거나 같은 key에 서로 다른 data가 있으면 관련 source를
  `normalization_reject.csv`에 기록합니다.
- 정규화 Reject가 된 source는 부분 모델을 남기지 않고 네 모델 모두에서 제외합니다.
- `parent_area_id` 또는 `top_area_id`가 현재 부분 snapshot의 `area_id`에 없으면
  차단하지 않고 `orphan_counts`에만 집계합니다.
- 모델 행은 key 순서로 정렬하고 각 모델 key의 유일성을 게시 전에 확인합니다.

### `temp/normalization_reject.csv`

이 파일은 Flat 표준화를 통과했지만 모델 투영에서 제외된 source만 기록합니다.
`temp/reject.csv`의 1·2차 Reject와 섞지 않습니다.

| 컬럼 | 내용 |
|---|---|
| `source_id` | Flat accept의 MongoDB `_id` 문자열 |
| `record_id` | Flat accept의 record ID |
| `reject_stage` | 항상 `NORMALIZATION` |
| `model_name` | 충돌 또는 key 누락이 발생한 모델명 |
| `model_key` | 단일 key 문자열 또는 복합 key JSON |
| `reason_code` | 모델 충돌·key 누락 코드 |
| `reason_detail` | 충돌 변형 수 또는 누락 key 설명 |
| `standardized_json` | lineage를 제외한 15개 표준 Flat 필드 JSON |

복합 key는 `{"area_id":"...","employee_id":"..."}` 형태로 직렬화합니다.
`standardized_json`에는 `raw_json`을 넣지 않습니다. Atlas 원본은 Bronze에 보존되고,
Flat 1·2차 Reject의 원본은 `temp/reject.csv`가 보존합니다.

정규화 Reject 코드는 다음 다섯 개입니다.

| 코드 | 의미 |
|---|---|
| `MODEL_KEY_MISSING` | 모델 필수 key가 비어 있음 |
| `EMPLOYEE_MODEL_CONFLICT` | 같은 `employee_id`에 서로 다른 직원 data가 있음 |
| `AREA_MODEL_CONFLICT` | 같은 `area_id`에 서로 다른 영역 data가 있음 |
| `PARENT_AREA_MODEL_CONFLICT` | 같은 `top_area_id`에 서로 다른 lookup data가 있음 |
| `JOIN_REFERENCE_MODEL_CONFLICT` | 같은 `(area_id, employee_id)`에 서로 다른 조인 data가 있음 |

한 source가 여러 모델에서 제외되면 Reject 행은 여러 개일 수 있습니다. 정규화
rejected source 수는 CSV 행 수가 아니라 고유 `source_id` 수로 계산합니다.

### `temp/models/`의 네 모델

| 파일 | key | 컬럼 |
|---|---|---|
| `silver_employee.csv` | `employee_id` | `employee_id`, `employee_name`, `employee_department_name`, `employee_position_name`, `employee_hire_datetime`, `employee_status_code` |
| `silver_area.csv` | `area_id` | `area_id`, `area_name`, `parent_area_id`, `employee_id`, `area_registration_date` |
| `silver_parent_area.csv` | `top_area_id` | `top_area_id`, `top_area_name`, `top_area_level_code`, `top_area_registration_date` |
| `silver_area_join_reference.csv` | `(area_id, employee_id)` | `area_id`, `parent_area_id`, `parent_area_name`, `employee_id`, `employee_name`, `employee_department_name`, `employee_position_name`, `employee_hire_datetime`, `employee_status_code` |

네 모델에는 `source_id`, `record_id`, `raw_json` 또는 다른 lineage 필드를 넣지
않습니다. `model_output.publish_normalization_outputs()`는
`normalization_reject.csv`와 네 모델 CSV를 하나의 publication 그룹으로 취급합니다.
다섯 staging 파일을 모두 완성하고 기존 파일을 모두 백업한 뒤 고정 순서로 교체하며,
중간 교체가 실패하면 기존 파일은 복원하고 이번에 처음 생성된 파일은 제거합니다.
정상 데이터가 없어도 각 CSV의 header는 생성합니다.

## 6. 멱등성과 checkpoint 순서

한 실행의 순서는 반드시 다음과 같습니다.

1. 기존 accept/reject에서 이미 반영된 `source_id`와 accept 중복 key를 읽습니다.
2. Bronze가 반환한 `_id`가 기존 CSV에 있으면 재분류·재기록하지 않습니다.
3. 신규 source만 accept, 1차 Reject, 2차 Reject 중 정확히 하나로 기록합니다.
4. `input = accept + first reject + second reject + replayed`를 검증합니다.
5. staging의 accept/reject pair를 최종 CSV에 publish합니다.
6. 누적 `accept.csv` 전체에서 정규화 projection을 만들고
   `normalization input sources = accepted sources + rejected sources`를 검증합니다.
7. `normalization_reject.csv`와 네 모델 CSV를 하나의 그룹으로 publish합니다.
8. 두 publication과 accounting이 모두 성공한 뒤 이번 실행에서 관찰한 모든 고유
   `_id`를 `mark_processed()`에 전달합니다.

5번 뒤 6~7번에서 실패하면 Flat pair는 남지만 checkpoint는 전진하지 않습니다.
재실행에서 같은 source는 2번의 대조로 replay 처리되고, 누적 `accept.csv` 전체에서
정규화 snapshot을 다시 만든 뒤 checkpoint가 따라잡습니다. 정규화 publication 뒤
8번에서 checkpoint가 실패한 경우에도 재실행은 같은 snapshot을 다시 게시하고
checkpoint를 재시도하므로 Flat 또는 모델 행을 중복 append하지 않습니다.

Atlas 신규 source가 없어도 Flat pair가 있고 정규화 출력이 없다면 누적
`accept.csv`에서 정규화 파일을 bootstrap합니다. 이때 이번 실행에서 관찰한 `_id`가
없으므로 `mark_processed()`는 호출하지 않습니다.

반대 방향인 processed-ID checkpoint만 존재하고 accept/reject 두 파일이 모두 없는
상태는 안전하게 복원할 근거가 없으므로 Atlas pipeline 생성 전에 fail-closed로
중단합니다. 모델 파일 누락만으로는 이 검사를 실패시키지 않으며, Flat pair가 있으면
누적 `accept.csv`를 복구 근거로 사용합니다.

정규화 data 충돌은 전체 실행 실패가 아니라 `normalization_reject.csv`로 라우팅합니다.
반면 누적 Flat header 손상, 모델 key 중복, source accounting 불일치, publication 실패는
checkpoint 전에 실행을 중단합니다.

`SilverRunSummary`의 count는 이번 호출에서 다음 의미를 가집니다.

| 필드 | 의미 |
|---|---|
| `input_count` | Bronze에서 받은 행 수 |
| `accepted_count` | 이번 호출에서 새로 추가한 accept 수 |
| `first_rejected_count` | 이번 호출에서 새로 추가한 1차 Reject 수 |
| `duplicate_rejected_count` | 이번 호출에서 새로 추가한 2차 Reject 수 |
| `rejected_count` | 두 Reject 수의 합 |
| `replayed_count` | CSV에 이미 반영되어 재기록하지 않은 source 수 |
| `normalization_input_source_count` | 누적 `accept.csv`의 고유 source 수 |
| `normalization_accepted_source_count` | 네 모델 투영에 남은 고유 source 수 |
| `normalization_rejected_source_count` | 정규화 Reject가 된 고유 source 수 |
| `normalization_reject_row_count` | `normalization_reject.csv` 행 수 |
| `model_row_counts` | 모델별 key 중복 제거 후 행 수 |
| `orphan_counts` | 비차단 `parent_area_id`·`top_area_id` 참조 누락 수 |

Flat accounting과 정규화 accounting은 다음처럼 분리합니다.

```text
이번 Atlas 입력
= 이번 Flat accept + 이번 Flat 1차 Reject + 이번 Flat 2차 Reject + replayed

누적 Flat accept 고유 source
= 정규화 accepted 고유 source + 정규화 rejected 고유 source
```

모델 행 수는 model key 기준으로 중복 제거된 수이므로 source 수와 직접 합산하지
않습니다.

## 7. 통합 MySQL 적재

`src/main.py`는 `flat_pipeline.py`를 먼저 완료한 뒤 `<temp-dir>/models`를
`mysql_loader.py`에 전달합니다. 적재 입력은 그 아래의 네 모델 CSV만 사용하며,
`normalization_reject.csv`는 적재하지 않습니다. 기본 cycle은 환경 설정과 CSV
계약만 검증하는 dry-run이므로 MySQL에 연결하거나 데이터를 변경하지 않습니다.

연결 설정은 `src/.env`의 다음 키를 사용합니다. 이미 프로세스 환경에 주입된 값이
있으면 그 값을 우선하며, 비밀번호는 로그나 설정 객체 표현에 출력하지 않습니다.

```dotenv
MYSQL_DATABASE=실제_데이터베이스명
MYSQL_USER=실제_계정
MYSQL_PASSWORD=실제_비밀번호
MYSQL_HOST=실제_MySQL_주소
MYSQL_PORT=3306
```

비밀번호가 없는 로컬 계정은 `MYSQL_PASSWORD=`처럼 key를 남기고 값을 비워 둘 수
있습니다. key 자체가 없으면 설정 누락으로 처리합니다.

테이블과 key는 다음과 같습니다.

| 테이블 | Primary key |
|---|---|
| `silver_employee` | `employee_id` |
| `silver_area` | `area_id` |
| `silver_parent_area` | `top_area_id` |
| `silver_area_join_reference` | `(area_id, employee_id)` |

MySQL 스키마에는 다음 물리 FK를 둡니다.

| 자식 컬럼 | 부모 컬럼 | 참조 동작 |
|---|---|---|
| `silver_area.employee_id` | `silver_employee.employee_id` | `ON DELETE CASCADE ON UPDATE CASCADE` |
| `silver_area_join_reference.area_id` | `silver_area.area_id` | `ON DELETE CASCADE ON UPDATE CASCADE` |
| `silver_area_join_reference.employee_id` | `silver_employee.employee_id` | `ON DELETE CASCADE ON UPDATE CASCADE` |

`parent_area_id`와 `top_area_id`는 현재 부분 snapshot에서 참조 누락을 허용하므로
논리적 참조로만 유지하고 물리 FK로 강제하지 않습니다. `employee_department_name`은
이름값이고 `area_name`은 unique key가 아니므로 물리 FK로 연결하지 않습니다.
`--init-schema`는 데이터베이스 자체를 만들지 않으며, 없는 고정 테이블에는
`CREATE TABLE IF NOT EXISTS`로 FK를 포함해 생성합니다. 이미 존재하는 테이블에서
누락된 고정 FK는 명시적 `ALTER TABLE ... ADD CONSTRAINT`로 추가합니다.
같은 이름의 FK 정의가 다르거나 예상 밖 FK가 있으면 자동 교체·삭제하지 않고
fail-closed로 중단합니다. 마지막으로 전체 컬럼·순서·타입·길이·nullability·PK와
FK의 자식·부모 컬럼 및 `CASCADE` 규칙을 검증합니다.

`--apply`는 기존 네 테이블이 고정 스키마와 일치할 때만 다음 작업을 하나의
트랜잭션으로 수행합니다.

1. 조인·최상위 영역·영역·직원 순서로 `DELETE`
2. 직원·영역·최상위 영역·조인 순서로 고정 parameterized `INSERT`
3. 테이블별 `COUNT(*)`와 CSV 행 수 비교
4. 모두 일치하면 commit, 하나라도 실패하면 rollback

적재 중 `foreign_key_checks`를 끄지 않습니다. `TRUNCATE`, `DROP`, 임의 upsert는
수행하지 않으며, `ALTER`는 `--init-schema`에서 누락된 고정 FK를 추가할 때만
사용합니다. 기본 insert chunk는 1,000행입니다.

```bash
# 환경·CSV 계약만 검증하며 DB에는 연결하지 않음
python -m src.silver.mysql_loader --models-dir temp/models

# 없는 고정 테이블 생성 및 스키마 검증만 수행
python -m src.silver.mysql_loader --models-dir temp/models --init-schema

# 기존 테이블 검증 후 네 snapshot을 트랜잭션으로 교체
python -m src.silver.mysql_loader --models-dir temp/models --apply

# 테이블 초기화와 적재를 한 번에 명시
python -m src.silver.mysql_loader --models-dir temp/models --init-schema --apply
```

## 실행

Python 명령은 Conda `sandbox` 환경에서 실행합니다.

```bash
source /opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh
conda activate sandbox

# 기본: 즉시 시작한 뒤 src/.env의 주기마다 Atlas→Silver→MySQL CSV dry-run 반복
python src/main.py

# module 실행도 동일
python -m src.main

# 단발 dry-run
python src/main.py --once

# 최초 단발 실제 적재
python src/main.py --once --init-schema --apply

# 기존 고정 스키마에 단발 실제 적재
python src/main.py --once --apply
```

통합 진입점과 Silver·MySQL CLI의 실행 요약은 터미널과 저장소 루트
`output/logs/pipeline.log`에 함께 기록됩니다. 로그 경로는 `--temp-dir`와 무관하며
UTF-8 append 방식으로 기록하고, 10 MiB 단위로 회전해 최대 5개 백업을 유지합니다.
로깅 메시지에 접속 설정 객체나 비밀번호를 직접 포함하지 않습니다. 독립 Bronze
수집기의 `src/bronze/logs/crawler.log`는 이 파일에 합치지 않습니다.

`--batch-size`는 Atlas 조회 batch 크기(기본 1,000), `--temp-dir`는 Flat·정규화
출력과 checkpoint를 두는 디렉터리(기본 `temp`), `--chunk-size`는 MySQL
`executemany()` insert chunk 크기(기본 1,000)입니다. `--once`는 cycle을 한 번만
실행합니다. 반복 주기는 프로세스 환경변수 또는 `src/.env`의
`PIPELINE_INTERVAL_SECONDS`에 양의 정수(초)로 설정하며, 미설정 시 30초입니다.
`--interval-seconds`를 명시하면 환경값보다 우선합니다.
`python src/main.py`와 `python -m src.main`은 같은 인자와 순서로 실행됩니다. 기존
Silver CLI 공개 경로 `main()`, `--batch-size`, `--temp-dir`와 코드 공개 경로
`write_clean_flat_data()`도 유지합니다. 기본 processed-ID 파일은 Bronze가 정렬된
문자열 배열로 관리하는 `<temp-dir>/processed_ids.json`입니다.

기본 loop는 첫 cycle을 즉시 실행한 뒤 각 cycle 시작 시각을 기준으로 설정 주기마다 다음
cycle을 시작합니다. cycle이 설정 주기를 넘으면 중첩하지 않고 완료 직후 다음 cycle을
시작합니다. 예외가 발생하거나 하위 단계가 nonzero를 반환하면 loop를 중단하며,
`Ctrl-C`로 종료할 수 있습니다. `--init-schema`는 첫 cycle에만 적용됩니다.

통합 실행에서도 checkpoint는 Silver publication과 accounting이 성공한 직후,
MySQL 적재 전에 확정됩니다. 따라서 MySQL 실패 뒤 같은 `--temp-dir`로 재실행할 수
있습니다. 규칙 변경 등으로 전체 source를 다시 처리하려면 새 `--temp-dir`를
사용하고, 프로세스 간 lock이 없으므로 같은 `--temp-dir`를 동시에 실행하지
않습니다.

`--apply`는 매 cycle마다 네 MySQL 테이블의 기존 행을 지운 뒤 현재 CSV snapshot
전체를 넣는 full replacement입니다. 빈 모델 snapshot도 유효한 입력이므로 실제
적용하면 해당 테이블이 비게 될 수 있습니다. 반복 실행에 `--apply`를 사용할 때는
매 cycle의 교체를 의도했는지 확인하고, 적용 전 모델 행 수를 dry-run 결과로 확인해야
합니다.

## 검증 범위

Silver 단위·통합 테스트는 실제 network를 사용하지 않고 `.records`,
`mark_processed()`, `close()`를 구현한 Bronze fake로 다음을 확인합니다.

- NFKC, 제어문자 제거, 공백 정규화
- 지정된 영역명·직원명 계열 6개 필드의 내부 공백 제거
- nullable 부모 필드의 결측 토큰만 `None`으로 허용
- 패턴 표준화 실패와 이상 토큰의 1차 Reject
- 1차 Reject가 중복 key를 오염시키지 않음
- Reject `raw_json`이 전처리 전 원본임
- 같은 batch·다른 batch의 전체값 중복 2차 Reject
- 이전 `accept.csv`와의 중복 2차 Reject
- accept·1차 Reject·2차 Reject·replayed accounting
- 네 모델의 정확한 `area_*` 컬럼·key와 모델 key 유일성
- 같은 model key·같은 data의 중복 제거와 상이 data의 source 전체 Reject
- `normalization_reject.csv`의 stage·reason·복합 key·15개 표준 필드 JSON
- 누적 `accept.csv` 기반 snapshot의 key 정렬과 재실행 byte 결정성
- 정규화 5개 파일의 빈 header 출력과 중간 교체 실패 시 전체 복원
- 처리 실패 시 Flat·정규화 최종 CSV와 processed IDs의 비정상 전진 방지
- 정규화 publication 실패 또는 checkpoint 실패 뒤 replay 재실행 멱등성
- Flat-only 기존 상태와 빈 Atlas 입력에서 정규화 출력 bootstrap
- Flat source와 정규화 source의 분리 accounting
- 신규 source만 추가 및 빈 신규 source
- 실제 `AtlasIncrementalPipeline`과 Silver 기본 factory의 fake-client 통합 재실행
- 구형 CSV header 이관, 불완전 pair·중복 source lineage의 fail-closed 처리
- checkpoint만 남고 output pair가 사라진 상태의 실행 전 차단

규칙 변경 뒤 기존 source를 다시 산출할 때는 기존 실행 디렉터리의 파일을 삭제하지
않고 새로운 `--temp-dir`로 전체 재실행합니다. 기존 디렉터리에서는 누적
`accept.csv`/`reject.csv`와 `processed_ids.json`에 기록된 source가 replay로
건너뛰어지므로 새 규칙이 과거 행에 소급 적용되지 않습니다.

```bash
source /opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh
conda activate sandbox
python -m pytest -q tests/pipeline/unit/silver tests/pipeline/integration/silver
ruff check src/silver tests/pipeline/unit/silver tests/pipeline/integration/silver
ruff format --check src/silver tests/pipeline/unit/silver tests/pipeline/integration/silver
```

## 가정과 현재 범위 밖

- 가장 오래된 순서와 미처리 필터링은 Bronze `AtlasIncrementalPipeline` 책임입니다.
- 한 시점에 하나의 Silver 실행을 전제로 하며 프로세스 간 lock은 없습니다.
- 기존 CSV가 새 `source_id` 컬럼 없이 만들어졌다면 해당 과거 accept 행은 값 중복
  기준에는 포함되지만 Mongo `_id` replay 판정에는 사용할 수 없습니다. 과거
  `reject_stage`가 없는 Reject는 reason code로 단계를 판정합니다. 이후 신규 출력이
  publish될 때 두 파일은 새 header로 이관되며, 복구할 수 없는 과거 `source_id`는
  빈 값으로 보존됩니다. 누적 accept에 빈 `source_id`가 남아 있으면 정규화 source
  accounting을 확정할 수 없으므로 정규화 publication 전에 fail-closed로 중단합니다.
- 데이터베이스 생성, 임의 값 보정, 별도 체크섬·contract lock은 구현하지 않습니다.
