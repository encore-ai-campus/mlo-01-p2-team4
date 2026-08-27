"""data/records.json의 신규 레코드를 MongoDB Atlas에 적재합니다."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pymongo import MongoClient, UpdateOne


BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "records.json"
STATE_PATH = BASE_DIR / "state" / "mongo_state.json"


def load_dotenv(path: Path) -> None:
    """간단한 KEY=VALUE 형식의 .env 파일을 읽습니다."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config() -> tuple[str, str, str]:
    """MongoDB Atlas 접속 설정을 읽고 필수값을 검증합니다."""
    load_dotenv(BASE_DIR / ".env")
    values = (
        os.getenv("MONGODB_URI"),
        os.getenv("MONGODB_DATABASE"),
        os.getenv("MONGODB_COLLECTION"),
    )
    if any(not value for value in values):
        raise ValueError(".env에 MONGODB_URI, MONGODB_DATABASE, MONGODB_COLLECTION을 모두 설정하세요.")
    return values[0], values[1], values[2]  # type: ignore[return-value]


def load_records() -> list[dict[str, Any]]:
    """records.json을 읽고 객체 배열인지 검증합니다."""
    with DATA_PATH.open("r", encoding="utf-8-sig") as file:
        records = json.load(file)
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise ValueError("records.json은 JSON 객체 배열이어야 합니다.")
    return records


def load_state() -> int | None:
    """마지막 성공 적재 record_id를 읽습니다."""
    if not STATE_PATH.exists():
        return None
    with STATE_PATH.open("r", encoding="utf-8") as file:
        state = json.load(file)
    value = state.get("last_loaded_record_id") if isinstance(state, dict) else None
    return int(value) if value is not None else None


def save_state(last_record_id: int) -> None:
    """Atlas 적재 성공 후 상태를 안전하게 저장합니다."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = STATE_PATH.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump({"last_loaded_record_id": last_record_id}, file, indent=2)
    temporary_path.replace(STATE_PATH)


def select_new_records(
    records: list[dict[str, Any]],
    last_record_id: int | None,
) -> list[dict[str, Any]]:
    """마지막 적재 ID보다 큰 레코드만 선택합니다."""
    return [
        record
        for record in records
        if isinstance(record.get("record_id"), int)
        and (last_record_id is None or record["record_id"] > last_record_id)
    ]


def upsert_records(
    uri: str,
    database: str,
    collection_name: str,
    records: list[dict[str, Any]],
) -> int:
    """record_id를 기준으로 MongoDB Atlas에 upsert합니다."""
    if not records:
        return 0
    client = MongoClient(uri, serverSelectionTimeoutMS=30000)
    try:
        collection = client[database][collection_name]
        collection.create_index("record_id", unique=True)
        operations = [
            UpdateOne({"record_id": record["record_id"]}, {"$set": record}, upsert=True)
            for record in records
        ]
        collection.bulk_write(operations, ordered=True)
        return len(records)
    finally:
        client.close()


def main() -> None:
    """신규 레코드를 Atlas에 적재합니다."""
    uri, database, collection = load_config()
    records = load_records()
    new_records = select_new_records(records, load_state())
    if not new_records:
        print("새로 적재할 데이터가 없습니다.")
        return
    loaded_count = upsert_records(uri, database, collection, new_records)
    new_last_id = max(record["record_id"] for record in new_records)
    save_state(new_last_id)
    print(f"MongoDB 적재 완료: {loaded_count}건, 마지막 record_id: {new_last_id}")


if __name__ == "__main__":
    main()

