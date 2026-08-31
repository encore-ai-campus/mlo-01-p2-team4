"""Bronze 원본 보존, cursor 증분 수집과 MongoDB Atlas 적재를 수행합니다."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .api_client import ApiClient, ApiPayloadError, ApiRequestError


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent
SETTINGS_PATH = BASE_DIR / "config" / "settings.json"
STATE_PATH = BASE_DIR / "state" / "cursor_state.json"
DATA_DIR = BASE_DIR / "data"
RECORDS_PATH = DATA_DIR / "records.json"
BRONZE_DIR = DATA_DIR / "bronze"
MANIFEST_DIR = DATA_DIR / "manifests"
QUARANTINE_DIR = DATA_DIR / "quarantine"
CHECKSUM_REGISTRY_PATH = MANIFEST_DIR / "checksum_registry.json"


def load_settings() -> dict[str, Any]:
    """설정 파일을 읽고 Bronze 설정을 반환합니다."""
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
    """저장된 cursor 상태를 읽습니다."""
    if not STATE_PATH.exists():
        return {"cursor": None}
    with STATE_PATH.open("r", encoding="utf-8") as file:
        state = json.load(file)
    if not isinstance(state, dict):
        raise ValueError("cursor 상태 파일은 JSON 객체여야 합니다.")
    return state


def save_state(state: dict[str, Any]) -> None:
    """상태를 임시 파일에 쓴 뒤 원자적으로 교체합니다."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = STATE_PATH.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    temporary_path.replace(STATE_PATH)


def get_items(payload: dict[str, Any]) -> list[Any]:
    """API 응답의 items가 배열인지 검증합니다."""
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError("records 응답의 items는 배열이어야 합니다.")
    return items


def start_request(run: dict[str, Any]) -> float:
    """요청 횟수를 증가시키고 시작 시간을 반환합니다."""
    run["metrics"]["request_count"] += 1
    return time.perf_counter()


def finish_request(run: dict[str, Any], started_at: float, success: bool) -> float:
    """요청 결과와 응답 시간을 실행 지표에 반영합니다."""
    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
    metrics = run["metrics"]
    if not success:
        metrics["failed_request_count"] += 1
    metrics["response_times_ms"].append(elapsed_ms)
    return elapsed_ms


def update_request_metadata(run: dict[str, Any], client: ApiClient) -> None:
    """마지막 상태 코드와 재시도 횟수를 실행 지표에 반영합니다."""
    metadata = client.last_request_metadata
    run["metrics"]["retry_count"] += int(metadata.get("retry_count", 0))
    status_code = metadata.get("status_code")
    if status_code is not None:
        counts = run["metrics"]["http_status_counts"]
        key = str(status_code)
        counts[key] = counts.get(key, 0) + 1


def append_records(payload: dict[str, Any]) -> Path:
    """items를 호환용 records.json 배열에 중복 없이 추가합니다."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    records: list[Any] = []
    if RECORDS_PATH.exists():
        with RECORDS_PATH.open("r", encoding="utf-8-sig") as file:
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


def mask_source_uri(source_uri: str) -> str:
    """로그와 manifest에 남기는 URL에서 cursor 값을 마스킹합니다."""
    return re.sub(r"([?&]cursor=)[^&]+", r"\1[MASKED]", source_uri)


def load_checksum_registry() -> dict[str, Any]:
    """checksum registry를 읽고 손상 시 manifest로 복구합니다."""
    if not CHECKSUM_REGISTRY_PATH.exists():
        return {}
    try:
        with CHECKSUM_REGISTRY_PATH.open("r", encoding="utf-8") as file:
            registry = json.load(file)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return recover_checksum_registry(f"JSON을 읽을 수 없습니다: {error}")
    if not isinstance(registry, dict):
        return recover_checksum_registry("checksum registry가 JSON 객체가 아닙니다.")
    return registry


def rebuild_checksum_registry() -> dict[str, Any]:
    """기존 manifest의 페이지 체크섬으로 registry를 재구성합니다."""
    registry: dict[str, Any] = {}
    for manifest_path in MANIFEST_DIR.glob("*.json"):
        if (
            manifest_path.name == CHECKSUM_REGISTRY_PATH.name
            or manifest_path.name.startswith(f"{CHECKSUM_REGISTRY_PATH.stem}.corrupt.")
        ):
            continue
        try:
            with manifest_path.open("r", encoding="utf-8") as file:
                manifest = json.load(file)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            logging.warning("manifest를 checksum 복구에 사용할 수 없습니다: %s (%s)", manifest_path, error)
            continue
        if not isinstance(manifest, dict):
            continue
        pages = manifest.get("files", [])
        if not isinstance(pages, list):
            continue
        for page in pages:
            if not isinstance(page, dict):
                continue
            checksum = page.get("checksum_sha256")
            raw_path = page.get("raw_path")
            if not isinstance(checksum, str) or not checksum:
                continue
            if not isinstance(raw_path, str) or not raw_path:
                continue
            if checksum in registry:
                continue
            registry[checksum] = {
                "run_id": manifest.get("run_id"),
                "raw_path": raw_path,
                "ingest_date": manifest.get("ingest_date"),
            }
    return registry


def save_checksum_registry(registry: dict[str, Any]) -> None:
    """checksum registry를 고유 임시 파일을 거쳐 교체합니다."""
    CHECKSUM_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = CHECKSUM_REGISTRY_PATH.with_name(
        f".{CHECKSUM_REGISTRY_PATH.stem}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(registry, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        temporary_path.replace(CHECKSUM_REGISTRY_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)


def recover_checksum_registry(reason: str) -> dict[str, Any]:
    """손상된 registry를 백업하고 manifest 기준으로 복구합니다."""
    backup_path = CHECKSUM_REGISTRY_PATH.with_name(
        f"{CHECKSUM_REGISTRY_PATH.stem}.corrupt.{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')}-{uuid.uuid4().hex[:8]}.json"
    )
    try:
        shutil.copy2(CHECKSUM_REGISTRY_PATH, backup_path)
        logging.warning("손상된 checksum registry를 백업했습니다: %s", backup_path)
    except OSError as error:
        logging.warning("손상된 checksum registry 백업에 실패했습니다: %s", error)
    registry = rebuild_checksum_registry()
    save_checksum_registry(registry)
    logging.warning("checksum registry를 기존 manifest 기준으로 복구했습니다: %d건 (%s)", len(registry), reason)
    return registry


def register_checksum(page_info: dict[str, Any], run: dict[str, Any]) -> None:
    """checksum 중복을 표시하고 신규 checksum을 registry에 추가합니다."""
    registry = load_checksum_registry()
    checksum = page_info["checksum_sha256"]
    previous = registry.get(checksum)
    if isinstance(previous, dict):
        page_info["duplicate"] = True
        page_info["duplicate_of_run_id"] = previous.get("run_id")
        page_info["duplicate_of_raw_path"] = previous.get("raw_path")
        return
    page_info["duplicate"] = False
    registry[checksum] = {
        "run_id": run["run_id"],
        "raw_path": page_info["raw_path"],
        "ingest_date": run["ingest_date"],
    }
    save_checksum_registry(registry)


def run_mongo_loader() -> None:
    """MongoDB Atlas 적재 모듈을 실행하고 실패 시 예외를 발생시킵니다."""
    result = subprocess.run(
        [sys.executable, "-m", "src.bronze.mongo_loader"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if stdout.strip():
        logging.info("MongoDB 적재 출력: %s", stdout.strip())
    if result.returncode != 0:
        error = stderr.strip() or stdout.strip() or "원인 메시지가 없습니다."
        raise RuntimeError(f"MongoDB 적재 실패: {error}")


def create_run_context(settings: dict[str, Any]) -> dict[str, Any]:
    """실행 식별자와 Bronze 파티션 경로를 생성합니다."""
    started_at = datetime.now().astimezone()
    ingest_date = started_at.date().isoformat()
    run_id = f"{started_at.strftime('%Y%m%dT%H%M%S%z')}-{uuid.uuid4().hex[:8]}"
    source_name = str(settings.get("source_name", "internal-api"))
    raw_dir = BRONZE_DIR / source_name / f"ingest_date={ingest_date}" / f"run_id={run_id}" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    return {
        "run_id": run_id,
        "source_name": source_name,
        "source_uri": f"{str(settings['base_url']).rstrip('/')}/api/v1/records",
        "ingest_date": ingest_date,
        "raw_dir": raw_dir,
        "crawler_version": str(settings.get("crawler_version", "bronze-v1")),
        "started_at": started_at,
        "aggregate_hash": hashlib.sha256(),
        "metrics": {
            "request_count": 0,
            "failed_request_count": 0,
            "response_times_ms": [],
            "record_count": 0,
            "retry_count": 0,
            "http_status_counts": {},
        },
    }


def save_raw_page(raw_bytes: bytes, run: dict[str, Any], page_number: int) -> dict[str, Any]:
    """API 원본 바이트를 페이지 파일로 저장합니다."""
    file_path = run["raw_dir"] / f"records_page_{page_number:04d}.json"
    with file_path.open("xb") as file:
        file.write(raw_bytes)
    return {
        "raw_path": file_path.relative_to(BASE_DIR).as_posix(),
        "content_type": "application/json",
        "file_size_bytes": len(raw_bytes),
        "checksum_sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }


def write_manifest(
    run: dict[str, Any],
    pages: list[dict[str, Any]],
    status: str,
    error: str | None = None,
    failure_target: dict[str, Any] | None = None,
) -> Path:
    """실행별 Bronze 전달 manifest를 생성합니다."""
    total_bytes = sum(page["file_size_bytes"] for page in pages)
    statuses = [page.get("http_status") for page in pages if page.get("http_status") is not None]
    content_types = {page.get("content_type") for page in pages if page.get("content_type")}
    metrics = run["metrics"]
    request_count = metrics["request_count"]
    manifest: dict[str, Any] = {
        "run_id": run["run_id"],
        "source_name": run["source_name"],
        "source_uri": run["source_uri"],
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ingest_date": run["ingest_date"],
        "raw_path": run["raw_dir"].relative_to(BASE_DIR).as_posix(),
        "content_type": next(iter(content_types)) if len(content_types) == 1 else "mixed",
        "file_size_bytes": total_bytes,
        "checksum_sha256": run["aggregate_hash"].hexdigest(),
        "http_status": statuses[-1] if statuses else None,
        "retry_count": metrics["retry_count"],
        "crawler_version": run["crawler_version"],
        "status": status,
        "record_count": metrics["record_count"],
        "request_count": request_count,
        "failed_request_count": metrics["failed_request_count"],
        "success_rate": round((request_count - metrics["failed_request_count"]) / request_count, 4) if request_count else 0,
        "total_response_time_ms": round(sum(metrics["response_times_ms"]), 2),
        "average_response_time_ms": round(sum(metrics["response_times_ms"]) / len(metrics["response_times_ms"]), 2) if metrics["response_times_ms"] else 0,
        "http_status_counts": metrics["http_status_counts"],
        "files": pages,
    }
    if error:
        manifest["error"] = error
    if failure_target:
        manifest["failure_target"] = failure_target
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = MANIFEST_DIR / f"{run['run_id']}.json"
    with manifest_path.open("x", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
    return manifest_path


def write_quarantine(
    run: dict[str, Any],
    error: Exception,
    status: str,
    failure_target: dict[str, Any] | None = None,
) -> Path:
    """실패 실행의 원인과 재처리 대상을 quarantine에 기록합니다."""
    quarantine_dir = QUARANTINE_DIR / f"run_id={run['run_id']}"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    error_path = quarantine_dir / "error.json"
    with error_path.open("x", encoding="utf-8") as file:
        payload: dict[str, Any] = {
            "run_id": run["run_id"],
            "status": status,
            "error": str(error),
        }
        if failure_target:
            payload["failed_targets"] = [failure_target]
        json.dump(payload, file, ensure_ascii=False, indent=2)
    return error_path


def collect() -> None:
    """현재 공개된 모든 페이지를 Bronze에 저장하고 성공 시 종료합니다."""
    settings = load_settings()
    base_url = settings.get("base_url")
    limit = settings.get("records_limit", 1000)
    if not isinstance(base_url, str) or not base_url:
        raise ValueError("settings.json의 base_url이 올바르지 않습니다.")
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("records_limit은 1 이상 1000 이하의 정수여야 합니다.")

    run = create_run_context(settings)
    pages: list[dict[str, Any]] = []
    client = ApiClient(base_url)
    cursor = load_state().get("cursor")
    page_number = 1
    current_target: dict[str, Any] | None = None

    try:
        current_target = {"source_uri": f"{base_url.rstrip('/')}/public/v1/key", "page_number": None}
        request_started = start_request(run)
        try:
            api_key = client.fetch_api_key()
        except Exception:
            update_request_metadata(run, client)
            finish_request(run, request_started, False)
            raise
        update_request_metadata(run, client)
        finish_request(run, request_started, True)

        current_target = {"source_uri": f"{base_url.rstrip('/')}/api/v1/meta", "page_number": None}
        request_started = start_request(run)
        try:
            meta = client.fetch_meta(api_key)
        except Exception:
            update_request_metadata(run, client)
            finish_request(run, request_started, False)
            raise
        update_request_metadata(run, client)
        finish_request(run, request_started, True)
        logging.info("run_id=%s 현재 공개 행 수: %s", run["run_id"], meta.get("released_rows"))

        while True:
            current_target = {
                "source_uri": mask_source_uri(f"{base_url.rstrip('/')}/api/v1/records?limit={limit}"),
                "page_number": page_number,
            }
            request_started = start_request(run)
            try:
                payload, raw_bytes, http_status, content_type, retry_count, source_uri = client.fetch_records_with_metadata(api_key, cursor, limit)
            except ApiPayloadError as error:
                update_request_metadata(run, client)
                response_time_ms = finish_request(run, request_started, True)
                page_info = save_raw_page(error.raw_bytes, run, page_number)
                page_info.update({
                    "http_status": error.status_code,
                    "content_type": error.content_type,
                    "retry_count": error.retry_count,
                    "source_uri": mask_source_uri(error.url),
                    "parse_error": True,
                    "response_time_ms": response_time_ms,
                })
                run["aggregate_hash"].update(error.raw_bytes)
                pages.append(page_info)
                register_checksum(page_info, run)
                raise
            except Exception:
                update_request_metadata(run, client)
                finish_request(run, request_started, False)
                raise

            update_request_metadata(run, client)
            response_time_ms = finish_request(run, request_started, True)
            run["aggregate_hash"].update(raw_bytes)
            page_info = save_raw_page(raw_bytes, run, page_number)
            page_info.update({
                "http_status": http_status,
                "content_type": content_type,
                "retry_count": retry_count,
                "source_uri": mask_source_uri(source_uri),
                "response_time_ms": response_time_ms,
            })
            pages.append(page_info)
            register_checksum(page_info, run)
            items = get_items(payload)
            next_cursor = payload.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise ValueError("records 응답에 유효한 next_cursor가 없습니다.")

            if items:
                run["metrics"]["record_count"] += len(items)
                path = append_records(payload)
                logging.info("run_id=%s %d건 저장: %s", run["run_id"], len(items), path)
                run_mongo_loader()
                cursor = next_cursor
                save_state({
                    "cursor": cursor,
                    "released_rows": meta.get("released_rows"),
                    "next_refresh_at": meta.get("next_refresh_at"),
                    "last_success": True,
                })
                page_number += 1
                continue

            logging.info("run_id=%s items가 비어 있어 이번 실행을 종료합니다.", run["run_id"])
            save_state({
                "cursor": cursor,
                "released_rows": meta.get("released_rows"),
                "next_refresh_at": meta.get("next_refresh_at"),
                "last_success": True,
            })
            manifest_path = write_manifest(run, pages, "success")
            logging.info("Bronze manifest 저장: %s", manifest_path)
            return
    except Exception as error:
        status = "partial_failure" if pages else "failed"
        failure_target = dict(current_target) if current_target else None
        if isinstance(error, ApiRequestError) and failure_target is not None:
            failure_target.update({
                "http_status": error.status_code,
                "retry_count": error.retry_count,
                "source_uri": mask_source_uri(error.url),
            })
        write_quarantine(run, error, status, failure_target)
        write_manifest(run, pages, status, str(error), failure_target)
        raise


def main() -> None:
    """로깅을 설정하고 Bronze 수집을 한 번 실행합니다."""
    configure_logging()
    try:
        collect()
    except Exception:
        logging.exception("Bronze 수집에 실패했습니다. 마지막 성공 cursor는 유지됩니다.")
        raise


if __name__ == "__main__":
    main()
