"""Atlas→Silver→MySQL 통합 진입점의 공개 CLI 계약을 검증한다."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

import src.bronze.environment as environment_module
import src.logging_config as logging_config_module
from src import main as main_module


INTERVAL_ENV_NAME = "PIPELINE_INTERVAL_SECONDS"


@pytest.fixture(autouse=True)
def isolated_pipeline_logging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    """통합 진입점 테스트의 로그를 임시 디렉터리에 격리한다."""
    log_path = tmp_path / "output" / "logs" / "pipeline.log"
    configure = main_module.configure_pipeline_logging
    monkeypatch.setattr(
        main_module,
        "configure_pipeline_logging",
        lambda: configure(log_path=log_path),
    )
    yield log_path

    logger = logging.getLogger(logging_config_module.LOGGER_NAMESPACE)
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    if hasattr(logger, "_mlo_pipeline_configuration"):
        delattr(logger, "_mlo_pipeline_configuration")


def test_parse_args_uses_safe_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(INTERVAL_ENV_NAME, raising=False)
    monkeypatch.setattr(environment_module, "ENV_PATH", tmp_path / "missing.env")

    args = main_module.parse_args([])

    assert args.batch_size == 1_000
    assert args.temp_dir == Path("temp")
    assert args.chunk_size == 1_000
    assert args.interval_seconds == 30
    assert args.once is False
    assert args.init_schema is False
    assert args.apply is False


def test_parse_args_reads_interval_seconds_from_src_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(f"{INTERVAL_ENV_NAME}=45\n", encoding="utf-8")
    monkeypatch.delenv(INTERVAL_ENV_NAME, raising=False)
    monkeypatch.setattr(environment_module, "ENV_PATH", env_path)

    args = main_module.parse_args([])

    assert args.interval_seconds == 45


def test_parse_args_prefers_process_interval_over_src_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(f"{INTERVAL_ENV_NAME}=45\n", encoding="utf-8")
    monkeypatch.setattr(environment_module, "ENV_PATH", env_path)
    monkeypatch.setenv(INTERVAL_ENV_NAME, "60")

    args = main_module.parse_args([])

    assert args.interval_seconds == 60


def test_parse_args_prefers_cli_interval_over_process_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(environment_module, "ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv(INTERVAL_ENV_NAME, "60")

    args = main_module.parse_args(["--interval-seconds", "75"])

    assert args.interval_seconds == 75


def test_main_once_runs_exactly_one_silver_then_mysql_cycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    isolated_pipeline_logging: Path,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def run_silver(argv: list[str]) -> int:
        calls.append(("silver", argv))
        return 0

    def run_mysql(argv: list[str]) -> int:
        calls.append(("mysql", argv))
        return 0

    monkeypatch.setattr(main_module.flat_pipeline, "main", run_silver)
    monkeypatch.setattr(main_module.mysql_loader, "main", run_mysql)

    exit_code = main_module.main(["--temp-dir", str(tmp_path), "--once"])

    assert exit_code == 0
    assert calls == [
        (
            "silver",
            ["--batch-size", "1000", "--temp-dir", str(tmp_path)],
        ),
        (
            "mysql",
            [
                "--models-dir",
                str(tmp_path / "models"),
                "--chunk-size",
                "1000",
            ],
        ),
    ]
    assert "pipeline_completed=true mode=dry-run" in capsys.readouterr().out
    assert "pipeline_completed=true mode=dry-run" in (
        isolated_pipeline_logging.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("flags", "expected_mysql_flags"),
    [
        (["--init-schema"], ["--init-schema"]),
        (["--apply"], ["--apply"]),
        (
            ["--init-schema", "--apply"],
            ["--init-schema", "--apply"],
        ),
    ],
)
def test_main_passes_requested_mysql_actions_exactly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    flags: list[str],
    expected_mysql_flags: list[str],
) -> None:
    silver_calls: list[list[str]] = []
    mysql_calls: list[list[str]] = []

    def run_silver(argv: list[str]) -> int:
        silver_calls.append(argv)
        return 0

    def run_mysql(argv: list[str]) -> int:
        mysql_calls.append(argv)
        return 0

    monkeypatch.setattr(main_module.flat_pipeline, "main", run_silver)
    monkeypatch.setattr(main_module.mysql_loader, "main", run_mysql)

    exit_code = main_module.main(
        [
            "--batch-size",
            "23",
            "--temp-dir",
            str(tmp_path),
            "--chunk-size",
            "17",
            "--once",
            *flags,
        ]
    )

    assert exit_code == 0
    assert silver_calls == [["--batch-size", "23", "--temp-dir", str(tmp_path)]]
    assert mysql_calls == [
        [
            "--models-dir",
            str(tmp_path / "models"),
            "--chunk-size",
            "17",
            *expected_mysql_flags,
        ]
    ]


def test_main_stops_before_mysql_and_propagates_silver_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    isolated_pipeline_logging: Path,
) -> None:
    mysql_called = False

    def run_silver(argv: list[str]) -> int:
        return 19

    def run_mysql(argv: list[str]) -> int:
        nonlocal mysql_called
        mysql_called = True
        return 0

    monkeypatch.setattr(main_module.flat_pipeline, "main", run_silver)
    monkeypatch.setattr(main_module.mysql_loader, "main", run_mysql)

    assert main_module.main(["--once"]) == 19
    assert mysql_called is False
    assert "pipeline_stage_failed=true stage=silver exit_code=19" in (
        capsys.readouterr().err
    )
    assert "pipeline_stage_failed=true stage=silver exit_code=19" in (
        isolated_pipeline_logging.read_text(encoding="utf-8")
    )


def test_run_loop_waits_only_for_time_remaining_from_cycle_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = main_module.parse_args(["--interval-seconds", "30"])
    now = 0.0
    cycle_starts: list[float] = []
    sleep_calls: list[float] = []

    def monotonic() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        sleep_calls.append(seconds)
        now += seconds

    def run_once(
        received_args: object,
        *,
        init_schema: bool | None = None,
    ) -> int:
        nonlocal now
        assert received_args is args
        cycle_starts.append(now)
        if len(cycle_starts) == 1:
            now += 8
            return 0
        return 17

    monkeypatch.setattr(main_module, "run_once", run_once)

    exit_code = main_module.run_loop(args, monotonic=monotonic, sleep=sleep)

    assert exit_code == 17
    assert cycle_starts == [0.0, 30.0]
    assert sleep_calls == [22.0]


def test_run_loop_uses_zero_sleep_when_cycle_exceeds_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = main_module.parse_args(["--interval-seconds", "30"])
    now = 0.0
    cycle_starts: list[float] = []
    sleep_calls: list[float] = []

    def monotonic() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        sleep_calls.append(seconds)
        now += seconds

    def run_once(
        received_args: object,
        *,
        init_schema: bool | None = None,
    ) -> int:
        nonlocal now
        assert received_args is args
        cycle_starts.append(now)
        if len(cycle_starts) == 1:
            now += 35
            return 0
        return 23

    monkeypatch.setattr(main_module, "run_once", run_once)

    exit_code = main_module.run_loop(args, monotonic=monotonic, sleep=sleep)

    assert exit_code == 23
    assert cycle_starts == [0.0, 35.0]
    assert sleep_calls == [0.0]


def test_run_loop_passes_init_schema_only_to_first_cycle_and_apply_to_each_cycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    args = main_module.parse_args(
        [
            "--temp-dir",
            str(tmp_path),
            "--interval-seconds",
            "30",
            "--init-schema",
            "--apply",
        ]
    )
    silver_calls = 0
    mysql_calls: list[list[str]] = []
    events: list[str] = []
    now = 0.0

    def run_silver(argv: list[str]) -> int:
        nonlocal silver_calls
        silver_calls += 1
        events.append(f"silver-{silver_calls}")
        return 29 if silver_calls == 3 else 0

    def run_mysql(argv: list[str]) -> int:
        mysql_calls.append(argv)
        events.append(f"mysql-{len(mysql_calls)}")
        return 0

    def monotonic() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        events.append("sleep")
        now += seconds

    monkeypatch.setattr(main_module.flat_pipeline, "main", run_silver)
    monkeypatch.setattr(main_module.mysql_loader, "main", run_mysql)

    exit_code = main_module.run_loop(args, monotonic=monotonic, sleep=sleep)

    common_loader_args = [
        "--models-dir",
        str(tmp_path / "models"),
        "--chunk-size",
        "1000",
    ]
    assert exit_code == 29
    assert silver_calls == 3
    assert events == [
        "silver-1",
        "mysql-1",
        "sleep",
        "silver-2",
        "mysql-2",
        "sleep",
        "silver-3",
    ]
    assert mysql_calls == [
        [*common_loader_args, "--init-schema", "--apply"],
        [*common_loader_args, "--apply"],
    ]


@pytest.mark.parametrize("failing_stage", ["silver", "mysql"])
def test_run_loop_returns_nonzero_without_sleeping(
    monkeypatch: pytest.MonkeyPatch,
    failing_stage: str,
) -> None:
    expected_exit_code = 31

    def run_silver(argv: list[str]) -> int:
        return expected_exit_code if failing_stage == "silver" else 0

    def run_mysql(argv: list[str]) -> int:
        return expected_exit_code if failing_stage == "mysql" else 0

    def unexpected_sleep(seconds: float) -> None:
        raise AssertionError(f"nonzero 종료 후 sleep({seconds})이 호출되었습니다.")

    monkeypatch.setattr(main_module.flat_pipeline, "main", run_silver)
    monkeypatch.setattr(main_module.mysql_loader, "main", run_mysql)

    args = main_module.parse_args([])

    assert (
        main_module.run_loop(args, monotonic=lambda: 0.0, sleep=unexpected_sleep) == 31
    )


@pytest.mark.parametrize("failing_stage", ["silver", "mysql"])
def test_main_does_not_hide_pipeline_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    failing_stage: str,
) -> None:
    error = RuntimeError(f"{failing_stage} failed")

    def run_silver(argv: list[str]) -> int:
        if failing_stage == "silver":
            raise error
        return 0

    def run_mysql(argv: list[str]) -> int:
        if failing_stage == "mysql":
            raise error
        raise AssertionError("Silver 예외 후 MySQL은 호출되면 안 됩니다.")

    monkeypatch.setattr(main_module.flat_pipeline, "main", run_silver)
    monkeypatch.setattr(main_module.mysql_loader, "main", run_mysql)

    with pytest.raises(RuntimeError) as caught:
        main_module.main([])

    assert caught.value is error


@pytest.mark.parametrize("argv", [[], ["--once"]])
def test_main_converts_keyboard_interrupt_to_exit_code_130(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
    isolated_pipeline_logging: Path,
) -> None:
    def interrupt(*args: object, **kwargs: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "run_once", interrupt)
    monkeypatch.setattr(main_module, "run_loop", interrupt)

    assert main_module.main(argv) == 130
    assert "pipeline_interrupted=true" in capsys.readouterr().err
    assert "pipeline_interrupted=true" in isolated_pipeline_logging.read_text(
        encoding="utf-8"
    )


def test_cli_logs_unhandled_exception_and_returns_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    isolated_pipeline_logging: Path,
) -> None:
    def fail(argv: object = None) -> int:
        del argv
        raise RuntimeError("unexpected pipeline failure")

    monkeypatch.setattr(main_module, "main", fail)

    assert main_module._run_cli(["--once"]) == 1
    stderr = capsys.readouterr().err
    assert "pipeline_failed=true" in stderr
    assert "RuntimeError: unexpected pipeline failure" in stderr
    file_text = isolated_pipeline_logging.read_text(encoding="utf-8")
    assert "pipeline_failed=true" in file_text
    assert "RuntimeError: unexpected pipeline failure" in file_text


@pytest.mark.parametrize(
    "flag",
    ["--batch-size", "--chunk-size", "--interval-seconds"],
)
@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_parse_args_rejects_invalid_sizes(flag: str, value: str) -> None:
    with pytest.raises(SystemExit) as caught:
        main_module.parse_args([flag, value])

    assert caught.value.code == 2


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_parse_args_rejects_invalid_interval_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    monkeypatch.setattr(environment_module, "ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv(INTERVAL_ENV_NAME, value)

    with pytest.raises(SystemExit) as caught:
        main_module.parse_args([])

    assert caught.value.code == 2
