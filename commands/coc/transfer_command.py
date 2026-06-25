"""
[양도/<골드 수>/<대상>]

- 발신자: context.user_name (= 마스토돈 display_name = 시트 칭호)
- 대상: `@아이디`, `@user@server`, 또는 `이름` (마스토돈 acct 우선 조회 → display_name).

처리 흐름:
1) 대상 acct 를 정규화하고 마스토돈 API 로 display_name 조회.
2) '장비 및 주식' 시트에서 두 캐릭터의 행을 모두 찾음.
3) 발신자 골드 >= 양도액 검증.
4) 발신자 −, 수신자 + (개별 update_cell — 분산 트랜잭션 시뮬레이션).
"""

from __future__ import annotations

import re
from typing import Optional

from commands.base_command import BaseCommand, CommandContext, CommandResponse
from commands.registry import register_command
from commands.trpg_common.fallback_helpers import acquire_user_lock
from utils.decorators import handle_command_errors
from utils.error_handling import CommandError
from utils.logging_config import logger
from utils.shared_sheet import (
    EQUIP_COL_GOLD,
    EQUIP_DATA_START_ROW,
    WS_EQUIP_STOCK,
    find_character_row,
    read_int_cell,
)


def _strip_acct(token: str) -> str:
    """'@user' / '@user@server' → 'user'(또는 'user@server')."""
    t = (token or '').strip()
    while t.startswith('@'):
        t = t[1:]
    return t


@register_command(
    name="양도",
    aliases=[],
    description="다른 캐릭터에게 골드 양도",
    category="아이템",
    examples=["[양도/100/@friend]"],
    requires_sheets=True,
    requires_api=True,
    priority=10,
)
class TransferCommand(BaseCommand):

    @handle_command_errors(
        system_tag="양도",
        user_error_message="양도 처리 중 오류가 발생했습니다.",
    )
    def execute(self, context: CommandContext) -> CommandResponse:
        if len(context.keywords) < 3:
            raise CommandError("사용법: [양도/골드수/@대상]")

        amount_raw = context.keywords[1].strip()
        target_raw = context.keywords[2].strip()

        try:
            amount = int(amount_raw)
        except (TypeError, ValueError):
            raise CommandError(f"'골드 수'는 정수여야 합니다. (입력: {amount_raw})")
        if amount <= 0:
            raise CommandError("양도 금액은 1 이상이어야 합니다.")

        sender_title = (context.user_name or '').strip()
        if not sender_title:
            raise CommandError("발신자의 마스토돈 표시명을 확인할 수 없습니다.")

        # 대상의 display_name 조회
        target_title = self._resolve_target_title(target_raw)
        if not target_title:
            raise CommandError(
                f"대상 '{target_raw}'의 마스토돈 계정을 조회할 수 없습니다."
            )
        if target_title == sender_title:
            raise CommandError("자기 자신에게는 양도할 수 없습니다.")

        sender_row = find_character_row(
            self.sheets_manager, WS_EQUIP_STOCK, sender_title, EQUIP_DATA_START_ROW,
        )
        if sender_row is None:
            raise CommandError(
                f"'장비 및 주식' 시트에서 발신자 '{sender_title}'를 찾지 못했습니다."
            )
        target_row = find_character_row(
            self.sheets_manager, WS_EQUIP_STOCK, target_title, EQUIP_DATA_START_ROW,
        )
        if target_row is None:
            raise CommandError(
                f"'장비 및 주식' 시트에서 대상 '{target_title}'를 찾지 못했습니다."
            )

        with acquire_user_lock(context.user_id, timeout=10.0):
            sender_gold = read_int_cell(
                self.sheets_manager, WS_EQUIP_STOCK, sender_row, EQUIP_COL_GOLD,
            )
            if sender_gold < amount:
                raise CommandError(
                    f"보유 골드가 부족합니다. (보유 {sender_gold} / 양도 {amount})"
                )
            target_gold = read_int_cell(
                self.sheets_manager, WS_EQUIP_STOCK, target_row, EQUIP_COL_GOLD,
            )

            new_sender = sender_gold - amount
            new_target = target_gold + amount

            ok_s = self.sheets_manager.update_cell(
                WS_EQUIP_STOCK, sender_row, EQUIP_COL_GOLD, str(new_sender),
            )
            if not ok_s:
                raise CommandError("발신자 골드 차감에 실패했습니다.")
            ok_t = self.sheets_manager.update_cell(
                WS_EQUIP_STOCK, target_row, EQUIP_COL_GOLD, str(new_target),
            )
            if not ok_t:
                # 발신자 골드 롤백 시도 — 부분 실패 시 일관성 회복.
                self.sheets_manager.update_cell(
                    WS_EQUIP_STOCK, sender_row, EQUIP_COL_GOLD, str(sender_gold),
                )
                raise CommandError(
                    "수신자 골드 입금에 실패하여 발신자 잔액을 복구했습니다."
                )

        message = (
            f"━━━ 골드 양도 ━━━\n"
            f"{sender_title} → {target_title}: {amount} 골드\n"
            f"{sender_title}: {sender_gold} → {new_sender}\n"
            f"{target_title}: {target_gold} → {new_target}"
        )
        logger.info(
            f"[양도] @{context.user_id} ({sender_title}) → ({target_title}) "
            f"{amount} 골드"
        )
        return CommandResponse.create_success(message)

    # ------------------------------------------------------------------
    def _resolve_target_title(self, raw: str) -> Optional[str]:
        """대상 토큰에서 마스토돈 display_name 조회.

        - `@user` 형태면 acct 로 검색.
        - 이미 칭호인 경우(시트 A열 직접 매칭)도 fallback 으로 시도.
        """
        if not raw:
            return None
        acct = _strip_acct(raw)
        if not acct:
            return None

        # 1) 마스토돈 API 로 acct → 계정 → display_name
        if self.api is not None:
            try:
                accounts = self.api.account_search(acct, limit=5)
            except Exception as e:
                logger.warning(f"[양도] account_search 실패 (acct={acct}): {e}")
                accounts = []
            for acc in accounts or []:
                acc_acct = (acc.get('acct') if isinstance(acc, dict) else getattr(acc, 'acct', '')) or ''
                # acct 가 'user' 또는 'user@server' 형태로 정확히 일치하는지 확인.
                if acc_acct.lower() == acct.lower():
                    display = (
                        acc.get('display_name') if isinstance(acc, dict)
                        else getattr(acc, 'display_name', '')
                    )
                    return (display or '').strip() or acc_acct
            # 정확 일치는 없지만 첫 결과를 fallback 으로 사용
            if accounts:
                acc = accounts[0]
                display = (
                    acc.get('display_name') if isinstance(acc, dict)
                    else getattr(acc, 'display_name', '')
                )
                if display:
                    return display.strip()

        # 2) Fallback: 입력값 자체를 칭호로 간주
        return acct
