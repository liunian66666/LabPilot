"""每次执行的预算；只在操作边界停止，不强杀进行中的外部副作用。"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic

from app.core.config import get_settings
from app.core.llm import IncompleteReplyError
from app.core.logging import log_event


class BudgetExceeded(IncompleteReplyError):
    pass


@dataclass
class RunBudget:
    llm_limit: int
    tool_limit: int
    seconds: float
    started: float = field(default_factory=monotonic)
    llm: int = 0
    tools: int = 0

    def consume(self, kind: str) -> None:
        if monotonic() - self.started >= self.seconds:
            raise BudgetExceeded(
                "本轮执行达到时间预算，已停止后续操作；已完成操作不会回滚，请检查任务记录。"
            )
        if kind == "llm":
            if self.llm >= self.llm_limit:
                raise BudgetExceeded(
                    "本轮达到模型调用上限，已停止；请缩小任务范围并检查已完成的操作。"
                )
            self.llm += 1
        else:
            if self.tools >= self.tool_limit:
                raise BudgetExceeded(
                    "本轮达到工具调用上限，已停止；请检查已完成的操作后继续。"
                )
            self.tools += 1


_budget: ContextVar[RunBudget | None] = ContextVar("run_budget", default=None)


def begin_budget():
    settings = get_settings()
    return _budget.set(
        RunBudget(
            settings.AGENT_MAX_LLM_CALLS,
            settings.AGENT_MAX_TOOL_CALLS,
            settings.AGENT_RUN_SECONDS,
        )
    )


def end_budget(token, **fields):
    budget = _budget.get()
    if budget:
        log_event(
            "agent_run_summary",
            llm_calls=budget.llm,
            tool_calls=budget.tools,
            duration_seconds=round(monotonic() - budget.started, 3),
            **fields,
        )
    _budget.reset(token)


def consume(kind: str):
    budget = _budget.get()
    if budget:
        budget.consume(kind)
