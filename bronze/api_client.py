"""공개 API 호출만 담당하는 클라이언트."""

from __future__ import annotations

from typing import Any

import requests


class ApiClient:
    """공개 API의 인증 및 데이터 조회를 담당합니다."""

    def __init__(self, base_url: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(
        self,
        path: str,
        api_key: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """GET 요청을 보내고 JSON 객체를 반환합니다."""
        headers = {"X-API-Key": api_key} if api_key else {}
        response = requests.get(
            f"{self.base_url}{path}",
            headers=headers,
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"JSON 객체 응답이 아닙니다: {path}")
        return payload

    def fetch_api_key(self) -> str:
        """오늘 사용할 API 키를 조회합니다."""
        payload = self._get("/public/v1/key")
        for field in ("api_key", "key", "token"):
            value = payload.get(field)
            if isinstance(value, str) and value:
                return value
        raise ValueError("API 키 응답에서 api_key/key/token 필드를 찾지 못했습니다.")

    def fetch_meta(self, api_key: str) -> dict[str, Any]:
        """현재 공개 데이터의 메타정보를 조회합니다."""
        return self._get("/api/v1/meta", api_key=api_key)

    def fetch_records(
        self,
        api_key: str,
        cursor: str | None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """cursor 기준으로 레코드 한 페이지를 조회합니다."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get("/api/v1/records", api_key=api_key, params=params)

