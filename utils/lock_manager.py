"""
동시성 제어를 위한 락 매니저
사용자별 아이템 사용 시 경쟁 조건(race condition)을 방지합니다.
"""

import threading
import time
from typing import Dict, Optional
from contextlib import contextmanager

try:
    from utils.logging_config import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class LockManager:
    """사용자별 락을 관리하는 클래스"""

    # 사용 흔적이 이만큼 오래된 락은 안전하게 정리 가능 (어떤 명령어도 30분 이상 락을 보유하지 않음).
    _IDLE_THRESHOLD_SECONDS = 1800.0
    # `_get_lock` 이 이 횟수마다 한 번씩 idle 락을 청소.
    _CLEANUP_EVERY_N = 100

    def __init__(self, lock_timeout: float = 30.0):
        """
        LockManager 초기화

        Args:
            lock_timeout: 락 타임아웃 시간 (초)
        """
        self._locks: Dict[str, threading.Lock] = {}
        self._lock_creation_lock = threading.Lock()
        self._lock_timeout = lock_timeout
        self._lock_acquire_times: Dict[str, float] = {}
        self._last_used: Dict[str, float] = {}
        self._access_counter: int = 0

    def _get_lock(self, user_id: str) -> threading.Lock:
        """
        사용자 ID에 대한 락 가져오기 (없으면 생성)

        Args:
            user_id: 사용자 ID

        Returns:
            threading.Lock: 사용자별 락
        """
        with self._lock_creation_lock:
            now = time.time()
            self._last_used[user_id] = now
            self._access_counter += 1
            if self._access_counter >= self._CLEANUP_EVERY_N:
                self._access_counter = 0
                self._purge_idle_locked()
            if user_id not in self._locks:
                self._locks[user_id] = threading.Lock()
            return self._locks[user_id]

    def _purge_idle_locked(self) -> None:
        """오래 사용되지 않은 unlocked 락 제거 (호출자가 `_lock_creation_lock` 보유)."""
        threshold = time.time() - self._IDLE_THRESHOLD_SECONDS
        stale = [
            uid for uid, lk in self._locks.items()
            if not lk.locked() and self._last_used.get(uid, 0.0) < threshold
        ]
        for uid in stale:
            del self._locks[uid]
            self._last_used.pop(uid, None)
            self._lock_acquire_times.pop(uid, None)
        if stale:
            logger.debug(f"[락매니저] 유휴 락 {len(stale)}개 정리")

    @contextmanager
    def acquire_lock(self, user_id: str, timeout: Optional[float] = None):
        """
        사용자별 락 획득 (컨텍스트 매니저)

        Args:
            user_id: 사용자 ID
            timeout: 락 획득 타임아웃 (None이면 기본값 사용)

        Yields:
            bool: 락 획득 성공 여부

        Example:
            with lock_manager.acquire_lock(user_id) as acquired:
                if acquired:
                    # 작업 수행
                    pass
                else:
                    # 락 획득 실패 처리
                    pass
        """
        lock = self._get_lock(user_id)
        timeout = timeout or self._lock_timeout

        # 락 획득 시도
        acquired = lock.acquire(timeout=timeout)

        if acquired:
            self._lock_acquire_times[user_id] = time.time()
            logger.debug(f"락 획득 성공: {user_id}")

        try:
            yield acquired
        finally:
            if acquired:
                with self._lock_creation_lock:
                    self._last_used[user_id] = time.time()
                if user_id in self._lock_acquire_times:
                    hold_time = time.time() - self._lock_acquire_times[user_id]
                    logger.debug(f"락 해제: {user_id} (보유 시간: {hold_time:.2f}초)")
                    del self._lock_acquire_times[user_id]

                lock.release()

    def is_locked(self, user_id: str) -> bool:
        """
        특정 사용자의 락 상태 확인

        Args:
            user_id: 사용자 ID

        Returns:
            bool: 락 획득 여부
        """
        if user_id not in self._locks:
            return False

        lock = self._locks[user_id]
        # 락을 획득할 수 있으면 즉시 해제하고 False 반환
        if lock.acquire(blocking=False):
            lock.release()
            return False
        return True

    def cleanup_old_locks(self, max_age: float = 3600.0):
        """
        오래된 락 정리 (메모리 누수 방지)

        Args:
            max_age: 락 최대 유지 시간 (초, 기본 1시간)
        """
        current_time = time.time()
        with self._lock_creation_lock:
            users_to_remove = []

            for user_id, lock in self._locks.items():
                # 락이 잠겨있지 않고, 획득 시간이 없거나 오래된 경우
                if not lock.locked():
                    acquire_time = self._lock_acquire_times.get(user_id, 0)
                    if current_time - acquire_time > max_age:
                        users_to_remove.append(user_id)

            # 오래된 락 제거
            for user_id in users_to_remove:
                del self._locks[user_id]
                if user_id in self._lock_acquire_times:
                    del self._lock_acquire_times[user_id]

            if users_to_remove:
                logger.debug(f"오래된 락 {len(users_to_remove)}개 정리 완료")

    def get_stats(self) -> Dict[str, int]:
        """
        락 매니저 통계 반환

        Returns:
            Dict: 통계 정보
        """
        with self._lock_creation_lock:
            locked_count = sum(1 for lock in self._locks.values() if lock.locked())
            return {
                'total_locks': len(self._locks),
                'locked_count': locked_count,
                'unlocked_count': len(self._locks) - locked_count
            }


# 전역 락 매니저 인스턴스
_global_lock_manager = None


def get_lock_manager() -> LockManager:
    """전역 LockManager 인스턴스 반환"""
    global _global_lock_manager
    if _global_lock_manager is None:
        _global_lock_manager = LockManager()
    return _global_lock_manager
