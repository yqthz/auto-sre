import importlib
from typing import Dict

_META_LOADED = False
_ALL_LOADED = False
LOAD_ERRORS: Dict[str, str] = {}

_META_TOOL_MODULES = [
    "app.agent.tools.dispatcher_meta_tools",
]

_TOOL_MODULES = [
    "app.agent.tools.actuator_tools",
    "app.agent.tools.docker_tools",
    "app.agent.tools.log_analysis_tools",
    "app.agent.tools.network_tools",
    "app.agent.tools.profile_tools",
    "app.agent.tools.prometheus_tools",
    "app.agent.tools.rag_tools",
]


def ensure_dispatcher_meta_tools_loaded() -> None:
    global _META_LOADED
    if _META_LOADED:
        return

    for module_name in _META_TOOL_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as e:
            LOAD_ERRORS[module_name] = str(e)

    _META_LOADED = True


def ensure_tool_modules_loaded() -> None:
    global _ALL_LOADED
    if _ALL_LOADED:
        return

    ensure_dispatcher_meta_tools_loaded()

    for module_name in _TOOL_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as e:
            # Keep incremental loading resilient: one tool import failure
            # should not make dispatcher unavailable.
            LOAD_ERRORS[module_name] = str(e)

    _ALL_LOADED = True
