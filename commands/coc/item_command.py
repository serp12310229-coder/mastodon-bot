"""
[아이템 사용/<이름(수량)>, ...]
[아이템 구매/<이름(수량)>, ...]

- 사용: 공동 창고에서 수량 차감 + (HP/MP 포션이면) 레이드 정보 회복.
- 구매: '상점' 페이지에서 재고/가격/잔액 검증 후 골드 차감 + 공동 창고 추가.

아이템 인자 포맷 예: `소형HP포션(2), 대형MP포션(1)` 또는 `이름` (수량 1 기본).
"""

from __future__ import annotations

import re
from typing import List, Tuple

from commands.base_command import BaseCommand, CommandContext, CommandResponse
from commands.registry import register_command
from commands.trpg_common.fallback_helpers import acquire_user_lock
from utils.decorators import handle_command_errors
from utils.error_handling import CommandError
from utils.logging_config import logger
from utils.shared_sheet import (
    EQUIP_COL_GOLD,
    EQUIP_DATA_START_ROW,
    POTION_EFFECTS,
    RAID_COL_HP_CUR,
    RAID_COL_HP_MAX,
    RAID_COL_MP_CUR,
    RAID_COL_MP_MAX,
    RAID_DATA_START_ROW,
    WS_EQUIP_STOCK,
    WS_RAID,
    _normalize_item_name,
    add_to_inventory,
    consume_from_inventory,
    find_character_row,
    find_inventory_item,
    find_shop_item,
    read_int_cell,
    update_shop_stock,
)


# `소형HP포션(2)` 또는 `대형MP포션` 패턴.
_ITEM_PATTERN = re.compile(r'^(?P<name>.+?)(?:\((?P<qty>\d+)\))?$')


def _parse_item_list(raw: str) -> List[Tuple[str, int]]:
    """`소형HP포션(2), 대형MP포션` → [('소형HP포션', 2), ('대형MP포션', 1)]."""
    items: List[Tuple[str, int]] = []
    for token in raw.split(','):
        token = token.strip()
        if not token:
            continue
        m = _ITEM_PATTERN.match(token)
        if not m:
            raise CommandError(f"'{token}' 형식을 인식할 수 없습니다. 예: 소형HP포션(2)")
        name = m.group('name').strip()
        qty_str = m.group('qty')
        qty = int(qty_str) if qty_str else 1
        if not name or qty <= 0:
            raise CommandError(f"'{token}' 형식을 인식할 수 없습니다.")
        items.append((name, qty))
    if not items:
        raise CommandError("아이템 목록이 비어 있습니다.")
    return items


@register_command(
    name="아이템",
    aliases=['아이템 사용', '아이템사용', '아이템 구매', '아이템구매'],
    description="아이템 사용 / 구매. 여러 개를 한 번에 처리할 수 있다.",
    category="아이템",
    examples=[
        "[아이템 사용/소형HP포션(1), 대형MP포션(3)]",
        "[아이템 구매/소형HP포션(2)]",
    ],
    requires_sheets=True,
    requires_api=False,
    priority=10,
)
class ItemCommand(BaseCommand):

    @handle_command_errors(
        system_tag="아이템",
        user_error_message="아이템 명령어 처리 중 오류가 발생했습니다.",
    )
    def execute(self, context: CommandContext) -> CommandResponse:
        head = context.keywords[0].replace(' ', '')

        if head == '아이템사용':
            return self._handle_use(context)
        if head == '아이템구매':
            return self._handle_buy(context)
        raise CommandError(
            "사용법: [아이템 사용/이름(수량), ...] / [아이템 구매/이름(수량), ...]"
        )

    # ------------------------------------------------------------------
    # 사용
    # ------------------------------------------------------------------
    def _handle_use(self, context: CommandContext) -> CommandResponse:
        if len(context.keywords) < 2:
            raise CommandError("사용할 아이템을 입력해 주세요.")

        title = (context.user_name or '').strip()
        if not title:
            raise CommandError("마스토돈 표시명(=칭호)을 확인할 수 없습니다.")

        # 키워드 1번 이후를 모두 합쳐서 파싱 ('/' 로 분할되어 다 들어옴)
        raw = ', '.join(context.keywords[1:])
        items = _parse_item_list(raw)

        # 사전 검증: 모든 아이템 보유량 확인
        for name, qty in items:
            entry = find_inventory_item(self.sheets_manager, name)
            if entry is None:
                raise CommandError(f"공동 창고에 '{name}'이(가) 없습니다.")
            if entry.qty < qty:
                raise CommandError(
                    f"'{name}' 보유량이 부족합니다. (보유 {entry.qty} / 요청 {qty})"
                )

        raid_row = find_character_row(
            self.sheets_manager, WS_RAID, title, RAID_DATA_START_ROW,
        )

        used_lines: List[str] = []
        effect_lines: List[str] = []

        with acquire_user_lock(context.user_id, timeout=10.0):
            for name, qty in items:
                ok = consume_from_inventory(self.sheets_manager, name, qty)
                if not ok:
                    raise CommandError(f"'{name}' 사용을 시트에 반영하지 못했습니다.")
                used_lines.append(f"{name} × {qty}")

                # HP/MP 포션 효과 적용
                potion = POTION_EFFECTS.get(_normalize_item_name(name))
                if potion is None:
                    continue
                if raid_row is None:
                    effect_lines.append(
                        f"  ↳ {name}: '레이드 정보'에서 캐릭터 행을 찾지 못해 효과 미적용"
                    )
                    continue

                kind, recover_per_use = potion
                total_recover = recover_per_use * qty
                if kind == 'hp':
                    cur_col, max_col, label = RAID_COL_HP_CUR, RAID_COL_HP_MAX, 'HP'
                else:
                    cur_col, max_col, label = RAID_COL_MP_CUR, RAID_COL_MP_MAX, 'MP'

                cur_val = read_int_cell(self.sheets_manager, WS_RAID, raid_row, cur_col)
                max_val = read_int_cell(self.sheets_manager, WS_RAID, raid_row, max_col)
                new_val = cur_val + total_recover
                if max_val > 0:
                    new_val = min(new_val, max_val)

                write_ok = self.sheets_manager.update_cell(
                    WS_RAID, raid_row, cur_col, str(new_val),
                )
                if not write_ok:
                    effect_lines.append(f"  ↳ {name}: {label} 시트 반영 실패")
                else:
                    effect_lines.append(
                        f"  ↳ {name}: {label} +{total_recover} ({cur_val} → {new_val})"
                    )

        body = "사용 아이템:\n  - " + '\n  - '.join(used_lines)
        if effect_lines:
            body += "\n효과:\n" + '\n'.join(effect_lines)
        logger.info(f"[아이템 사용] @{context.user_id} ({title}) {used_lines}")
        return CommandResponse.create_success(body)

    # ------------------------------------------------------------------
    # 구매
    # ------------------------------------------------------------------
    def _handle_buy(self, context: CommandContext) -> CommandResponse:
        if len(context.keywords) < 2:
            raise CommandError("구매할 아이템을 입력해 주세요.")

        title = (context.user_name or '').strip()
        if not title:
            raise CommandError("마스토돈 표시명(=칭호)을 확인할 수 없습니다.")

        equip_row = find_character_row(
            self.sheets_manager, WS_EQUIP_STOCK, title, EQUIP_DATA_START_ROW,
        )
        if equip_row is None:
            raise CommandError(
                f"'장비 및 주식' 시트에서 '{title}' 캐릭터를 찾을 수 없습니다."
            )

        raw = ', '.join(context.keywords[1:])
        items = _parse_item_list(raw)

        # 사전 검증
        resolved = []  # [(ShopItem, qty, line_total)]
        total_cost = 0
        for name, qty in items:
            shop_item = find_shop_item(self.sheets_manager, name)
            if shop_item is None:
                raise CommandError(f"'상점'에서 '{name}'을(를) 찾을 수 없습니다.")
            if shop_item.stock < qty:
                raise CommandError(
                    f"'{name}' 재고 부족 (재고 {shop_item.stock} / 요청 {qty})"
                )
            line_total = shop_item.price * qty
            total_cost += line_total
            resolved.append((shop_item, qty, line_total))

        with acquire_user_lock(context.user_id, timeout=10.0):
            current_gold = read_int_cell(
                self.sheets_manager, WS_EQUIP_STOCK, equip_row, EQUIP_COL_GOLD,
            )
            if current_gold < total_cost:
                raise CommandError(
                    f"골드 부족 (보유 {current_gold} / 필요 {total_cost})"
                )

            # 골드 차감
            new_gold = current_gold - total_cost
            gold_ok = self.sheets_manager.update_cell(
                WS_EQUIP_STOCK, equip_row, EQUIP_COL_GOLD, str(new_gold),
            )
            if not gold_ok:
                raise CommandError("골드 차감에 실패했습니다.")

            lines: List[str] = []
            for shop_item, qty, line_total in resolved:
                stock_ok = update_shop_stock(
                    self.sheets_manager, shop_item.row, shop_item.stock - qty,
                )
                inv_ok = add_to_inventory(self.sheets_manager, shop_item.name, qty)
                status = '✓' if stock_ok and inv_ok else '⚠ 일부 실패'
                lines.append(
                    f"  - {shop_item.name} × {qty} = {line_total} 골드 {status}"
                )

        body = (
            f"━━━ {title}님의 아이템 구매 ━━━\n"
            f"총액: {total_cost} 골드 (보유 {current_gold} → {new_gold})\n"
            + '\n'.join(lines)
        )
        logger.info(
            f"[아이템 구매] @{context.user_id} ({title}) cost={total_cost} "
            f"{current_gold}→{new_gold}"
        )
        return CommandResponse.create_success(body)
