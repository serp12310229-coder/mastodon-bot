"""
명령어 결과 데이터 모델 - 개선된 버전
명령어 실행 결과를 관리하는 데이터 클래스들을 정의합니다.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Union
from datetime import datetime, timedelta
from enum import Enum
import pytz

# 경로 설정 (VM 환경 대응)
try:
    from config.settings import config
    from utils.error_handling import CommandError
    from utils.message_chunking import MessageChunker
    IMPORTS_AVAILABLE = True
except ImportError:
    import logging as _logging
    _logging.warning("설정 파일을 불러올 수 없습니다. 기본값을 사용합니다.")
    IMPORTS_AVAILABLE = False

    class CommandError(Exception):
        pass

    config = None

    class MessageChunker:  # type: ignore[no-redef]
        """폴백 MessageChunker (import 실패 시)"""
        def __init__(self, max_length: int = None):
            self.max_length = max_length or 1000
        def split_message(self, text: str) -> List[str]:
            if not text or len(text) <= self.max_length:
                return [text] if text else []
            return [text[i:i+self.max_length] for i in range(0, len(text), self.max_length)]

try:
    from models.dynamic_command_types import DynamicCommandType as CommandType
except ImportError:
    class CommandType(Enum):
        DICE = "dice"
        HELP = "help"
        UNKNOWN = "unknown"


class CommandStatus(Enum):
    """명령어 실행 상태"""
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    ERROR = "error"


# 명령어 결과 데이터 클래스들

@dataclass(frozen=True)
class DiceResult:
    """다이스 굴리기 결과 (불변 객체)"""
    
    expression: str                          # 다이스 표현식 (예: "2d6", "1d20+5")
    rolls: tuple                            # 각 주사위 결과 (불변 tuple)
    total: int                              # 총합
    modifier: int = 0                       # 보정값
    threshold: Optional[int] = None         # 성공/실패 임계값
    threshold_type: Optional[str] = None    # 임계값 타입 ('<' 또는 '>')
    success_count: Optional[int] = None     # 성공한 주사위 개수
    fail_count: Optional[int] = None        # 실패한 주사위 개수
    
    def __post_init__(self):
        # rolls를 tuple로 변환 (불변성 보장)
        if not isinstance(self.rolls, tuple):
            object.__setattr__(self, 'rolls', tuple(self.rolls))
    
    @property
    def base_total(self) -> int:
        """보정값 제외한 주사위 합계"""
        return sum(self.rolls)
    
    @property
    def has_threshold(self) -> bool:
        """성공/실패 조건 여부"""
        return self.threshold is not None and self.threshold_type is not None
    
    @property
    def is_success(self) -> Optional[bool]:
        """성공 여부 (단일 주사위 + 임계값인 경우)"""
        if not self.has_threshold or len(self.rolls) != 1:
            return None
        
        roll_value = self.rolls[0]
        if self.threshold_type == '<':
            return roll_value <= self.threshold
        elif self.threshold_type == '>':
            return roll_value >= self.threshold
        return None
    
    def get_detailed_result(self) -> str:
        """상세한 결과 문자열 반환"""
        if len(self.rolls) == 1:
            # 단일 주사위
            if self.has_threshold:
                success = self.is_success
                if success is not None:
                    result_text = "성공" if success else "실패"
                    return f"{self.rolls[0]} ({result_text})"
            return str(self.total)
        else:
            # 복수 주사위
            rolls_str = ", ".join(str(roll) for roll in self.rolls)
            if self.has_threshold:
                return f"{rolls_str}\n성공: {self.success_count}개, 실패: {self.fail_count}개"
            else:
                return f"{rolls_str}\n합계: {self.total}"
    
    def get_simple_result(self) -> str:
        """간단한 결과 문자열 반환"""
        if len(self.rolls) == 1:
            return str(self.rolls[0])
        return f"합계: {self.total}"
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'expression': self.expression,
            'rolls': list(self.rolls),
            'total': self.total,
            'modifier': self.modifier,
            'threshold': self.threshold,
            'threshold_type': self.threshold_type,
            'success_count': self.success_count,
            'fail_count': self.fail_count,
            'base_total': self.base_total,
            'has_threshold': self.has_threshold,
            'is_success': self.is_success
        }


@dataclass(frozen=True)
class HelpResult:
    """도움말 결과 (불변 객체)"""
    
    help_text: str                          # 도움말 텍스트
    command_count: int                      # 총 명령어 개수
    
    def get_result_text(self) -> str:
        """결과 텍스트 반환"""
        return self.help_text
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'help_text': self.help_text,
            'command_count': self.command_count
        }



@dataclass
class CommandResultGroup:
    """명령어 결과 그룹 (multiple 결과 전용 클래스)"""
    
    results: List['CommandResult'] = field(default_factory=list)
    group_title: str = ""
    
    def add_result(self, result: 'CommandResult') -> None:
        """결과 추가"""
        self.results.append(result)
    
    def get_combined_text(self) -> str:
        """모든 결과를 결합한 텍스트 반환"""
        if not self.results:
            return ""
        
        combined_texts = []
        if self.group_title:
            combined_texts.append(self.group_title)
        
        for i, result in enumerate(self.results, 1):
            if len(self.results) > 1:
                combined_texts.append(f"{i}. {result.get_user_message()}")
            else:
                combined_texts.append(result.get_user_message())
        
        return "\n".join(combined_texts)
    
    @property
    def is_all_successful(self) -> bool:
        """모든 결과가 성공인지 확인"""
        return all(result.is_successful() for result in self.results)
    
    @property
    def has_any_error(self) -> bool:
        """하나라도 오류가 있는지 확인"""
        return any(result.has_error() for result in self.results)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'group_title': self.group_title,
            'results_count': len(self.results),
            'results': [result.to_dict() for result in self.results],
            'is_all_successful': self.is_all_successful,
            'has_any_error': self.has_any_error
        }


@dataclass(frozen=True)
class CommandResult:
    """명령어 실행 결과 통합 클래스 (개선된 불변 객체)"""
    
    command_type: CommandType               # 명령어 타입
    status: CommandStatus                   # 실행 상태
    user_id: str                           # 실행한 사용자 ID
    user_name: str                         # 사용자 이름
    original_command: str                  # 원본 명령어
    message: str                           # 결과 메시지
    result_data: Optional[Union[DiceResult, HelpResult]] = None
    error: Optional[Exception] = None      # 오류 (있는 경우)
    execution_time: Optional[float] = None # 실행 시간 (초)
    timestamp: datetime = field(default_factory=lambda: datetime.now(pytz.timezone('Asia/Seoul')))
    metadata: Dict[str, Any] = field(default_factory=dict)  # 추가 메타데이터
    
    # 기본 오류 메시지 상수
    DEFAULT_ERROR_MESSAGE = "명령어 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
    
    def __post_init__(self):
        # 에러인데 메시지가 없는 경우 기본 메시지 설정
        if self.status == CommandStatus.ERROR and not self.message:
            object.__setattr__(self, 'message', self.DEFAULT_ERROR_MESSAGE)
        
        # metadata를 딕셔너리로 변환 (불변성 보장을 위해)
        if not isinstance(self.metadata, dict):
            object.__setattr__(self, 'metadata', dict(self.metadata))
    
    @classmethod
    def success(cls, command_type: CommandType, user_id: str, user_name: str, 
                original_command: str, message: str, result_data: Any = None,
                execution_time: float = None, **metadata) -> 'CommandResult':
        """
        성공 결과 생성 (팩토리 메서드)
        
        Args:
            command_type: 명령어 타입
            user_id: 사용자 ID
            user_name: 사용자 이름
            original_command: 원본 명령어
            message: 결과 메시지
            result_data: 결과 데이터
            execution_time: 실행 시간
            **metadata: 추가 메타데이터
            
        Returns:
            CommandResult: 성공 결과 객체
        """
        return cls(
            command_type=command_type,
            status=CommandStatus.SUCCESS,
            user_id=user_id,
            user_name=user_name,
            original_command=original_command,
            message=message,
            result_data=result_data,
            execution_time=execution_time,
            metadata=metadata
        )
    
    @classmethod
    def failure(cls, command_type: CommandType, user_id: str, user_name: str,
                original_command: str, error: Exception, execution_time: float = None,
                **metadata) -> 'CommandResult':
        """
        실패 결과 생성 (팩토리 메서드)
        
        Args:
            command_type: 명령어 타입
            user_id: 사용자 ID
            user_name: 사용자 이름
            original_command: 원본 명령어
            error: 발생한 오류
            execution_time: 실행 시간
            **metadata: 추가 메타데이터
            
        Returns:
            CommandResult: 실패 결과 객체
        """
        return cls(
            command_type=command_type,
            status=CommandStatus.FAILED,
            user_id=user_id,
            user_name=user_name,
            original_command=original_command,
            message=str(error) or cls.DEFAULT_ERROR_MESSAGE,
            error=error,
            execution_time=execution_time,
            metadata=metadata
        )
    
    @classmethod
    def error(cls, command_type: CommandType, user_id: str, user_name: str,
              original_command: str, error: Exception, execution_time: float = None,
              **metadata) -> 'CommandResult':
        """
        오류 결과 생성 (팩토리 메서드)
        
        Args:
            command_type: 명령어 타입
            user_id: 사용자 ID
            user_name: 사용자 이름
            original_command: 원본 명령어
            error: 발생한 오류
            execution_time: 실행 시간
            **metadata: 추가 메타데이터
            
        Returns:
            CommandResult: 오류 결과 객체
        """
        error_message = str(error) if error else cls.DEFAULT_ERROR_MESSAGE
        
        return cls(
            command_type=command_type,
            status=CommandStatus.ERROR,
            user_id=user_id,
            user_name=user_name,
            original_command=original_command,
            message=error_message,
            error=error,
            execution_time=execution_time,
            metadata=metadata
        )
    
    @classmethod
    def long_text(cls, command_type: CommandType, user_id: str, user_name: str,
                  original_command: str, text: str, max_length: int = None,
                  execution_time: float = None, **metadata) -> 'CommandResultGroup':
        """
        긴 텍스트 결과 생성 (그룹으로 반환)
        
        Args:
            command_type: 명령어 타입
            user_id: 사용자 ID
            user_name: 사용자 이름
            original_command: 원본 명령어
            text: 긴 텍스트
            max_length: 최대 길이
            execution_time: 실행 시간
            **metadata: 추가 메타데이터
            
        Returns:
            CommandResultGroup: 결과 그룹 (여러 CommandResult 포함)
        """
        # 텍스트 분할
        chunker = MessageChunker(max_length=max_length)
        chunks = chunker.split_message(text)
        
        # 각 청크를 개별 CommandResult로 생성
        group = CommandResultGroup(group_title=f"{user_name}의 {original_command} 결과")
        
        for i, chunk in enumerate(chunks):
            chunk_result = cls.success(
                command_type=command_type,
                user_id=user_id,
                user_name=user_name,
                original_command=f"{original_command} ({i+1}/{len(chunks)})",
                message=chunk,
                execution_time=execution_time if i == 0 else None,  # 첫 번째만 실행 시간 포함
                **metadata
            )
            group.add_result(chunk_result)
        
        return group
    
    def is_successful(self) -> bool:
        """성공 여부 확인"""
        return self.status == CommandStatus.SUCCESS
    
    def has_error(self) -> bool:
        """오류 여부 확인"""
        return self.error is not None
    
    def get_log_message(self) -> str:
        """로그용 메시지 반환"""
        status_text = "성공" if self.is_successful() else "실패"
        execution_info = f" ({self.execution_time:.3f}초)" if self.execution_time else ""
        return f"[{self.command_type.value}] {self.user_name} | {self.original_command} | {status_text}{execution_info}"
    
    def get_user_message(self) -> str:
        """사용자에게 표시할 메시지 반환"""
        return self.message
    
    def get_result_summary(self) -> Dict[str, Any]:
        """결과 요약 정보 반환"""
        summary = {
            'command_type': self.command_type.value,
            'status': self.status.value,
            'user_id': self.user_id,
            'user_name': self.user_name,
            'command': self.original_command,
            'success': self.is_successful(),
            'has_error': self.has_error(),
            'execution_time': self.execution_time,
            'timestamp': self.timestamp.isoformat()
        }
        
        if self.result_data:
            if hasattr(self.result_data, 'to_dict'):
                summary['result_data'] = self.result_data.to_dict()
            else:
                summary['result_data'] = str(self.result_data)
        
        if self.error:
            summary['error_type'] = type(self.error).__name__
            summary['error_message'] = str(self.error)
        
        summary.update(self.metadata)
        
        return summary
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환 (직렬화용)"""
        data = {
            'command_type': self.command_type.value,
            'status': self.status.value,
            'user_id': self.user_id,
            'user_name': self.user_name,
            'original_command': self.original_command,
            'message': self.message,
            'execution_time': self.execution_time,
            'timestamp': self.timestamp.isoformat(),
            'metadata': self.metadata.copy()  # 복사본 반환
        }
        
        if self.result_data and hasattr(self.result_data, 'to_dict'):
            data['result_data'] = self.result_data.to_dict()
        
        if self.error:
            data['error'] = {
                'type': type(self.error).__name__,
                'message': str(self.error)
            }
        
        return data
    
    def add_metadata(self, key: str, value: Any) -> 'CommandResult':
        """
        메타데이터 추가 (불변 객체이므로 새 객체 반환)
        
        Args:
            key: 메타데이터 키
            value: 메타데이터 값
            
        Returns:
            CommandResult: 메타데이터가 추가된 새 객체
        """
        new_metadata = self.metadata.copy()
        new_metadata[key] = value
        
        return CommandResult(
            command_type=self.command_type,
            status=self.status,
            user_id=self.user_id,
            user_name=self.user_name,
            original_command=self.original_command,
            message=self.message,
            result_data=self.result_data,
            error=self.error,
            execution_time=self.execution_time,
            timestamp=self.timestamp,
            metadata=new_metadata
        )
    
    def get_metadata(self, key: str, default: Any = None) -> Any:
        """메타데이터 조회"""
        return self.metadata.get(key, default)
    
    def __str__(self) -> str:
        """문자열 표현 (사용자 메시지)"""
        return self.message
    
    def __repr__(self) -> str:
        """개발자용 문자열 표현 (디버깅용)"""
        return (f"CommandResult(type={self.command_type.value}, "
                f"status={self.status.value}, user={self.user_name!r}, "
                f"command={self.original_command!r}, success={self.is_successful()})")


# ----------------------------------------------------------------------
# 결과 객체 생성 헬퍼 (명령어에서 사용)
# ----------------------------------------------------------------------

def create_dice_result(
    expression: str,
    rolls: List[int],
    total: int,
    modifier: int = 0,
    threshold: Optional[int] = None,
    threshold_type: Optional[str] = None,
    success_count: Optional[int] = None,
    fail_count: Optional[int] = None,
) -> DiceResult:
    """다이스 결과 객체 생성."""
    return DiceResult(
        expression=expression,
        rolls=tuple(rolls),
        total=total,
        modifier=modifier,
        threshold=threshold,
        threshold_type=threshold_type,
        success_count=success_count,
        fail_count=fail_count,
    )


def create_help_result(help_text: str, command_count: int = 0) -> HelpResult:
    """도움말 결과 객체 생성."""
    return HelpResult(help_text=help_text, command_count=command_count)

