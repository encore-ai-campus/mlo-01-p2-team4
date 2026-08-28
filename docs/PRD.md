# PRD - 레거시 인사 데이터 통합 및 사내 규정 기반 연차 관리 시스템

## 1. 병목과 실행 기준

| 병목 | 구현 조치 | 릴리스 확인 기준 |
|---|---|---|
| 원천 구조·코드·명칭이 달라 재사용이 어렵다. | Bronze 원본 보존 후 Silver 계약으로 식별자·명칭·상태·조직 관계를 표준화한다. | Accepted 행의 대상 필드 매핑 완전성 100%, 핵심 품질 오류 0건 |
| 증분 수집 중 저장 실패 뒤 cursor가 이동하면 행을 잃을 수 있다. | 원천 JSON 저장 성공 후에만 `next_cursor`를 확정하고, 실패 시 마지막 성공 cursor를 유지한다. | 저장 전 cursor 갱신 0건, 재시작 시 마지막 성공 cursor부터 재개 |
| 오류 행이 정상 데이터에 섞여 원천과 사유를 다시 확인하기 어렵다. | 행별 Reject에 원천 JSON·lineage·모든 위반 사유를 보존한다. | `input = accepted + rejected + excluded`, `excluded = 0` |
| 조직·직원 모델을 배치별로 다르게 만들면 조회 결과가 달라질 수 있다. | Phase4Output을 Phase5Processor에 직접 전달하고, 결정적 fingerprint·중복 제거·교차 검증을 적용한다. | 입력 순서·배치 크기 변경 후 동일 결과, first/last wins 0건 |
| 연차 규정·부여 주기가 달라 수동 계산과 중복 확인이 발생한다. | 담당자가 규정을 직접 선택하고, 선택 규정만 계산·확인·저장하며 중복 key를 검사한다. | 계산 불일치 0건, 동일 규정·동일 적용 기간 중복 부여 0건 |

## 2. 목표와 KPI

### 2.1 릴리스 KPI

| KPI | 측정식 | 목표 | 미달 시 조치 |
|---|---|---:|---|
| Bronze 원천 보존율 | `저장된 원천 items / API 수신 items × 100` | 100% | 해당 batch의 cursor를 확정하지 않고 재수집 |
| Bronze cursor 일관성 | `저장 성공 후 cursor 갱신 건 / 전체 cursor 갱신 건 × 100` | 100% | 저장·상태 처리 수정 후 복구 시험 재실행 |
| Silver 데이터 복원율 | `accepted_count / input_count × 100` | ≥ 90% | 릴리스 차단, Reject 분포·원천 품질 검토 |
| Silver Reject 비율 | `rejected_count / input_count × 100` | ≤ 10% | 릴리스 차단, 사유별 조치·승인 필요 |
| Silver 필드 매핑 완전성 | `15개 표준 필드가 모두 유효한 Accepted 행 / Accepted 행 × 100` | 100% | 매핑·검증 계약 수정 후 재검증 |
| Gold 규정 테스트 일치율 | `기대 결과와 일치한 fixture / 승인 fixture × 100` | 100% | 해당 규정 출시 차단 |
| 연차 계산 불일치 | `선택 규정별·총합 불일치 건수` | 0건 | 저장 차단 및 계산 로직 검토 |
| 부여 이력 저장·조회 성공률 | `정상 재조회 건 / 검수 대상 저장 건 × 100` | 100% | Service 출시 차단 |
| 중복 부여 | `동일 규정·동일 적용 기간 중복 저장 건수` | 0건 | 저장 차단 및 이력 재검증 |

초기 실행에서 실제 `input_count`, `accepted_count`, `rejected_count`, Reject 사유 분포를 기록한다. 위 90%·10%는 관측 결과가 아니라 릴리스 목표값이다.

### 2.2 BRD 추적

| BR | 구현 위치 | 수용 기준 |
|---|---|---|
| BR-001 | Bronze·Silver | 원천 보존율 100%, 원천·lineage·실행 결과 재조회 가능 |
| BR-002 | Silver | Accepted 행 15개 표준 필드 매핑 완전성 100% |
| BR-003 | Silver | 모든 입력 행이 Accepted 또는 Reject에 한 번만 배정 |
| BR-004 | Gold | 승인 규정 fixture 일치율 100% |
| BR-005 | Service | 직원·현재 잔여 연차·기존 이력 조회 가능 |
| BR-006 | Service | 선택 규정별 일수와 총합 불일치 0건 |
| BR-007 | Service | 필수 이력 저장·재조회 성공률 100% |
| BR-008 | Service | 중복 부여 0건 |

## 3. 사용자와 범위

| 사용자 | 실행 목적 |
|---|---|
| 인사 담당자 | 직원·잔여 연차·이력을 확인하고 적용할 규정을 선택해 추가 연차를 부여 |
| 인사 책임자 | 데이터 기준·규정·예외·릴리스 결과 승인 |
| 데이터·시스템 운영 담당자 | 수집 상태, Reject, 품질 지표, checkpoint, 감사 결과 운영 |

### 3.1 포함 범위

- 내부 공개 API의 최초·증분 수집과 원본 보존
- Silver 표준화, 품질 검증, Reject, 네 개 Silver 모델, 실행 이력
- 연차 산정용 Gold 데이터와 규정 fixture
- 직원 조회, 담당자 규정 선택, 계산·중복 확인, 부여 이력 저장·조회

### 3.2 제외 범위

- 규정 충족 여부의 자동 판정 또는 미선택 규정의 자동 부여
- 연차 신청·승인·취소·사용 처리
- 원천 시스템·사내 규정 자체의 등록·수정
- 급여·근태·평가 전체 시스템 구축
- 미확정 규정의 운영 적용

## 4. 실행 흐름

```text
API key 조회 → meta·cursor 확인 → 최초/증분 records 요청(≤1,000건)
→ 원천 JSON 원자 저장 → cursor 확정 → Bronze batch
→ S1~S4 표준화·Reject → Phase4Output
→ S5~S8 모델 투영·검증 → 통합 적재·checkpoint
→ Gold 규정 fixture → 담당자 규정 선택·계산·중복 확인·이력 저장
```

```text
직원 검색 → 현재 잔여 연차·기존 이력 확인 → 규정 직접 선택
→ 선택 규정별 일수·총합 계산 → 중복 경고 → 담당자 확인 → 저장·재조회
```

## 5. Phase-Step 요구사항

### 5.1 단계 게이트

| Phase | 담당 | 시작 조건 | 종료 게이트 |
|---|---|---|---|
| P1 Bronze | 안길찬 | API 계약·설정 확인 | 원천 보존율 100%, cursor 복구 시험 통과 |
| P2 Silver S1~S4 | 이형인 | Bronze batch·ReferenceSnapshot | 복원율 ≥90%, Reject ≤10%, 행 accounting 통과 |
| P3 Silver S5~S8 | 김남동 | 유효한 Phase4Output | 네 모델·결정성·교차 검증 통과 |
| P4 통합·적재 | 이형인·김남동·안길찬 | P2·P3 계약 lock | idempotency·transaction·checkpoint·fencing 통과 |
| P5 Gold·Service | 안길찬 | 승인 Gold fixture·규정 | 계산·저장·중복 KPI 통과 |

### 5.2 P1 Bronze Collection

| Step | 실행할 일 | 산출물 | 수용 기준 |
|---|---|---|---|
| B-01 | 매 실행 시 `/public/v1/key`로 API 키를 조회하고 인증 오류 시 재조회 | API client | 키 재조회 후 요청 재시도, 키 로그 노출 0건 |
| B-02 | `/health/ready`, `/api/v1/meta`로 준비 상태·공개량·다음 갱신 시각 확인 | meta 결과 | 준비되지 않은 API에서 records 호출 0건 |
| B-03 | cursor가 없으면 미전달, 있으면 응답의 `next_cursor`를 그대로 전달 | records 요청 | 요청당 최대 1,000건, cursor 변형 0건 |
| B-04 | `items` 배열을 변경 없이 `crawling/data/records.json`에 임시 파일 후 교체 방식으로 누적 | 원천 JSON | 원천 보존율 100%, JSON 손상 0건 |
| B-05 | JSON 저장 성공 후 `crawling/state/cursor_state.json`에 cursor·공개량·성공 상태 저장 | cursor state | 저장 전 cursor 갱신 0건 |
| B-06 | 무데이터·잘못된 `next_refresh_at`·API/저장 오류 복구 | 로그·재시도 | 무데이터/파싱 실패 시 1,800초 후 같은 cursor 재시도 |

`GET /api/v1/records/{id}`는 특정 원천 레코드 확인에 사용한다. `records_path`의 `data/records.json`은 `crawling/` 기준 상대 경로다.

### 5.3 P2 Silver S1~S4: 표준화·Reject

| Step | 실행할 일 | 산출물 | 수용 기준 |
|---|---|---|---|
| S2 | wrapper·payload·raw JSON·observed lineage를 검증 | RejectedRecord | 구조 오류 행은 raw JSON·모든 위반 사유와 함께 Reject |
| S3 | null, ID, 조직명, 상태, 수준, 일시, 계층 규칙을 표준화 | StandardizedBusinessRecord | `EMP######`, `BIZ_#####`, 승인 mapping 외 값의 임의 보정 0건 |
| S4 | batch conflict, fingerprint, metrics, output schema를 검증 | Phase4Output | `input = accepted + rejected + excluded`, `excluded = 0`, 복원율·Reject 비율 KPI 통과 |

`ReferenceSnapshot.complete=false`, context/version 불일치, snapshot 불일치는 batch를 중단한다. 행 단위 오류와 같은 canonical key 충돌은 관련 행 전체를 Reject한다.

### 5.4 P3 Silver S5~S8: 모델 투영·검증

| Step | 실행할 일 | 산출물 | 수용 기준 |
|---|---|---|---|
| S5 | `Phase4Output`을 `Phase5Processor`에 직접 전달하고 4개 모델에 투영 | Phase5Output | 중간 파일·DTO·재표준화 0건 |
| S6 | 같은 key·같은 data만 병합하고 source ID·model fingerprint를 결정적으로 생성 | 중복 제거 모델 | 값이 다른 행의 first/last wins 0건 |
| S7 | key uniqueness, 컬럼 순서, 모델 간 직원·조직·parent 정합성 검증 | validation 결과 | 일반 모델에 raw·lineage·source 컬럼 0개 |
| S8 | accepted/rejected/duplicate/order-change/invalid-output/1,000행 fixture 실행 | 인수 결과 | 입력 순서·배치 크기 변경 후 결과 동일 |

### 5.5 P4 통합·적재

| Step | 실행할 일 | 산출물 | 수용 기준 |
|---|---|---|---|
| I1 | execution lock·fencing, checkpoint, batch idempotency, ReferenceSnapshot을 확인 | 실행 context | stale token commit·중복 batch commit 0건 |
| I2 | Phase4 → Phase5 → CanonicalReconciler → CommitBundle → SilverSink → checkpoint를 실행 | commit receipt | model·Reject·ledger·checkpoint 후보가 같은 transaction 경계에서 처리 |
| I3 | batch 간 conflict reclassification, rollback, checkpoint recovery, audit를 검증 | audit·recovery 결과 | Bronze 원본 변경 0건, Silver disposition·ledger만 재분류 |
| I4 | scheduler 활성화 전 batch-size invariance와 failure case를 검증 | scheduler gate | idempotency·rollback·checkpoint·fencing 실패 케이스 통과 전 scheduler 비활성 |

### 5.6 P5 Gold·Service

| Step | 실행할 일 | 산출물 | 수용 기준 |
|---|---|---|---|
| G-01 | Silver 통과 데이터를 규정별 산정 입력으로 구성 | Gold input | 미확정·누락 값을 임의 보정하지 않음 |
| G-02 | Appendix 기준으로 규정 fixture와 기대 결과 작성 | Golden fixture | 승인 fixture 일치율 100% |
| V-01 | 직원, 재직 상태, 고용일, 조직, 현재 잔여 연차, 기존 이력 조회 | 직원 조회 | 대상 정보·이력 조회 성공률 100% |
| V-02 | 담당자가 적용 규정만 직접 선택 | 선택 규정 | 미선택 규정 자동 계산·부여 0건 |
| V-03 | 선택 규정별 일수·총합 계산, 중복 검사, 저장 전 확인 | 계산 결과 | 계산 불일치·중복 부여 0건 |
| V-04 | 규정·근거·운영자·적용 기간·규정 버전을 이력으로 저장·재조회 | grant history | 검수 대상 저장·재조회 성공률 100% |

## 6. 구현 계약

### 6.1 Bronze 상태 계약

| 항목 | 값 |
|---|---|
| API | `/public/v1/key`, `/api/v1/meta`, `/api/v1/records`, `/api/v1/records/{id}`, `/health/ready` |
| 인증 | 인증 API 요청에 `X-API-Key` header 사용 |
| 페이지 상한 | 1,000건 |
| 저장 원칙 | `items` 원형 보존, 임시 파일 저장 후 교체 |
| cursor 상태 | `cursor`, `released_rows`, `next_refresh_at`, `last_success` |
| 재시도 | 데이터 없음 또는 잘못된 갱신 시각이면 1,800초 후 같은 cursor |
| 금지 | 저장 실패 뒤 cursor 갱신, cursor 의미 해석·변형, 잘못된 `next_cursor` 상태 저장 |

### 6.2 Silver 입력·출력 계약

```text
SourceBatch + ReferenceSnapshot → Phase4Request → Phase4Output → Phase5Output
```

Phase4의 일반 업무 필드는 다음 15개다.

`division_id`, `division_name`, `parent_division_id`, `parent_division_name`, `top_division_id`, `top_division_name`, `division_level_code`, `employee_id`, `employee_name`, `employee_department_name`, `employee_position_name`, `employee_hire_datetime`, `employee_status_code`, `division_registered_datetime`, `top_division_registered_datetime`

| 모델 | 일반 업무 필드 |
|---|---|
| `silver_employee` | `employee_id`, `employee_name`, `employee_department_name`, `employee_position_name`, `employee_hire_datetime`, `employee_status_code` |
| `silver_area` | `division_id`, `division_name`, `parent_division_id`, `employee_id`, `division_registered_datetime` |
| `silver_parent_area` | `top_division_id`, `top_division_name`, `division_level_code`, `top_division_registered_datetime` |
| `silver_area_join_reference` | `division_id`, `parent_division_id`, `parent_division_name`, `employee_id`, `employee_name`, `employee_department_name`, `employee_position_name`, `employee_hire_datetime`, `employee_status_code` |

Reject에는 `raw_json`, 가능한 lineage, `batch_record_index`, violation code/rule/field/detail을 보존한다. 일반 Silver 모델에는 raw JSON, 원천 행 번호, lineage, 처리 메타데이터를 넣지 않는다.

### 6.3 연차 규정·중복·이력 계약

| 규정 | 계산 기준 | 중복 기준 |
|---|---|---|
| 장기근속 3년 | 3년 도달 시 2일, 1회 | 직원 + 규정 |
| 장기근속 5년 | 5년 도달 시 4일, 1회. 3년분 재합산 없음 | 직원 + 규정 |
| 장기근속 7년 이상 | 7년 후 입사기념일마다 5일 | 직원 + 규정 + 입사기념일 cycle |
| 우수 근태 | 반기 최종 지각 0~2회, 전체 근무·무휴가·재직·근태 확정 후 0.5일 | 직원 + 규정 + 연도 + 반기 |
| 성과평가 | 부여일 1년 이상 재직, 전년도 B 이상이면 연 0.5일 | 직원 + 규정 + 연도 |
| 직원평가 | 반기 부서 1명, 1일, 연 최대 2일 | 직원 + 규정 + 연도 + 반기 |
| 우수 부서 | 기준 미정 | `TBD`, 운영 적용 금지 |
| 출산 장려 | 직원/배우자의 1·2번째 자녀마다 1일, 재직 3개월 후, 최대 2일 | 직원 + 규정 + 이벤트 |
| 다자녀 | 자녀 3명 이상, 2일, 재직 3개월 후 적용 | 직원 + 규정 + 연도 |

- 장기근속은 휴직 기간을 제외하며, 비윤년의 2월 29일 입사자는 2월 28일을 기준으로 한다.
- 우수 근태는 중도 입사자에게 다음 반기부터 적용하고, 승인 출장·외근은 지각으로 세지 않는다.
- 성과 결과가 없으면 성과평가 규정만 제외한다. 직원평가 부서는 반기 말 기준이며 같은 직원은 연 최대 2일까지 가능하다.
- 출산·다자녀는 대기 중 조건 충족 시 재직 3개월 도달일을 적용한다. 다자녀는 최초 해당 연도 출산일, 이후 매년 1월 1일에 적용하며 같은 해 중복 부여하지 않는다.
- 모든 부여는 실제 부여일 재직자만 가능하며, 규정 변경은 과거 이력을 변경하지 않는다.
- 이력 필수 필드: `employee_identifier`, `rule_identifier`, `rule_name`, `grant_days`, `application_period_or_event`, `operator`, `saved_at`, `rule_version`.
- `evidence_or_memo`는 선택이다. `history_status`, 보정·취소 권한, `rule_version` 값 형식은 `TBD`다.

## 7. 출시·측정·미결정

### 7.1 출시 순서

1. P1 Bronze 원천 보존율·cursor 복구를 검증한다.
2. P2에서 복원율 ≥90%, Reject 비율 ≤10%, 행 accounting을 확인한다.
3. P3·P4에서 모델 결정성, idempotency, rollback, checkpoint, fencing을 확인한다.
4. P5에서 Gold fixture 100%, 계산 불일치 0건, 중복 0건, 저장·조회 100%를 확인한다.

각 Phase 표의 정량 수용 기준 중 하나라도 미달하면 다음 단계 또는 운영 scheduler 출시를 차단한다. 최소 차단 항목은 원천 보존율, cursor 저장 순서, 페이지 상한·1,800초 재시도, Silver 복원율·Reject 비율·15개 필드 매핑 완전성·행 accounting(`input = accepted + rejected + excluded`, `excluded = 0`), Gold fixture, 계산 불일치, 중복 부여, 저장·조회 성공률이다.

### 7.2 운영 측정 항목

| 영역 | 반드시 기록할 값 |
|---|---|
| Bronze | 수신·저장 items 수, 보존율, cursor, retry, API 키 재조회, 저장 실패 |
| Silver | input/accepted/rejected/excluded, 복원율, Reject 비율·사유 분포, fingerprint·conflict |
| 통합 | idempotency hit, transaction 결과, checkpoint recovery, fencing 거부, audit receipt |
| Gold·Service | 규정 fixture 일치율, 계산 불일치, 중복 차단, 저장·재조회 성공률 |

### 7.3 미결정

| ID | 항목 | 출시 처리 |
|---|---|---|
| OI-005 | 우수 부서 기준 | 확정 전 계산·부여 금지 |
| OI-006 | 쌍둥이 출산 장려 적용 | 확정 전 자동 계산 금지, 담당자 확인 |
| OI-007 | 이력 보정·취소 권한 | 저장 기능 운영 전 승인 필요 |
| OI-008 | 추가 연차의 현재 잔여 연차 반영 시점·방법 | Service 출시 전 확정 필요 |

계약의 상세 매핑·스키마·fixture는 Silver phase-step과 승인된 계약 버전에서 관리한다. 이 PRD는 실행 목표·수용 기준·릴리스 판단 기준만 고정한다.
