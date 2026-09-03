"""Abstract tool interface used by agents."""

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def description(self) -> str:
        pass

    @abstractmethod
    def use(self, args: Any) -> str:
        pass
