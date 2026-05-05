import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


CONFIG_ENV = "AUTO_SRE_RUNTIME_PROFILE_PATH"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "runtime_profile.toml"


@dataclass(frozen=True)
class AppProfile:
    service_name: str
    container_name: str
    host_base_url: str
    health_endpoint: str
    prometheus_endpoint: str
    log_dir: Path
    log_patterns: List[str]


@dataclass(frozen=True)
class MysqlProfile:
    service_name: str
    container_name: str
    host: str
    host_port: int
    container_host: str
    container_port: int


@dataclass(frozen=True)
class PrometheusProfile:
    base_url: str
    job: str


@dataclass(frozen=True)
class AlertmanagerProfile:
    base_url: str
    webhook_url: str


@dataclass(frozen=True)
class RuntimeProfile:
    config_path: Path
    project: str
    timezone: str
    compose_project_dir: Path
    app: AppProfile
    mysql: MysqlProfile
    prometheus: PrometheusProfile
    alertmanager: AlertmanagerProfile


def _config_path() -> Path:
    raw = os.getenv(CONFIG_ENV)
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_CONFIG_PATH


def _load_config() -> tuple[Path, Dict[str, Any]]:
    path = _config_path()
    if not path.exists():
        raise RuntimeError(
            f"runtime profile config not found: {path}. "
            f"Set {CONFIG_ENV} or create config/runtime_profile.toml."
        )
    with path.open("rb") as fp:
        data = tomllib.load(fp)
    if not isinstance(data, dict):
        raise RuntimeError(f"runtime profile config must be a TOML object: {path}")
    return path, data


def _section(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    value = data.get(name)
    if not isinstance(value, dict):
        raise RuntimeError(f"runtime profile missing section [{name}]")
    return value


def _str(section: Dict[str, Any], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"runtime profile field `{key}` must be a non-empty string")
    return value.strip()


def _int(section: Dict[str, Any], key: str) -> int:
    raw = section.get(key)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"runtime profile field `{key}` must be an integer") from exc


def _str_list(section: Dict[str, Any], key: str) -> List[str]:
    raw = section.get(key)
    if not isinstance(raw, list):
        raise RuntimeError(f"runtime profile field `{key}` must be a string list")
    values = [str(item).strip() for item in raw if str(item).strip()]
    if not values:
        raise RuntimeError(f"runtime profile field `{key}` must not be empty")
    return values


def _resolve_path(raw: str, base: Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (base / path).resolve()


def get_runtime_profile() -> RuntimeProfile:
    config_path, raw = _load_config()
    runtime = _section(raw, "runtime")
    app = _section(raw, "app")
    mysql = _section(raw, "mysql")
    prometheus = _section(raw, "prometheus")
    alertmanager = _section(raw, "alertmanager")

    config_dir = config_path.parent
    project_dir = _resolve_path(_str(runtime, "compose_project_dir"), config_dir)
    log_dir = _resolve_path(_str(app, "log_dir"), project_dir)

    return RuntimeProfile(
        config_path=config_path,
        project=_str(runtime, "project"),
        timezone=_str(runtime, "timezone"),
        compose_project_dir=project_dir,
        app=AppProfile(
            service_name=_str(app, "service_name"),
            container_name=_str(app, "container_name"),
            host_base_url=_str(app, "host_base_url"),
            health_endpoint=_str(app, "health_endpoint"),
            prometheus_endpoint=_str(app, "prometheus_endpoint"),
            log_dir=log_dir,
            log_patterns=_str_list(app, "log_patterns"),
        ),
        mysql=MysqlProfile(
            service_name=_str(mysql, "service_name"),
            container_name=_str(mysql, "container_name"),
            host=_str(mysql, "host"),
            host_port=_int(mysql, "host_port"),
            container_host=_str(mysql, "container_host"),
            container_port=_int(mysql, "container_port"),
        ),
        prometheus=PrometheusProfile(
            base_url=_str(prometheus, "base_url"),
            job=_str(prometheus, "job"),
        ),
        alertmanager=AlertmanagerProfile(
            base_url=_str(alertmanager, "base_url"),
            webhook_url=_str(alertmanager, "webhook_url"),
        ),
    )


def profile_summary() -> Dict[str, object]:
    profile = get_runtime_profile()
    return {
        "config_path": str(profile.config_path),
        "project": profile.project,
        "timezone": profile.timezone,
        "compose_project_dir": str(profile.compose_project_dir),
        "app": {
            "service_name": profile.app.service_name,
            "container_name": profile.app.container_name,
            "host_base_url": profile.app.host_base_url,
            "health_endpoint": profile.app.health_endpoint,
            "prometheus_endpoint": profile.app.prometheus_endpoint,
            "log_dir": str(profile.app.log_dir),
            "log_patterns": profile.app.log_patterns,
        },
        "mysql": {
            "service_name": profile.mysql.service_name,
            "container_name": profile.mysql.container_name,
            "host": profile.mysql.host,
            "host_port": profile.mysql.host_port,
            "container_host": profile.mysql.container_host,
            "container_port": profile.mysql.container_port,
        },
        "prometheus": {
            "base_url": profile.prometheus.base_url,
            "job": profile.prometheus.job,
        },
        "alertmanager": {
            "base_url": profile.alertmanager.base_url,
            "webhook_url": profile.alertmanager.webhook_url,
        },
    }
