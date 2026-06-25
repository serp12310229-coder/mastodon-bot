"""
YN 명령어: [YN] / [yn]

무작위로 '예' 또는 '아니오'를 반환합니다.
"""

import os
import random
import sys
from typing import List

try:
    from utils.logging_config import logger
    from utils.error_handling import CommandError
    from commands.base_command import BaseCommand, CommandContext, CommandResponse
    from commands.registry import register_command
except ImportError as e:
    import logging
    logger = logging.getLogger('yn_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


_YN_CHOICES = ('예', '아니오')


@register_command(
    name="yn",
    aliases=["YN"],
    description="'예' 또는 '아니오'를 무작위로 반환합니다.",
    category="게임",
    examples=["[YN]", "[yn]"],
    requires_sheets=False,
    requires_api=False,
)
class YNCommand(BaseCommand):
    """YN 명령어 — 예/아니오 이진 판정."""

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ['yn', 'YN']

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        logger.debug("YNCommand 초기화 완료")

    def execute(self, context: CommandContext) -> CommandResponse:
        try:
            choice = random.choice(_YN_CHOICES)
            return CommandResponse.create_success(choice, data={'choice': choice})
        except CommandError as e:
            return CommandResponse.create_error(str(e), error=e)
        except Exception as e:
            logger.error(f"YN 명령어 실행 오류: {e}", exc_info=True)
            return CommandResponse.create_error("YN 판정 중 오류가 발생했습니다.", error=e)
