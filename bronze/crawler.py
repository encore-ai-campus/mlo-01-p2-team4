"""API 크롤링, JSON 누적, 로그 기록과 MongoDB Atlas 적재를 수행합니다."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from api_client import ApiClient


BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "config" / "settings.json"
STATE_PATH = BASE_DIR / "state" / "cursor_state.json"
DATA_DIR = BASE_DIR / "data"
RECORDS_PATH = DATA_DIR / "records.json"


def load_settings() -> dict[str, Any]:
    """설정 파일을 읽습니다."""
    with SETTINGS_PATH.open("r", encoding="utf-8") as file:
        settings = json.load(file)
    if not isinstance(settings, dict):
        raise ValueError("설정 파일은 JSON 객체여야 합니다.")
    return settings


def configure_logging() -> None:
    """콘솔과 logs/crawler.log에 실행 로그를 기록합니다."""
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "crawler.log", encoding="utf-8"),
        ],
    )


def load_state() -> dict[str, Any]:
    """마지막 성공 cursor를 읽습니다."""
    if not STATE_PATH.exists():
        return {"cursor": None}
    with STATE_PATH.open("r", encoding="utf-8") as file:
        state = json.load(file)
    if not isinstance(state, dict):
        raise ValueError("cursor 상태 파일은 JSON 객체여야 합니다.")
    return state


def save_state(state: dict[str, Any]) -> None:
    """상태를 임시 파일에 기록한 뒤 교체합니다."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = STATE_PATH.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    temporary_path.replace(STATE_PATH)


def get_items(payload: dict[str, Any]) -> list[Any]:
    """응답의 items 배열을 검증합니다."""
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError("records 응답의 items는 배열이어야 합니다.")
    return items


def append_records(payload: dict[str, Any]) -> Path:
    """items를 data/records.json에 중복 없이 추가합니다."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    records: list[Any] = []
    if RECORDS_PATH.exists():
        with RECORDS_PATH.open("r", encoding="utf-8") as file:
            existing = json.load(file)
        if not isinstance(existing, list):
            raise ValueError("data/records.json은 JSON 배열이어야 합니다.")
        records = existing

    existing_ids = {
        record.get("record_id")
        for record in records
        if isinstance(record, dict) and record.get("record_id") is not None
    }
    for item in get_items(payload):
        if isinstance(item, dict) and item.get("record_id") in existing_ids:
            continue
        records.append(item)
        if isinstance(item, dict) and item.get("record_id") is not None:
            existing_ids.add(item.get("record_id"))

    temporary_path = RECORDS_PATH.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(records, file, ensure_ascii=False, indent=2)
    temporary_path.replace(RECORDS_PATH)
    return RECORDS_PATH


def run_mongo_loader() -> None:
    """JSON 저장 후 MongoDB Atlas 적재 모듈을 실행합니다."""
    loader_path = BASE_DIR / "mongo_loader.py"
    result = subprocess.run(
        [sys.executable, str(loader_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="mbcs",
        errors="replace",
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if stdout.strip():
        logging.info("MongoDB 적재 출력: %s", stdout.strip())
    if result.returncode != 0:
        error = stderr.strip() or stdout.strip() or "원인 메시지가 없습니다."
        raise RuntimeError(f"MongoDB 적재 실패: {error}")


def collect() -> None:
    """현재 공개된 페이지를 수집하고 Atlas 적재 성공 후 cursor를 저장합니다."""
    settings = load_settings()
    base_url = settings.get("base_url")
    limit = settings.get("records_limit", 1000)
    if not isinstance(base_url, str) or not base_url:
        raise ValueError("settings.json의 base_url이 올바르지 않습니다.")
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("records_limit은 1 이상 1000 이하의 정수여야 합니다.")

    client = ApiClient(base_url)
    state = load_state()
    cursor = state.get("cursor")
    api_key = client.fetch_api_key()
    meta = client.fetch_meta(api_key)
    logging.info("현재 공개 행 수: %s", meta.get("released_rows"))

    while True:
        payload = client.fetch_records(api_key, cursor, limit)
        items = get_items(payload)
        next_cursor = payload.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            raise ValueError("records 응답에 유효한 next_cursor가 없습니다.")

        if not items:
            save_state({
                "cursor": cursor,
                "released_rows": meta.get("released_rows"),
                "next_refresh_at": meta.get("next_refresh_at"),
                "last_success": True,
            })
            logging.info("새로운 데이터가 없어 수집을 종료합니다.")
            return

        path = append_records(payload)
        logging.info("%d건 저장: %s", len(items), path)
        run_mongo_loader()
        cursor = next_cursor
        save_state({
            "cursor": cursor,
            "released_rows": meta.get("released_rows"),
            "next_refresh_at": meta.get("next_refresh_at"),
            "last_success": True,
        })


def main() -> None:
    """로깅을 설정하고 수집을 실행합니다."""
    configure_logging()
    try:
        collect()
    except Exception:
        logging.exception("수집에 실패했습니다. 마지막 성공 cursor는 유지됩니다.")
        raise


if __name__ == "__main__":
    main()

