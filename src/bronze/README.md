# Atlas Bronze 수집

`atlas_download.py`는 Atlas에서 받은 원본 문서를 수정하지 않고 append-only Bronze JSONL로 보존합니다. 수집 전에는 네트워크에 접속하지 않으며, 실행 시 안정 키 `record_id`를 전체 preflight로 검증합니다.

## 설치와 환경

프로젝트의 Conda 환경에서 고정된 의존성을 설치합니다.

```bash
python -m pip install -r requirements.txt
```

다음 읽기 전용 환경변수를 운영체제에 주입하거나 `src/.env`에 설정합니다. 값이나 연결 문자열은 저장소, 명령 이력, 로그에 넣지 않습니다.

- `MONGODB_URI`
- `MONGODB_DATABASE`
- `MONGODB_COLLECTION`

로컬 개발에서는 통합 예시 파일을 복사해 사용할 수 있습니다.

```bash
cp src/.env.example src/.env
```

모든 Bronze 실행 경로는 `src/.env` 하나만 읽습니다. 이미 운영체제에 주입된 값이
있으면 해당 값이 우선하며 `.env`가 덮어쓰지 않습니다. 실제 `.env`는
`src/.gitignore`와 저장소 루트 `.gitignore`에서 제외됩니다.

## 통합된 모듈 구성

```text
src/
├─ .env.example                  Atlas 환경변수 예시
├─ .gitignore                    src 로컬 실행 산출물 제외
└─ bronze/
   ├─ api_client.py              내부 공개 API 호출·재시도
   ├─ crawler.py                 API 원본 수집과 Atlas 증분 적재 조정
   ├─ mongo_loader.py            records.json의 record_id 증분 upsert
   ├─ config/settings.json       내부 API 수집 설정
   ├─ requirements.txt           Bronze 실행 의존성 subset
   ├─ atlas_download.py          record_id 기반 append-only Atlas 수집
   ├─ atlas_pipeline.py          Silver용 _id 증분 전달
   ├─ raw_store.py               raw JSONL·manifest 보존
   └─ atlas_snapshot_export.py   선택적 Atlas JSON snapshot 내보내기
```

기존 `bronze/requirements.txt`도 `src/bronze/requirements.txt`로 이동했습니다.
프로젝트 전체 실행에는 이 두 의존성을 포함해 버전이 고정된 루트
`requirements.txt`를 사용합니다.

## 내부 API 수집과 Atlas 적재

내부 공개 API에서 cursor 기준으로 원본을 수집하고 Atlas에 적재하려면 저장소
루트에서 다음 모듈을 실행합니다.

```bash
python -m src.bronze.crawler
```

설정은 `src/bronze/config/settings.json`, 접속 정보는 `src/.env`에서 읽습니다.
API 응답 바이트와 체크섬을 보존하고 `src/bronze/data/records.json`을 누적한 뒤,
`record_id` 기준 Atlas upsert가 성공해야만 cursor를 전진시킵니다.

```text
src/bronze/data/bronze/internal-api/ingest_date=YYYY-MM-DD/run_id=.../raw/
src/bronze/data/manifests/<run_id>.json
src/bronze/data/quarantine/run_id=.../error.json
src/bronze/state/cursor_state.json
src/bronze/state/mongo_state.json
src/bronze/logs/crawler.log
```

Atlas 컬렉션을 일회성 JSON으로 확인해야 하는 기존 기능은 정본
`atlas_download.py`와 이름이 겹치지 않도록 아래 경로로 보존했습니다.

```bash
python -m src.bronze.atlas_snapshot_export
```

이 명령은 `_id`를 제외한 snapshot을 `src/bronze/data/atlas_records.json`에
저장합니다. append-only Bronze 보존과 재시작 계약이 필요한 실행은 아래
`src.bronze.atlas_download`를 사용합니다.

## 한 batch 수집

기본 `--limit`은 1000입니다. `dataset_id`, `snapshot_id`, `run_id`, `batch_sequence`은 append-only artifact 위치를 결정하는 수집 metadata입니다.

```bash
python -m src.bronze.atlas_download \
  --dataset-id hr \
  --snapshot-id snapshot-20260828 \
  --run-id run-001 \
  --batch-sequence 1 \
  --limit 1000
```

기존 `main()`은 위와 같이 한 batch를 append-only Bronze로 보존합니다.
`AtlasSourceReader.iter_batches(limit=n)`도 기존 `record_id` cursor 기반 전체 순회
API로 유지합니다. 현재 Silver 증분 실행은 이 API를 바꾸지 않고 아래의 별도
`AtlasIncrementalPipeline`을 사용합니다.

`src.bronze` 패키지의 공개 클래스는 사용할 때 소유 모듈을 지연 로딩합니다. 따라서
`python -m src.bronze.atlas_download` 실행 전에 같은 모듈을 선행 import하지 않으며,
기존 `from src.bronze import AtlasSettings` 공개 경로도 유지합니다.

```bash
python -m src.silver.flat_pipeline --batch-size 1000 --temp-dir temp
```

Silver 실행 결과는 `temp/accept.csv`와 `temp/reject.csv`이며, 세부 정규화·멱등성
규칙은 `src/silver/README.md`에 정리되어 있습니다.

## Silver 처리 확인 기반 증분 전달

`atlas_pipeline.py`의 `AtlasIncrementalPipeline`은 기존 `AtlasSettings`와 lazy Atlas
client 생성 로직을 재사용하면서, MongoDB `_id` 오름차순으로 아직 처리되지 않은
원본을 전달합니다. 표준 `ObjectId`를 사용하는 source에서는 이 순서가 생성 시각이
오래된 문서부터의 순서입니다. 이 경로는 기존 append-only raw artifact 수집을
대체하거나 그 checksum/cursor 계약을 변경하지 않습니다.

원본 전체를 하나의 cursor로 읽어 Python에서만 나누지 않습니다. 각 physical page는
MongoDB에 `_id > 직전 page의 마지막 _id`, `_id` 오름차순, `limit=n`을 요청합니다.
처리 완료 ID 때문에 한 physical page의 미처리 건수가 부족하면 다음 keyset page를
계속 읽어 Silver에 전달하는 batch를 최대 n건까지 채웁니다.

Silver가 사용하는 공개 API는 다음과 같습니다.

```python
from pathlib import Path

from src.bronze.atlas_download import AtlasSettings
from src.bronze.atlas_pipeline import AtlasIncrementalPipeline

settings = AtlasSettings.from_environment()
pipeline = AtlasIncrementalPipeline(
    settings,
    processed_ids_path=Path("temp/processed_ids.json"),
)

try:
    for batch in pipeline.iter_batches(limit=1000):
        records = batch.records
        # Silver가 records 처리를 성공으로 확정한 뒤에만 호출합니다.
        pipeline.mark_processed(record["_id"] for record in records)
finally:
    pipeline.close()
```

- `iter_batches(limit: int)`는 nonempty `AtlasIncrementalBatch`를 반환하며,
  `batch.records`는 원본 mapping과 `_id`를 변경하지 않은 tuple입니다. `limit`은
  bool이 아닌 1 이상의 정수여야 합니다. 각 full-record keyset 조회에도 동일한
  server-side limit을 적용합니다.
- 처리 완료 `_id`가 source 중간에 있어도 정렬 cursor의 후속 문서를 계속 읽어
  마지막 batch 전까지 미처리 레코드 `limit`건을 채웁니다. source가 소진된 마지막
  batch만 `limit`보다 작을 수 있습니다.
- 읽거나 batch를 반환하는 것만으로 처리 상태는 전진하지 않습니다. 성공한 batch의
  source `_id`를 `mark_processed(source_ids)`에 명시적으로 전달한 뒤에만 다음
  실행에서 제외됩니다.
- 처리 ID 상태는 JSON 문자열 배열로 저장됩니다. 중복을 제거한 사전순 전체 상태를
  같은 디렉터리의 임시 파일에 기록한 뒤 원자적으로 교체합니다. 중복 mark는 파일을
  다시 쓰지 않는 멱등 연산입니다.
- 상태 파일이 없으면 처음부터 시작합니다. JSON 파싱, 문자열 타입, 중복 또는 정렬
  계약이 손상된 상태 파일은 Atlas client를 만들기 전에 fail-closed로 중단합니다.
- paginated 원본 조회 전에 전체 `_id` projection을 검사합니다. `_id` 누락 또는
  정확한 Python 타입이 둘 이상 섞인 source는 안전한 정렬·진행을 보장할 수 없으므로
  fail-closed로 중단합니다. 반환할 때는 raw record와 `_id` 값·타입을 그대로
  보존합니다.
- 빈 source 또는 모든 `_id`가 이미 처리된 source는 batch를 반환하지 않습니다.
- client 종료 책임은 호출자에게 있으며 `close()`는 pipeline이 만든 client를 닫습니다.

I1의 안정 키 계약은 승인된 `record_id` 양의 정수 하나로 고정됩니다. alternate stable key 옵션은 제공하지 않습니다. cross-process 재시작에는 직접 cursor 값이나 hash를 입력하지 않습니다. 이전 finalized manifest 경로를 `--resume-manifest`로 전달하면, CLI가 대응 raw JSONL의 checksum·collection metadata·dataset/snapshot/run 경계·실제 마지막 `record_id`를 검증한 뒤에만 manifest가 생성한 `resume_cursor`를 복원합니다.

```bash
python -m src.bronze.atlas_download \
  --dataset-id hr \
  --snapshot-id snapshot-20260828 \
  --run-id run-001 \
  --batch-sequence 2 \
  --resume-manifest data/runs/hr/snapshot-20260828/run-001/bronze/manifests/1.json
```

성공한 batch는 다음 두 finalized artifact를 만듭니다.

```text
data/runs/{dataset_id}/{snapshot_id}/{run_id}/bronze/batches/{batch_sequence}.jsonl
data/runs/{dataset_id}/{snapshot_id}/{run_id}/bronze/manifests/{batch_sequence}.json
```

JSONL에는 MongoDB `_id`를 포함한 모든 원본 필드가 BSON-safe Extended JSON으로 보존됩니다. manifest에는 행 수, raw bytes SHA-256, 컬렉션 식별 metadata, 행별 파일 위치 locator와 실제 다음 batch의 `resume_cursor` provenance를 기록합니다. provenance에는 전체 정렬 stable-key set fingerprint와 cursor 위치까지의 prefix fingerprint가 함께 들어갑니다. raw와 manifest는 같은 filesystem staging에서 완성한 뒤 overwrite 없는 pair publication으로 공개됩니다. 같은 batch sequence의 JSONL 또는 manifest가 이미 있으면 덮어쓰지 않고 실패하며, 한쪽만 남은 artifact는 안전한 복구 전까지 재시도를 차단합니다.

## 수집 게이트와 제한

- `record_id`는 모든 문서에 있어야 하고, 양의 정수·고유·오름차순이어야 합니다. reader는 batch query 직전과 candidate read 직후에 전체 source keyset을 preflight하고 구조·전체 fingerprint를 비교합니다. 누락, null/빈 값, 중복, 타입 혼합, 정렬 불가, legacy provenance 없는 cursor, 전체/prefix fingerprint 불일치 또는 pre/post source 변경은 raw 값을 출력하지 않고 fail-closed로 중단합니다.
- `atlas_download.py`와 `raw_store.py`는 원본 Bronze 수집·보존을 담당합니다.
  `atlas_pipeline.py`는 원본을 변환하지 않고 Silver 전달과 processed-ID 확인만
  담당합니다. 어느 경로도 `payload`, release 필드, `source_record_sha256` 같은
  Silver transport 필드를 만들지 않습니다.
- `AtlasIncrementalPipeline`은 2,505건 fake에서 server-side `limit=1000`을 적용한
  `1000/1000/505` keyset page와 batch 경계로 검증했습니다. 기존
  `AtlasSourceReader`와 `atlas_download.main()`의 append-only 회귀 테스트도 별도로
  유지합니다. MySQL 적재는 이번 범위에 포함하지 않았습니다.
