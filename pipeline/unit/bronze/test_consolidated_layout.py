"""Bronze 디렉터리와 환경 파일 통합 경로의 회귀 테스트."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import src
import src.bronze.environment as environment_module
from src.bronze import atlas_snapshot_export, crawler, mongo_loader
from src.bronze.atlas_download import AtlasSettings


MONGODB_ENV_NAMES = (
    "MONGODB_URI",
    "MONGODB_DATABASE",
    "MONGODB_COLLECTION",
)


def test_bronze_runtime_paths_are_consolidated_under_src() -> None:
    """구형 top-level 경로 없이 설정·데이터·환경 경로가 src 아래에 모인다."""
    src_dir = Path(src.__file__).resolve().parent
    project_root = src_dir.parent

    assert not (project_root / "bronze").exists()
    assert crawler.BASE_DIR == src_dir / "bronze"
    assert crawler.PROJECT_ROOT == project_root
    assert crawler.SETTINGS_PATH == src_dir / "bronze" / "config" / "settings.json"
    assert mongo_loader.DATA_PATH == src_dir / "bronze" / "data" / "records.json"
    assert atlas_snapshot_export.OUTPUT_PATH == (
        src_dir / "bronze" / "data" / "atlas_records.json"
    )
    assert environment_module.ENV_PATH == src_dir / ".env"
    assert (src_dir / ".env.example").is_file()
    assert (src_dir / ".gitignore").is_file()

    settings = crawler.load_settings()
    assert settings["records_path"] == "src/bronze/data/records.json"


def test_src_dotenv_is_shared_and_process_environment_wins(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """세 Atlas 진입점이 같은 env 파일을 읽고 기존 process 값은 보존한다."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "MONGODB_URI=mongodb://from-file.invalid\n"
        "MONGODB_DATABASE=from_file_database\n"
        "MONGODB_COLLECTION=from_file_collection\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(environment_module, "ENV_PATH", env_path)
    for name in MONGODB_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MONGODB_URI", "mongodb://from-process.invalid")

    settings = AtlasSettings.from_environment()

    assert settings.uri == "mongodb://from-process.invalid"
    assert settings.database == "from_file_database"
    assert settings.collection == "from_file_collection"
    assert mongo_loader.load_config() == (
        "mongodb://from-process.invalid",
        "from_file_database",
        "from_file_collection",
    )
    assert atlas_snapshot_export.load_config() == (
        "mongodb://from-process.invalid",
        "from_file_database",
        "from_file_collection",
    )

    monkeypatch.setattr(environment_module, "ENV_PATH", tmp_path / "missing.env")
    assert atlas_snapshot_export.load_config() == (
        "mongodb://from-process.invalid",
        "from_file_database",
        "from_file_collection",
    )


def test_crawler_runs_mongo_loader_by_package_module(monkeypatch) -> None:
    """이동 뒤 loader를 구형 파일 경로가 아닌 통합 package에서 실행한다."""
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(crawler.subprocess, "run", fake_run)

    crawler.run_mongo_loader()

    assert observed["command"] == [
        sys.executable,
        "-m",
        "src.bronze.mongo_loader",
    ]
    assert observed["cwd"] == crawler.PROJECT_ROOT
    assert observed["encoding"] == "utf-8"
