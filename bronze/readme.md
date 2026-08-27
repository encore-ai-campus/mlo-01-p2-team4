# Bronze 크롤러 사용 안내

이 폴더는 내부 API의 새 데이터를 받아 원본으로 보관하고 MongoDB Atlas에 적재합니다.

Bronze에서는 컬럼명 변경, 타입 변환, 조인과 같은 데이터 가공을 하지 않습니다. API가 보낸 원본과 수집 이력을 그대로 남기는 것이 목적입니다.

Atlas 컬렉션의 데이터를 다시 내려받는 `atlas_download.py`는 Bronze 기능이 아니므로 이 버전에서는 제외합니다. 해당 기능은 Silver 단계에서 진행합니다.

## 실행 흐름

```text
API 키 확인
→ cursor 기준으로 데이터 조회
→ API 원본 파일 저장
→ 파일 크기·SHA-256 기록
→ records.json에 추가
→ MongoDB Atlas upsert
→ 성공한 cursor 저장
→ manifest·로그 저장 후 종료
```

## 파일 설명

```text
ver.5/
├─ api_client.py       API 요청·재시도
├─ crawler.py          전체 수집
├─ mongo_loader.py     MongoDB 적재
├─ .env.example        접속 설정 예시
├─ requirements.txt    Python 의존성
├─ config/settings.json API 설정
├─ readme.md           Bronze 사용 안내
└─ .gitignore          비밀값·실행 산출물 제외
```

## 처음 설정

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`에 MongoDB Atlas 정보를 입력합니다.

```env
MONGODB_URI=mongodb+srv://...
MONGODB_DATABASE=internal_data
MONGODB_COLLECTION=records
```

`.env`에는 비밀번호가 있으므로 공유하거나 Git에 올리면 안 됩니다. API 키는 실행할 때 `/public/v1/key`에서 가져옵니다.

## 실행

```powershell
python crawler.py
```

1. API 키와 현재 공개 행 수를 확인합니다.
2. 저장된 cursor 다음부터 페이지를 조회합니다.
3. 원본을 `data/bronze/`에 저장합니다.
4. `data/records.json`에 중복 없이 추가합니다.
5. `record_id` 기준으로 MongoDB Atlas에 upsert합니다.
6. MongoDB 적재 성공 후 cursor를 저장합니다.
7. manifest를 만들고 종료합니다.

## 실행 후 확인

```text
data/bronze/internal-api/ingest_date=YYYY-MM-DD/run_id=.../raw/
data/manifests/<run_id>.json
data/quarantine/run_id=.../error.json
state/cursor_state.json
state/mongo_state.json
logs/crawler.log
```

원본은 API 응답 바이트 그대로 저장합니다. manifest에는 레코드 수, 요청·실패 횟수, 성공률, 응답 시간, HTTP 상태 분포와 파일별 체크섬을 기록합니다.

연결·응답 timeout, 429와 5xx는 최대 3회 재시도합니다. 인증 오류와 영구적인 4xx는 바로 실패합니다. MongoDB 적재가 성공하기 전에는 cursor를 갱신하지 않습니다.

## 현재 범위

이 버전은 API 원본 수집, Bronze 저장, manifest·체크섬 기록, `records.json` 누적과 MongoDB Atlas 적재까지만 담당합니다.

표준화·전처리·조인·집계·AI용 파생 컬럼 생성과 Atlas 데이터 다운로드는 Silver 단계의 범위입니다.

