"""
[상태창] — '전투용 정보' (능력치) + '레이드 정보' (HP/MP) + '장비 및 주식' (골드/장비)
종합 출력.

각 시트 A열의 칭호(= display_name) 로 행을 찾는다.
- 능력치 5종(근력/민첩/지능/행운/정신)은 '전투용 정보' 시트의 헤더 텍스트 매칭으로 조회.
- HP/MP는 '레이드 정보' 시트의 K/L/M/N 고정 컬럼.
- 골드/장비는 '장비 및 주식' 시트의 B/C/D/E 고정 컬럼.
"""

from __future__ import annotations

from commands.base_command import BaseCommand, CommandContext, CommandResponse
from commands.registry import register_command
from utils.decorators import handle_command_errors
from utils.error_handling import CommandError
from utils.logging_config import logger
from utils.shared_sheet import (
    EQUIP_COL_ACCESSORY,
    EQUIP_COL_ARMOR,
    EQUIP_COL_GOLD,
    EQUIP_COL_WEAPON,
    EQUIP_DATA_START_ROW,
    RAID_COL_HP_CUR,
    RAID_COL_HP_MAX,
    RAID_COL_MP_CUR,
    RAID_COL_MP_MAX,
    RAID_DATA_START_ROW,
    WS_EQUIP_STOCK,
    WS_RAID,
    find_character_row,
    get_combat_stat,
    read_int_cell,
    read_str_cell,
)


# 상태창에 표시할 능력치 — '전투용 정보' 시트 헤더에 동일 이름 컬럼이 있어야 함.
_ABILITY_NAMES = ('근력', '민첩', '지능', '행운', '정신')


@register_command(
    name="상태창",
    aliases=[],
    description="능력치/HP/MP/골드/장비 종합 표시",
    category="레이드",
    examples=["[상태창]"],
    requires_sheets=True,
    requires_api=False,
    priority=10,
)
class StatusBoardCommand(BaseCommand):

    @handle_command_errors(
        system_tag="상태창",
        user_error_message="상태창 조회 중 오류가 발생했습니다.",
    )
    def execute(self, context: CommandContext) -> CommandResponse:
        title = (context.user_name or '').strip()
        if not title:
            raise CommandError("마스토돈 표시명(=칭호)을 확인할 수 없습니다.")

        equip_row = find_character_row(
            self.sheets_manager, WS_EQUIP_STOCK, title, EQUIP_DATA_START_ROW,
        )
        raid_row = find_character_row(
            self.sheets_manager, WS_RAID, title, RAID_DATA_START_ROW,
        )

        # 능력치는 '전투용 정보'에서 조회. 한 시트라도 있으면 진행.
        ability_pairs = []
        for name in _ABILITY_NAMES:
            value = get_combat_stat(self.sheets_manager, title, name)
            ability_pairs.append((name, value))

        if all(v is None for _, v in ability_pairs) and equip_row is None and raid_row is None:
            raise CommandError(
                f"'{title}' 캐릭터를 어느 시트에서도 찾지 못했습니다. "
                f"'장비 및 주식'/'레이드 정보'/'전투용 정보' A열의 칭호를 확인해 주세요."
            )

        # 능력치 라인 -----------------------------------------------------
        if any(v is not None for _, v in ability_pairs):
            ability_line = ' / '.join(
                f"{name} {value if value is not None else '?'}"
                for name, value in ability_pairs
            )
        else:
            ability_line = "(전투용 정보에서 능력치를 찾지 못했습니다.)"

        # HP/MP -----------------------------------------------------------
        if raid_row is None:
            hpmp_line = "(레이드 정보 행을 찾지 못했습니다.)"
        else:
            hp_cur = read_int_cell(self.sheets_manager, WS_RAID, raid_row, RAID_COL_HP_CUR)
            hp_max = read_int_cell(self.sheets_manager, WS_RAID, raid_row, RAID_COL_HP_MAX)
            mp_cur = read_int_cell(self.sheets_manager, WS_RAID, raid_row, RAID_COL_MP_CUR)
            mp_max = read_int_cell(self.sheets_manager, WS_RAID, raid_row, RAID_COL_MP_MAX)
            hpmp_line = f"HP {hp_cur}/{hp_max}    MP {mp_cur}/{mp_max}"

        # 장비 정보 -------------------------------------------------------
        if equip_row is None:
            equip_block = "(장비 및 주식 행을 찾지 못했습니다.)"
        else:
            gold = read_int_cell(self.sheets_manager, WS_EQUIP_STOCK, equip_row, EQUIP_COL_GOLD)
            armor = read_str_cell(self.sheets_manager, WS_EQUIP_STOCK, equip_row, EQUIP_COL_ARMOR) or '(없음)'
            weapon = read_str_cell(self.sheets_manager, WS_EQUIP_STOCK, equip_row, EQUIP_COL_WEAPON) or '(없음)'
            accessory = read_str_cell(self.sheets_manager, WS_EQUIP_STOCK, equip_row, EQUIP_COL_ACCESSORY) or '(없음)'
            equip_block = (
                f"보유 골드: {gold}\n"
                f"방어구: {armor}\n"
                f"무기: {weapon}\n"
                f"액세서리: {accessory}"
            )

        message = (
            f"━━━ {title}님의 상태창 ━━━\n"
            f"[능력치]\n{ability_line}\n"
            f"{hpmp_line}\n"
            f"[장비]\n{equip_block}"
        )
        logger.info(f"[상태창] @{context.user_id} ({title}) 출력")
        return CommandResponse.create_success(message)
