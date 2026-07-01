"""
[골드 정산] 명령어 상태 저장소 (JSON 영속화)

캐릭터별로 다음을 보관:
- last_total       : DM 제외 누적 게시글 수 (정확한 카운트)
- last_floor       : 정산 기준치 — 직전 정산 후의 100 단위 내림값
- last_status_id   : 직전 정산 시점에 본 가장 최신 status id
                     (다음 정산에서 since_id 파라미터로 증분 페이지네이션)

정산 산식:
  current_total = last_total + new_non_dm  (since_id 이후 받은 새 글 중 DM 제외)
  delta         = current_total - last_floor
  bonus         = (delta // 100) * 20  골드
  new_floor     = (current_total // 100) * 100
  → last_total ← current_total, last_floor ← new_floor 로 저장.

stock_engine.py 와 같은 패턴: 단일 JSON 파일, 락 보호, atomic write.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from utils.logging_config import logger


_DEFAULT_STATE_FILE = (
    Path(__file__).resolve().parent.parent / 'data' / 'gold_settlement.json'
)


@dataclass
class SettlementRecord:
    last_total: int = 0
    last_floor: int = 0
    last_status_id: Optional[str] = None


class _SettlementStore:
    """프로세스 단일 인스턴스로 사용. 첫 호출 시 lazy 로드."""

    def __init__(self, path: Path = _DEFAULT_STATE_FILE):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._state: dict = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if self.path.exists():
                try:
                    with open(self.path, 'r', encoding='utf-8') as f:
                        self._state = json.load(f) or {}
                except Exception as e:
                    logger.warning(f"[정산] 상태 로드 실패 — 초기화: {e}")
                    self._state = {}
            self._loaded = True

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            logger.warning(f"[정산] 상태 저장 실패: {e}")

    def get_record(self, user_id: str) -> SettlementRecord:
        """`user_id` 의 기록. 없으면 모두 0. 옛 포맷(last_count) 자동 마이그레이션."""
        self._ensure_loaded()
        with self._lock:
            rec = self._state.get(user_id) or {}

            # 옛 포맷: {'last_count': N, 'last_status_id': ...}
            # 새 포맷: {'last_total': N, 'last_floor': F, 'last_status_id': ...}
            if 'last_total' in rec:
                last_total = int(rec.get('last_total', 0))
                last_floor = int(rec.get('last_floor', 0))
            else:
                # 옛 포맷 → 새 포맷으로 해석.
                # last_count 가 실제 누적이었으므로, floor 는 그것의 100단위 내림.
                # (마이그레이션은 다음 set_record 호출에서 새 포맷으로 저장됨)
                last_total = int(rec.get('last_count', 0))
                last_floor = (last_total // 100) * 100

            return SettlementRecord(
                last_total=last_total,
                last_floor=last_floor,
                last_status_id=rec.get('last_status_id'),
            )

    def set_record(
        self,
        user_id: str,
        last_total: int,
        last_floor: int,
        last_status_id: Optional[str],
    ) -> None:
        self._ensure_loaded()
        with self._lock:
            self._state[user_id] = {
                'last_total': int(last_total),
                'last_floor': int(last_floor),
                'last_status_id': last_status_id,
            }
            self._save()


_store = _SettlementStore()


def get_record(user_id: str) -> SettlementRecord:
    return _store.get_record(user_id)


def set_record(
    user_id: str,
    last_total: int,
    last_floor: int,
    last_status_id: Optional[str],
) -> None:
    _store.set_record(user_id, last_total, last_floor, last_status_id)
