"""
자동봇 가동 기간 관리 (KST 기준)

`OPERATION_START_DATE` / `OPERATION_END_DATE` 환경 변수를 KST 기준 0시 경계로
해석한다. 둘 중 비어 있는 값은 "제한 없음" 으로 처리.

경계 의미:
  - START_DATE = 2026-04-28 → 2026-04-28 00:00 KST 부터 활성 (이전엔 'pre')
  - END_DATE   = 2026-05-01 → 2026-05-01 00:00 KST 부터 만료 ('expired')

상태 전이:
  - 'pre'     : 가동 시작 전. 명령어를 무시 (응답 없음).
  - 'active'  : 정상 가동.
  - 'expired' : 가동 기간 만료. 사용자당 1회 만료 안내 후 이후 연락 무시.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

KST = timezone(timedelta(hours=9))

EXPIRATION_MESSAGE: str = (
    "자동봇 가동 기간이 만료되어 가동을 종료합니다. "
    "본 자동봇은 한참 커미션으로 진행되었습니다. "
    "https://crepe.cm/@longwhile/lw5w0ofg"
)


def parse_kst_date(value: Optional[str]) -> Optional[date]:
    """`YYYY-MM-DD` 문자열을 `date` 로 파싱.

    빈 값/공백/None/형식 오류 → None (호출자가 '제한 없음' 으로 처리).

    Args:
        value: 파싱할 문자열.

    Returns:
        파싱된 `date` 또는 None.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def get_operation_status(
    start_date: Optional[date],
    end_date: Optional[date],
    now: Optional[datetime] = None,
) -> str:
    """현재 KST 시각 기준의 가동 상태를 결정.

    Args:
        start_date: 시작 날짜 (이 날짜 00:00 KST 부터 'active'). None 이면 무제한.
        end_date: 종료 날짜 (이 날짜 00:00 KST 부터 'expired'). None 이면 무제한.
        now: 비교할 시각 (테스트용). None 이면 현재 KST. naive datetime 은 KST 로 간주.

    Returns:
        'pre' | 'active' | 'expired'
    """
    if now is None:
        current = datetime.now(KST)
    elif now.tzinfo is None:
        current = now.replace(tzinfo=KST)
    else:
        current = now.astimezone(KST)

    if end_date is not None:
        end_boundary = datetime.combine(end_date, datetime.min.time(), tzinfo=KST)
        if current >= end_boundary:
            return 'expired'

    if start_date is not None:
        start_boundary = datetime.combine(start_date, datetime.min.time(), tzinfo=KST)
        if current < start_boundary:
            return 'pre'

    return 'active'


def get_current_operation_status() -> str:
    """`config.settings.config` 의 env 값을 읽어 현재 상태를 반환.

    런타임 호출자(예: stream_handler)가 이 함수만 부르면 됨. config 임포트는
    함수 내부에서 lazy 로 — 모듈 임포트 시점의 순환을 피한다.
    """
    try:
        from config.settings import config
    except Exception:  # 임포트 실패 시 안전 폴백 — 무제한으로 동작
        return 'active'

    start = parse_kst_date(getattr(config, 'OPERATION_START_DATE', '') or '')
    end = parse_kst_date(getattr(config, 'OPERATION_END_DATE', '') or '')
    return get_operation_status(start, end)
