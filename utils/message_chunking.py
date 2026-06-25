"""
메시지 분할 (CoC 봇)

긴 응답 메시지를 마스토돈 툿 한 개 크기로 잘라 스레드 전송이 가능하도록 한다.

외부 사용: `MessageChunker(max_length).split_message(text) -> List[str]` 한 가지.
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional

from utils.logging_config import logger

try:
    from config.settings import config
except ImportError:
    config = None


class MessageChunker:
    """메시지를 `max_length` 이하로 쪼개고 (계속)/(계속...) 마커를 덧붙인다."""

    def __init__(self, max_length: Optional[int] = None):
        if max_length is None:
            # 안전 임계가 있으면 그것을, 없으면 server limit, 그것도 없으면 1000.
            if config and hasattr(config, 'safe_message_length'):
                self.max_length = config.safe_message_length()
            elif config:
                self.max_length = config.MAX_MESSAGE_LENGTH
            else:
                self.max_length = 1000
        else:
            self.max_length = max_length

    # ------------------ public ------------------

    def split_message(self, message: str) -> List[str]:
        """일반 문자열 분할 (줄 → 단어 단위로 순차 축적)."""
        if not message:
            return []
        if len(message) <= self.max_length:
            return [message]

        chunks: List[str] = []
        current = ""
        for line in message.split('\n'):
            if len(line) > self.max_length:
                if current:
                    chunks.append(current.strip())
                    current = ""
                word_chunks = self._split_long_line(line)
                chunks.extend(word_chunks[:-1])
                current = word_chunks[-1] if word_chunks else ""
                continue

            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > self.max_length:
                chunks.append(current.strip())
                current = line
            else:
                current = candidate

        if current:
            chunks.append(current.strip())

        return self._add_continuation_markers(chunks)

    # ------------------ private ------------------

    def _split_long_line(self, line: str) -> List[str]:
        """한 줄이 max_length 를 넘어가면 단어 단위로 분할."""
        words = line.split(' ')
        chunks: List[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}" if current else word
            if len(candidate) <= self.max_length:
                current = candidate
            else:
                if current:
                    chunks.append(current.strip())
                current = word
        if current:
            chunks.append(current.strip())
        return chunks

    def _add_continuation_markers(self, chunks: List[str]) -> List[str]:
        """여러 청크일 때 '(계속)' / '(계속...)' 마커를 붙여 연속성을 표시."""
        if len(chunks) <= 1:
            return chunks

        marked: List[str] = []
        for i, chunk in enumerate(chunks):
            if i == 0:
                marked.append(chunk + "\n\n(계속...)")
            elif i == len(chunks) - 1:
                marked.append("(계속)\n\n" + chunk)
            else:
                marked.append("(계속)\n\n" + chunk + "\n\n(계속...)")
        return marked
