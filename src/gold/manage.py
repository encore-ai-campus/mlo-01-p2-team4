#!/usr/bin/env python
"""Django 관리 명령 진입점입니다."""

import os
import sys


def main() -> None:
    """Django 관리 명령을 실행합니다."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as error:
        raise ImportError(
            "Django를 불러올 수 없습니다. requirements.txt를 설치했는지 확인하세요."
        ) from error
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
