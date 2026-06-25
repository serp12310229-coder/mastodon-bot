"""
도움말 명령어 구현 - 개선된 BaseCommand 아키텍처
Google Sheets에서 도움말 정보를 가져와 표시하는 명령어 클래스입니다.
"""

import os
import sys
import re
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

# 경로 설정 (VM 환경 대응)
# 의존성 임포트
try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.cache_manager import bot_cache
    from commands.base_command import BaseCommand, CommandContext, CommandResponse
    from commands.registry import register_command
    from models.command_result import HelpResult, create_help_result
    DUMMY_MODE = False
except ImportError as e:
    import logging
    logger = logging.getLogger('help_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


@dataclass
class HelpItem:
    """도움말 항목 데이터 클래스"""
    command: str
    description: str
    
    def __post_init__(self):
        self.command = self.command.strip()
        self.description = self.description.strip()
    
    @property
    def is_valid(self) -> bool:
        """유효한 도움말 항목인지 확인"""
        return bool(self.command and self.description)
    
    @property
    def formatted_command(self) -> str:
        """대괄호가 포함된 형식의 명령어 반환"""
        if not self.command.startswith('[') or not self.command.endswith(']'):
            return f"[{self.command}]"
        return self.command
    
    def matches_keyword(self, keyword: str) -> bool:
        """키워드와 매칭되는지 확인"""
        if not keyword:
            return False
        keyword_lower = keyword.lower().strip()
        return (keyword_lower in self.command.lower() or 
                keyword_lower in self.description.lower())


class HelpDataLoader:
    """도움말 데이터 로딩 + 캐싱.

    캐시는 sheet_name 별로 분리되며 TTL/저장은 `BotCacheManager` 가 일임 처리한다
    (이전엔 자체 TTL 로직 + 이중 저장으로 캐시가 사실상 깨져 있었음).
    """

    def __init__(self, sheets_manager=None, cache_manager=None):
        self.sheets_manager = sheets_manager
        self.cache_manager = cache_manager or bot_cache

    def load_help_items(self, sheet_name: Optional[str] = None) -> List[HelpItem]:
        """캐시 우선 → 시트 후순위. sheet_name 별 캐시 키 분리."""
        cached = self.cache_manager.get_help_items(sheet_name=sheet_name)
        if cached:
            logger.debug(f"캐시 적중 (도움말, sheet={sheet_name or '(기본)'}, {len(cached)}개)")
            return [
                HelpItem(item.get('명령어', ''), item.get('설명', ''))
                for item in cached
            ]

        sheet_items = self._load_from_sheet(sheet_name=sheet_name)
        if sheet_items:
            self.cache_manager.cache_help_items(
                [{'명령어': it.command, '설명': it.description} for it in sheet_items],
                sheet_name=sheet_name,
            )
            logger.info(
                f"시트에서 도움말 로드 + 캐시 (sheet={sheet_name or '(기본)'}, "
                f"{len(sheet_items)}개)"
            )
            return sheet_items

        logger.warning(f"도움말 항목 없음 (sheet={sheet_name or '(기본)'}) - 빈 리스트")
        return []

    def _load_from_sheet(self, sheet_name: Optional[str] = None) -> Optional[List[HelpItem]]:
        """시트 직접 로드. 캐시 갱신은 호출자가 담당.

        Args:
            sheet_name: 도움말 시트 이름. None 이면 기본 시트.
        """
        if not self.sheets_manager:
            logger.debug("시트 매니저가 없음")
            return None

        try:
            raw_items = self.sheets_manager.get_help_items(sheet_name=sheet_name)
            if not raw_items:
                logger.warning(f"시트에 도움말 데이터가 없음 (sheet={sheet_name or '(기본)'})")
                return None

            help_items = [
                HelpItem(
                    command=item.get('명령어', ''),
                    description=item.get('설명', ''),
                )
                for item in raw_items
            ]
            help_items = [it for it in help_items if it.is_valid]
            return help_items if help_items else None

        except Exception as e:
            logger.error(f"시트에서 도움말 항목 로드 실패: {e}")
            return None

    def get_cache_status(self, sheet_name: Optional[str] = None) -> Dict[str, Any]:
        """캐시 상태 정보 (validator 진단용)."""
        cached = self.cache_manager.get_help_items(sheet_name=sheet_name)
        if not cached:
            return {'cached': False, 'message': '캐시에 데이터가 없습니다.'}
        return {
            'cached': True,
            'items_count': len(cached),
            'sheet_name': sheet_name or '(기본)',
            'message': f"캐시됨 ({len(cached)}개 항목, sheet={sheet_name or '(기본)'})",
        }

    def refresh_cache(self, sheet_name: Optional[str] = None) -> Dict[str, Any]:
        """지정 시트 캐시 무효화 + 시트 재로드. 사용자/관리자 호출용."""
        try:
            self.cache_manager.invalidate_help_cache(sheet_name=sheet_name)
            new_items = self._load_from_sheet(sheet_name=sheet_name)
            item_count = len(new_items) if new_items else 0
            if new_items:
                self.cache_manager.cache_help_items(
                    [{'명령어': it.command, '설명': it.description} for it in new_items],
                    sheet_name=sheet_name,
                )
            return {
                'success': True,
                'new_items_count': item_count,
                'sheet_name': sheet_name or '(기본)',
                'message': (
                    f"도움말 캐시 새로고침 완료 "
                    f"({item_count}개 항목, sheet={sheet_name or '(기본)'})"
                ),
            }
        except Exception as e:
            logger.error(f"캐시 새로고침 실패: {e}")
            return {'success': False, 'error': str(e)}


class HelpTextGenerator:
    """도움말 텍스트 생성 유틸리티.

    출력 포맷:
        도움말

        [명령어] 설명
        [명령어] 설명
        ...

    맨 위에 "도움말" 한 줄 + 빈 줄, 그 아래 시트 행을 그대로 `<명령어> <설명>`.
    명령어가 이미 대괄호를 포함하면 그대로 사용, 아니면 자동으로 감쌈.
    """

    HEADER: str = "도움말\n\n"
    """본문 앞에 붙는 제목 + 빈 줄."""

    # 기본 도움말 본문 (시트가 비었거나 미연결 시 폴백). HEADER 는 별도로 붙음.
    DEFAULT_HELP_BODY = (
        "[NdM] / [NdM+K] / [NdM-K] N개의 M면 주사위를 굴리고 K 를 더하거나 뺍니다.\n"
        "[NdM<K] M면 주사위를 N개 굴리고, K 이하면 성공.\n"
        "[NdM>K] M면 주사위를 N개 굴리고, K 이상이면 성공.\n"
        "[랜덤/옵션1, 옵션2, ...] 옵션 중 하나를 무작위로 선택.\n"
        "[YN] / [yn] '예' 또는 '아니오' 를 무작위로 반환.\n"
        "[도움말] 이 도움말을 보여줍니다."
    )

    # 옛 호출자 호환을 위해 유지 — `HEADER + DEFAULT_HELP_BODY`.
    DEFAULT_HELP = HEADER + DEFAULT_HELP_BODY

    @classmethod
    def generate_help_text(cls, help_items: List[HelpItem]) -> str:
        """도움말 텍스트 생성. 시트 행을 `[명령어] 설명` 한 줄씩으로 묶는다."""
        if not help_items:
            logger.info("도움말 항목 없음, 기본 도움말 사용")
            return cls.DEFAULT_HELP

        valid_items = [item for item in help_items if item.is_valid]
        if not valid_items:
            logger.warning("유효한 도움말 항목 없음, 기본 도움말 사용")
            return cls.DEFAULT_HELP

        body = "\n".join(
            f"{item.formatted_command} {item.description}"
            for item in valid_items
        )
        return cls.HEADER + body

    @classmethod
    def count_commands_in_text(cls, help_text: str) -> int:
        """도움말 텍스트에서 명령어 개수 계산 (정규식 사용).

        새 포맷은 `[명령어] 설명` (대시 없음). 같은 줄에 `[A] / [B] 설명`
        같이 여러 대괄호가 들어 있어도 라인 단위로만 1회 카운트.
        """
        if not help_text:
            return 0

        # 라인 시작이 `[...]` 인 패턴. 같은 라인의 추가 [B] 는 무시.
        pattern = r'^\s*\[[^\]]+\]'
        matches = re.findall(pattern, help_text, re.MULTILINE)
        return len(matches)
    
    @classmethod
    def extract_commands_from_text(cls, help_text: str) -> List[str]:
        """도움말 텍스트에서 명령어들 추출"""
        if not help_text:
            return []
        
        # [명령어] 패턴 찾기
        pattern = r'\[([^\]]+)\]'
        matches = re.findall(pattern, help_text)
        
        # 중복 제거하고 정리
        commands = []
        for match in matches:
            command = match.strip()
            if command and command not in commands:
                commands.append(command)
        
        return commands


class HelpValidator:
    """도움말 데이터 유효성 검증"""
    
    def __init__(self, data_loader: HelpDataLoader):
        self.data_loader = data_loader
    
    def validate_help_data(self) -> Dict[str, Any]:
        """도움말 데이터 유효성 검증"""
        results = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'info': {}
        }
        
        try:
            # 시트 데이터 검증
            self._validate_sheet_data(results)
            
            # 캐시 상태 검증
            self._validate_cache_status(results)
            
            # 기본 도움말 검증
            self._validate_default_help(results)
            
            # 오류가 있으면 유효하지 않음
            if results['errors']:
                results['valid'] = False
            
        except Exception as e:
            logger.error(f"도움말 데이터 검증 중 예외 발생: {e}")
            results['valid'] = False
            results['errors'].append(f"검증 중 오류: {str(e)}")
        
        return results
    
    def _validate_sheet_data(self, results: Dict[str, Any]) -> None:
        """시트 데이터 검증"""
        if not self.data_loader.sheets_manager:
            results['warnings'].append("시트 매니저가 없습니다. 기본 도움말을 사용합니다.")
            results['info']['will_use_default'] = True
            return
        
        try:
            help_items = self.data_loader._load_from_sheet()
            if not help_items:
                results['warnings'].append("시트에 도움말 데이터가 없습니다.")
                results['info']['will_use_default'] = True
            else:
                results['info']['sheet_items_count'] = len(help_items)
                results['info']['will_use_default'] = False
                
                # 중복 명령어 확인
                commands = [item.command for item in help_items]
                duplicates = [cmd for cmd in set(commands) if commands.count(cmd) > 1]
                if duplicates:
                    results['warnings'].append(f"중복된 명령어: {', '.join(duplicates)}")
                
                # 대괄호 형식 확인
                invalid_format = [item.command for item in help_items 
                                if not (item.command.startswith('[') and item.command.endswith(']'))]
                if invalid_format:
                    results['warnings'].append(f"대괄호 형식이 아닌 명령어: {', '.join(invalid_format[:3])}...")
        
        except Exception as e:
            results['errors'].append(f"시트 데이터 로드 실패: {str(e)}")
            results['info']['will_use_default'] = True
    
    def _validate_cache_status(self, results: Dict[str, Any]) -> None:
        """캐시 상태 검증"""
        try:
            cache_status = self.data_loader.get_cache_status()
            results['info']['cache_available'] = cache_status.get('cached', False)
            results['info']['cache_expired'] = cache_status.get('expired', False)
            
            if cache_status.get('cached'):
                results['info']['cache_age_minutes'] = cache_status.get('age_minutes', 0)
                results['info']['cache_remaining_minutes'] = cache_status.get('remaining_minutes', 0)
                results['info']['cached_items_count'] = cache_status.get('items_count', 0)
                
        except Exception as e:
            logger.debug(f"캐시 상태 확인 실패: {e}")
            results['info']['cache_available'] = False
    
    def _validate_default_help(self, results: Dict[str, Any]) -> None:
        """기본 도움말 검증"""
        try:
            default_command_count = HelpTextGenerator.count_commands_in_text(
                HelpTextGenerator.DEFAULT_HELP
            )
            results['info']['default_command_count'] = default_command_count
        except Exception as e:
            logger.warning(f"기본 도움말 검증 실패: {e}")
            results['warnings'].append("기본 도움말 검증 실패")


@dataclass
class DummyHelpResult:
    """HelpResult가 없을 때 사용할 더미 결과 클래스"""
    help_text: str
    command_count: int


@register_command(
    name="도움말",
    aliases=["help", "헬프"],
    description="사용 가능한 명령어 도움말 표시",
    category="시스템",
    examples=["[도움말]", "[help]", "[헬프]"],
    requires_sheets=True,
    requires_api=False
)
class HelpCommand(BaseCommand):
    """
    도움말 명령어 클래스
    
    Google Sheets의 '도움말' 시트에서 명령어 정보를 가져와 표시합니다.
    
    지원하는 형식:
    - [도움말] : 모든 명령어 도움말 표시
    - [help] : 영문 도움말 명령어
    - [헬프] : 도움말 별칭
    """
    
    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.data_loader = HelpDataLoader(sheets_manager, bot_cache)
        self.validator = HelpValidator(self.data_loader)

    def execute(self, context: CommandContext) -> CommandResponse: # type: ignore
        """도움말 명령어 실행 (캐시 우선, 기본 시트)."""
        try:
            help_items = self.data_loader.load_help_items(sheet_name=None)

            help_text = HelpTextGenerator.generate_help_text(help_items)
            command_count = HelpTextGenerator.count_commands_in_text(help_text)
            help_result = self._build_help_result(help_text, command_count)

            logger.info(f"도움말 명령어 실행 완료: {command_count}개 명령어")
            return CommandResponse.create_success(help_text, data=help_result)

        except Exception as e:
            logger.error(f"도움말 명령어 실행 실패: {e}", exc_info=True)
            # 사용자 메시지에는 내부 오류 본문 노출하지 않음.
            return CommandResponse.create_error(
                "도움말을 불러오는 중 오류가 발생했습니다.",
                error=e,
            )

    def _build_help_result(self, help_text: str, command_count: int):
        """HelpResult 객체 생성. 모델 import 실패/예외 시 더미 객체 폴백."""
        if DUMMY_MODE:
            return DummyHelpResult(help_text, command_count)
        try:
            return create_help_result(help_text, command_count)
        except Exception as e:
            logger.warning(f"HelpResult 생성 실패, 더미 객체 사용: {e}")
            return DummyHelpResult(help_text, command_count)
    
    def validate_context(self, context: CommandContext) -> Optional[str]: # type: ignore
        """컨텍스트 유효성 검증 (오버라이드)"""
        # 기본 검증
        base_validation = super().validate_context(context)
        if base_validation:
            return base_validation
        
        # 도움말은 특별한 추가 검증이 필요하지 않음
        return None
    
    def get_help_statistics(self) -> Dict[str, Any]:
        """도움말 통계 정보 반환"""
        try:
            help_items = self.data_loader.load_help_items()
            help_text = HelpTextGenerator.generate_help_text(help_items)
            
            # 시트 항목 수 확인
            sheet_items = self.data_loader._load_from_sheet()
            sheet_items_count = len(sheet_items) if sheet_items else 0
            
            # 캐시 상태 확인
            cache_status = self.data_loader.get_cache_status()
            
            stats = {
                'total_help_items': len(help_items),
                'sheet_items_count': sheet_items_count,
                'using_default_help': sheet_items_count == 0,
                'command_count_in_help': HelpTextGenerator.count_commands_in_text(help_text),
                'cache_status': cache_status
            }
            
            # 캐시 관련 정보 추가
            if cache_status.get('cached'):
                stats.update({
                    'cache_available': True,
                    'cache_expired': cache_status.get('expired', False),
                    'cache_age_minutes': cache_status.get('age_minutes', 0),
                    'cache_remaining_minutes': cache_status.get('remaining_minutes', 0)
                })
            else:
                stats['cache_available'] = False
            
            return stats
            
        except Exception as e:
            logger.error(f"도움말 통계 조회 실패: {e}")
            return {'error': str(e)}
    
    def get_available_help_commands(self) -> List[str]:
        """도움말에서 사용 가능한 명령어 목록 추출"""
        try:
            help_items = self.data_loader.load_help_items()
            commands = []
            
            for item in help_items:
                if item.is_valid:
                    # 대괄호 제거
                    command = item.command
                    if command.startswith('[') and command.endswith(']'):
                        command = command[1:-1]
                    commands.append(command)
            
            return commands
        except Exception as e:
            logger.error(f"도움말 명령어 목록 추출 실패: {e}")
            return []
    
    def search_help_by_keyword(self, keyword: str) -> List[HelpItem]:
        """키워드로 도움말 항목 검색"""
        try:
            help_items = self.data_loader.load_help_items()
            if not keyword:
                return help_items
            
            return [item for item in help_items if item.matches_keyword(keyword)]
            
        except Exception as e:
            logger.error(f"도움말 검색 실패: {e}")
            return []
    
    def validate_help_data(self) -> Dict[str, Any]:
        """도움말 데이터 유효성 검증"""
        return self.validator.validate_help_data()
    
    def refresh_help_cache(self) -> Dict[str, Any]:
        """도움말 캐시 새로고침"""
        return self.data_loader.refresh_cache()

    @staticmethod
    def get_supported_keywords() -> List[str]:
        """지원 키워드 (대표 우선)"""
        return ['도움말', 'help', '헬프']


# 유틸리티 함수들
def is_help_command(keyword: str) -> bool:
    """키워드가 도움말 명령어인지 확인"""
    if not keyword:
        return False
    
    keyword = keyword.lower().strip()
    return keyword in ['도움말', 'help', '헬프']


def generate_simple_help(commands_info: List[Dict[str, str]]) -> str:
    """간단한 도움말 텍스트 생성"""
    if not commands_info:
        return "사용 가능한 명령어가 없습니다."
    
    help_items = []
    for info in commands_info:
        command = info.get('command', '').strip()
        description = info.get('description', '').strip()
        
        if command and description:
            help_item = HelpItem(command, description)
            help_items.append(help_item)
    
    if not help_items:
        return "유효한 명령어 정보가 없습니다."
    
    return HelpTextGenerator.generate_help_text(help_items)


def create_help_command(sheets_manager=None, api=None) -> HelpCommand:
    """도움말 명령어 인스턴스 생성"""
    return HelpCommand(sheets_manager=sheets_manager, api=api)