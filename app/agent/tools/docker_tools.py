import json
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.agent.runtime_profile import get_runtime_profile
from app.agent.tools.security import register_tool
from app.core.logger import logger

MAX_LOG_CHARS = 12000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_docker(args: List[str], project_dir: str, timeout: int = 8) -> Dict[str, Any]:
    """运行 docker 命令"""
    try:

        logger.info(f"exec docker {args}, project_dir: {project_dir}")

        proc = subprocess.run(
            ["docker", *args],
            cwd=project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
            check=False,
        )
    except Exception as e:
        return {
            "ok": False,
            "error_type": type(e).__name__,
            "error": str(e),
            "stdout": "",
            "stderr": "",
            "returncode": None,
        }

    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "error": "" if proc.returncode == 0 else (proc.stderr or proc.stdout or "docker command failed"),
    }


def _json_result(ok: bool, **extra: Any) -> str:
    payload = {
        "queried_at": _now(),
        "ok": ok,
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _parse_ps_json_lines(stdout: str) -> List[Dict[str, Any]]:
    stripped = stdout.strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
            if isinstance(parsed, dict):
                return [parsed]
        except Exception:
            pass

    services: List[Dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            services.append(item)
    return services


@register_tool(
    name="docker_compose_ps",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["docker"],
    description="List Compose services and container states for the current project.",
)
def docker_compose_ps(project_dir: str = "") -> str:
    """
    只读查询 `docker compose ps`，返回编排项目中服务与容器的运行状态。

    功能解释:
    - 优先使用 `docker compose ps --format json` 获取结构化结果。
    - 若当前 Docker 版本不支持 JSON 格式，则回退到普通 `docker compose ps`。
    - 返回服务列表、原始输出、错误信息和退出码，便于 agent 继续判断。

    使用场景:
    - 快速确认编排服务是否已启动、是否重启、是否退出。
    - 排查本地 Compose 环境中容器状态异常。
    - 在查看日志前先建立“服务是否在跑”的基本判断。

    参数说明:
    - `project_dir` (str，可选，默认 `""`)：
      - Compose 项目目录。
      - 为空时自动使用运行时配置中的 `profile.compose_project_dir`。

    必填字段:
    - 无。

    调用方法:
    - 直接调用：`docker_compose_ps()`
    - 指定项目目录：`docker_compose_ps(project_dir="/opt/app")`

    返回关键字段:
    - `queried_at`：查询时间。
    - `ok`：是否成功。
    - `project_dir`：实际使用的项目目录。
    - `services`：服务状态列表。
    - `raw_stdout`：原始命令输出（会截断）。
    - 失败时附带 `stderr`、`error`、`returncode`。
    """
    profile = get_runtime_profile()
    selected_project_dir = project_dir or str(profile.compose_project_dir)

    result = _run_docker(["compose", "ps", "--format", "json"], selected_project_dir, timeout=8)

    logger.info(f"exec docker compose ps result: {result}")

    if result.get("ok"):
        rows = _parse_ps_json_lines(str(result.get("stdout") or ""))
        return _json_result(
            True,
            project_dir=selected_project_dir,
            services=rows,
            raw_stdout=str(result.get("stdout") or "")[:MAX_LOG_CHARS],
        )

    fallback = _run_docker(["compose", "ps"], selected_project_dir, timeout=8)
    return _json_result(
        bool(fallback.get("ok")),
        project_dir=selected_project_dir,
        services=[],
        raw_stdout=str(fallback.get("stdout") or "")[:MAX_LOG_CHARS],
        stderr=str(fallback.get("stderr") or result.get("stderr") or "")[:4000],
        error=str(fallback.get("error") or result.get("error") or ""),
        returncode=fallback.get("returncode"),
    )


@register_tool(
    name="docker_inspect_container",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["docker"],
    description="Inspect one container and return a status summary.",
)
def docker_inspect_container(container_name: str = "") -> str:
    """
    只读执行 `docker inspect`，返回单个容器的状态摘要。

    功能解释:
    - 汇总容器运行状态、健康状态、端口映射、挂载信息和重启次数。
    - 若未传入容器名，默认使用运行时配置里的主应用容器名。
    - 适合快速判断容器“是否在跑、是否健康、是否暴露了正确端口”。

    使用场景:
    - 查看某个容器是否正在运行、是否健康、是否存在异常退出或重启。
    - 排查端口映射、挂载路径、健康检查状态是否符合预期。
    - 对单个依赖容器做定点诊断。

    参数说明:
    - `container_name` (str，可选，默认 `""`)：
      - 目标容器名。
      - 为空时使用 `profile.app.container_name`。

    必填字段:
    - 无。

    调用方法:
    - 直接调用：`docker_inspect_container()`
    - 指定容器：`docker_inspect_container(container_name="mysql")`

    返回关键字段:
    - `container_name`：检查的容器名。
    - `state`：容器状态摘要（`status` / `running` / `restarting` / `exit_code` / `health` 等）。
    - `restart_count`：重启次数。
    - `ports`：端口映射。
    - `binds`：挂载信息。
    - 失败时附带 `error`、`stderr`、`returncode`。
    """
    profile = get_runtime_profile()
    selected_container = container_name or profile.app.container_name
    selected_project_dir = str(profile.compose_project_dir)

    result = _run_docker(["inspect", selected_container], selected_project_dir, timeout=8)

    logger.info(f"exec docker inspect result: {result}")

    if not result.get("ok"):
        return _json_result(
            False,
            container_name=selected_container,
            error=str(result.get("error") or ""),
            stderr=str(result.get("stderr") or "")[:4000],
            returncode=result.get("returncode"),
        )

    try:
        parsed = json.loads(str(result.get("stdout") or "[]"))
    except Exception as e:
        return _json_result(False, container_name=selected_container, error_type="InvalidJson", error=str(e))

    item = parsed[0] if isinstance(parsed, list) and parsed else {}
    # 获取运行状态
    state = item.get("State") if isinstance(item, dict) else {}
    # 获取端口映射
    network_settings = item.get("NetworkSettings") if isinstance(item, dict) else {}
    # 获取挂载配置
    host_config = item.get("HostConfig") if isinstance(item, dict) else {}

    # 获取健康检查状态
    health = None
    if isinstance(state, dict):
        health_obj = state.get("Health")
        if isinstance(health_obj, dict):
            health = health_obj.get("Status")

    return _json_result(
        True,
        container_name=selected_container,
        state={
            "status": state.get("Status") if isinstance(state, dict) else None,
            "running": state.get("Running") if isinstance(state, dict) else None,
            "restarting": state.get("Restarting") if isinstance(state, dict) else None,
            "exit_code": state.get("ExitCode") if isinstance(state, dict) else None,
            "health": health,
            "started_at": state.get("StartedAt") if isinstance(state, dict) else None,
            "finished_at": state.get("FinishedAt") if isinstance(state, dict) else None,
        },
        restart_count=item.get("RestartCount") if isinstance(item, dict) else None,
        ports=network_settings.get("Ports") if isinstance(network_settings, dict) else {},
        binds=host_config.get("Binds") if isinstance(host_config, dict) else [],
    )


@register_tool(
    name="docker_compose_logs",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["docker"],
    description="Read recent logs for one Compose service.",
)
def docker_compose_logs(service: str = "", tail: int = 200, project_dir: str = "") -> str:
    """
    只读读取某个 Compose service 的最近日志。

    功能解释:
    - 读取指定服务的最近 `tail` 行日志。
    - 自动提取包含 `ERROR` / `WARN` 的行，便于快速定位异常。
    - 对日志做长度截断，避免输出过大。

    使用场景:
    - 故障排查时查看目标服务最近报错。
    - 结合容器状态与健康检查定位启动失败原因。
    - 快速筛选日志中是否出现错误告警。

    参数说明:
    - `service` (str，可选，默认 `""`)：
      - Compose 服务名。
      - 为空时使用运行时配置中的默认服务名。
    - `tail` (int，可选，默认 `200`)：
      - 读取尾部日志行数。
      - 实际会被限制在 `10 ~ 1000`。
    - `project_dir` (str，可选，默认 `""`)：
      - Compose 项目目录。

    必填字段:
    - 无。

    调用方法:
    - 直接调用：`docker_compose_logs()`
    - 指定服务：`docker_compose_logs(service="app", tail=300)`

    返回关键字段:
    - `project_dir`、`service`、`tail`、`line_count`。
    - `recent_error_lines`：最近的错误/告警行。
    - `logs`：日志正文截断结果。
    - 失败时附带 `stderr`、`error`、`returncode`。
    """
    profile = get_runtime_profile()
    selected_project_dir = project_dir or str(profile.compose_project_dir)
    selected_service = service or profile.app.service_name

    tail = max(10, min(int(tail), 1000))
    result = _run_docker(
        ["compose", "logs", "--tail", str(tail), selected_service],
        selected_project_dir,
        timeout=12,
    )

    logger.info(f"exec docker compose log result: {result}")

    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    lines = stdout.splitlines()
    error_lines = [line for line in lines if "ERROR" in line.upper() or "WARN" in line.upper()]
    return _json_result(
        bool(result.get("ok")),
        project_dir=selected_project_dir,
        service=selected_service,
        tail=tail,
        line_count=len(lines),
        recent_error_lines=error_lines[-30:],
        logs=stdout[-MAX_LOG_CHARS:],
        stderr=stderr[-4000:],
        error=str(result.get("error") or ""),
        returncode=result.get("returncode"),
    )


@register_tool(
    name="docker_service_status_summary",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["docker"],
    description="Summarize the health of key Docker Compose services.",
)
def docker_service_status_summary(project_dir: str = "") -> str:
    """
    汇总编排环境的主要服务状态，属于高层只读摘要工具。

    功能解释:
    - 组合执行 `docker compose ps` 与若干 `docker inspect`。
    - 返回主应用、数据库以及 Prometheus/Grafana 等关键容器的状态概览。
    - 适合作为“环境总览”入口，而不是单容器细查入口。

    使用场景:
    - 一键查看整套本地编排环境的总体健康情况。
    - 故障定位时先拿到“全局图”，再下钻到单容器和日志。
    - 适合 agent 先判断环境是否整体异常。

    参数说明:
    - `project_dir` (str，可选，默认 `""`)：
      - Compose 项目目录。
      - 为空时使用运行时默认目录。

    必填字段:
    - 无。

    调用方法:
    - 直接调用：`docker_service_status_summary()`
    - 指定项目目录：`docker_service_status_summary(project_dir="/opt/app")`

    返回关键字段:
    - `project_dir`：实际使用的项目目录。
    - `compose`：`docker compose ps` 的结构化结果。
    - `containers`：关键容器的 inspect 摘要列表。
    """
    profile = get_runtime_profile()
    selected_project_dir = project_dir or str(profile.compose_project_dir)

    ps_payload = json.loads(docker_compose_ps(selected_project_dir))

    containers = [
        profile.app.container_name,
        profile.mysql.container_name,
        "prometheus",
        "alertmanager",
        "grafana",
    ]
    inspect_summaries = []
    for container in containers:
        inspect_summaries.append(json.loads(docker_inspect_container(container)))

    logger.info(f"exec docker service status summary, payload: {ps_payload}, inspect_summaries: {inspect_summaries}")

    return _json_result(
        bool(ps_payload.get("ok")),
        project_dir=selected_project_dir,
        compose=ps_payload,
        containers=inspect_summaries,
    )


@register_tool(
    name="docker_restart_container",
    permission="danger",
    roles=["admin", "sre"],
    tags=["docker"],
    requires_approval=True,
    description="Restart a docker container (high risk, approval required).",
)
def docker_restart_container(container_name: str = "", project_dir: str = "", timeout: int = 20) -> str:
    """
    重启指定 Docker 容器，属于高危写操作，必须走审批流程。

    功能解释:
    - 执行 `docker restart` 重启目标容器。
    - 直接影响线上进程，可能造成短暂中断或连接重建。
    - 该工具被标记为 `danger`，并显式要求审批。

    使用场景:
    - 容器卡死、健康检查异常或需要快速恢复时。
    - 诊断确认后，执行有明确回滚预期的容器重启。

    参数说明:
    - `container_name` (str, optional, default `""`):
      - 目标容器名。
      - 为空时使用运行时配置中的主应用容器名。
    - `project_dir` (str, optional, default `""`):
      - Docker Compose 项目目录。
      - 为空时使用运行时配置中的 `profile.compose_project_dir`。
    - `timeout` (int, optional, default `20`):
      - 命令超时时间，限制在 `5 ~ 120` 秒。

    返回字段:
    - `queried_at`: 执行时间。
    - `ok`: 是否成功。
    - `container_name`: 实际重启的容器名。
    - `project_dir`: 实际使用的项目目录。
    - `stdout` / `stderr` / `error` / `returncode`: 命令输出与失败信息。
    """
    profile = get_runtime_profile()
    selected_container = (container_name or profile.app.container_name).strip()
    selected_project_dir = project_dir or str(profile.compose_project_dir)
    selected_timeout = max(5, min(int(timeout), 120))

    result = _run_docker(["restart", selected_container], selected_project_dir, timeout=selected_timeout)

    logger.info(f"exec docker restart container resule: {result}")

    return _json_result(
        bool(result.get("ok")),
        project_dir=selected_project_dir,
        container_name=selected_container,
        stdout=str(result.get("stdout") or "")[:MAX_LOG_CHARS],
        stderr=str(result.get("stderr") or "")[:4000],
        error=str(result.get("error") or ""),
        returncode=result.get("returncode"),
    )
