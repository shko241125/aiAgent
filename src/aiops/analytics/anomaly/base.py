"""이상 탐지 공통 인터페이스 (3.1)."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import BaseModel


class Anomaly(BaseModel):
    index: int
    value: float
    score: float  # 탐지기별 이상 점수 (클수록 이상)
    expected: float | None = None
    method: str


class AnomalyDetector(ABC):
    name: str = "base"

    @abstractmethod
    def detect(self, values: Sequence[float]) -> list[Anomaly]:
        """시계열 전체를 받아 이상 지점 목록을 반환 (배치 방식).

        TODO(3.1): 스트리밍(온라인) 탐지용 `update(value) -> Anomaly | None` 인터페이스 추가.
        """
