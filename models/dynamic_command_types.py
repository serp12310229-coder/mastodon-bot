"""
명령어 타입 (CoC 봇)

이전 버전은 메타클래스 기반 동적 Enum 이었으나, 고정된 공용 명령어 4~5종만
다루므로 일반 Enum 으로 충분하다.

외부 호출 규약:
- `CommandType` / `DynamicCommandType` (동일 객체 alias) 을 사용
- `get_command_type(keyword)` 로 키워드 → CommandType 조회
"""

from enum import Enum
from typing import Optional


class CommandType(Enum):
    """공용 명령어 타입."""

    DICE = "dice"
    RANDOM = "random"
    YN = "yn"
    HELP = "help"
    CACHE_RESET = "cache_reset"
    UNKNOWN = "unknown"


# 기존 코드 호환성 alias. 새 코드는 CommandType 을 쓰자.
DynamicCommandType = CommandType


# keyword(lowercased) → CommandType
_KEYWORD_TO_TYPE = {
    # 다이스
    '다이스': CommandType.DICE,
    'dice': CommandType.DICE,
    'ndm': CommandType.DICE,
    # 랜덤
    '랜덤': CommandType.RANDOM,
    'random': CommandType.RANDOM,
    '무작위': CommandType.RANDOM,
    '선택': CommandType.RANDOM,
    # YN
    'yn': CommandType.YN,
    # 도움말
    '도움말': CommandType.HELP,
    'help': CommandType.HELP,
    # 시트 업데이트 (옛 이름: 캐시 리셋 — 호환 유지)
    '시트 업데이트': CommandType.CACHE_RESET,
    '시트업데이트': CommandType.CACHE_RESET,
    '캐시 리셋': CommandType.CACHE_RESET,
    '캐시리셋': CommandType.CACHE_RESET,
    '캐시 초기화': CommandType.CACHE_RESET,
    '캐시초기화': CommandType.CACHE_RESET,
}


def get_command_type(keyword: str) -> Optional[CommandType]:
    """
    키워드에서 CommandType 조회.

    Args:
        keyword: 명령어 키워드 (대소문자·공백 무관)

    Returns:
        CommandType 또는 None (미등록 키워드)
    """
    if not keyword:
        return None
    return _KEYWORD_TO_TYPE.get(keyword.strip().lower())
