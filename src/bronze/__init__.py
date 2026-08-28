"""Atlas Bronze 공개 인터페이스를 실행 시점에 지연 로딩한다."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "AtlasBatch": ".atlas_download",
    "AtlasCursor": ".atlas_download",
    "AtlasIncrementalBatch": ".atlas_pipeline",
    "AtlasIncrementalPipeline": ".atlas_pipeline",
    "AtlasPreflight": ".atlas_download",
    "AtlasSettings": ".atlas_download",
    "AtlasSourceReader": ".atlas_download",
    "AtlasSourceShapeError": ".atlas_download",
    "RawBatchArtifact": ".raw_store",
    "RawBatchStore": ".raw_store",
    "RawResumeCursor": ".raw_store",
    "RawRowLocator": ".raw_store",
    "load_finalized_resume_cursor": ".raw_store",
}


def __getattr__(name: str) -> Any:
    """공개 이름이 실제 사용될 때만 소유 모듈을 import한다."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """대화형 탐색에서 lazy 공개 이름을 함께 반환한다."""
    return sorted((*globals(), *_EXPORT_MODULES))


__all__ = sorted(_EXPORT_MODULES)
