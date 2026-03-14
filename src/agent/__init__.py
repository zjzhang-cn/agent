from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
	from .ai_agent import AIAgent


def __getattr__(name: str) -> Any:
	if name == "AIAgent":
		from .ai_agent import AIAgent

		return AIAgent
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__version__ = "0.1.0"
__all__ = ["AIAgent"]