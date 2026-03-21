"""
监控服务
处理 Prometheus 数据并转换为前端所需格式
"""
from datetime import datetime
from typing import List, Optional
from app.service.prometheus_service import prometheus_service
from app.schema.monitoring import (
    ContainerCurrentMetrics,
    ContainerHistoryMetrics,
    ContainerMetricsResponse,
    MonitoringOverviewResponse
)


class MonitoringService:
    """监控服务"""

    def _parse_prometheus_result(self, result: dict) -> List[dict]:
        """
        解析 Prometheus 查询结果

        Args:
            result: Prometheus API 返回的结果

        Returns:
            解析后的数据点列表 [{"timestamp": "...", "value": ..., "container_name": "..."}, ...]
        """
        if result.get("status") != "success":
            return []

        data = result.get("data", {})
        result_type = data.get("resultType")

        if result_type == "matrix":
            # 范围查询结果
            parsed_data = []
            for series in data.get("result", []):
                container_name = series.get("metric", {}).get("name", "unknown")
                for value in series.get("values", []):
                    timestamp, val = value
                    parsed_data.append({
                        "timestamp": datetime.fromtimestamp(timestamp).isoformat() + "Z",
                        "value": float(val),
                        "container_name": container_name
                    })
            return parsed_data

        elif result_type == "vector":
            # 即时查询结果
            parsed_data = []
            for series in data.get("result", []):
                container_name = series.get("metric", {}).get("name", "unknown")
                value = series.get("value", [])
                if len(value) == 2:
                    timestamp, val = value
                    parsed_data.append({
                        "timestamp": datetime.fromtimestamp(timestamp).isoformat() + "Z",
                        "value": float(val),
                        "container_name": container_name
                    })
            return parsed_data

        return []

    def _group_by_container(self, data: List[dict]) -> dict:
        """
        按容器名称分组数据

        Args:
            data: 数据点列表

        Returns:
            按容器名称分组的字典 {"container_name": [data_points]}
        """
        grouped = {}
        for point in data:
            container_name = point["container_name"]
            if container_name not in grouped:
                grouped[container_name] = []
            grouped[container_name].append({
                "timestamp": point["timestamp"],
                "value": point["value"]
            })
        return grouped

    async def get_container_metrics(
        self,
        time_range: str = "1h",
        step: str = "1m",
        container: Optional[str] = None
    ) -> ContainerMetricsResponse:
        """
        获取容器资源指标

        Args:
            time_range: 时间范围（1h, 6h, 24h, 7d）
            step: 数据点间隔
            container: 容器名称过滤

        Returns:
            容器资源指标响应
        """
        # 获取当前指标
        current_data = await prometheus_service.get_current_container_metrics(container)

        # 解析当前指标
        cpu_current = self._parse_prometheus_result(current_data["cpu"])
        memory_current = self._parse_prometheus_result(current_data["memory"])
        memory_percent_current = self._parse_prometheus_result(current_data["memory_percent"])
        network_in_current = self._parse_prometheus_result(current_data["network_in"])
        network_out_current = self._parse_prometheus_result(current_data["network_out"])

        # 构建当前指标列表
        current_metrics = []
        container_names = set()

        for point in cpu_current:
            container_names.add(point["container_name"])

        for container_name in container_names:
            cpu_val = next((p["value"] for p in cpu_current if p["container_name"] == container_name), None)
            memory_val = next((p["value"] for p in memory_current if p["container_name"] == container_name), None)
            memory_percent_val = next((p["value"] for p in memory_percent_current if p["container_name"] == container_name), None)
            network_in_val = next((p["value"] for p in network_in_current if p["container_name"] == container_name), None)
            network_out_val = next((p["value"] for p in network_out_current if p["container_name"] == container_name), None)

            current_metrics.append(ContainerCurrentMetrics(
                container_name=container_name,
                cpu_percent=cpu_val,
                memory_bytes=int(memory_val) if memory_val else None,
                memory_percent=memory_percent_val,
                network_in_bps=network_in_val,
                network_out_bps=network_out_val
            ))

        # 获取历史指标
        cpu_history = await prometheus_service.get_container_cpu_metrics(time_range, step, container)
        memory_history = await prometheus_service.get_container_memory_metrics(time_range, step, container)
        memory_percent_history = await prometheus_service.get_container_memory_percent_metrics(time_range, step, container)
        network_history = await prometheus_service.get_container_network_metrics(time_range, step, container)

        # 解析历史指标
        cpu_data = self._parse_prometheus_result(cpu_history)
        memory_data = self._parse_prometheus_result(memory_history)
        memory_percent_data = self._parse_prometheus_result(memory_percent_history)
        network_in_data = self._parse_prometheus_result(network_history["network_in"])
        network_out_data = self._parse_prometheus_result(network_history["network_out"])

        # 按容器分组
        cpu_grouped = self._group_by_container(cpu_data)
        memory_grouped = self._group_by_container(memory_data)
        memory_percent_grouped = self._group_by_container(memory_percent_data)
        network_in_grouped = self._group_by_container(network_in_data)
        network_out_grouped = self._group_by_container(network_out_data)

        # 构建历史指标列表
        history_metrics = []
        all_containers = set(cpu_grouped.keys()) | set(memory_grouped.keys())

        for container_name in all_containers:
            history_metrics.append(ContainerHistoryMetrics(
                container_name=container_name,
                cpu_data=cpu_grouped.get(container_name, []),
                memory_data=memory_grouped.get(container_name, []),
                memory_percent_data=memory_percent_grouped.get(container_name, []),
                network_in_data=network_in_grouped.get(container_name, []),
                network_out_data=network_out_grouped.get(container_name, [])
            ))

        return ContainerMetricsResponse(
            time_range=time_range,
            step=step,
            current=current_metrics,
            history=history_metrics
        )

    async def get_monitoring_overview(self) -> MonitoringOverviewResponse:
        """
        获取监控概览

        Returns:
            监控概览响应
        """
        # 获取当前容器指标
        current_data = await prometheus_service.get_current_container_metrics()

        # 解析当前指标
        cpu_current = self._parse_prometheus_result(current_data["cpu"])
        memory_current = self._parse_prometheus_result(current_data["memory"])
        memory_percent_current = self._parse_prometheus_result(current_data["memory_percent"])
        network_in_current = self._parse_prometheus_result(current_data["network_in"])
        network_out_current = self._parse_prometheus_result(current_data["network_out"])

        # 构建容器指标列表
        container_names = set()
        for point in cpu_current:
            container_names.add(point["container_name"])

        containers = []
        health_status = "healthy"

        for container_name in container_names:
            cpu_val = next((p["value"] for p in cpu_current if p["container_name"] == container_name), None)
            memory_val = next((p["value"] for p in memory_current if p["container_name"] == container_name), None)
            memory_percent_val = next((p["value"] for p in memory_percent_current if p["container_name"] == container_name), None)
            network_in_val = next((p["value"] for p in network_in_current if p["container_name"] == container_name), None)
            network_out_val = next((p["value"] for p in network_out_current if p["container_name"] == container_name), None)

            containers.append(ContainerCurrentMetrics(
                container_name=container_name,
                cpu_percent=cpu_val,
                memory_bytes=int(memory_val) if memory_val else None,
                memory_percent=memory_percent_val,
                network_in_bps=network_in_val,
                network_out_bps=network_out_val
            ))

            # 判断健康状态
            if cpu_val and cpu_val > 85:
                health_status = "critical"
            elif cpu_val and cpu_val > 70:
                if health_status == "healthy":
                    health_status = "warning"

            if memory_percent_val and memory_percent_val > 90:
                health_status = "critical"
            elif memory_percent_val and memory_percent_val > 80:
                if health_status == "healthy":
                    health_status = "warning"

        return MonitoringOverviewResponse(
            containers=containers,
            health_status=health_status,
            timestamp=datetime.utcnow().isoformat() + "Z"
        )


# 全局实例
monitoring_service = MonitoringService()
