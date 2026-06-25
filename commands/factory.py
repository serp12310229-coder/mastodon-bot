"""
명령어 팩토리 (CoC 봇)

레지스트리에 등록된 명령어 클래스를 **싱글톤**으로 인스턴스화한다.
PROTOTYPE/REQUEST 스코프를 사용하지 않으므로, 팩토리는 매우 단순하다.

핵심 API:
- `get_factory()` : 전역 싱글톤 팩토리
- `CommandFactory.configure_dependencies(sheets_manager, mastodon_api, **extras)`
- `CommandFactory.create_command_by_keyword(keyword)` : 키워드 → 인스턴스
- `CommandFactory.create_command_by_name(name)` : 등록명 → 인스턴스
- `CommandFactory.create_all_singleton_instances()` : 부팅 시 일괄 생성
- `CommandFactory.get_instance_statistics()` / `validate_dependencies()` : 헬스 체크용
"""

import os
import sys
import inspect
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

try:
    from commands.registry import CommandRegistry, RegisteredCommand, get_registry
    from commands.base_command import BaseCommand, CommandContext, create_command_context
    from utils.sheets_operations import SheetsManager
    IMPORTS_AVAILABLE = True
except ImportError as e:
    logging.getLogger(__name__).warning(f"팩토리 의존성 임포트 실패: {e}")

    class CommandRegistry:  # type: ignore[no-redef]
        pass

    class RegisteredCommand:  # type: ignore[no-redef]
        pass

    class SheetsManager:  # type: ignore[no-redef]
        pass

    class CommandContext:  # type: ignore[no-redef]
        pass

    class BaseCommand:  # type: ignore[no-redef]
        pass

    def get_registry():  # type: ignore[no-redef]
        return None

    def create_command_context(*args, **kwargs):  # type: ignore[no-redef]
        return None

    IMPORTS_AVAILABLE = False


logger = logging.getLogger(__name__)


@dataclass
class DependencyConfig:
    """팩토리가 명령어 생성 시 주입할 의존성."""

    sheets_manager: Optional[SheetsManager] = None
    mastodon_api: Optional[Any] = None
    additional_deps: Dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# 명령어 인스턴스 생성 로직
# ----------------------------------------------------------------------

def _instantiate(command_class: Type, deps: DependencyConfig) -> Any:
    """
    명령어 클래스에 의존성을 주입해 인스턴스를 만든다.

    1차: `(sheets_manager, api, **extras)` 시그니처 (표준 BaseCommand)
    2차: 시그니처 분석 후 파라미터 이름 매칭
    """
    if IMPORTS_AVAILABLE and inspect.isclass(command_class) and issubclass(command_class, BaseCommand):
        return command_class(
            sheets_manager=deps.sheets_manager,
            api=deps.mastodon_api,
            **deps.additional_deps,
        )

    # 레거시 클래스 — 시그니처 분석해서 매칭
    try:
        sig = inspect.signature(command_class.__init__)
        params = list(sig.parameters.keys())[1:]
    except (TypeError, ValueError):
        params = []

    kwargs: Dict[str, Any] = {}
    for name in params:
        if name in ('sheets_manager', 'sheet_manager'):
            kwargs[name] = deps.sheets_manager
        elif name in ('api', 'mastodon_api'):
            kwargs[name] = deps.mastodon_api
        elif name in deps.additional_deps:
            kwargs[name] = deps.additional_deps[name]

    try:
        return command_class(**kwargs)
    except TypeError:
        pass

    # 최후 폴백
    for attempt in (
        lambda: command_class(deps.sheets_manager, deps.mastodon_api),
        lambda: command_class(deps.sheets_manager),
        lambda: command_class(),
    ):
        try:
            return attempt()
        except TypeError:
            continue

    raise RuntimeError(f"명령어 인스턴스 생성 실패: {command_class.__name__}")


# ----------------------------------------------------------------------
# 팩토리 본체
# ----------------------------------------------------------------------

class CommandFactory:
    """싱글톤 전용 명령어 팩토리."""

    def __init__(self, registry: Optional[CommandRegistry] = None):
        self.registry = registry or get_registry()
        self.dependency_config = DependencyConfig()
        self._instances: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._creation_count = 0
        self._error_count = 0
        logger.info("CommandFactory 초기화 완료")

    # ------------------- 의존성 주입 -------------------
    def configure_dependencies(
        self,
        sheets_manager: Optional[SheetsManager] = None,
        mastodon_api: Optional[Any] = None,
        **additional_deps,
    ) -> None:
        """명령어 클래스에 주입할 의존성 설정."""
        self.dependency_config = DependencyConfig(
            sheets_manager=sheets_manager,
            mastodon_api=mastodon_api,
            additional_deps=additional_deps,
        )
        logger.info(
            "의존성 설정 완료 (sheets=%s, api=%s, extras=%d)",
            sheets_manager is not None, mastodon_api is not None, len(additional_deps),
        )

    # ------------------- 인스턴스 생성 -------------------
    def create_command_by_name(
        self,
        command_name: str,
        force_new: bool = False,
    ) -> Optional[Any]:
        """등록된 이름으로 명령어 인스턴스 획득 (싱글톤)."""
        if not IMPORTS_AVAILABLE or not self.registry:
            return None

        registered = self.registry.get_command_by_name(command_name)
        if not registered or not registered.metadata.enabled:
            return None

        if not self._check_dependencies(registered):
            return None

        with self._lock:
            if not force_new and command_name in self._instances:
                return self._instances[command_name]

            try:
                instance = _instantiate(registered.command_class, self.dependency_config)
                self._instances[command_name] = instance
                self._creation_count += 1
                return instance
            except Exception as e:
                self._error_count += 1
                logger.error(
                    f"명령어 인스턴스 생성 실패: {command_name} - {e}",
                    exc_info=True,
                )
                return None

    def create_command_by_keyword(
        self,
        keyword: str,
        force_new: bool = False,
    ) -> Optional[Any]:
        """키워드(별칭 포함)로 명령어 인스턴스 획득."""
        if not IMPORTS_AVAILABLE or not self.registry:
            return None
        registered = self.registry.get_command_by_keyword(keyword)
        if not registered:
            return None
        return self.create_command_by_name(registered.metadata.name, force_new=force_new)

    def create_all_singleton_instances(self) -> Dict[str, bool]:
        """레지스트리의 모든 명령어를 미리 인스턴스화 (부팅 시)."""
        results: Dict[str, bool] = {}
        if not IMPORTS_AVAILABLE or not self.registry:
            return results

        for name, registered in self.registry.get_enabled_commands().items():
            try:
                if not self._check_dependencies(registered):
                    results[name] = False
                    continue
                instance = _instantiate(registered.command_class, self.dependency_config)
                with self._lock:
                    self._instances[name] = instance
                self._creation_count += 1
                results[name] = True
            except Exception as e:
                self._error_count += 1
                logger.warning(f"싱글톤 생성 실패: {name} — {e}")
                results[name] = False
        return results

    # ------------------- 의존성 점검 -------------------
    def _check_dependencies(self, registered: RegisteredCommand) -> bool:
        """메타데이터의 `requires_sheets`, `requires_api` 충족 여부."""
        meta = registered.metadata
        if getattr(meta, 'requires_sheets', False) and self.dependency_config.sheets_manager is None:
            return False
        if getattr(meta, 'requires_api', False) and self.dependency_config.mastodon_api is None:
            return False
        return True

    # ------------------- 관리/헬스 -------------------
    def cleanup_all_instances(self) -> None:
        """모든 인스턴스 폐기. 재로드 경로에서 사용."""
        with self._lock:
            self._instances.clear()

    def get_instance_statistics(self) -> Dict[str, Any]:
        """헬스 체크용 간단한 통계."""
        with self._lock:
            return {
                'total_instances': len(self._instances),
                'creation_count': self._creation_count,
                'error_count': self._error_count,
            }

    def validate_dependencies(self) -> Dict[str, Any]:
        """등록된 명령어들의 의존성 충족 여부 점검."""
        result: Dict[str, Any] = {'valid': True, 'errors': [], 'warnings': []}
        if not IMPORTS_AVAILABLE or not self.registry:
            result['valid'] = False
            result['errors'].append('팩토리/레지스트리 의존성 임포트 실패')
            return result

        for name, registered in self.registry.get_enabled_commands().items():
            meta = registered.metadata
            if getattr(meta, 'requires_sheets', False) and self.dependency_config.sheets_manager is None:
                result['warnings'].append(f"'{name}' 은 sheets_manager 를 요구하지만 미설정")
            if getattr(meta, 'requires_api', False) and self.dependency_config.mastodon_api is None:
                result['warnings'].append(f"'{name}' 은 mastodon_api 를 요구하지만 미설정")
        return result


# ----------------------------------------------------------------------
# 전역 싱글톤
# ----------------------------------------------------------------------

_global_factory: Optional[CommandFactory] = None
_factory_lock = threading.Lock()


def get_factory() -> CommandFactory:
    """전역 CommandFactory 반환 (lazy init)."""
    global _global_factory
    if _global_factory is None:
        with _factory_lock:
            if _global_factory is None:
                _global_factory = CommandFactory()
    return _global_factory
