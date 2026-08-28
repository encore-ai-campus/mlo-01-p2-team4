"""통합된 ``src/.env`` 파일을 읽는 Bronze 환경 설정 도우미."""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SRC_DIR / ".env"


def load_dotenv(
    path: Path | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
    required: bool = False,
) -> bool:
    """KEY=VALUE 파일을 읽되 이미 주입된 환경변수를 덮어쓰지 않는다.

    Args:
        path: 읽을 파일. 생략하면 통합 위치인 ``src/.env``를 사용한다.
        environ: 테스트에서 대체할 환경변수 mapping.
        required: 파일이 없을 때 ``FileNotFoundError``를 발생시킬지 여부.

    Returns:
        환경 파일을 읽었으면 ``True``, 선택 파일이 없으면 ``False``.
    """
    resolved_path = ENV_PATH if path is None else Path(path)
    if not resolved_path.exists():
        if required:
            raise FileNotFoundError(f".env 파일이 없습니다: {resolved_path}")
        return False

    target = os.environ if environ is None else environ
    for raw_line in resolved_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            continue
        target.setdefault(
            normalized_key,
            value.strip().strip('"').strip("'"),
        )
    return True


__all__ = ["ENV_PATH", "SRC_DIR", "load_dotenv"]
