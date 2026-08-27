"""내부 공개 API 호출과 제한적 재시도를 담당하는 모듈."""

from __future__ import annotations

import random
import time
from typing import Any

import requests


class ApiRequestError(RuntimeError):
    """HTTP 또는 네트워크 요청 실패를 메타데이터와 함께 표현합니다."""

    def __init__(
        self,
        message: str,
        url: str,
        status_code: int | None,
        retry_count: int,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.retry_count = retry_count


class ApiPayloadError(ApiRequestError):
    """HTTP 요청은 성공했지만 JSON 파싱에 실패한 오류입니다."""

    def __init__(
        self,
        message: str,
        url: str,
        status_code: int,
        retry_count: int,
        raw_bytes: bytes,
        content_type: str,
    ) -> None:
        super().__init__(message, url, status_code, retry_count)
        self.raw_bytes = raw_bytes
        self.content_type = content_type


class ApiClient:
    """공개 API의 인증 및 데이터 조회를 담당하는 클라이언트."""

    def __init__(self, base_url: str, timeout: tuple[int, int] = (10, 30)) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.last_request_metadata: dict[str, Any] = {}

    def _request_json(
        self,
        path: str,
        api_key: str | None = None,
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> tuple[dict[str, Any], bytes, int, str, int, str]:
        """JSON GET 요청과 Bronze 기록용 응답 메타데이터를 처리합니다.

        429, 5xx, 연결 오류, 연결·응답 timeout만 지수 백오프와 지터를
        적용하여 재시도합니다. 인증 오류와 영구적인 4xx는 즉시 실패합니다.
        """
        headers = {"X-API-Key": api_key} if api_key else {}
        url = f"{self.base_url}{path}"
        retry_count = 0
        while True:
            try:
                response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
                self.last_request_metadata = {
                    "url": response.url,
                    "status_code": response.status_code,
                    "content_type": response.headers.get("Content-Type", ""),
                    "retry_count": retry_count,
                }
                retryable_status = response.status_code == 429 or response.status_code >= 500
                if retryable_status and retry_count < max_retries:
                    time.sleep(min(30, 2**retry_count) + random.uniform(0, 0.5))
                    retry_count += 1
                    continue
                if response.status_code >= 400:
                    raise ApiRequestError(
                        f"HTTP 요청 실패: {response.status_code}",
                        response.url,
                        response.status_code,
                        retry_count,
                    )

                raw_bytes = response.content
                content_type = response.headers.get("Content-Type", "")
                try:
                    payload = response.json()
                except ValueError as error:
                    raise ApiPayloadError(
                        "JSON 응답 파싱 실패",
                        response.url,
                        response.status_code,
                        retry_count,
                        raw_bytes,
                        content_type,
                    ) from error
                if not isinstance(payload, dict):
                    raise ApiPayloadError(
                        f"JSON 객체 응답이 아닙니다: {path}",
                        response.url,
                        response.status_code,
                        retry_count,
                        raw_bytes,
                        content_type,
                    )
                return (
                    payload,
                    raw_bytes,
                    response.status_code,
                    content_type,
                    retry_count,
                    response.url,
                )
            except ApiRequestError:
                raise
            except (
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError,
            ) as error:
                if retry_count >= max_retries:
                    raise ApiRequestError(str(error), url, None, retry_count) from error
                time.sleep(min(30, 2**retry_count) + random.uniform(0, 0.5))
                retry_count += 1

    def fetch_api_key(self) -> str:
        """오늘 사용할 API 키를 조회합니다."""
        payload, _, _, _, _, _ = self._request_json("/public/v1/key")
        for field in ("api_key", "key", "token"):
            value = payload.get(field)
            if isinstance(value, str) and value:
                return value
        raise ValueError("API 키 응답에서 api_key/key/token 필드를 찾지 못했습니다.")

    def fetch_meta(self, api_key: str) -> dict[str, Any]:
        """현재 공개 행 수와 다음 공개 시각을 조회합니다."""
        payload, _, _, _, _, _ = self._request_json("/api/v1/meta", api_key=api_key)
        return payload

    def fetch_records_with_metadata(
        self,
        api_key: str,
        cursor: str | None,
        limit: int = 1000,
    ) -> tuple[dict[str, Any], bytes, int, str, int, str]:
        """cursor 기반 records 페이지와 원본 응답 메타데이터를 반환합니다."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request_json("/api/v1/records", api_key=api_key, params=params)

