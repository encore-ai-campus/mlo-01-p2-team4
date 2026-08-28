"""Atlas records 컬렉션을 일회성 로컬 JSON snapshot으로 내보낸다."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from .environment import ENV_PATH, load_dotenv


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR / "data" / "atlas_records.json"


def load_config() -> tuple[str, str, str]:
    """Atlas 접속 설정을 읽고 필수값을 검증합니다."""
    load_dotenv()
    values = (
        os.getenv("MONGODB_URI"),
        os.getenv("MONGODB_DATABASE"),
        os.getenv("MONGODB_COLLECTION"),
    )
    if any(not value for value in values):
        raise ValueError(
            f"{ENV_PATH} 또는 실행 환경에 MONGODB_URI, MONGODB_DATABASE, "
            "MONGODB_COLLECTION을 모두 설정하세요."
        )
    return values[0], values[1], values[2]  # type: ignore[return-value]


def download_records(
    uri: str,
    database_name: str,
    collection_name: str,
) -> list[dict[str, Any]]:
    """Atlas 컬렉션의 문서를 조회하고 내부 _id는 제외합니다."""
    client = MongoClient(uri, serverSelectionTimeoutMS=30000)
    try:
        collection = client[database_name][collection_name]
        return list(collection.find({}, {"_id": 0}))
    finally:
        client.close()


def save_records(records: list[dict[str, Any]]) -> None:
    """조회한 레코드를 임시 파일을 거쳐 저장합니다."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = OUTPUT_PATH.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(records, file, ensure_ascii=False, indent=2)
    temporary_path.replace(OUTPUT_PATH)


def main() -> None:
    """Atlas 데이터를 조회하고 로컬 JSON 파일로 저장합니다."""
    uri, database, collection = load_config()
    records = download_records(uri, database, collection)
    save_records(records)
    print(f"다운로드 완료: {len(records)}건")
    print(f"저장 위치: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
