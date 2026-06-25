"""
사용자 데이터 모델 (CoC 봇)

명단(roster) 전역 테이블을 사용하지 않는다. 캐릭터 데이터는 시트 안의 사용자
acct 이름 워크시트에 직접 저장. 이 모듈은 명령어 실행 시점에 잠깐 쓰이는
최소한의 User dataclass 만 제공한다.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

@dataclass
class User:
    """명령어 문맥에서 사용자 1명을 표현하는 경량 DTO."""

    id: str
    name: str = ""
    additional_data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.name:
            self.name = self.id

    def __str__(self) -> str:
        return f"User(id={self.id}, name={self.name})"


def create_empty_user(user_id: str) -> User:
    """id 만 채운 빈 User. BaseCommand 폴백 경로에서 사용."""
    return User(id=user_id or '', name=user_id or '')
