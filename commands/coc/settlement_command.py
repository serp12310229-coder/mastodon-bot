"""
[골드 정산] — 100 단위 floor 기록 방식.

산식:
  current_total = last_total + new_non_dm    # DM 제외 누적 게시글 수
  delta         = current_total - last_floor
  bonus         = (delta // 100) * 20 G
  new_floor     = (current_total // 100) * 100   # 다음 정산의 기준선

예:
  1차) 첫 정산, glob=351개. delta=351, bonus=60G, 기록 floor=300
  2차) 누적 782 (new=431). delta=782-300=482, bonus=80G, 기록 floor=700
  3차) 누적 890 (new=108). delta=890-700=190, bonus=20G, 기록 floor=800
  4차) 누적 989 (new=99).  delta=989-800=189, bonus=20G, 기록 floor=900

DM 은 visibility == 'direct' 인 status. 마스토돈 API 의
account_statuses 페이지네이션으로 since_id 이후만 받아 증분 처리.
"""

from __future__ import annotations

from typing import Any, List

from commands.base_command import BaseCommand, CommandContext, CommandResponse
from commands.registry import register_command
from commands.trpg_common.fallback_helpers import acquire_user_lock
from utils.decorators import handle_command_errors
from utils.error_handling import CommandError
from utils.gold_settlement import get_record, set_record
from utils.logging_config import logger
from utils.shared_sheet import (
    EQUIP_COL_GOLD,
    EQUIP_DATA_START_ROW,
    WS_EQUIP_STOCK,
    find_character_row,
    read_int_cell,
)


_GOLD_PER_BUCKET = 20
_POSTS_PER_BUCKET = 100

# 페이지네이션 안전장치
_PAGE_LIMIT = 40
_MAX_PAGES = 100   # 4000 statuses


def _attr(obj: Any, key: str, default=None):
    """마스토돈 응답(AttribAccessDict)과 일반 dict 둘 다 안전 처리."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@register_command(
    name="골드 정산",
    aliases=["골드정산"],
    description="DM 제외 누적 툿 수 / 100 × 20G 지급 (100단위 floor 기록)",
    category="레이드",
    examples=["[골드 정산]"],
    requires_sheets=True,
    requires_api=True,
    priority=10,
)
class SettlementCommand(BaseCommand):

    @handle_command_errors(
        system_tag="골드 정산",
        user_error_message="골드 정산 처리 중 오류가 발생했습니다.",
    )
    def execute(self, context: CommandContext) -> CommandResponse:
        title = (context.user_name or '').strip()
        if not title:
            raise CommandError("마스토돈 표시명(=칭호)을 확인할 수 없습니다.")

        user_id = context.user_id
        if not user_id:
            raise CommandError("발신자 acct 를 확인할 수 없습니다.")

        equip_row = find_character_row(
            self.sheets_manager, WS_EQUIP_STOCK, title, EQUIP_DATA_START_ROW,
        )
        if equip_row is None:
            raise CommandError(
                f"'장비 및 주식' 시트에서 '{title}' 캐릭터를 찾을 수 없습니다."
            )

        account_id = self._resolve_account_id(user_id)

        record = get_record(user_id)
        new_statuses = self._fetch_new_statuses(account_id, record.last_status_id)

        # DM 제외 카운트
        new_non_dm = sum(
            1 for s in new_statuses
            if _attr(s, 'visibility', '') != 'direct'
        )

        # 누적 / 차이 / 골드 / 다음 기준선
        current_total = record.last_total + new_non_dm
        delta = current_total - record.last_floor
        bonus = (delta // _POSTS_PER_BUCKET) * _GOLD_PER_BUCKET
        new_floor = (current_total // _POSTS_PER_BUCKET) * _POSTS_PER_BUCKET

        # 최신 status_id (없으면 이전 값 유지)
        if new_statuses:
            latest_status_id = str(_attr(new_statuses[0], 'id', record.last_status_id))
        else:
            latest_status_id = record.last_status_id

        with acquire_user_lock(user_id, timeout=10.0):
            current_gold = read_int_cell(
                self.sheets_manager, WS_EQUIP_STOCK, equip_row, EQUIP_COL_GOLD,
            )
            new_gold = current_gold + bonus
            if bonus > 0:
                ok = self.sheets_manager.update_cell(
                    WS_EQUIP_STOCK, equip_row, EQUIP_COL_GOLD, str(new_gold),
                )
                if not ok:
                    raise CommandError("골드 갱신을 시트에 저장하지 못했습니다.")
            # 골드 0 이어도 카운트와 floor 는 진척시켜야 중복 카운트가 안 생긴다.
            set_record(user_id, current_total, new_floor, latest_status_id)

        logger.info(
            f"[골드 정산] @{user_id} ({title}) "
            f"total {record.last_total}→{current_total} "
            f"floor {record.last_floor}→{new_floor} "
            f"delta={delta} bonus={bonus}G "
            f"gold {current_gold}→{new_gold}"
        )

        message = (
            f"[골드 정산] 현재 툿 수: {current_total} / "
            f"이전 정산 툿 수: {record.last_floor} "
            f"─ [취득 골드: {bonus}G]"
        )
        return CommandResponse.create_success(
            message,
            data={
                'total_before': record.last_total,
                'total_after': current_total,
                'floor_before': record.last_floor,
                'floor_after': new_floor,
                'delta': delta,
                'bonus': bonus,
                'gold_before': current_gold,
                'gold_after': new_gold,
            },
        )

    # ------------------------------------------------------------------
    def _resolve_account_id(self, acct: str) -> Any:
        """acct → 마스토돈 account_id. lookup 우선, 실패 시 search 폴백."""
        if self.api is None:
            raise CommandError("마스토돈 API 가 연결되어 있지 않습니다.")

        lookup = getattr(self.api, 'account_lookup', None)
        if callable(lookup):
            try:
                acc = lookup(acct)
                acc_id = _attr(acc, 'id')
                if acc_id is not None:
                    return acc_id
            except Exception as e:
                logger.debug(f"[정산] account_lookup 실패 — search 폴백: {e}")

        try:
            results = self.api.account_search(acct, limit=5, resolve=True)
        except Exception as e:
            raise CommandError(f"마스토돈 계정 조회 실패: {e}")

        for acc in results or []:
            acc_acct = (_attr(acc, 'acct', '') or '').lower()
            if acc_acct == acct.lower():
                acc_id = _attr(acc, 'id')
                if acc_id is not None:
                    return acc_id

        if results:
            acc_id = _attr(results[0], 'id')
            if acc_id is not None:
                return acc_id

        raise CommandError(f"마스토돈 계정 '{acct}' 을(를) 찾을 수 없습니다.")

    def _fetch_new_statuses(
        self,
        account_id: Any,
        since_id,
    ) -> List[Any]:
        """since_id 이후의 statuses 페이지네이션."""
        results: List[Any] = []
        max_id = None
        for _ in range(_MAX_PAGES):
            kwargs = {'limit': _PAGE_LIMIT}
            if since_id:
                kwargs['since_id'] = since_id
            if max_id is not None:
                kwargs['max_id'] = max_id
            try:
                page = self.api.account_statuses(account_id, **kwargs)
            except Exception as e:
                raise CommandError(f"마스토돈 글 목록 조회 실패: {e}")

            if not page:
                break
            results.extend(page)
            last_id = _attr(page[-1], 'id')
            if not last_id:
                break
            max_id = last_id
            if len(page) < _PAGE_LIMIT:
                break

        if len(results) >= _MAX_PAGES * _PAGE_LIMIT:
            logger.warning(
                f"[정산] account={account_id} 페이지 한도 도달 "
                f"({_MAX_PAGES} 페이지). 누락된 과거 글은 이번 정산에서 제외됨."
            )
        return results
