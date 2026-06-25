"""
명령어 개발을 위한 유용한 데코레이터들
보일러플레이트 코드를 줄이고 개발 편의성을 높입니다.
"""

import os
import sys
from functools import wraps
from typing import Callable, Any

# 경로 설정
try:
    from utils.logging_config import logger
    from utils.error_handling import CommandError
    from commands.base_command import CommandContext, CommandResponse
except ImportError as e:
    import logging
    logger = logging.getLogger('decorators')


def handle_command_errors(
    func: Callable = None,
    *,
    system_tag: str = None,
    user_error_message: str = "처리 중 오류가 발생했습니다.",
) -> Callable:
    """
    명령어 에러 자동 처리 데코레이터.

    두 가지 호출 형태 모두 지원:

        @handle_command_errors
        def execute(self, context): ...

        @handle_command_errors(system_tag="CoC", user_error_message="CoC 처리 오류")
        def execute(self, context): ...

    자동 처리:
        - CommandError      : 메시지 그대로 사용자에게 표시
        - 그 외 Exception   : `[system_tag]` 접두 로그 + `user_error_message` 응답

    Args:
        func: 데코레이트할 함수 (인자 없이 호출 시 자동 전달).
        system_tag: 로그 접두사용 시스템 식별자(예: "CoC"). `None` 이면 클래스명을 사용.
        user_error_message: 일반 Exception 발생 시 사용자에게 표시할 메시지.
    """

    def decorate(target: Callable) -> Callable:
        @wraps(target)
        def wrapper(self, context: CommandContext) -> CommandResponse:
            try:
                return target(self, context)
            except CommandError as e:
                # 비즈니스 예외: 사용자에게 그대로 표시
                return CommandResponse.create_error(str(e), error=e)
            except Exception as e:
                # 시스템 예외: 시스템 태그 또는 클래스명 접두로 로그 남김
                tag = system_tag or self.__class__.__name__
                logger.error(
                    f"[{tag}] 명령어 실행 오류: {e}", exc_info=True,
                )
                return CommandResponse.create_error(
                    user_error_message,
                    error=e,
                )
        return wrapper

    # `@handle_command_errors` (인자 없이) 호출되면 func 가 직접 전달된다.
    if callable(func):
        return decorate(func)
    # `@handle_command_errors(...)` 형태면 데코레이터를 반환한다.
    return decorate


def validate_keywords(min_length: int = None, max_length: int = None) -> Callable:
    """
    키워드 개수 검증 데코레이터
    
    사용법:
        @validate_keywords(min_length=2, max_length=3)
        def execute(self, context: CommandContext) -> CommandResponse:
            # keywords는 이미 검증됨
            ...
    
    Args:
        min_length: 최소 키워드 개수
        max_length: 최대 키워드 개수
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, context: CommandContext) -> CommandResponse:
            keywords = context.keywords
            
            # 최소 길이 검증
            if min_length is not None and len(keywords) < min_length:
                command_name = keywords[0] if keywords else "명령어"
                return CommandResponse.create_error(
                    f"입력 형식이 올바르지 않습니다. 최소 {min_length}개의 인자가 필요합니다."
                )
            
            # 최대 길이 검증
            if max_length is not None and len(keywords) > max_length:
                command_name = keywords[0] if keywords else "명령어"
                return CommandResponse.create_error(
                    f"입력 형식이 올바르지 않습니다. 최대 {max_length}개의 인자만 허용됩니다."
                )
            
            return func(self, context)
        
        return wrapper
    return decorator


def log_execution(func: Callable) -> Callable:
    """
    명령어 실행 로깅 데코레이터
    
    사용법:
        @log_execution
        def execute(self, context: CommandContext) -> CommandResponse:
            ...
    
    자동으로 실행 로그를 기록합니다.
    """
    @wraps(func)
    def wrapper(self, context: CommandContext) -> CommandResponse:
        logger.info(f"{self.__class__.__name__} 실행: {context.keywords}")
        result = func(self, context)
        
        if result.is_successful():
            logger.info(f"{self.__class__.__name__} 성공")
        else:
            logger.warning(f"{self.__class__.__name__} 실패: {result.message}")
        
        return result
    
    return wrapper


