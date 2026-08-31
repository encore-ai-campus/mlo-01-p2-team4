# MLO 1기 1차 프로젝트

## 1. 팀 소개

### 팀명
`mlo-01-p2-team4`

### 멤버
| 이름 | 역할 | GitHub |
|---|---|---|
| 안길찬 | 팀장 | (https://github.com/Lear-an) |
| 김남동 | 팀원 | (https://github.com/rlaskaehd) |
| 이형인 | 팀원 | (https://github.com/LEEHYUNGIN) |

## 2. 프로젝트 개요

### 프로젝트 명
임직원 연차 데이터 통합·분석 플랫폼

### 프로젝트 소개
레거시 인사 데이터를 원본 그대로 보존하고, 데이터 구조와 관계를 분석하여 공통 데이터 모델로 표준화하는 프로젝트입니다.

수집된 데이터는 Delta Lake 기반 Medallion 아키텍처에 따라 Bronze, Silver, Gold 레이어로 구분합니다.

- Bronze에는 원본 데이터와 수집 사실을 보존합니다.
- Silver에서는 컬럼명, 타입, 코드 및 식별자를 표준화하고 데이터 품질을 검증합니다.
- Gold에서는 기준 연차일수 조회, 분석 및 AI 활용 목적에 맞는 2차 컬럼과 AI Ready Data를 제공합니다.

프로젝트의 최종 목표는 **임직원 연차 내역 관리 서비스를 구축하는 것**입니다. 다만 현재 MVP에서는 연차 신청·승인·사용 이력 관리보다 레거시 인사 데이터의 표준화와 기준 연차일수 조회를 위한 데이터 기반 구축에 집중합니다.

## 3. 프로젝트 배경
레거시 인사 데이터는 수집 시점과 업무 목적에 따라 서로 다른 컬럼명, 코드, 날짜 형식 및 식별자 표현을 사용하고 있습니다.

이로 인해 다음과 같은 문제가 발생할 수 있습니다.

- 동일한 조직이나 직원을 서로 다른 값으로 인식할 수 있습니다.
- 원본 데이터와 가공 데이터의 관계를 확인하기 어렵습니다.
- 필요한 데이터를 사용할 때마다 별도의 정제와 검증이 필요합니다.
- 담당자와 업무에 따라 데이터 해석 기준이 달라질 수 있습니다.
- 잘못된 데이터가 연차 산정이나 분석 결과에 포함될 수 있습니다.
- 분석 및 AI 모델이 원천별 예외 처리에 의존하게 됩니다.

본 프로젝트에서는 원본을 변경하지 않고 보존하면서 표준 단어, 용어, 명명 규칙 및 데이터 도메인을 수립합니다. 이후 정상 데이터와 추가 확인이 필요한 데이터를 분리하여 재사용 가능한 인사 데이터 기반을 구축합니다.

## 4. 프로젝트 목표

### Bronze 목표

- 원본 파일과 원본 행을 변경 없이 보존합니다.
- 원본 컬럼명, 값, 공백, 대소문자 및 오류 형태를 유지합니다.
- 수집 시각, 원천 파일, 실행 ID 및 체크섬 등 수집 사실을 기록합니다.
- 파싱할 수 없는 데이터도 삭제하지 않고 원본과 실패 사유를 보존합니다.

### Silver 목표

- 레거시 컬럼을 승인된 표준 컬럼명으로 변환합니다.
- 식별자, 문자열, 날짜, 코드 및 상태값을 표준화합니다.
- 조직, 직원 및 조직-직원 관계를 정규화합니다.
- PK 중복, 필수값 결측, FK orphan 및 도메인 위반을 검증합니다.
- 정상 데이터와 격리 데이터를 분리합니다.
- 원본 행에서 표준 레코드까지의 데이터 계보를 관리합니다.

### Gold 목표

- 기준일별 직원 기준 연차일수를 제공합니다.
- 분석 및 AI 활용에 필요한 2차 컬럼을 생성합니다.
- Gold 컬럼의 원천 Silver 컬럼과 계산식을 추적할 수 있도록 합니다.
- 피처 계산 기준 시점인 `as_of_date`를 관리합니다.
- 분석 및 AI 코드가 원천 파일을 직접 사용하지 않도록 합니다.

### 프로젝트 서비스 구현 기획

- **홈 대시보드**: 최근 데이터 갱신 시각, 처리 건수 및 오류 현황을 요약해 제공한다.
- **파이프라인 대시보드**: Bronze–Silver–Gold 단계별 처리 상태와 격리 데이터 현황을 조회한다.
- **기준 연차일수 조회**: 직원과 기준일을 선택하여 입사일, 재직 상태 및 기준 연차일수를 확인한다.
- **데이터 추적 기능**: 조회 결과가 어떤 원본과 표준화 과정을 거쳐 생성되었는지 확인한다.

> 서비스 구현에 대해서는 조정 필요

## 5. 기술 스택

| 구분 | 기술 |
|---|---|
| Language | Python |
| Data | Delta Lake, MySQL, MongoDB |
| Collaboration | GitHub |

## 6. WBS 및 요구사항 명세서

### 6.1 WBS

| 작업 항목 | 작업 내용 |
|---|---|
| `안길찬` |  |
| BRONZE | 데이터 수집 ･ MongoDB Atlas 적재 |
| SILVER | 데이터 다운로드 로직 구현 |
| GOLD | Django 기능 구현 |
| `김남동` |  |
| 문서 | PRD, DRD 작성 |
| SILVER | 데이터 역공학 ･ 정규화 ･ 통합 파트 구현, MySQL 적재 |
| `이형인` |  |
| 문서 | BRD 작성, 서비스 기획 |
| SILVER | 표준화 |

## 7. ERD

추후 업로드 예정

## 8. 주요 프로시저

- 원본 데이터와 수집 메타데이터를 Bronze에 보존
- 표준 사전에 따른 컬럼명·타입·코드 변환
- 품질 검증 후 정상 데이터와 격리 데이터 분리
- 직원별 기준 연차일수와 AI Ready Data 생성
- 파이프라인 실행 이력과 데이터 계보 관리

## 9. 수행결과(테스트/시연 페이지)

- Bronze 원본 행 수 및 무결성 검증
- Silver PK·FK·결측·도메인 및 격리 결과 확인
- Gold 직원별 기준 연차일수 계산 결과 확인
- 파이프라인 상태 및 기준 연차일수 조회 화면 시연

## 10. 한 줄 회고

| 이름 | 회고 |
|---|---|
| 안길찬 | 작성예정 |
| 김남동 | 1차 프로젝트에서 느낀 문제를 보완하기 위해 작업과 검증 범위를 세분화해 진행했고, 그 과정에서 상세한 계획과 계약 자체보다 팀이 함께 이해할 수 있는 최소한의 기준을 세우고 지속적으로 소통하며 통합 가능성을 검증해 나가는 것이 더 중요하다는 점을 배운 프로젝트였습니다. |
| 이형인 | 작성예정 |

### 11. PPT 링크

PPT 링크 : 추후 첨부 예정

## 12. 현재 Atlas/Silver/MySQL 통합 파이프라인

- `src/bronze/`: Atlas 원본 보존과 `_id` 오름차순 증분 전달
- `src/silver/`: 최소 전처리와 Flat 표준화 후 누적 결과를 네 모델 snapshot으로 정규화
  - Flat 출력: `<temp-dir>/accept.csv`, `<temp-dir>/reject.csv`
  - 정규화 Reject: `<temp-dir>/normalization_reject.csv`
  - 정규화 모델: `<temp-dir>/models/silver_employee.csv`,
    `<temp-dir>/models/silver_area.csv`, `<temp-dir>/models/silver_parent_area.csv`,
    `<temp-dir>/models/silver_area_join_reference.csv`
  - 모든 Flat·정규화 출력 게시와 source accounting이 성공한 뒤에만
    `<temp-dir>/processed_ids.json` checkpoint 갱신
- `src/main.py`: Atlas→Silver가 성공하면 네 모델 CSV를 MySQL 적재기에 전달
- 실행 규칙:
  - `data-contracts/standard-term.csv`
  - `standards/area-name-normalization.csv`
  - `standards/code-normalization.yaml`

현행 표준 산출물은 모두 `standards/` 바로 아래에 두며, 통합 전 Silver v1 snapshot은
`archive/standards/silver-v1-before-v2-integration/`에 실행 표준과 분리해 보존합니다.

기본 실행은 Atlas→Silver 처리와 checkpoint 기록 후 MySQL CSV 계약만 검증하는
dry-run cycle을 즉시 한 번 실행하고, 각 cycle 시작 시각을 기준으로 `src/.env`의
`PIPELINE_INTERVAL_SECONDS` 주기마다 반복합니다. 이 값이 없으면 30초를 사용하며,
`python src/main.py`와 `python -m src.main`은 동등합니다.

```dotenv
# src/.env: 양의 정수(초)
PIPELINE_INTERVAL_SECONDS=30
```

```bash
# 기본: 즉시 시작한 뒤 src/.env의 주기마다 Atlas→Silver→MySQL CSV dry-run 반복
python src/main.py

# module 실행도 동일
python -m src.main

# 단발 dry-run
python src/main.py --once

# 최초 단발 실제 적재: 없는 테이블 생성·검증 후 전체 snapshot 교체
python src/main.py --once --init-schema --apply

# 고정 스키마가 이미 존재할 때 단발 실제 적재
python src/main.py --once --apply
```

통합 실행 로그는 실행 위치나 `--temp-dir`와 관계없이 저장소 루트의
`output/logs/pipeline.log`에 UTF-8로 누적됩니다. `INFO`는 터미널 표준 출력에,
`WARNING`·`ERROR`는 표준 오류에 표시되며 모든 level이 같은 로그 파일에도
기록됩니다. 파일은 10 MiB 단위로 회전하고 최대 5개 백업을 유지하며,
`output/logs/`는 실행 시 자동 생성되고 Git 추적에서는 제외됩니다.

```bash
tail -f output/logs/pipeline.log
```

이 통합 로그는 독립 Bronze 수집 명령의 `src/bronze/logs/crawler.log`와 별도입니다.

`--batch-size`는 Atlas 조회 batch 크기(기본 1,000), `--temp-dir`는 Silver 출력·
checkpoint·모델 디렉터리(기본 `temp`), `--chunk-size`는 MySQL insert chunk 크기
(기본 1,000)입니다. `--once`는 cycle을 한 번만 실행합니다. 반복 주기의 우선순위는
`--interval-seconds` 명시값, 프로세스 환경변수, `src/.env`의
`PIPELINE_INTERVAL_SECONDS`, 30초 순입니다.

반복 실행은 cycle을 중첩하지 않습니다. cycle이 설정 주기보다 오래 걸리면 완료 직후 다음
cycle을 시작합니다. 예외가 발생하거나 하위 단계가 nonzero를 반환하면 loop를
중단하며, `Ctrl-C`로도 종료할 수 있습니다. `--init-schema`는 첫 cycle에만 적용되지만
`--apply`는 매 cycle마다 네 테이블을 full replacement하므로 반복 적재에는 명시적인
주의가 필요합니다.

Silver checkpoint는 MySQL 단계 전에 확정됩니다. MySQL 실패 후에는 같은
`--temp-dir`로 재실행할 수 있고, 규칙 변경 등으로 전체 source를 다시 처리할 때는
새 `--temp-dir`를 사용해야 합니다. 같은 `--temp-dir`에서 두 프로세스를 동시에
실행하면 안 됩니다. 빈 모델 snapshot에 `--apply`를 사용하면 대상 테이블도 비게
됩니다.

세부 계약은 `src/bronze/README.md`와 `src/silver/README.md`를 참고합니다.
