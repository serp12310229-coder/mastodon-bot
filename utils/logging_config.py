"""
로깅 설정 (CoC 봇)

콘솔과 회전 파일 로그를 설정한다. KST 기준 타임스탬프 + 레벨별 색상/이모지.

외부 API (실제 사용되는 것만):
- `logger`                        : 전역 Python logger
- `setup_logging()`               : 부팅 시 1회 호출
- `shutdown_logging()`            : atexit 에서 자동 호출 (핸들러 flush + close)
- `should_log_debug()`            : DEBUG 모드 여부
- `log_critical(msg, exc_info=True)`
- `log_api_operation(service, op, success, duration=None, item_count=None)`
- `log_command_result(user_id, command, success, duration=None, result_length=None)`
- `LogFormatter.command(...)`     : 명령어 실행 한 줄 포맷
- `LogContext(operation, **kv)`   : `with LogContext(...):` 실행 구간 로그
- `bot_logger.log_sheet_operation(op, ws, success, error=None)` : 시트 작업 한 줄
- `bot_logger.log_error_with_context(error, context_dict)`      : 에러 + 컨텍스트
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import pytz

from config.settings import config


# ----------------------------------------------------------------------
# 포매터
# ----------------------------------------------------------------------

class KSTFormatter(logging.Formatter):
    """KST 기준 타임스탬프를 사용하는 파일용 포매터."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=pytz.timezone('Asia/Seoul'))
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime('%Y-%m-%d %H:%M:%S KST')


class ColoredKSTFormatter(KSTFormatter):
    """
    콘솔용 친화적 포매터 (비개발자 대상).

    - INFO 는 라벨 없이 메시지만 표시 (회색 시각 + 본문)
    - WARNING / ERROR / CRITICAL 만 이모지로 강조
    - 날짜 경계에서 구분선 자동 삽입
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 인스턴스 변수로 둬서 동일 프로세스에 핸들러 두 개가 붙어도 (재 import,
        # 테스트 재실행 등) 서로 간섭하지 않게 한다.
        self._last_date: Optional[str] = None

    COLORS = {
        'RESET': '\033[0m',
        'BOLD': '\033[1m',
        'DIM': '\033[2m',
        'RED': '\033[31m',
        'YELLOW': '\033[33m',
        'BRIGHT_BLACK': '\033[90m',
        'BRIGHT_CYAN': '\033[96m',
    }

    LEVEL_STYLES = {
        # INFO 는 본문 색상 없음 (자연스러운 일반 텍스트)
        'INFO':     {'emoji': '',   'msg_color': ''},
        'WARNING':  {'emoji': '⚠️', 'msg_color': COLORS['YELLOW']},
        'ERROR':    {'emoji': '❌', 'msg_color': COLORS['RED']},
        'CRITICAL': {'emoji': '🚨', 'msg_color': COLORS['BOLD'] + COLORS['RED']},
    }

    def format(self, record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created, tz=pytz.timezone('Asia/Seoul'))

        # 날짜 경계 구분선 (자정 넘어가면 표시)
        current_date = dt.strftime('%Y-%m-%d')
        separator = ""
        if self._last_date is not None and current_date != self._last_date:
            bar = '─' * 20
            separator = (
                f"\n{self.COLORS['BRIGHT_CYAN']}"
                f"{bar} {dt.month}월 {dt.day}일 {bar}"
                f"{self.COLORS['RESET']}\n\n"
            )
        self._last_date = current_date

        time_str = dt.strftime('%H:%M:%S')
        dim = self.COLORS['BRIGHT_BLACK']
        reset = self.COLORS['RESET']

        style = self.LEVEL_STYLES.get(record.levelname, {'emoji': '', 'msg_color': ''})
        emoji = style['emoji']
        msg_color = style['msg_color']

        message = record.getMessage()
        # 예외 트레이스: ERROR/CRITICAL 같은 중요 로그에서만 포함 (WARNING 이하는 생략).
        # WARNING 에 트레이스 첨부되어 비개발자에게 겁주는 사례 방지.
        if record.exc_info and record.levelno >= logging.ERROR:
            if not getattr(record, 'exc_text', None):
                record.exc_text = self.formatException(record.exc_info)
            tb = record.exc_text
            if tb:
                message = f"{message}\n{tb}"

        # WARNING 이상은 이모지 + 색상 적용, INFO 는 본문 그대로
        if emoji:
            line = f"{dim}{time_str}{reset}  {msg_color}{emoji} {message}{reset}"
        else:
            line = f"{dim}{time_str}{reset}  {message}"

        return separator + line if separator else line


# ----------------------------------------------------------------------
# BotLogger 싱글톤
# ----------------------------------------------------------------------

class BotLogger:
    """콘솔·파일 양쪽에 쓰는 전역 로거 싱글톤."""

    _instance: Optional['BotLogger'] = None
    _logger: Optional[logging.Logger] = None

    def __new__(cls) -> 'BotLogger':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._logger is None:
            self._setup_logger()

    # ---------------- 내부 설정 ----------------

    def _setup_logger(self) -> None:
        self._logger = logging.getLogger('trpg_bot')
        self._logger.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
        self._logger.propagate = False  # root 로거로 중복 전파 방지

        # 기존 핸들러 초기화 (pytest 등 재-import 대응)
        if self._logger.handlers:
            for h in list(self._logger.handlers):
                self._logger.removeHandler(h)

        self._setup_file_handler()
        if config.ENABLE_CONSOLE_LOG:
            self._setup_console_handler()
        self._tune_external_loggers()

        # 로깅 시스템 초기화 — 파일 로그에만 기록 (콘솔에는 노출하지 않음).
        self._logger.debug(
            f"[초기화] 로깅 {config.LOG_LEVEL.upper()} "
            f"→ {config.LOG_FILE_PATH} "
            f"(console={'on' if config.ENABLE_CONSOLE_LOG else 'off'}, "
            f"debug={'on' if config.DEBUG_MODE else 'off'})"
        )

    def _setup_file_handler(self) -> None:
        try:
            log_path = Path(config.LOG_FILE_PATH)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                filename=config.LOG_FILE_PATH,
                maxBytes=config.LOG_MAX_BYTES,
                backupCount=config.LOG_BACKUP_COUNT,
                encoding='utf-8',
            )
            handler.setFormatter(KSTFormatter(
                fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S',
            ))
            handler.setLevel(logging.DEBUG)  # 파일에는 전부 기록
            self._logger.addHandler(handler)
        except Exception as e:
            # 파일 핸들러 실패 시 조용히 계속 (콘솔만 사용)
            print(f"[초기화 경고] 파일 로그 핸들러 설정 실패: {e}", file=sys.stderr)

    def _setup_console_handler(self) -> None:
        try:
            # Windows 기본 cp949 에서 한글/이모지가 깨지지 않도록 UTF-8 강제.
            # `reconfigure` 는 Python 3.7+ 에서만 있고, 이미 TextIOWrapper 가
            # utf-8 이면 no-op.
            stream = sys.stdout
            reconfigure = getattr(stream, 'reconfigure', None)
            if reconfigure and getattr(stream, 'encoding', '').lower() != 'utf-8':
                try:
                    reconfigure(encoding='utf-8', errors='replace')
                except Exception:
                    pass

            handler = logging.StreamHandler(stream)
            # 포맷 문자열은 사용하지 않음 — ColoredKSTFormatter.format() 에서 직접 조립.
            handler.setFormatter(ColoredKSTFormatter())
            handler.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
            self._logger.addHandler(handler)
        except Exception as e:
            print(f"[초기화 경고] 콘솔 로그 핸들러 설정 실패: {e}", file=sys.stderr)

    @staticmethod
    def _tune_external_loggers() -> None:
        """외부 라이브러리 로거의 레벨을 조정해 노이즈를 줄임."""
        for name, level in (
            ('gspread', logging.WARNING),
            ('requests', logging.WARNING),
            ('urllib3', logging.WARNING),
            ('mastodon', logging.INFO),
        ):
            logging.getLogger(name).setLevel(level)

    # ---------------- 공용 속성/메서드 ----------------

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    def log_sheet_operation(
        self,
        operation: str,
        worksheet: str,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Google Sheets 작업 한 줄 로그."""
        if success:
            self._logger.debug(f"[시트] {worksheet}/{operation} → ok")
        else:
            tail = f" | {error}" if error else ""
            self._logger.warning(f"[시트] {worksheet}/{operation} → 실패{tail}")

    def log_error_with_context(self, error: Exception, context: Optional[dict] = None) -> None:
        """에러 + 컨텍스트 dict 을 한 줄로 묶어 로깅."""
        ctx_str = ""
        if context:
            ctx_str = " | " + " | ".join(f"{k}={v}" for k, v in context.items())
        self._logger.error(
            f"{type(error).__name__}: {error}{ctx_str}",
            exc_info=config.DEBUG_MODE,
        )

    def shutdown(self) -> None:
        """애플리케이션 종료 시 파일 핸들러 flush + close (stdout 보호)."""
        if self._logger is None:
            return
        for handler in list(self._logger.handlers):
            try:
                handler.flush()
            except Exception:
                pass
            try:
                handler.close()
            except Exception:
                pass
            try:
                self._logger.removeHandler(handler)
            except Exception:
                pass


# ----------------------------------------------------------------------
# 모듈 레벨 API
# ----------------------------------------------------------------------

def setup_logging() -> BotLogger:
    """부팅 시 한 번 호출. 싱글톤이므로 반복 호출해도 안전."""
    return BotLogger()


def shutdown_logging() -> None:
    """atexit 에서 자동 호출."""
    try:
        BotLogger().shutdown()
    except Exception:
        pass


bot_logger: BotLogger = setup_logging()
logger: logging.Logger = bot_logger.logger


def should_log_debug() -> bool:
    """DEBUG 모드 여부. 디버그 로그 생성 전 가드용."""
    return config.DEBUG_MODE


def log_critical(message: str, exc_info: bool = True) -> None:
    """치명적 에러 (미처리 예외 경로 등)."""
    logger.critical(message, exc_info=exc_info)


# ----------------------------------------------------------------------
# LogFormatter — 명령어 실행 한 줄 포맷 (command_router 에서 사용)
# ----------------------------------------------------------------------

class LogFormatter:
    """여러 곳에서 쓰이는 일관된 로그 문자열 빌더.

    설계 의도: 콘솔 로그만 보고도 운영자가 30초 안에 (어디서/무엇이/왜) 실패했는지
    식별할 수 있어야 한다. 자유 형식 메시지를 흩뿌리지 말고 이 빌더를 거쳐 일관된
    구조 — `[<단계>] <동작> 결과 | <ExceptionType> | k=v ...` — 를 유지한다.
    """

    @staticmethod
    def command(
        user_id: str,
        command: str,
        success: bool,
        duration: Optional[float] = None,
        details: Optional[str] = None,
    ) -> str:
        status = "✅ 성공" if success else "❌ 실패"
        msg = f"[{command}] @{user_id} → {status}"
        if duration is not None:
            msg += f" | {duration:.3f}s"
        if details:
            msg += f" | {details}"
        return msg

    # ------------------------------------------------------------------
    # 부팅 / 작업 결과 표준 포맷
    # ------------------------------------------------------------------

    @staticmethod
    def _format_context(context: dict) -> str:
        """`{'sheet_id': 'abc', 'count': 4}` → `'sheet_id=abc | count=4'`."""
        return " | ".join(
            f"{k}={'(빈값)' if v in (None, '') else v}" for k, v in context.items()
        )

    @staticmethod
    def boot_phase(step: int, total: int, name: str) -> str:
        """부팅 단계 진입 헤더. `[부팅 1/4] 설정 검증` 형태."""
        return f"[부팅 {step}/{total}] {name}"

    @staticmethod
    def boot_ok(step: int, total: int, name: str, **context) -> str:
        """부팅 단계 성공. 컨텍스트는 운영자에게 도움되는 정보만 (예: ws_count=12)."""
        msg = f"[부팅 {step}/{total}] {name} ✓"
        if context:
            msg += " | " + LogFormatter._format_context(context)
        return msg

    @staticmethod
    def boot_fail(
        step: int, total: int, name: str, error: BaseException, **context
    ) -> str:
        """부팅 단계 실패. ExceptionType 과 컨텍스트가 한 줄에 함께 나오도록."""
        head = f"[부팅 {step}/{total}] {name} 실패 | {type(error).__name__}: {error}"
        if context:
            head += " | " + LogFormatter._format_context(context)
        return head

    @staticmethod
    def operation_fail(operation: str, error: BaseException, **context) -> str:
        """런타임 작업 실패용. 부팅 외 코드 경로 (시트 쓰기, API 호출 등)에서 사용."""
        head = f"{operation} 실패 | {type(error).__name__}: {error}"
        if context:
            head += " | " + LogFormatter._format_context(context)
        return head


def log_command_result(
    user_id: str,
    command: str,
    success: bool,
    duration: Optional[float] = None,
    result_length: Optional[int] = None,
) -> None:
    """명령어 실행 결과 단일 로그."""
    details = f"{result_length}자" if result_length is not None else None
    line = LogFormatter.command(user_id, command, success, duration, details)
    (logger.info if success else logger.warning)(line)


def log_api_operation(
    service: str,
    operation: str,
    success: bool,
    duration: Optional[float] = None,
    item_count: Optional[int] = None,
) -> None:
    """API 작업 로그. 성공은 DEBUG, 실패는 WARNING."""
    status = "✅" if success else "❌"
    msg = f"[{service}] {operation} → {status}"
    if duration is not None:
        msg += f" | {duration:.3f}s"
    if item_count is not None:
        msg += f" | {item_count}개"
    (logger.debug if success else logger.warning)(msg)


# ----------------------------------------------------------------------
# LogContext — `with LogContext("작업명", **kv):` 형태 사용
# ----------------------------------------------------------------------

class LogContext:
    """작업 구간 + 소요시간 로깅 (DEBUG 레벨)."""

    def __init__(self, operation: str, **context):
        self.operation = operation
        self.context = context
        self.start_time: Optional[datetime] = None

    def __enter__(self) -> 'LogContext':
        self.start_time = datetime.now()
        if config.DEBUG_MODE and self.context:
            ctx_str = " | ".join(f"{k}={v}" for k, v in self.context.items())
            logger.debug(f"[시작] {self.operation} | {ctx_str}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time is None:
            return False
        duration = (datetime.now() - self.start_time).total_seconds()
        if exc_type is None:
            if config.DEBUG_MODE:
                logger.debug(f"[완료] {self.operation} | {duration:.3f}s")
        else:
            logger.error(
                f"[실패] {self.operation} | {duration:.3f}s | {type(exc_val).__name__}: {exc_val}",
                exc_info=config.DEBUG_MODE,
            )
        return False  # 예외 전파


# ----------------------------------------------------------------------
# 종료 처리 등록 — 프로세스 종료 시 핸들러 정리
# ----------------------------------------------------------------------

import atexit
atexit.register(shutdown_logging)
