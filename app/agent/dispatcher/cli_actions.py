import asyncio
import inspect
from typing import Any, Callable, Dict

from app.agent.dispatcher.registry import get_action_meta


def _load_handler(action: str) -> Callable[..., Any]:
    meta = get_action_meta(action)
    if not meta:
        raise ValueError(f"unsupported cli action: {action}")

    handler = meta.fn
    if handler is None or not callable(handler):
        raise ValueError(f"invalid action handler for action: {action}")

    return handler


def has_cli_handler(action: str) -> bool:
    return get_action_meta(action) is not None


def run_cli_action(action: str, params: Dict[str, Any]) -> Any:
    handler = _load_handler(action)
    result = handler(**params)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result
