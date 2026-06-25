"""
명령어 패키지 (commands) — CoC 봇

핵심:
- base_command.py: 기본 명령어 클래스
- factory.py: 명령어 팩토리 패턴
- registry.py: 명령어 레지스트리

서브 패키지:
- default/: 공용 명령어 (다이스, 랜덤, YN)
- system/: 시스템 명령어 (도움말, 캐시 리셋)
- trpg_common/: CoC 폴백이 공유하는 스캐폴딩 (시트 주소 변환 / 락 헬퍼)
- coc/: CoC 룰 명령어
"""

from .base_command import BaseCommand, CommandContext, CommandResponse, create_command_context
from .registry import CommandRegistry, get_registry, register_command
from .factory import CommandFactory, get_factory

from .default import (
    DiceCommand,
    RandomCommand,
    YNCommand,
)

from .system import (
    HelpCommand,
    CacheResetCommand,
)

__all__ = [
    # 기본 클래스
    'BaseCommand',
    'CommandContext',
    'CommandResponse',
    'create_command_context',

    # 레지스트리/팩토리
    'CommandRegistry',
    'get_registry',
    'register_command',
    'CommandFactory',
    'get_factory',

    # 공용 명령어
    'DiceCommand',
    'RandomCommand',
    'YNCommand',

    # 시스템 명령어
    'HelpCommand',
    'CacheResetCommand',
]
