"""工具执行上下文：user_id / session_id。

产物收集见 app.agent.artifacts（线程安全全局表；工具可能跑在工作线程）。
"""

from contextvars import ContextVar

_current_user: ContextVar[str] = ContextVar("labpilot_user", default="dev")
_current_session: ContextVar[str] = ContextVar("labpilot_session", default="dev")


def set_actor_context(user_id: str, session_id: str):
    return _current_user.set(user_id), _current_session.set(session_id)


def reset_actor_context(tokens) -> None:
    _current_user.reset(tokens[0])
    _current_session.reset(tokens[1])


def current_user_id() -> str:
    return _current_user.get()


def current_session_id() -> str:
    return _current_session.get()
