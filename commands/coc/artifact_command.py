"""
[아티팩트 장착/<슬롯>/<이름>]
[아티팩트 해제/<슬롯>]  또는  [아티팩트 해제/<이름>]

슬롯: 방어구(C) / 무기(D) / 액세서리·부속품(E)

장착:
1) 공동 창고에서 해당 아이템 −1 (보유량 부족 시 실패).
2) 기존 착용 아이템이 있으면 공동 창고로 +1 반환.
3) '장비 및 주식' 해당 셀에 새 아이템 이름 기입.

해제:
1) 해당 슬롯 셀을 비우고, 공동 창고에 +1 반환.

설계 노트:
- 슬롯은 사용자가 명시적으로 지정해야 한다. 공동 창고 데이터에는 슬롯 분류
  정보가 없어 자동 추정이 불가능하다. (이전에는 상점 E열을 참조했으나,
  사용자 요청으로 공동 창고만 사용하도록 변경)
- 해제는 슬롯 이름 또는 현재 착용 중인 아이템 이름으로 모두 가능.
"""

from __future__ import annotations

from typing import Optional, Tuple

from commands.base_command import BaseCommand, CommandContext, CommandResponse
from commands.registry import register_command
from commands.trpg_common.fallback_helpers import acquire_user_lock
from utils.decorators import handle_command_errors
from utils.error_handling import CommandError
from utils.logging_config import logger
from utils.shared_sheet import (
    EQUIP_COL_ACCESSORY,
    EQUIP_COL_ARMOR,
    EQUIP_COL_WEAPON,
    EQUIP_DATA_START_ROW,
    WS_EQUIP_STOCK,
    add_to_inventory,
    consume_from_inventory,
    find_character_row,
    find_inventory_item,
    normalize_item_name,
    read_str_cell,
)


# 슬롯명 → (정식 라벨, 컬럼)
_SLOT_MAP = {
    '방어구': ('방어구', EQUIP_COL_ARMOR),
    '무기':   ('무기', EQUIP_COL_WEAPON),
    '액세서리': ('액세서리', EQUIP_COL_ACCESSORY),
    '부속품': ('액세서리', EQUIP_COL_ACCESSORY),
    '엑세서리': ('액세서리', EQUIP_COL_ACCESSORY),  # 사양 표기 오타 흡수
}


def _resolve_slot_token(token: str) -> Optional[Tuple[str, int]]:
    """슬롯 키워드 → (정식 라벨, 컬럼). 알 수 없으면 None."""
    return _SLOT_MAP.get((token or '').strip())


@register_command(
    name="아티팩트",
    aliases=['아티팩트 장착', '아티팩트장착', '아티팩트 해제', '아티팩트해제'],
    description="장비 슬롯에 아이템 장착/해제 (슬롯은 명시 필수)",
    category="아이템",
    examples=[
        "[아티팩트 장착/무기/철검]",
        "[아티팩트 장착/방어구/사슬갑옷]",
        "[아티팩트 해제/무기]",
        "[아티팩트 해제/사슬갑옷]",
    ],
    requires_sheets=True,
    requires_api=False,
    priority=10,
)
class ArtifactCommand(BaseCommand):

    @handle_command_errors(
        system_tag="아티팩트",
        user_error_message="아티팩트 명령어 처리 중 오류가 발생했습니다.",
    )
    def execute(self, context: CommandContext) -> CommandResponse:
        head = context.keywords[0].replace(' ', '')
        if head == '아티팩트장착':
            return self._handle_equip(context)
        if head == '아티팩트해제':
            return self._handle_unequip(context)
        raise CommandError(
            "사용법: [아티팩트 장착/슬롯/이름] / [아티팩트 해제/슬롯|이름]\n"
            "슬롯: 방어구 / 무기 / 액세서리"
        )

    # ------------------------------------------------------------------
    def _resolve_character_row(self, context: CommandContext) -> Tuple[str, int]:
        title = (context.user_name or '').strip()
        if not title:
            raise CommandError("마스토돈 표시명(=칭호)을 확인할 수 없습니다.")
        row = find_character_row(
            self.sheets_manager, WS_EQUIP_STOCK, title, EQUIP_DATA_START_ROW,
        )
        if row is None:
            raise CommandError(
                f"'장비 및 주식' 시트에서 '{title}' 캐릭터를 찾을 수 없습니다."
            )
        return title, row

    # ------------------------------------------------------------------
    def _handle_equip(self, context: CommandContext) -> CommandResponse:
        # [아티팩트 장착/<슬롯>/<이름>] — keywords = ['아티팩트 장착', 슬롯, 이름]
        if len(context.keywords) < 3:
            raise CommandError(
                "사용법: [아티팩트 장착/슬롯/이름]\n"
                "슬롯: 방어구 / 무기 / 액세서리\n"
                "예: [아티팩트 장착/무기/철검]"
            )

        slot_info = _resolve_slot_token(context.keywords[1])
        if slot_info is None:
            raise CommandError(
                f"'{context.keywords[1]}'은(는) 알 수 없는 슬롯입니다. "
                f"방어구 / 무기 / 액세서리 중 하나를 입력해 주세요."
            )
        slot_label, slot_col = slot_info

        # 이름이 슬래시를 포함할 수 있어 나머지 키워드를 모두 합침.
        item_name = '/'.join(context.keywords[2:]).strip()
        if not item_name:
            raise CommandError("장착할 아이템 이름이 비어 있습니다.")

        # 공동 창고에 존재하는지 확인
        entry = find_inventory_item(self.sheets_manager, item_name)
        if entry is None or entry.qty < 1:
            raise CommandError(f"공동 창고에 '{item_name}'이(가) 없습니다.")

        title, equip_row = self._resolve_character_row(context)

        with acquire_user_lock(context.user_id, timeout=10.0):
            # 1) 기존 착용 아이템 반환
            prev_equipped = read_str_cell(
                self.sheets_manager, WS_EQUIP_STOCK, equip_row, slot_col,
            )
            returned_msg = ''
            if prev_equipped:
                if not add_to_inventory(self.sheets_manager, prev_equipped, 1):
                    raise CommandError(
                        f"기존 장비 '{prev_equipped}' 반환에 실패했습니다."
                    )
                returned_msg = f"기존 {slot_label} '{prev_equipped}' → 창고 반환 (+1)\n"

            # 2) 공동 창고 차감
            if not consume_from_inventory(self.sheets_manager, item_name, 1):
                # 차감 실패 시, 기존 장비를 이미 반환했다면 롤백 시도.
                if prev_equipped:
                    consume_from_inventory(self.sheets_manager, prev_equipped, 1)
                raise CommandError(
                    f"공동 창고에서 '{item_name}' 차감에 실패했습니다."
                )

            # 3) 슬롯 셀 갱신 (창고 항목의 정식 표기 사용)
            canonical_name = entry.name
            ok = self.sheets_manager.update_cell(
                WS_EQUIP_STOCK, equip_row, slot_col, canonical_name,
            )
            if not ok:
                raise CommandError("장비 슬롯 셀 갱신에 실패했습니다.")

        message = (
            f"━━━ {title}님의 {slot_label} 장착 ━━━\n"
            f"{returned_msg}"
            f"{slot_label} ← '{canonical_name}' 장착 (창고 −1)"
        )
        logger.info(
            f"[아티팩트 장착] @{context.user_id} ({title}) {slot_label}={canonical_name} "
            f"prev={prev_equipped or '(none)'}"
        )
        return CommandResponse.create_success(message)

    # ------------------------------------------------------------------
    def _handle_unequip(self, context: CommandContext) -> CommandResponse:
        if len(context.keywords) < 2:
            raise CommandError(
                "사용법: [아티팩트 해제/슬롯] 또는 [아티팩트 해제/이름]\n"
                "슬롯: 방어구 / 무기 / 액세서리"
            )

        arg = context.keywords[1].strip()
        # 1순위: 슬롯명으로 해석
        slot_info = _resolve_slot_token(arg)

        title, equip_row = self._resolve_character_row(context)

        if slot_info is None:
            # 2순위: 이름으로 검색하여 어느 슬롯에 있는지 자동 탐색
            arg_norm = normalize_item_name(arg)
            for slot_label, slot_col in (
                ('방어구', EQUIP_COL_ARMOR),
                ('무기', EQUIP_COL_WEAPON),
                ('액세서리', EQUIP_COL_ACCESSORY),
            ):
                cell_value = read_str_cell(
                    self.sheets_manager, WS_EQUIP_STOCK, equip_row, slot_col,
                )
                if cell_value and normalize_item_name(cell_value) == arg_norm:
                    slot_info = (slot_label, slot_col)
                    break

        if slot_info is None:
            raise CommandError(
                f"'{arg}' 슬롯 또는 장착 아이템을 찾지 못했습니다. "
                f"[아티팩트 해제/방어구|무기|액세서리] 형태로 다시 시도해 주세요."
            )
        slot_label, slot_col = slot_info

        with acquire_user_lock(context.user_id, timeout=10.0):
            equipped = read_str_cell(
                self.sheets_manager, WS_EQUIP_STOCK, equip_row, slot_col,
            )
            if not equipped:
                raise CommandError(f"{slot_label} 슬롯에 장착된 아이템이 없습니다.")

            if not add_to_inventory(self.sheets_manager, equipped, 1):
                raise CommandError(
                    f"'{equipped}' 창고 반환에 실패했습니다."
                )
            clear_ok = self.sheets_manager.update_cell(
                WS_EQUIP_STOCK, equip_row, slot_col, '',
            )
            if not clear_ok:
                # 반환은 이미 됐는데 셀 비우기 실패 — 중복 반환 막기 위해 롤백.
                consume_from_inventory(self.sheets_manager, equipped, 1)
                raise CommandError("슬롯 비우기에 실패했습니다.")

        message = (
            f"━━━ {title}님의 {slot_label} 해제 ━━━\n"
            f"'{equipped}' → 공동 창고 반환 (+1)"
        )
        logger.info(
            f"[아티팩트 해제] @{context.user_id} ({title}) {slot_label}={equipped}"
        )
        return CommandResponse.create_success(message)
