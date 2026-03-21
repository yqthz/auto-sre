"""
Prometheus 查询服务
用于查询容器资源指标（CPU、内存、网络）
"""
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import aiohttp
import redis.asyncio as redis
from app.core.config import settings


class PrometheusService:
    """Prometheus 查询服务"""

    def __init__(self):
        self.prometheus_url = settings.PROMETHEUS_URL
        self.redis_client: Optional[redis.Redis] = None

    async def _get_redis_client(self) -> redis.Redis:
        """获取 Redis 客户端"""
        if self.redis_client is None:
            self.redis_client = await redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True
            )
        return self.redis_client

    async def _query_prometheus(
        self,
        query: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        step: str = "1m"
    ) -> Dict:
        """
        查询 Prometheus

        Args:
            query: PromQL 查询语句
            start: 开始时间（ISO 8601 格式）
            end: 结束时间（ISO 8601 格式）
            step: 数据点间隔

        Returns:
            查询结果
        """
        async with aiohttp.ClientSession() as session:
            if start and end:
                # 范围查询
                url = f"{self.prometheus_url}/api/v1/query_range"
                params = {
                    "query": query,
                    "start": start,
                    "end": end,
                    "step": step
                }
            else:
                # 即时查询
                url = f"{self.prometheus_url}/api/v1/query"
                params = {"query": query}

            async with session.get(url, params=params) as response:
                if response.status != 200:
                    raise Exception(f"Prometheus query failed: {response.status}")
                return await response.json()

    async def _get_cached_data(self, cache_key: str) -> Optional[Dict]:
        """从缓存获取数据"""
        try:
            redis_client = await self._get_redis_client()
            cached = await redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            print(f"Redis get error: {e}")
        return None

    async def _set_cached_data(self, cache_key: str, data: Dict, ttl: int = 30):
        """设置缓存数据"""
        try:
            redis_client = await self._get_redis_client()
            await redis_client.setex(cache_key, ttl, json.dumps(data))
        except Exception as e:
            print(f"Redis set error: {e}")

    def _parse_time_range(self, time_range: str) -> tuple[str, str]:
        """
        解析时间范围

        Args:
            time_range: 1h, 6h, 24h, 7d

        Returns:
            (start_time, end_time) ISO 8601 格式
        """
        end_time = datetime.utcnow()

        if time_range == "1h":
            start_time = end_time - timedelta(hours=1)
        elif time_range == "6h":
            start_time = end_time - timedelta(hours=6)
        elif time_range == "24h":
            start_time = end_time - timedelta(hours=24)
        elif time_range == "7d":
            start_time = end_time - timedelta(days=7)
        else:
            start_time = end_time - timedelta(hours=1)

        return start_time.isoformat() + "Z", end_time.isoformat() + "Z"

    async def get_container_cpu_metrics(
        self,
        time_range: str = "1h",
        step: str = "1m",
        container: Optional[str] = None
    ) -> Dict:
        """
        获取容器 CPU 使用率指标

        Args:
            time_range: 时间范围（1h, 6h, 24h, 7d）
            step: 数据点间隔
            container: 容器名称过滤（可选）

        Returns:
            容器 CPU 指标数据
        """
        cache_key = f"monitoring:containers:cpu:{time_range}:{step}:{container or 'all'}"

        # 尝试从缓存获取
        cached_data = await self._get_cached_data(cache_key)
        if cached_data:
            return cached_data

        # 构建 PromQL 查询
        if container:
            query = f'rate(container_cpu_usage_seconds_total{{name="{container}"}}[5m]) * 100'
        else:
            query = 'rate(container_cpu_usage_seconds_total{name!=""}[5m]) * 100'

        start, end = self._parse_time_range(time_range)
        result = await self._query_prometheus(query, start, end, step)

        # 缓存结果
        await self._set_cached_data(cache_key, result, ttl=30)

        return result

    async def get_container_memory_metrics(
        self,
        time_range: str = "1h",
        step: str = "1m",
        container: Optional[str] = None
    ) -> Dict:
        """
        获取容器内存使用指标

        Args:
            time_range: 时间范围
            step: 数据点间隔
            container: 容器名称过滤

        Returns:
            容器内存指标数据
        """
        cache_key = f"monitoring:containers:memory:{time_range}:{step}:{container or 'all'}"

        cached_data = await self._get_cached_data(cache_key)
        if cached_data:
            return cached_data

        if container:
            query = f'container_memory_usage_bytes{{name="{container}"}}'
        else:
            query = 'container_memory_usage_bytes{name!=""}'

        start, end = self._parse_time_range(time_range)
        result = await self._query_prometheus(query, start, end, step)

        await self._set_cached_data(cache_key, result, ttl=30)

        return result

    async def get_container_memory_percent_metrics(
        self,
        time_range: str = "1h",
        step: str = "1m",
        container: Optional[str] = None
    ) -> Dict:
        """
        获取容器内存使用率指标

        Args:
            time_range: 时间范围
            step: 数据点间隔
            container: 容器名称过滤

        Returns:
            容器内存使用率数据
        """
        cache_key = f"monitoring:containers:memory_percent:{time_range}:{step}:{container or 'all'}"

        cached_data = await self._get_cached_data(cache_key)
        if cached_data:
            return cached_data

        if container:
            query = f'container_memory_usage_bytes{{name="{container}"}} / container_spec_memory_limit_bytes{{name="{container}"}} * 100'
        else:
            query = 'container_memory_usage_bytes{name!=""} / container_spec_memory_limit_bytes{name!=""} * 100'

        start, end = self._parse_time_range(time_range)
        result = await self._query_prometheus(query, start, end, step)

        await self._set_cached_data(cache_key, result, ttl=30)

        return result

    async def get_container_network_metrics(
        self,
        time_range: str = "1h",
        step: str = "1m",
        container: Optional[str] = None
    ) -> Dict:
        """
        获取容器网络流量指标

        Args:
            time_range: 时间范围
            step: 数据点间隔
            container: 容器名称过滤

        Returns:
            容器网络流量数据（包含入站和出站）
        """
        cache_key = f"monitoring:containers:network:{time_range}:{step}:{container or 'all'}"

        cached_data = await self._get_cached_data(cache_key)
        if cached_data:
            return cached_data

        # 查询入站流量
        if container:
            query_in = f'rate(container_network_receive_bytes_total{{name="{container}"}}[5m])'
            query_out = f'rate(container_network_transmit_bytes_total{{name="{container}"}}[5m])'
        else:
            query_in = 'rate(container_network_receive_bytes_total{name!=""}[5m])'
            query_out = 'rate(container_network_transmit_bytes_total{name!=""}[5m])'

        start, end = self._parse_time_range(time_range)

        result_in = await self._query_prometheus(query_in, start, end, step)
        result_out = await self._query_prometheus(query_out, start, end, step)

        result = {
            "network_in": result_in,
            "network_out": result_out
        }

        await self._set_cached_data(cache_key, result, ttl=30)

        return result

    async def get_current_container_metrics(self, container: Optional[str] = None) -> Dict:
        """
        获取当前容器资源指标（即时查询）

        Args:
            container: 容器名称过滤

        Returns:
            当前容器资源指标
        """
        cache_key = f"monitoring:containers:current:{container or 'all'}"

        cached_data = await self._get_cached_data(cache_key)
        if cached_data:
            return cached_data

        # 构建查询
        if container:
            cpu_query = f'rate(container_cpu_usage_seconds_total{{name="{container}"}}[5m]) * 100'
            memory_query = f'container_memory_usage_bytes{{name="{container}"}}'
            memory_percent_query = f'container_memory_usage_bytes{{name="{container}"}} / container_spec_memory_limit_bytes{{name="{container}"}} * 100'
            network_in_query = f'rate(container_network_receive_bytes_total{{name="{container}"}}[5m])'
            network_out_query = f'rate(container_network_transmit_bytes_total{{name="{container}"}}[5m])'
        else:
            cpu_query = 'rate(container_cpu_usage_seconds_total{name!=""}[5m]) * 100'
            memory_query = 'container_memory_usage_bytes{name!=""}'
            memory_percent_query = 'container_memory_usage_bytes{name!=""} / container_spec_memory_limit_bytes{name!=""} * 100'
            network_in_query = 'rate(container_network_receive_bytes_total{name!=""}[5m])'
            network_out_query = 'rate(container_network_transmit_bytes_total{name!=""}[5m])'

        # 并发查询
        cpu_result = await self._query_prometheus(cpu_query)
        memory_result = await self._query_prometheus(memory_query)
        memory_percent_result = await self._query_prometheus(memory_percent_query)
        network_in_result = await self._query_prometheus(network_in_query)
        network_out_result = await self._query_prometheus(network_out_query)

        result = {
            "cpu": cpu_result,
            "memory": memory_result,
            "memory_percent": memory_percent_result,
            "network_in": network_in_result,
            "network_out": network_out_result
        }

        await self._set_cached_data(cache_key, result, ttl=30)

        return result

    async def close(self):
        """关闭连接"""
        if self.redis_client:
            await self.redis_client.close()


# 全局实例
prometheus_service = PrometheusService()
