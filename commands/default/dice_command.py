"""
다이스 명령어 구현 - 새로운 BaseCommand 아키텍처
주사위 굴리기 기능을 제공하는 명령어 클래스입니다.
"""

import os
import sys
import random
import re
from typing import List, Tuple, Any, Optional, Dict

# 경로 설정 (VM 환경 대응)
try:
    from config.settings import config
    from utils.logging_config import logger
    from commands.base_command import BaseCommand, CommandContext, CommandResponse
    from commands.registry import register_command
    from models.command_result import DiceResult, create_dice_result
except ImportError as e:
    import logging
    logger = logging.getLogger('dice_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="NdM",
    aliases=["dice", "다이스"],
    description="주사위 굴리기",
    category="게임",
    examples=["[1d6]", "[2d6]", "[1d20+5]", "[3d6-2]", "[NdM/1d100]", "[3d6<4]", "[1d20>15]"],
    requires_sheets=False,
    requires_api=False
)
class DiceCommand(BaseCommand):
    """
    다이스 굴리기 명령어 클래스

    지원하는 형식:
    - [다이스/1d100] : 100면체 주사위 1개
    - [다이스/2d6] : 6면체 주사위 2개
    - [1d20+5] : 20면체 주사위 1개 + 5 보정
    - [2d6-3] : 6면체 주사위 2개 - 3 보정
    - [다이스/3d6<4] : 6면체 주사위 3개, 4 이하면 성공
    - [다이스/1d20>15] : 20면체 주사위 1개, 15 이상이면 성공
    - [1d6] : 직접 다이스 표현식 (다이스 키워드 없이)
    """
    
    def execute(self, context: CommandContext) -> CommandResponse:
        """
        다이스 명령어 실행
        
        Args:
            context: 명령어 실행 컨텍스트
            
        Returns:
            CommandResponse: 실행 결과
        """
        try:
            # 키워드에서 다이스 표현식 추출
            dice_expression = self._extract_dice_expression(context.keywords)
            
            # 다이스 표현식 파싱
            dice_config = self._parse_dice_expression(dice_expression)
            
            # 제한 검증
            self._validate_dice_limits(dice_config)
            
            # 주사위 굴리기
            rolls = self._roll_dice(dice_config['num_dice'], dice_config['dice_sides'])
            
            # 결과 계산
            dice_result = self._calculate_result(dice_expression, rolls, dice_config)
            
            # 결과 메시지 생성
            message = self._format_result_message(dice_result)
            
            return CommandResponse.create_success(message, data=dice_result)
            
        except ValueError as e:
            # 사용자 입력 오류 — 메시지를 그대로 노출 (이미 친화적 한국어).
            logger.debug(f"다이스 입력 오류: {e}")
            return CommandResponse.create_error(str(e), error=e)
        except Exception as e:
            # 내부 오류 — 사용자에게는 일반화된 메시지만, 상세는 로그로.
            logger.error(f"다이스 명령어 내부 오류: {e}", exc_info=True)
            return CommandResponse.create_error(
                "주사위 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                error=e,
            )
    
    def _extract_dice_expression(self, keywords: List[str]) -> str:
        """
        키워드에서 다이스 표현식 추출 (개선된 버전)
        
        Args:
            keywords: 키워드 리스트
            
        Returns:
            str: 다이스 표현식
            
        Raises:
            ValueError: 표현식이 없거나 잘못된 경우
        """
        if not keywords:
            raise ValueError("주사위 식을 입력해 주세요. 예: [2d6], [1d20+5]")
        
        # 케이스 1: [다이스/2d6] 형태
        if len(keywords) >= 2 and keywords[0].lower() in ['다이스', 'dice']:
            return keywords[1].replace(" ", "")
        
        # 케이스 2: [2d6] 형태 (직접 다이스 표현식)
        elif len(keywords) >= 1:
            potential_expr = keywords[0].replace(" ", "")
            if self._is_dice_expression(potential_expr):
                return potential_expr
        
        # 케이스 3: [다이스] 키워드만 있는 경우 - 기본값 제공
        if len(keywords) == 1 and keywords[0].lower() in ['다이스', 'dice']:
            # 기본 1d6 제공하고 안내 메시지
            logger.info(f"기본 다이스(1d6) 사용 - 사용자에게 안내 메시지 포함")
            return "1d6"  # 기본값으로 6면체 주사위 1개
        
        raise ValueError(
            "주사위 형식이 올바르지 않습니다. 예: [2d6], [1d20+5], [3d6<4]"
        )
    
    def _is_dice_expression(self, expression: str) -> bool:
        """
        문자열이 다이스 표현식인지 확인

        Args:
            expression: 확인할 문자열

        Returns:
            bool: 다이스 표현식 여부
        """
        # 기본 다이스 패턴: 숫자d숫자[+/-숫자][</>숫자]
        dice_pattern = re.compile(r'^\d+[dD]\d+([\+\-]\d+)?([<>]\d+)?$')
        return bool(dice_pattern.match(expression))
    
    def _parse_dice_expression(self, dice_expression: str) -> Dict[str, Any]:
        """
        다이스 표현식 파싱

        Args:
            dice_expression: 다이스 표현식 (예: "2d6", "3d6<4", "1d20+5")

        Returns:
            Dict: 파싱된 다이스 설정

        Raises:
            ValueError: 파싱 실패
        """
        if not dice_expression:
            raise ValueError("주사위 식을 입력해 주세요. 예: [2d6], [1d20+5]")

        # 성공/실패 조건 파싱
        threshold = None
        threshold_type = None

        if '<' in dice_expression:
            dice_part, threshold_str = dice_expression.split('<')
            threshold = int(threshold_str)
            threshold_type = '<'
        elif '>' in dice_expression:
            dice_part, threshold_str = dice_expression.split('>')
            threshold = int(threshold_str)
            threshold_type = '>'
        else:
            dice_part = dice_expression

        # 보정값(modifier) 파싱 (예: 1d20+5, 2d6-3)
        modifier = 0
        modifier_match = re.search(r'([\+\-]\d+)', dice_part)
        if modifier_match:
            modifier_str = modifier_match.group(1)
            modifier = int(modifier_str)
            # 보정값 부분을 제거하여 기본 다이스만 남김
            dice_part = dice_part.replace(modifier_str, '')

        # 기본 다이스 표현식 파싱 (예: 2d6)
        match = re.match(r'(\d+)[dD](\d+)', dice_part.lower())
        if not match:
            raise ValueError(
                f"'{dice_expression}'을(를) 주사위 식으로 인식할 수 없습니다. "
                f"예: [2d6], [1d20+5], [3d6<4]"
            )

        try:
            num_dice = int(match.group(1))
            dice_sides = int(match.group(2))
        except ValueError:
            raise ValueError(
                f"'{dice_expression}'에 숫자가 아닌 값이 섞여 있어 처리할 수 없습니다."
            )

        return {
            'num_dice': num_dice,
            'dice_sides': dice_sides,
            'modifier': modifier,
            'threshold': threshold,
            'threshold_type': threshold_type,
            'original_expression': dice_expression
        }
    
    def _validate_dice_limits(self, dice_config: Dict[str, Any]) -> None:
        """
        다이스 제한 검증
        
        Args:
            dice_config: 다이스 설정
            
        Raises:
            ValueError: 제한 초과
        """
        num_dice = dice_config['num_dice']
        dice_sides = dice_config['dice_sides']
        
        # 주사위 개수 제한
        if num_dice < 1:
            raise ValueError("주사위 개수는 1개 이상이어야 합니다.")
        
        if num_dice > config.MAX_DICE_COUNT:
            raise ValueError(f"주사위 개수는 최대 {config.MAX_DICE_COUNT}개까지 가능합니다.")
        
        # 주사위 면수 제한
        if dice_sides < 2:
            raise ValueError("주사위 면수는 2면 이상이어야 합니다.")
        
        if dice_sides > config.MAX_DICE_SIDES:
            raise ValueError(f"주사위 면수는 최대 {config.MAX_DICE_SIDES}면까지 가능합니다.")
        
        # 성공 기준값 검증
        threshold = dice_config.get('threshold')
        if threshold is not None:
            if threshold < 1 or threshold > dice_sides:
                raise ValueError(
                    f"성공 기준값은 1과 {dice_sides} 사이의 숫자여야 합니다."
                )
    
    def _roll_dice(self, num_dice: int, dice_sides: int) -> List[int]:
        """
        주사위 굴리기
        
        Args:
            num_dice: 주사위 개수
            dice_sides: 주사위 면수
            
        Returns:
            List[int]: 각 주사위 결과
        """
        rolls = []
        for _ in range(num_dice):
            roll = random.randint(1, dice_sides)
            rolls.append(roll)
        
        logger.debug(f"주사위 굴리기: {num_dice}d{dice_sides} = {rolls}")
        return rolls
    
    def _calculate_result(self, expression: str, rolls: List[int], dice_config: Dict[str, Any]) -> DiceResult:
        """
        다이스 결과 계산

        Args:
            expression: 원본 다이스 표현식
            rolls: 주사위 결과들
            dice_config: 다이스 설정

        Returns:
            DiceResult: 계산된 결과
        """
        threshold = dice_config.get('threshold')
        threshold_type = dice_config.get('threshold_type')
        modifier = dice_config.get('modifier', 0)

        # 성공/실패 개수 계산
        success_count = None
        fail_count = None

        if threshold is not None and threshold_type:
            success_count = 0
            for roll in rolls:
                if threshold_type == '<' and roll <= threshold:
                    success_count += 1
                elif threshold_type == '>' and roll >= threshold:
                    success_count += 1

            fail_count = len(rolls) - success_count

        # DiceResult 객체 생성
        try:
            return create_dice_result(
                expression=expression,
                rolls=rolls,
                modifier=modifier,
                threshold=threshold,
                threshold_type=threshold_type
            )
        except Exception as e:
            logger.warning("DiceResult 생성 실패, 더미 객체 사용: %s", e)
            # create_dice_result가 없는 경우 더미 객체
            class DummyDiceResult:
                def __init__(self):
                    self.expression = expression
                    self.rolls = rolls
                    self.modifier = modifier
                    self.total = sum(rolls) + modifier
                    self.threshold = threshold
                    self.threshold_type = threshold_type
                    self.success_count = success_count
                    self.fail_count = fail_count
                    self.has_threshold = threshold is not None

                def is_success(self):
                    if not self.has_threshold or len(self.rolls) != 1:
                        return None
                    roll_value = self.rolls[0] + self.modifier
                    if self.threshold_type == '<':
                        return roll_value <= self.threshold
                    elif self.threshold_type == '>':
                        return roll_value >= self.threshold
                    return None

            return DummyDiceResult()
    
    def _format_result_message(self, dice_result) -> str:
        """
        결과 메시지 포맷팅 (개선된 버전)

        Args:
            dice_result: 다이스 결과

        Returns:
            str: 포맷된 결과 메시지
        """
        expression = getattr(dice_result, 'expression', '')
        rolls = dice_result.rolls
        modifier = getattr(dice_result, 'modifier', 0)

        # 시각적 개선
        if len(rolls) == 1:
            # 단일 주사위
            result_value = rolls[0]

            # 보정값이 있으면 표시
            if modifier != 0:
                modifier_str = f"{modifier:+d}"  # +5 또는 -3 형식
                total_value = result_value + modifier

                if hasattr(dice_result, 'has_threshold') and dice_result.has_threshold:
                    # 성공/실패 조건이 있는 경우
                    success = dice_result.is_success() if hasattr(dice_result, 'is_success') else None
                    if success is not None:
                        result_text = "[성공]" if success else "[실패]"
                        return f"{result_value}{modifier_str} = {total_value} {result_text}"
                    else:
                        return f"{result_value}{modifier_str} = {total_value}"
                else:
                    # 일반 단일 주사위 + 보정값
                    return f"{result_value}{modifier_str} = {total_value}"
            else:
                # 보정값 없음
                if hasattr(dice_result, 'has_threshold') and dice_result.has_threshold:
                    # 성공/실패 조건이 있는 경우
                    success = dice_result.is_success() if hasattr(dice_result, 'is_success') else None
                    if success is not None:
                        result_text = "[성공]" if success else "[실패]"
                        return f"{result_value} {result_text}"
                    else:
                        return f"{result_value}"
                else:
                    # 일반 단일 주사위
                    return f"{result_value}"
        else:
            # 복수 주사위
            rolls_str = ", ".join(str(roll) for roll in rolls)
            rolls_sum = sum(rolls)

            # 보정값이 있으면 표시
            if modifier != 0:
                modifier_str = f"{modifier:+d}"
                total = rolls_sum + modifier

                if hasattr(dice_result, 'has_threshold') and dice_result.has_threshold and dice_result.success_count is not None:
                    # 성공/실패 조건이 있는 경우
                    success_text = "[성공]" if dice_result.success_count > 0 else "[실패]"
                    return f"{rolls_str}{modifier_str}\n합계: {total} {success_text} 성공: {dice_result.success_count}개, 실패: {dice_result.fail_count}개"
                else:
                    # 일반 복수 주사위 + 보정값
                    return f"{rolls_str}{modifier_str}\n합계: {total}"
            else:
                # 보정값 없음
                if hasattr(dice_result, 'has_threshold') and dice_result.has_threshold and dice_result.success_count is not None:
                    # 성공/실패 조건이 있는 경우
                    success_text = "[성공]" if dice_result.success_count > 0 else "[실패]"
                    return f"{rolls_str}\n{success_text} 성공: {dice_result.success_count}개, 실패: {dice_result.fail_count}개"
                else:
                    # 일반 복수 주사위
                    return f"{rolls_str}\n합계: {rolls_sum}"
    
    def validate_context(self, context: CommandContext) -> Optional[str]:
        """컨텍스트 유효성 검증 (오버라이드)"""
        # 기본 검증
        base_validation = super().validate_context(context)
        if base_validation:
            return base_validation
        
        # 다이스 특화 검증
        if not context.keywords:
            return "다이스 표현식이 필요합니다."
        
        return None

    @staticmethod
    def get_supported_keywords() -> List[str]:
        """지원 키워드 (대표 우선)"""
        return ['NdM', 'dice', '다이스']
    
    def get_random_example(self) -> str:
        """랜덤한 다이스 예시 반환"""
        examples = [
            "1d100",
            "2d6", 
            "3d6",
            "1d20",
            "4d6",
            "1d12",
            "2d10",
            "3d6<4",
            "1d20>10",
            "2d6>7"
        ]
        return random.choice(examples)
    
    def simulate_dice_roll(self, expression: str, iterations: int = 1000) -> Dict[str, Any]:
        """
        다이스 굴리기 시뮬레이션 (통계용)
        
        Args:
            expression: 다이스 표현식
            iterations: 시뮬레이션 횟수
            
        Returns:
            Dict: 시뮬레이션 결과 통계
        """
        if iterations > 10000:  # 과도한 시뮬레이션 방지
            iterations = 10000
        if iterations <= 0:
            return {
                'error': 'iterations 는 1 이상이어야 합니다',
                'expression': expression,
                'iterations': iterations,
            }

        try:
            dice_config = self._parse_dice_expression(expression)
            self._validate_dice_limits(dice_config)

            results = []
            success_counts = []

            for _ in range(iterations):
                rolls = self._roll_dice(dice_config['num_dice'], dice_config['dice_sides'])
                dice_result = self._calculate_result(expression, rolls, dice_config)

                results.append(dice_result.total)
                if hasattr(dice_result, 'success_count') and dice_result.success_count is not None:
                    success_counts.append(dice_result.success_count)

            # _calculate_result 가 모든 반복에서 None/예외성 결과를 낸 비정상 상황 방어.
            if not results:
                return {
                    'error': '시뮬레이션 결과가 비어있습니다',
                    'expression': expression,
                    'iterations': iterations,
                }

            stats = {
                'expression': expression,
                'iterations': iterations,
                'min_result': min(results),
                'max_result': max(results),
                'average': sum(results) / len(results),
                'most_common': max(set(results), key=results.count)
            }
            
            if success_counts:
                stats['average_successes'] = sum(success_counts) / len(success_counts)
                stats['success_rate'] = (sum(1 for s in success_counts if s > 0) / len(success_counts)) * 100
            
            return stats
            
        except Exception as e:
            return {'error': str(e)}


# 다이스 표현식 직접 검증 함수들 (유틸리티)
def is_dice_command(keyword: str) -> bool:
    """
    키워드가 다이스 명령어인지 확인

    Args:
        keyword: 확인할 키워드

    Returns:
        bool: 다이스 명령어 여부
    """
    if not keyword:
        return False

    keyword = keyword.lower().strip()

    # '다이스' 키워드
    if keyword in ['다이스', 'dice']:
        return True

    # 직접 다이스 표현식 (예: "2d6", "1d20+5", "1d100<50")
    dice_pattern = re.compile(r'^\d+[dD]\d+([\+\-]\d+)?([<>]\d+)?$')
    return bool(dice_pattern.match(keyword))


def extract_dice_from_text(text: str) -> List[str]:
    """
    텍스트에서 다이스 표현식들 추출

    Args:
        text: 분석할 텍스트

    Returns:
        List[str]: 발견된 다이스 표현식들
    """
    dice_pattern = re.compile(r'\b\d+[dD]\d+([\+\-]\d+)?([<>]\d+)?\b')
    return dice_pattern.findall(text)


def validate_dice_expression(expression: str) -> Tuple[bool, str]:
    """
    다이스 표현식 유효성 검사 (독립 함수)
    
    Args:
        expression: 검증할 표현식
        
    Returns:
        Tuple[bool, str]: (유효성, 메시지)
    """
    try:
        dice_command = DiceCommand()
        dice_config = dice_command._parse_dice_expression(expression)
        dice_command._validate_dice_limits(dice_config)
        return True, "유효한 다이스 표현식입니다."
    except Exception as e:
        return False, f"오류: {str(e)}"


# 다이스 명령어 인스턴스 생성 함수
def create_dice_command(sheets_manager=None, api=None) -> DiceCommand:
    """
    다이스 명령어 인스턴스 생성
    
    Args:
        sheets_manager: Google Sheets 관리자
        api: API 인스턴스
        
    Returns:
        DiceCommand: 다이스 명령어 인스턴스
    """
    return DiceCommand(sheets_manager=sheets_manager, api=api)