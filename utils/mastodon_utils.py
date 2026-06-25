"""마스토돈 응답 메시지 파싱·멘션 포맷 유틸.

원래 `handlers/stream_handler.py` 안에 정의되어 있던 HTMLCleaner / MentionManager 를
별도 파일로 끌어내, 스트림 핸들러를 ~110줄 줄이고 두 유틸을 단위 테스트와 다른
호출자(예: 향후 폴링 모듈)가 재사용하기 쉽게 한다.

행동 변경 없음 — 기존 호출자(stream_handler.py) 의 결과는 그대로 유지된다.
"""

from __future__ import annotations

from typing import List

from bs4 import BeautifulSoup

from utils.logging_config import logger

try:
    from config.settings import config
except ImportError:  # 테스트/임포트 폴백
    config = None  # type: ignore[assignment]


class HTMLCleaner:
    """HTML 콘텐츠 → 텍스트 / 멘션 추출."""

    @staticmethod
    def extract_text(html_content: str) -> str:
        """HTML 태그 제거 후 텍스트만 추출. 파싱 실패 시 원문 반환(데이터 손실 방지)."""
        if not html_content:
            return ""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            return soup.get_text(separator=' ', strip=True)
        except Exception as e:
            logger.warning(f"HTML 파싱 오류: {e}")
            return html_content

    @staticmethod
    def extract_mentions(html_content: str) -> List[str]:
        """HTML 의 `<a class="mention">` 링크에서 사용자 ID(@뒷부분) 만 뽑아낸다."""
        mentions: List[str] = []
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            mention_links = soup.find_all('a', class_='mention')
            for link in mention_links:
                href = link.get('href', '')
                # href 예: https://instance.com/@username
                if '@' in href:
                    user_id = href.split('@')[-1]
                    if user_id:
                        mentions.append(user_id)
        except Exception as e:
            logger.warning(f"HTML 멘션 추출 실패: {e}")
        return mentions


class MentionManager:
    """답변 본문에 들어갈 `@user @user2 ...` 문자열을 길이 제한 내에서 만든다."""

    MAX_USERS_TO_MENTION = 5  # 최대 멘션할 사용자 수

    @staticmethod
    def get_max_mention_length() -> int:
        """전체 메시지 길이의 20% 를 멘션용 상한으로 사용 (없으면 100자)."""
        if config:
            return min(100, config.MAX_MESSAGE_LENGTH // 5)
        return 100

    @staticmethod
    def format_mentions(mentioned_users: List[str]) -> str:
        """사용자 목록을 멘션 문자열로. 길이 초과 시 일부만 + "외 N명" 으로 축약."""
        if not mentioned_users:
            return ""

        users_to_mention = mentioned_users[:MentionManager.MAX_USERS_TO_MENTION]
        mentions = ' '.join([f"@{user}" for user in users_to_mention])

        max_mention_length = MentionManager.get_max_mention_length()
        if len(mentions) <= max_mention_length:
            return mentions

        # 길이 초과 — 가능한 만큼만 포함하고 나머지는 "외 N명" 표기로 축약.
        truncated_users: List[str] = []
        current_length = 0
        for user in users_to_mention:
            mention = f"@{user}"
            if current_length + len(mention) + 1 > max_mention_length - 10:  # 여유 공간
                break
            truncated_users.append(user)
            current_length += len(mention) + 1

        if truncated_users:
            mentions = ' '.join([f"@{user}" for user in truncated_users])
            excluded_count = len(mentioned_users) - len(truncated_users)
            if excluded_count > 0:
                mentions += f" 외 {excluded_count}명"
            return mentions

        # 한 명도 포함할 수 없는 극단 케이스 — 첫 사용자명 일부만 + "외 N명".
        return f"@{mentioned_users[0][:10]}... 외 {len(mentioned_users) - 1}명"
