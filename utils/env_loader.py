"""
환경 변수 / `.env` 파일 로딩 공용 헬퍼.

진실 우선순위:
    1. 현재 프로세스 환경 변수 (`os.environ`)
    2. `.env` 파일
    3. 호출자가 지정한 fallback 값

`.env` 파싱 규칙:
    - 빈 줄 / `#` 로 시작하는 줄 무시
    - `KEY=value` 형식만 인식
    - 줄 안의 `#` 이후는 주석으로 절단
    - 값 양 끝의 `'` `"` 따옴표는 벗긴다
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union


# `True` 로 평가되는 문자열 집합.
_TRUE_TOKENS = frozenset({"1", "true", "yes", "y", "on"})

# `False` 로 평가되는 문자열 집합 — 명시적 false 표기를 인식하기 위해 둔다.
_FALSE_TOKENS = frozenset({"0", "false", "no", "n", "off"})


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    """
    환경 변수 문자열을 bool 로 평가.

    Args:
        value: 입력 문자열 (`None` 허용).
        default: `value` 가 `None`/공백/알 수 없는 토큰일 때의 반환값.

    Returns:
        `True`/`False`. 알 수 없는 값은 `default` 반환.
    """
    if value is None:
        return default
    token = value.strip().lower()
    if not token:
        return default
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return default


def _strip_inline_comment(line: str) -> str:
    """`KEY=value  # 주석` → `KEY=value` 로 절단."""
    if "#" in line:
        return line.split("#", 1)[0].rstrip()
    return line


def _strip_quotes(value: str) -> str:
    """값 양 끝 따옴표(`'` 또는 `"`) 한 쌍 제거."""
    return value.strip().strip("'").strip('"')


def read_env_file(path: Union[str, Path]) -> dict:
    """
    `.env` 파일을 읽어 `{key: value}` 딕셔너리로 반환.

    파일이 없으면 빈 dict 를 반환한다 (호출자가 fallback 처리).
    파일 읽기 자체가 실패할 경우 예외를 그대로 전파한다.

    Args:
        path: `.env` 파일 경로.

    Returns:
        파싱된 환경 변수 dict. 파일 부재 시 `{}`.
    """
    env_path = Path(path)
    if not env_path.exists():
        return {}

    result: dict = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        line = _strip_inline_comment(line)
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        result[key] = _strip_quotes(value)

    return result


def get_env_value(
    key: str,
    env_file_path: Union[str, Path] = ".env",
    fallback: Optional[str] = None,
) -> Optional[str]:
    """
    단일 키 조회: `os.environ` → `.env` → `fallback` 순서.

    이 헬퍼는 한 번에 하나의 키만 필요한 호출 지점에 적합하다. 다수의 키가
    필요하면 `read_env_file` 로 한 번 읽고 dict 를 캐시해 사용할 것.

    Args:
        key: 조회할 환경 변수 이름.
        env_file_path: `.env` 파일 경로.
        fallback: env 와 파일 모두에 없을 때의 반환값.

    Returns:
        문자열 값 또는 `fallback`.
    """
    value = os.environ.get(key)
    if value is not None:
        return value

    try:
        env_dict = read_env_file(env_file_path)
    except OSError:
        # 파일은 있는데 읽기에 실패한 경우. 부팅 단계 헬퍼라서 예외 대신
        # fallback 으로 진행해 봇이 죽지 않도록 한다.
        return fallback

    if key in env_dict:
        return env_dict[key]
    return fallback


def get_env_bool(
    key: str,
    env_file_path: Union[str, Path] = ".env",
    default: bool = False,
) -> bool:
    """
    `os.environ` → `.env` → `default` 우선순위로 bool 환경 변수 평가.

    Args:
        key: 환경 변수 이름.
        env_file_path: `.env` 파일 경로.
        default: 키가 없거나 알 수 없는 값일 때의 반환값.

    Returns:
        `True`/`False`.
    """
    raw = get_env_value(key, env_file_path=env_file_path, fallback=None)
    if raw is None:
        return default
    return parse_bool(raw, default=default)
