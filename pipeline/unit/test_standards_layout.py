"""현행 표준의 평탄 구조와 legacy snapshot 분리 경계를 검증한다."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
STANDARDS_ROOT = PROJECT_ROOT / "standards"
LEGACY_ARCHIVE_ROOT = (
    PROJECT_ROOT / "archive" / "standards" / "silver-v1-before-v2-integration"
)

REQUIRED_ACTIVE_STANDARD_FILES = {
    "area-name-normalization.csv",
    "code-normalization.yaml",
    "modeling.yaml",
    "phase4-output-accepted.json",
    "phase4-output-rejected.json",
    "phase4-output.schema.json",
    "phase5-output.json",
    "phase5-output.schema.json",
    "pipeline.yaml",
    "reference-snapshot.schema.json",
    "silver-model-columns.csv",
    "source-batch-transport.schema.json",
    "source-record.schema.json",
    "standardization.yaml",
}

REQUIRED_LEGACY_ARCHIVE_FILES = {
    Path("SHA256SUMS"),
    Path("catalogs/silver-model-columns.csv"),
    Path("examples/phase5-output.json"),
    Path("rules/modeling.yaml"),
    Path("schemas/phase5-output.schema.json"),
}


def test_active_standard_files_are_flattened_under_standards_root() -> None:
    """필수 active 표준은 standards 직하에 있고 하위 디렉터리는 두지 않는다."""
    entries = tuple(STANDARDS_ROOT.iterdir())
    active_filenames = {entry.name for entry in entries if entry.is_file()}

    assert REQUIRED_ACTIVE_STANDARD_FILES <= active_filenames
    assert not any(entry.is_dir() for entry in entries)


def test_legacy_standard_snapshot_is_preserved_outside_active_root() -> None:
    """legacy snapshot은 active 표준과 섞지 않고 archive에 보존한다."""
    archived_files = {
        path.relative_to(LEGACY_ARCHIVE_ROOT)
        for path in LEGACY_ARCHIVE_ROOT.rglob("*")
        if path.is_file()
    }

    assert REQUIRED_LEGACY_ARCHIVE_FILES <= archived_files
