"""
명령어 레지스트리 - 개선된 버전
모든 명령어를 자동 발견하고 관리하는 중앙 시스템
"""

import os
import sys
import importlib
import inspect
from pathlib import Path
from typing import Dict, List, Type, Optional, Any, Set, Union
from dataclasses import dataclass, field
from enum import Enum
import logging

# 경로 설정
logger = logging.getLogger(__name__)

# should_log_debug 헬퍼 함수 (logging_config에서 임포트 시도)
try:
    from utils.logging_config import should_log_debug
except ImportError:
    def should_log_debug():
        return False  # 임포트 실패 시 기본적으로 DEBUG 로그 비활성화


def _keyword_variants(keyword: str) -> List[str]:
    """키워드의 정규화된 검색 후보들.

    레지스트리는 사용자 입력의 대소문자/공백 차이를 흡수해야 한다. 같은 키워드를
    여러 형태로 등록·조회하기 위한 단일 진입점 — `keyword_lower.replace(' ', '')`
    같은 인라인 정규화를 5+ 곳에서 반복하던 것을 한 곳으로 모은다.

    Args:
        keyword: 원본 키워드 문자열

    Returns:
        ["lower"] 또는 ["lower", "nospace"] (공백을 포함한 키워드만 후자도 포함).
        빈 입력은 빈 리스트.
    """
    if not keyword:
        return []
    lower = keyword.lower()
    nospace = lower.replace(' ', '')
    if nospace == lower:
        return [lower]
    return [lower, nospace]


@dataclass
class CommandMetadata:
    """명령어 메타데이터 (개선됨)"""
    name: str                           # 기본 명령어 이름
    aliases: List[str] = field(default_factory=list)  # 별칭들
    description: str = ""               # 설명
    category: str = "기타"              # 카테고리
    examples: List[str] = field(default_factory=list)  # 사용 예시
    admin_only: bool = False            # 관리자 전용 여부
    enabled: bool = True                # 활성화 여부
    priority: int = 0                   # 우선순위 (높을수록 먼저 검사)
    requires_sheets: bool = True        # Google Sheets 필요 여부
    requires_api: bool = False          # Mastodon API 필요 여부
    source: str = "unknown"             # 메타데이터 소스 ("decorator", "class_attr", "inferred")
    command_package: str = ""           # 명령어가 속한 서브패키지 (default, system, coc, trpg_common)
    
    def __post_init__(self):
        """초기화 후 검증 및 정리"""
        # 이름 정리
        if not self.name:
            raise ValueError("명령어 이름이 비어있습니다.")
        self.name = self.name.strip().lower()
        
        # 별칭 정리
        # 원본 표기 유지 (소문자 변환 금지). 검색/매칭은 별도 로직에서 소문자 처리
        self.aliases = [str(alias).strip() for alias in self.aliases if str(alias).strip()]
        
        # 설명 정리
        if self.description:
            self.description = self.description.strip()
        
        # 카테고리 정리
        if self.category:
            self.category = self.category.strip()
    
    def get_all_keywords(self) -> List[str]:
        """모든 키워드 반환 (이름 + 별칭)"""
        return [self.name] + self.aliases
    
    def matches_keyword(self, keyword: str) -> bool:
        """키워드가 이 명령어와 매치되는지 확인 (공백 무시)"""
        candidates = set(_keyword_variants(keyword))
        if not candidates:
            return False
        for k in self.get_all_keywords():
            if any(v in candidates for v in _keyword_variants(k)):
                return True
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'name': self.name,
            'aliases': self.aliases,
            'description': self.description,
            'category': self.category,
            'examples': self.examples,
            'admin_only': self.admin_only,
            'enabled': self.enabled,
            'priority': self.priority,
            'requires_sheets': self.requires_sheets,
            'requires_api': self.requires_api,
            'source': self.source,
            'all_keywords': self.get_all_keywords()
        }


@dataclass
class RegisteredCommand:
    """등록된 명령어 정보 (개선됨)"""
    command_class: Type                 # 명령어 클래스
    metadata: CommandMetadata           # 메타데이터
    module_name: str                    # 모듈 이름
    file_path: str                      # 파일 경로
    instance: Optional[Any] = None      # 인스턴스 (지연 생성)
    registration_time: Optional[float] = None  # 등록 시간 (timestamp)
    
    def __post_init__(self):
        """초기화 후 검증 (안전성 개선)"""
        import time
        
        # 등록 시간 설정
        if self.registration_time is None:
            self.registration_time = time.time()
        
        # 명령어 클래스 검증 (더 안전하게)
        if not self._validate_command_class():
            raise ValueError(f"유효하지 않은 명령어 클래스: {self.command_class.__name__}")
    
    def _validate_command_class(self) -> bool:
        """명령어 클래스 유효성 검증 (레거시 지원)"""
        if not inspect.isclass(self.command_class):
            return False

        # execute 또는 _execute_command 메서드 존재 여부 확인 (레거시 지원)
        has_execute = hasattr(self.command_class, 'execute')
        has_execute_command = hasattr(self.command_class, '_execute_command')

        if not (has_execute or has_execute_command):
            return False

        # execute 메서드가 있으면 검증
        if has_execute:
            execute_method = getattr(self.command_class, 'execute')
            if not callable(execute_method):
                return False

            # execute 메서드 시그니처 기본 검증
            try:
                sig = inspect.signature(execute_method)
                params = list(sig.parameters.keys())
                # 최소한 self와 context(또는 user, keywords) 파라미터 필요
                if len(params) < 2:
                    logger.warning(f"명령어 클래스 {self.command_class.__name__}의 execute 메서드 시그니처가 이상합니다.")
            except Exception as e:
                logger.debug(f"시그니처 검증 실패 ({self.command_class.__name__}): {e}")

        # _execute_command 메서드가 있으면 검증
        if has_execute_command:
            execute_command_method = getattr(self.command_class, '_execute_command')
            if not callable(execute_command_method):
                return False

        return True
    
    def can_create_instance(self) -> bool:
        """인스턴스 생성 가능 여부 확인 (안전한 테스트)"""
        try:
            # BaseCommand 스타일 (sheets_manager, api 인수)
            test_instance = self.command_class(None, None)
            return True
        except TypeError:
            try:
                # 인수 없는 생성자
                test_instance = self.command_class()
                return True
            except Exception:
                pass
        except Exception:
            pass
        
        return False
    
    def get_instance_safely(self, *args, **kwargs) -> Optional[Any]:
        """안전하게 인스턴스 생성"""
        try:
            return self.command_class(*args, **kwargs)
        except Exception as e:
            logger.error(
                f"명령어 인스턴스 생성 실패 ({self.command_class.__name__}): {e}",
                exc_info=True,
            )
            return None
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'class_name': self.command_class.__name__,
            'module_name': self.module_name,
            'file_path': self.file_path,
            'metadata': self.metadata.to_dict(),
            'has_instance': self.instance is not None,
            'can_create_instance': self.can_create_instance(),
            'registration_time': self.registration_time
        }


class CommandCategory(Enum):
    """명령어 카테고리."""
    DICE = "다이스"
    GAME = "게임"
    UTILITY = "유틸리티"
    ADMIN = "관리자"
    SYSTEM = "시스템"
    OTHER = "기타"
    # 룰 카테고리는 각 명령어가 문자열로 직접 지정 (예: "CoC")
    
    @classmethod
    def get_category_value(cls, category: Union[str, 'CommandCategory']) -> str:
        """카테고리를 문자열로 변환"""
        if isinstance(category, cls):
            return category.value
        return str(category)
    
    @classmethod
    def is_valid_category(cls, category: str) -> bool:
        """유효한 카테고리인지 확인"""
        return category in [c.value for c in cls]


class CommandRegistry:
    """
    명령어 레지스트리 - 개선된 버전
    
    모든 명령어를 자동으로 발견하고 관리합니다.
    안전성과 예외 처리가 대폭 개선되었습니다.
    """
    
    _instance: Optional['CommandRegistry'] = None
    
    def __new__(cls):
        """싱글톤 패턴"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """레지스트리 초기화"""
        if hasattr(self, '_initialized'):
            return
        
        self._commands: Dict[str, RegisteredCommand] = {}
        self._keyword_map: Dict[str, str] = {}  # keyword -> command_name
        self._categories: Dict[str, List[str]] = {}  # category -> command_names
        self._command_types: Set[str] = set()  # 동적 CommandType용
        self._discovery_paths: List[Path] = []
        self._excluded_files: Set[str] = {
            '__init__.py', 'base_command.py', 'registry.py', 'factory.py',
            '__pycache__', '.pyc', 'test_', '_test.py'
        }
        self._discovery_count = 0
        self._last_discovery_time: Optional[float] = None
        self._base_command_available = False
        self._initialized = True
        
        # BaseCommand 가용성 확인
        self._check_base_command_availability()
        
        logger.info("CommandRegistry 초기화 완료")
    
    def _check_base_command_availability(self) -> None:
        """BaseCommand 가용성 확인 (안전한 임포트)"""
        try:
            from commands.base_command import BaseCommand
            self._base_command_available = True
            logger.debug("BaseCommand 임포트 성공")
        except ImportError as e:
            self._base_command_available = False
            logger.debug(f"BaseCommand 임포트 실패 (정상): {e}")
        except Exception as e:
            self._base_command_available = False
            logger.warning(f"BaseCommand 임포트 중 예상치 못한 오류: {e}")
    
    def add_discovery_path(self, path: Union[str, Path]) -> bool:
        """
        명령어 발견 경로 추가 (개선됨)
        
        Args:
            path: 추가할 경로
            
        Returns:
            bool: 추가 성공 여부
        """
        try:
            path_obj = Path(path) if isinstance(path, str) else path
            
            if not path_obj.exists():
                logger.warning(f"존재하지 않는 경로: {path_obj}")
                return False
            
            if not path_obj.is_dir():
                logger.warning(f"디렉토리가 아닌 경로: {path_obj}")
                return False
            
            if path_obj not in self._discovery_paths:
                self._discovery_paths.append(path_obj)
                logger.debug(f"명령어 발견 경로 추가: {path_obj}")
                return True
            else:
                logger.debug(f"이미 추가된 발견 경로: {path_obj}")
                return False
                
        except Exception as e:
            logger.error(f"발견 경로 추가 실패 ({path}): {e}", exc_info=True)
            return False
    
    def discover_commands(self) -> int:
        """
        명령어 자동 발견 (예외 처리 개선)
        
        Returns:
            int: 발견된 명령어 개수
        """
        import time
        
        logger.info("명령어 자동 발견 시작...")
        discovery_start_time = time.time()
        discovered_count = 0
        
        try:
            # 기본 경로 추가
            commands_dir = Path(__file__).parent
            if commands_dir not in self._discovery_paths:
                self.add_discovery_path(commands_dir)

            # 하위 폴더들도 추가 (default, system, trpg_common, coc)
            for subdir in ['default', 'system', 'trpg_common', 'coc']:
                subdir_path = commands_dir / subdir
                if subdir_path.exists() and subdir_path.is_dir():
                    if subdir_path not in self._discovery_paths:
                        self.add_discovery_path(subdir_path)

            # 각 경로 스캔
            for discovery_path in self._discovery_paths:
                try:
                    path_count = self._scan_directory(discovery_path)
                    discovered_count += path_count
                    if should_log_debug():
                        logger.debug(f"경로 {discovery_path}에서 {path_count}개 명령어 발견")
                except Exception as e:
                    logger.error(
                        f"경로 스캔 실패 ({discovery_path}): {e}", exc_info=True
                    )
            
            # 맵 구축
            self._build_all_maps()
            
            # 통계 업데이트
            self._discovery_count += 1
            self._last_discovery_time = time.time()
            
            discovery_time = self._last_discovery_time - discovery_start_time
            logger.info(f"명령어 발견 완료: {discovered_count}개 명령어 등록됨 ({discovery_time:.3f}초 소요)")
            
        except Exception as e:
            logger.error(f"명령어 발견 중 예상치 못한 오류: {e}", exc_info=True)
        
        return discovered_count
    
    def _scan_directory(self, directory: Path) -> int:
        """디렉토리 스캔하여 명령어 파일 찾기 (필터링 개선)"""
        count = 0
        
        try:
            # Python 파일만 필터링
            python_files = list(directory.glob("*.py"))
            
            for file_path in python_files:
                # 제외 파일 확인 (더 정확한 필터링)
                if self._should_exclude_file(file_path):
                    continue
                
                try:
                    file_count = self._load_command_from_file(file_path)
                    count += file_count
                    if file_count > 0:
                        logger.debug(f"파일 {file_path.name}에서 {file_count}개 명령어 로드됨")
                except Exception as e:
                    logger.error(
                        f"명령어 파일 로드 실패: {file_path} - {e}", exc_info=True
                    )
        
        except Exception as e:
            logger.error(f"디렉토리 스캔 실패: {directory} - {e}", exc_info=True)
        
        return count
    
    def _should_exclude_file(self, file_path: Path) -> bool:
        """파일 제외 여부 확인 (개선된 필터링)"""
        file_name = file_path.name
        
        # 기본 제외 파일
        if file_name in self._excluded_files:
            return True
        
        # 패턴 기반 제외
        if file_name.startswith('test_') or file_name.endswith('_test.py'):
            return True
        
        if file_name.startswith('__') and file_name.endswith('__'):
            return True
        
        # 명령어 파일 패턴 확인 (권장사항)
        if file_name.endswith('_command.py'):
            return False
        
        # 기타 Python 파일도 스캔 (BaseCommand를 상속한 클래스가 있을 수 있음)
        return False
    
    def _load_command_from_file(self, file_path: Path) -> int:
        """파일에서 명령어 로드.

        이미 정상 import 경로(예: `from commands.coc.character import X`) 로 로드된
        모듈이면 `sys.modules` 의 인스턴스를 그대로 재사용한다. 그렇지 않을 때만
        새로 spec/exec 한다 — 이 때 반드시 `sys.modules` 에 먼저 등록해야 모듈
        내부의 `@dataclass` 데코레이터가 자기 클래스의 `__module__` 을 통해 모듈을
        다시 찾을 수 있다(Python 3.13 의 `dataclasses._is_type` 가
        `sys.modules.get(cls.__module__).__dict__` 를 직접 참조하므로 미등록 시
        `'NoneType' object has no attribute '__dict__'` 오류).
        """
        count = 0

        try:
            # 모듈 이름 생성 (더 안전하게)
            relative_path = file_path.relative_to(Path(__file__).parent.parent)
            module_parts = list(relative_path.with_suffix('').parts)
            module_name = '.'.join(module_parts)

            logger.debug(f"파일 스캔: {file_path.name}")

            # 1) 이미 임포트된 모듈이 있으면 재사용 — 클래스 동일성 유지에도 유리.
            module = sys.modules.get(module_name)

            # 2) 없으면 새로 로드 (단, sys.modules 에 미리 등록 필수).
            if module is None:
                spec = importlib.util.spec_from_file_location(module_name, file_path)
                if spec is None or spec.loader is None:
                    logger.warning(f"모듈 스펙 생성 실패: {file_path}")
                    return 0

                module = importlib.util.module_from_spec(spec)
                # exec 전에 등록 — 데코레이터/메타클래스가 sys.modules 를 참조할 수 있다.
                sys.modules[module_name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception as e:
                    # 로드 실패 시 stale 엔트리가 남지 않도록 정리.
                    sys.modules.pop(module_name, None)
                    logger.warning(f"모듈 실행 실패: {file_path} - {e}")
                    return 0

            # 모듈에서 명령어 클래스 찾기
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if self._is_command_class(obj, module):
                    try:
                        self._register_command_class(obj, module_name, str(file_path))
                        count += 1
                        logger.debug(f"명령어 클래스 등록: {name} from {file_path.name}")
                    except Exception as e:
                        logger.error(
                            f"명령어 클래스 등록 실패: {name} - {e}", exc_info=True
                        )

        except Exception as e:
            logger.error(f"파일 로드 실패: {file_path} - {e}", exc_info=True)

        return count
    
    def _is_command_class(self, cls: Type, module) -> bool:
        """클래스가 명령어 클래스인지 확인 (개선된 판별 로직)"""
        try:
            class_name = cls.__name__

            # 0. 추상 클래스는 제외 (BaseStoreCommand 등)
            if inspect.isabstract(cls):
                logger.debug(f"  └─ {class_name}: 추상 클래스이므로 제외")
                return False

            # 1. 모듈에 정의된 클래스여야 함
            if cls.__module__ != module.__name__:
                logger.debug(f"  └─ {class_name}: 모듈 불일치 ({cls.__module__} != {module.__name__})")
                return False

            # 2. execute 또는 _execute_command 메서드가 있어야 함 (레거시 지원)
            has_execute = hasattr(cls, 'execute') and callable(getattr(cls, 'execute'))
            has_execute_command = hasattr(cls, '_execute_command') and callable(getattr(cls, '_execute_command'))

            logger.debug(f"  └─ {class_name}: execute={has_execute}, _execute_command={has_execute_command}")

            if not (has_execute or has_execute_command):
                logger.debug(f"  └─ {class_name}: execute 또는 _execute_command 메서드 없음")
                return False
            
            # 3. BaseCommand 상속 확인 (안전하게)
            if self._base_command_available:
                try:
                    from commands.base_command import BaseCommand
                    if issubclass(cls, BaseCommand) and cls != BaseCommand:
                        logger.debug(f"  └─ {class_name}: BaseCommand 상속 확인 ✓ -> 명령어로 등록")
                        return True
                    else:
                        logger.debug(f"  └─ {class_name}: BaseCommand 상속하지 않음")
                except Exception as e:
                    logger.debug(f"  └─ {class_name}: BaseCommand 상속 확인 실패: {e}")

            # 4. 메타데이터나 명령어 관련 속성이 있는지 확인
            command_indicators = [
                '_command_metadata', 'command_name', 'command_aliases',
                'command_description', 'command_category'
            ]

            if any(hasattr(cls, attr) for attr in command_indicators):
                logger.debug(f"  └─ {class_name}: 메타데이터 속성 발견 -> 명령어로 등록")
                return True

            # 5. 클래스 이름 패턴 확인
            class_name_lower = cls.__name__.lower()
            if class_name_lower.endswith('command') and class_name_lower != 'basecommand':
                logger.debug(f"  └─ {class_name}: 'Command' 이름 패턴 -> 명령어로 등록")
                return True

            logger.debug(f"  └─ {class_name}: 명령어 조건 불충족")
            return False
            
        except Exception as e:
            logger.debug(f"명령어 클래스 판별 실패 ({cls.__name__}): {e}")
            return False
    
    def _register_command_class(self, command_class: Type, module_name: str, file_path: str) -> None:
        """명령어 클래스 등록 (예외 처리 개선)"""
        try:
            # 메타데이터 추출
            metadata = self._extract_metadata(command_class)

            # 파일 경로에서 패키지 정보 파악
            metadata.command_package = self._detect_package_from_path(file_path)

            # 중복 이름 확인
            if metadata.name in self._commands:
                existing_cmd = self._commands[metadata.name]
                # 같은 모듈에서 로드하려는 경우 스킵
                if existing_cmd.module_name == module_name:
                    logger.debug(f"동일 모듈에서 중복 로드 시도, 스킵: {metadata.name}")
                    return
                
                logger.warning(f"중복된 명령어 이름: {metadata.name} - "
                             f"기존: {existing_cmd.module_name}, 새로운: {module_name}")
                
                # 우선순위 비교
                if metadata.priority <= existing_cmd.metadata.priority:
                    logger.info(f"기존 명령어 유지: {metadata.name} (우선순위: {existing_cmd.metadata.priority})")
                    return
                else:
                    logger.info(f"새 명령어로 교체: {metadata.name} (우선순위: {metadata.priority})")
            
            # 등록된 명령어 객체 생성
            registered_command = RegisteredCommand(
                command_class=command_class,
                metadata=metadata,
                module_name=module_name,
                file_path=file_path
            )
            
            # 레지스트리에 등록
            self._commands[metadata.name] = registered_command
            
            logger.debug(f"명령어 등록 완료: {metadata.name} (별칭: {metadata.aliases})")
            
        except Exception as e:
            logger.error(
                f"명령어 클래스 등록 실패 ({command_class.__name__}): {e}",
                exc_info=True,
            )
            raise
    
    def _extract_metadata(self, command_class: Type) -> CommandMetadata:
        """클래스에서 메타데이터 추출 (안전성 및 우선순위 개선)"""
        metadata_source = "inferred"
        
        try:
            # 1. 데코레이터에서 추출 시도 (최우선)
            if hasattr(command_class, '_command_metadata'):
                metadata = command_class._command_metadata
                metadata.source = "decorator"

                # get_supported_keywords()가 있으면 데코레이터 메타데이터에 병합 (대표 키워드 우선)
                try:
                    if hasattr(command_class, 'get_supported_keywords'):
                        instance = self._create_test_instance(command_class)
                        if instance and callable(getattr(instance, 'get_supported_keywords', None)):
                            supported_keywords = instance.get_supported_keywords()
                            if supported_keywords and isinstance(supported_keywords, list):
                                # 순서: supported_keywords 먼저, 이후 기존 aliases
                                seen = set()
                                merged: List[str] = []
                                for alias in supported_keywords + (metadata.aliases or []):
                                    alias_str = str(alias).strip()
                                    alias_lower = alias_str.lower()
                                    if alias_str and alias_lower not in seen and alias_lower != metadata.name.lower():
                                        seen.add(alias_lower)
                                        merged.append(alias_str)
                                # 병합 결과를 메타데이터에 반영 (원본 케이스 유지)
                                metadata.aliases = merged
                                logger.debug(f"{command_class.__name__}: 데코레이터 메타데이터에 get_supported_keywords 병합 (대표={merged[0] if merged else 'N/A'})")
                except Exception as e:
                    logger.debug(f"데코레이터 기반 키워드 병합 실패 ({command_class.__name__}): {e}")

                return metadata
            
            # 2. 클래스 속성에서 추출
            # 기본 aliases 가져오기
            aliases = self._safe_getattr(command_class, 'command_aliases', [])

            # get_supported_keywords 메서드가 있으면 동적으로 키워드 추출
            if hasattr(command_class, 'get_supported_keywords'):
                try:
                    instance = self._create_test_instance(command_class)
                    if instance and callable(getattr(instance, 'get_supported_keywords', None)):
                        supported_keywords = instance.get_supported_keywords()
                        if supported_keywords and isinstance(supported_keywords, list):
                            # 모든 키워드를 aliases에 추가 (원본 케이스 유지하되, 검색용으로만 소문자 사용)
                            # 첫 번째 키워드가 대표 키워드이므로 순서 유지
                            additional_aliases = supported_keywords  # 원본 유지
                            # 기존 aliases와 합치되, 순서 유지 (중복 제거)
                            seen = set()
                            unique_aliases = []
                            for alias in additional_aliases + aliases:
                                alias_lower = alias.lower()
                                if alias_lower not in seen:
                                    seen.add(alias_lower)
                                    unique_aliases.append(alias)
                            aliases = unique_aliases
                            logger.debug(f"{command_class.__name__}: get_supported_keywords()에서 {len(supported_keywords)}개 키워드 추출 (대표={supported_keywords[0] if supported_keywords else 'N/A'})")
                except Exception as e:
                    logger.debug(f"get_supported_keywords 호출 실패 ({command_class.__name__}): {e}")

            metadata = CommandMetadata(
                name=self._safe_getattr(command_class, 'command_name',
                                       command_class.__name__.lower().replace('command', '')),
                aliases=aliases,
                description=self._safe_getattr(command_class, 'command_description',
                                              command_class.__doc__ or ''),
                category=self._safe_getattr(command_class, 'command_category', '기타'),
                examples=self._safe_getattr(command_class, 'command_examples', []),
                admin_only=self._safe_getattr(command_class, 'admin_only', False),
                enabled=self._safe_getattr(command_class, 'enabled', True),
                priority=self._safe_getattr(command_class, 'priority', 0),
                requires_sheets=self._safe_getattr(command_class, 'requires_sheets', True),
                requires_api=self._safe_getattr(command_class, 'requires_api', False),
                source="class_attr"
            )
            
            # 3. BaseCommand 메서드에서 추출 시도 (안전하게)
            if self._base_command_available and hasattr(command_class, '_get_command_name'):
                try:
                    if callable(getattr(command_class, '_get_command_name')):
                        # 안전한 인스턴스 생성
                        instance = self._create_test_instance(command_class)
                        if instance:
                            try:
                                extracted_name = instance._get_command_name()
                                if extracted_name:
                                    metadata.name = extracted_name
                                    metadata.source = "method"
                            except Exception as e:
                                logger.debug(f"_get_command_name 호출 실패: {e}")
                except Exception as e:
                    logger.debug(f"BaseCommand 메서드 추출 실패: {e}")
            
            return metadata
            
        except Exception as e:
            logger.error(
                f"메타데이터 추출 실패 ({command_class.__name__}): {e}",
                exc_info=True,
            )
            # 최소한의 메타데이터 반환
            return CommandMetadata(
                name=command_class.__name__.lower().replace('command', ''),
                description="메타데이터 추출 실패",
                source="fallback"
            )
    
    def _detect_package_from_path(self, file_path: str) -> str:
        """파일 경로에서 패키지 정보 파악"""
        try:
            # 경로를 정규화
            normalized_path = file_path.replace('\\', '/')

            # 패키지 목록
            if '/commands/default/' in normalized_path or '\\commands\\default\\' in file_path:
                return 'default'
            elif '/commands/system/' in normalized_path or '\\commands\\system\\' in file_path:
                return 'system'
            elif '/commands/trpg_common/' in normalized_path or '\\commands\\trpg_common\\' in file_path:
                return 'trpg_common'
            elif '/commands/coc/' in normalized_path or '\\commands\\coc\\' in file_path:
                return 'coc'
            else:
                # 루트 commands 폴더에 있는 경우 (base_command.py 등)
                return ''
        except Exception as e:
            logger.debug(f"패키지 감지 실패: {e}")
            return ''

    def _safe_getattr(self, obj: Any, attr: str, default: Any) -> Any:
        """안전한 속성 접근"""
        try:
            value = getattr(obj, attr, default)
            # 빈 문자열이나 None인 경우 기본값 사용
            if value is None or (isinstance(value, str) and not value.strip()):
                return default
            return value
        except Exception:
            return default
    
    def _create_test_instance(self, command_class: Type) -> Optional[Any]:
        """테스트용 인스턴스 생성 (안전하게)"""
        try:
            # 여러 방법으로 시도
            constructors = [
                lambda: command_class(None, None),  # BaseCommand 스타일
                lambda: command_class(None),        # 단일 인수
                lambda: command_class(),            # 인수 없음
            ]
            
            for constructor in constructors:
                try:
                    return constructor()
                except TypeError:
                    continue
                except Exception:
                    continue
            
            logger.debug(f"테스트 인스턴스 생성 실패: {command_class.__name__}")
            return None
            
        except Exception as e:
            logger.debug(f"테스트 인스턴스 생성 중 오류: {e}")
            return None
    
    def _build_all_maps(self) -> None:
        """모든 맵 구축 (통합 메서드)"""
        try:
            self._build_keyword_map()
            self._build_category_map()
            self._build_command_types()
            logger.debug("모든 맵 구축 완료")
        except Exception as e:
            logger.error(f"맵 구축 실패: {e}", exc_info=True)
    
    def _build_keyword_map(self) -> None:
        """키워드 맵 구축 (충돌 처리 개선, 공백 무시 지원)"""
        self._keyword_map.clear()
        conflicts = {}

        # 우선순위 순으로 정렬
        sorted_commands = sorted(
            self._commands.items(),
            key=lambda x: x[1].metadata.priority,
            reverse=True
        )

        def _add_to_map(key: str, command_name: str, metadata_priority: int, label: str = "") -> None:
            """키워드 맵에 항목 추가 (충돌 처리 포함)"""
            if key in self._keyword_map:
                existing_command = self._keyword_map[key]
                if key not in conflicts:
                    conflicts[key] = []
                conflicts[key].append((existing_command, command_name))

                existing_priority = self._commands[existing_command].metadata.priority
                if metadata_priority > existing_priority:
                    self._keyword_map[key] = command_name
                    if label:
                        logger.debug(f"키워드 '{key}' ({label}) 교체: {existing_command} -> {command_name} "
                                   f"(우선순위: {existing_priority} -> {metadata_priority})")
            else:
                self._keyword_map[key] = command_name

        for command_name, registered_command in sorted_commands:
            metadata = registered_command.metadata

            # 모든 키워드를 맵에 추가 (lower / nospace 둘 다, _keyword_variants 가 결정).
            for keyword in metadata.get_all_keywords():
                variants = _keyword_variants(keyword)
                if not variants:
                    continue
                _add_to_map(variants[0], command_name, metadata.priority, "원본")
                for nospace in variants[1:]:
                    _add_to_map(nospace, command_name, metadata.priority, "공백제거")

        # 충돌 로깅
        if conflicts:
            logger.info(f"키워드 충돌 해결됨: {len(conflicts)}개")
            for keyword, conflict_list in conflicts.items():
                logger.debug(f"키워드 '{keyword}' 충돌: {conflict_list}")
    
    def _build_category_map(self) -> None:
        """카테고리 맵 구축"""
        self._categories.clear()
        
        for command_name, registered_command in self._commands.items():
            category = registered_command.metadata.category
            if category not in self._categories:
                self._categories[category] = []
            self._categories[category].append(command_name)
        
        # 각 카테고리 내에서 이름순 정렬
        for category in self._categories:
            self._categories[category].sort()
    
    def _build_command_types(self) -> None:
        """CommandType 세트 구축"""
        self._command_types.clear()
        
        for command_name, registered_command in self._commands.items():
            # 명령어 이름을 CommandType으로 사용
            self._command_types.add(command_name)
            
            # 별칭들도 추가
            for alias in registered_command.metadata.aliases:
                self._command_types.add(alias)
    
    def get_command_by_keyword(self, keyword: str) -> Optional[RegisteredCommand]:
        """키워드로 명령어 찾기 (봇 타입 및 멀티 봇 필터링, 공백 무시)"""
        command_name: Optional[str] = None
        for variant in _keyword_variants(keyword):
            command_name = self._keyword_map.get(variant)
            if command_name:
                break

        if not command_name:
            logger.debug(f"키워드 '{keyword}'에 매핑된 명령어 없음. 등록된 키워드: {list(self._keyword_map.keys())[:10]}")
            return None

        command = self._commands.get(command_name)
        if not command:
            logger.debug(f"명령어 '{command_name}'을 찾을 수 없음")
            return None

        # 명령어가 비활성화되어 있으면 None 반환
        if not command.metadata.enabled:
            logger.debug(f"명령어 '{command_name}'이 비활성화됨")
            return None

        logger.debug(
            f"명령어 '{command_name}' 찾음: 패키지={command.metadata.command_package}"
        )

        return command
    
    def get_command_by_name(self, name: str) -> Optional[RegisteredCommand]:
        """이름으로 명령어 찾기"""
        if not name:
            return None
        return self._commands.get(name.lower())

    def get_all_commands(self) -> Dict[str, RegisteredCommand]:
        """모든 등록된 명령어 반환"""
        return self._commands.copy()
    
    def get_all_command_names(self) -> List[str]:
        """모든 명령어 이름 반환"""
        return list(self._commands.keys())
    
    def get_commands_by_category(self, category: str) -> List[RegisteredCommand]:
        """카테고리별 명령어 반환"""
        command_names = self._categories.get(category, [])
        return [self._commands[name] for name in command_names if name in self._commands]
    
    def get_enabled_commands(self) -> Dict[str, RegisteredCommand]:
        """활성화된 명령어만 반환."""
        return {
            name: cmd
            for name, cmd in self._commands.items()
            if cmd.metadata.enabled
        }
    
    def get_command_types(self) -> Set[str]:
        """동적 CommandType 반환"""
        return self._command_types.copy()
    
    def get_all_keywords(self) -> List[str]:
        """모든 키워드 반환"""
        return list(self._keyword_map.keys())
    
    def get_categories(self) -> List[str]:
        """모든 카테고리 반환"""
        return list(self._categories.keys())
    
    def is_system_keyword(self, keyword: str) -> bool:
        """시스템 키워드인지 확인 (공백 무시)"""
        return any(v in self._keyword_map for v in _keyword_variants(keyword))
    
    def enable_command(self, command_name: str) -> bool:
        """명령어 활성화"""
        if not command_name:
            return False
        
        command_name = command_name.lower()
        if command_name in self._commands:
            self._commands[command_name].metadata.enabled = True
            logger.info(f"명령어 활성화: {command_name}")
            return True
        return False
    
    def disable_command(self, command_name: str) -> bool:
        """명령어 비활성화"""
        if not command_name:
            return False
        
        command_name = command_name.lower()
        if command_name in self._commands:
            self._commands[command_name].metadata.enabled = False
            logger.info(f"명령어 비활성화: {command_name}")
            return True
        return False
    
    def reload_commands(self) -> int:
        """명령어 재로드"""
        logger.info("🔄 명령어 재로드 시작...")

        # 기존 데이터 클리어
        old_count = len(self._commands)
        self._commands.clear()
        self._keyword_map.clear()
        self._categories.clear()
        self._command_types.clear()

        # 다시 발견
        new_count = self.discover_commands()

        logger.info(f"✅ 명령어 재로드 완료 | {old_count} → {new_count}")
        return new_count
    
    def validate_all_commands(self) -> Dict[str, Any]:
        """모든 명령어 유효성 검증 (개선됨)"""
        validation_result = {
            'valid': True,
            'total_commands': len(self._commands),
            'errors': [],
            'warnings': [],
            'command_results': {},
            'statistics': {
                'valid_commands': 0,
                'warning_commands': 0,
                'error_commands': 0,
                'instance_creation_failures': 0
            }
        }
        
        for command_name, registered_command in self._commands.items():
            command_validation = {
                'status': 'valid',
                'issues': []
            }
            
            try:
                # 1. 기본 정보 검증
                if not registered_command.metadata.name:
                    command_validation['issues'].append("명령어 이름이 없습니다.")
                    command_validation['status'] = 'error'
                
                # 2. 클래스 검증 (execute 또는 _execute_command 메서드 확인)
                has_execute = hasattr(registered_command.command_class, 'execute') and callable(getattr(registered_command.command_class, 'execute'))
                has_execute_command = hasattr(registered_command.command_class, '_execute_command') and callable(getattr(registered_command.command_class, '_execute_command'))

                if not (has_execute or has_execute_command):
                    command_validation['issues'].append("execute 또는 _execute_command 메서드가 없습니다.")
                    command_validation['status'] = 'error'
                
                # 3. 메타데이터 품질 검증
                if not registered_command.metadata.description:
                    command_validation['issues'].append("설명이 없습니다.")
                    if command_validation['status'] == 'valid':
                        command_validation['status'] = 'warning'
                
                # 4. 인스턴스 생성 테스트 (안전하게)
                if not registered_command.can_create_instance():
                    command_validation['issues'].append("인스턴스 생성 실패")
                    validation_result['statistics']['instance_creation_failures'] += 1
                    if command_validation['status'] == 'valid':
                        command_validation['status'] = 'warning'
                
                # 5. 키워드 중복 검사
                conflicts = self._check_keyword_conflicts(registered_command)
                if conflicts:
                    command_validation['issues'].append(f"키워드 충돌: {', '.join(conflicts)}")
                    if command_validation['status'] == 'valid':
                        command_validation['status'] = 'warning'
                
                # 통계 업데이트
                if command_validation['status'] == 'valid':
                    validation_result['statistics']['valid_commands'] += 1
                elif command_validation['status'] == 'warning':
                    validation_result['statistics']['warning_commands'] += 1
                    validation_result['warnings'].extend([
                        f"명령어 '{command_name}': {issue}" for issue in command_validation['issues']
                    ])
                else:  # error
                    validation_result['statistics']['error_commands'] += 1
                    validation_result['errors'].extend([
                        f"명령어 '{command_name}': {issue}" for issue in command_validation['issues']
                    ])
                    validation_result['valid'] = False
                    registered_command.metadata.enabled = False
                    logger.warning("검증 실패로 명령어 비활성화: %s", command_name)

                validation_result['command_results'][command_name] = command_validation

            except Exception as e:
                error_msg = f"명령어 '{command_name}' 검증 중 오류: {e}"
                validation_result['errors'].append(error_msg)
                validation_result['valid'] = False
                validation_result['command_results'][command_name] = {
                    'status': 'error',
                    'issues': [str(e)]
                }
                validation_result['statistics']['error_commands'] += 1
                registered_command.metadata.enabled = False
                logger.warning("검증 실패로 명령어 비활성화: %s", command_name)
        
        return validation_result
    
    def _check_keyword_conflicts(self, registered_command: RegisteredCommand) -> List[str]:
        """특정 명령어의 키워드 충돌 검사"""
        conflicts = []
        
        for keyword in registered_command.metadata.get_all_keywords():
            keyword_lower = keyword.lower()
            mapped_command = self._keyword_map.get(keyword_lower)
            
            # 다른 명령어에 매핑되어 있으면 충돌
            if mapped_command and mapped_command != registered_command.metadata.name:
                conflicts.append(f"{keyword} -> {mapped_command}")
        
        return conflicts
    
    def get_statistics(self) -> Dict[str, Any]:
        """레지스트리 통계 반환 (개선됨)"""
        enabled_commands = self.get_enabled_commands()
        total_count = len(self._commands)
        enabled_count = len(enabled_commands)
        
        category_stats = {}
        for category, commands in self._categories.items():
            category_stats[category] = {
                'total': len(commands),
                'enabled': len([cmd for cmd in commands if cmd in enabled_commands])
            }
        
        # 우선순위 분포
        priority_stats = {}
        for cmd in self._commands.values():
            priority = cmd.metadata.priority
            priority_stats[priority] = priority_stats.get(priority, 0) + 1
        
        # 소스 분포 (메타데이터 출처)
        source_stats = {}
        for cmd in self._commands.values():
            source = cmd.metadata.source
            source_stats[source] = source_stats.get(source, 0) + 1
        
        return {
            'total_commands': total_count,
            'enabled_commands': enabled_count,
            'disabled_commands': total_count - enabled_count,
            'total_keywords': len(self._keyword_map),
            'total_categories': len(self._categories),
            'discovery_count': self._discovery_count,
            'last_discovery_time': self._last_discovery_time,
            'base_command_available': self._base_command_available,
            'category_stats': category_stats,
            'priority_distribution': priority_stats,
            'metadata_sources': source_stats,
            'discovery_paths': [str(p) for p in self._discovery_paths]
        }
    
    def get_help_data(self) -> List[Dict[str, str]]:
        """도움말용 데이터 반환 (개선됨)"""
        help_data = []
        
        # 활성화된 명령어만 포함
        enabled_commands = self.get_enabled_commands()
        
        for command_name, registered_command in enabled_commands.items():
            metadata = registered_command.metadata
            
            # 키워드 목록 생성 (더 깔끔하게)
            keywords = metadata.get_all_keywords()
            keyword_str = f"[{keywords[0]}]"
            if len(keywords) > 1:
                alias_str = ', '.join([f"[{k}]" for k in keywords[1:]])
                keyword_str += f" (별칭: {alias_str})"
            
            # 예시 생성
            examples_str = ""
            if metadata.examples:
                examples_str = "\n💡 예시: " + ", ".join(metadata.examples)
            
            # 관리자 전용 표시
            admin_info = ""
            if metadata.admin_only:
                admin_info = " 🔒"
            
            # 의존성 정보
            dependency_info = ""
            deps = []
            if metadata.requires_sheets:
                deps.append("Sheets")
            if metadata.requires_api:
                deps.append("API")
            if deps:
                dependency_info = f" (필요: {', '.join(deps)})"
            
            help_data.append({
                'category': metadata.category,
                'command': keyword_str + admin_info,
                'description': metadata.description + examples_str + dependency_info,
                'priority': metadata.priority,
                'admin_only': "관리자 전용" if metadata.admin_only else ""
            })
        
        # 카테고리별, 우선순위별, 이름순으로 정렬
        help_data.sort(key=lambda x: (x['category'], -x['priority'], x['command']))
        
        return help_data
    
    def get_command_info(self, command_name: str) -> Optional[Dict[str, Any]]:
        """특정 명령어의 상세 정보 반환"""
        if not command_name:
            return None
        
        registered_command = self.get_command_by_name(command_name)
        if not registered_command:
            return None
        
        return registered_command.to_dict()
    
    def search_commands(self, query: str) -> List[Dict[str, Any]]:
        """명령어 검색"""
        if not query:
            return []
        
        query_lower = query.lower()
        results = []
        
        for command_name, registered_command in self._commands.items():
            metadata = registered_command.metadata
            score = 0
            
            # 이름 매치
            if query_lower in metadata.name.lower():
                score += 10
            
            # 별칭 매치
            for alias in metadata.aliases:
                if query_lower in alias.lower():
                    score += 8
            
            # 설명 매치
            if query_lower in metadata.description.lower():
                score += 3
            
            # 카테고리 매치
            if query_lower in metadata.category.lower():
                score += 2
            
            if score > 0:
                result = registered_command.to_dict()
                result['search_score'] = score
                results.append(result)
        
        # 점수순으로 정렬
        results.sort(key=lambda x: x['search_score'], reverse=True)
        return results
    
    def export_registry_data(self) -> Dict[str, Any]:
        """레지스트리 데이터 내보내기 (백업/분석용)"""
        export_data = {
            'metadata': {
                'total_commands': len(self._commands),
                'discovery_count': self._discovery_count,
                'last_discovery_time': self._last_discovery_time,
                'base_command_available': self._base_command_available,
                'discovery_paths': [str(p) for p in self._discovery_paths]
            },
            'commands': {},
            'keyword_map': self._keyword_map.copy(),
            'categories': self._categories.copy(),
            'command_types': list(self._command_types),
            'statistics': self.get_statistics()
        }
        
        # 명령어 정보 (인스턴스 제외)
        for name, registered_command in self._commands.items():
            export_data['commands'][name] = registered_command.to_dict()
        
        return export_data


# 전역 레지스트리 인스턴스
registry = CommandRegistry()


def register_command(
    name: str,
    aliases: List[str] = None,
    description: str = "",
    category: str = "기타",
    examples: List[str] = None,
    admin_only: bool = False,
    enabled: bool = True,
    priority: int = 0,
    requires_sheets: bool = True,
    requires_api: bool = False
):
    """
    명령어 등록 데코레이터 (개선됨)
    
    사용법:
    @register_command("dice", aliases=["다이스"], description="주사위 굴리기")
    class DiceCommand(BaseCommand):
        def execute(self, context):
            return CommandResponse.create_success("주사위 결과!")
    """
    def decorator(command_class: Type) -> Type:
        try:
            metadata = CommandMetadata(
                name=name,
                aliases=aliases or [],
                description=description,
                category=category,
                examples=examples or [],
                admin_only=admin_only,
                enabled=enabled,
                priority=priority,
                requires_sheets=requires_sheets,
                requires_api=requires_api,
                source="decorator"
            )
            
            # 클래스에 메타데이터 첨부
            command_class._command_metadata = metadata
            
            # BaseCommand의 클래스 속성도 업데이트 (하위 호환성)
            command_class.command_name = name
            command_class.command_aliases = aliases or []
            command_class.command_description = description
            command_class.command_category = category
            command_class.command_examples = examples or []
            command_class.admin_only = admin_only
            command_class.enabled = enabled
            command_class.priority = priority
            command_class.requires_sheets = requires_sheets
            command_class.requires_api = requires_api
            
            logger.debug(f"명령어 데코레이터 적용: {name}")
            
        except Exception as e:
            logger.error(
                f"명령어 데코레이터 적용 실패 ({name}): {e}", exc_info=True
            )
            raise
        
        return command_class
    
    return decorator


def get_registry() -> CommandRegistry:
    """전역 레지스트리 반환"""
    return registry


def discover_all_commands() -> int:
    """모든 명령어 발견 (편의 함수)"""
    return registry.discover_commands()


def get_command_by_keyword(keyword: str) -> Optional[RegisteredCommand]:
    """키워드로 명령어 찾기 (편의 함수)"""
    return registry.get_command_by_keyword(keyword)


def validate_registry() -> Dict[str, Any]:
    """레지스트리 검증 (편의 함수)"""
    return registry.validate_all_commands()


def get_registry_statistics() -> Dict[str, Any]:
    """레지스트리 통계 (편의 함수)"""
    return registry.get_statistics()


# 개발자를 위한 유틸리티
def debug_registry() -> str:
    """레지스트리 디버그 정보 출력 (개발용)"""
    try:
        stats = registry.get_statistics()
        validation = registry.validate_all_commands()
        
        debug_info = []
        debug_info.append("=== CommandRegistry 디버그 정보 ===")
        
        # 기본 통계
        debug_info.append(f"총 명령어: {stats['total_commands']}개")
        debug_info.append(f"활성화된 명령어: {stats['enabled_commands']}개")
        debug_info.append(f"비활성화된 명령어: {stats['disabled_commands']}개")
        debug_info.append(f"총 키워드: {stats['total_keywords']}개")
        debug_info.append(f"카테고리: {stats['total_categories']}개")
        debug_info.append(f"발견 횟수: {stats['discovery_count']}회")
        debug_info.append(f"BaseCommand 가용: {'✅' if stats['base_command_available'] else '❌'}")
        
        # 검증 결과
        debug_info.append(f"\n검증 결과: {'✅ 유효' if validation['valid'] else '❌ 무효'}")
        debug_info.append(f"유효한 명령어: {validation['statistics']['valid_commands']}개")
        debug_info.append(f"경고가 있는 명령어: {validation['statistics']['warning_commands']}개")
        debug_info.append(f"오류가 있는 명령어: {validation['statistics']['error_commands']}개")
        
        # 카테고리별 분포
        if stats['category_stats']:
            debug_info.append(f"\n카테고리별 분포:")
            for category, cat_stats in stats['category_stats'].items():
                debug_info.append(f"  {category}: {cat_stats['enabled']}/{cat_stats['total']}개")
        
        # 메타데이터 소스 분포
        if stats['metadata_sources']:
            debug_info.append(f"\n메타데이터 소스:")
            for source, count in stats['metadata_sources'].items():
                debug_info.append(f"  {source}: {count}개")
        
        # 주요 오류 (최대 3개)
        if validation['errors']:
            debug_info.append(f"\n주요 오류:")
            for error in validation['errors'][:3]:
                debug_info.append(f"  - {error}")
            if len(validation['errors']) > 3:
                debug_info.append(f"  ... 외 {len(validation['errors']) - 3}개")
        
        debug_info.append("=== 디버그 정보 완료 ===")
        return "\n".join(debug_info)
        
    except Exception as e:
        return f"디버그 정보 생성 실패: {e}"


# 모듈이 직접 실행될 때 테스트
if __name__ == "__main__":
    # 로깅 설정
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    
    print("=== CommandRegistry 테스트 (개선 버전) ===")
    
    # 기본 통계
    print(f"\n초기 상태:")
    stats = registry.get_statistics()
    print(f"  명령어: {stats['total_commands']}개")
    print(f"  BaseCommand 가용: {'✅' if stats['base_command_available'] else '❌'}")
    
    # 명령어 발견
    print(f"\n명령어 발견 시작...")
    discovered = registry.discover_commands()
    print(f"발견된 명령어: {discovered}개")
    
    # 발견 후 통계
    stats = registry.get_statistics()
    print(f"\n발견 후 통계:")
    print(f"  총 명령어: {stats['total_commands']}개")
    print(f"  활성화된 명령어: {stats['enabled_commands']}개")
    print(f"  총 키워드: {stats['total_keywords']}개")
    print(f"  카테고리: {stats['total_categories']}개")
    
    # 카테고리별 분포
    if stats['category_stats']:
        print(f"\n카테고리별 분포:")
        for category, cat_stats in stats['category_stats'].items():
            print(f"  {category}: {cat_stats['total']}개 (활성화: {cat_stats['enabled']}개)")
    
    # 검증 실행
    print(f"\n검증 실행...")
    validation = registry.validate_all_commands()
    print(f"  전체 유효성: {'✅ 통과' if validation['valid'] else '❌ 실패'}")
    print(f"  유효: {validation['statistics']['valid_commands']}개")
    print(f"  경고: {validation['statistics']['warning_commands']}개")
    print(f"  오류: {validation['statistics']['error_commands']}개")
    
    # 주요 오류/경고 출력
    if validation['errors']:
        print(f"\n주요 오류:")
        for error in validation['errors'][:3]:
            print(f"  - {error}")
    
    if validation['warnings']:
        print(f"\n주요 경고:")
        for warning in validation['warnings'][:3]:
            print(f"  - {warning}")
    
    # 디버그 정보
    print(f"\n" + debug_registry())
    
    print(f"\n=== 테스트 완료 ===")