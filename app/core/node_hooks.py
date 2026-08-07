"""
节点 Hook 工具：为 LangGraph 节点统一提供「进入/退出/异常」的相呼应日志、
耗时统计、任务状态更新与可配置重试策略。

设计思路：
- 通过装饰器包装节点函数，节点内部逻辑零侵入；
- 日志采用「>>> 开始 / <<< 完成(耗时) / !!! 异常」三段式，便于按节点名 grep 整条链路；
- 重试直接复用 LangGraph 原生 RetryPolicy（指数退避 + 抖动），不在业务代码里手写重试循环。
"""
import functools
import time
from typing import Any, Callable, Dict, Optional, Sequence, Type, TypeVar

from langgraph.types import RetryPolicy

from app.core.logger import logger
from app.utils.task_utils import add_done_task, add_running_task, update_task_status, TASK_STATUS_FAILED

# 节点函数泛型：state 的 TypedDict 子类型
StateT = TypeVar("StateT", bound=Dict[str, Any])

# -----------------------------
# 节点耗时指标（进程内，供日志与后续指标上报）
# key: node_name, value: {"count": n, "total_ms": float}
# -----------------------------
_NODE_METRICS: Dict[str, Dict[str, float]] = {}


def get_node_metrics() -> Dict[str, Dict[str, float]]:
    """获取节点耗时统计（只读快照），便于接入 Prometheus / 监控面板。"""
    return {name: dict(meta) for name, meta in _NODE_METRICS.items()}


def _record_metric(node_name: str, elapsed_ms: float) -> None:
    meta = _NODE_METRICS.setdefault(node_name, {"count": 0.0, "total_ms": 0.0})
    meta["count"] += 1
    meta["total_ms"] += elapsed_ms


def node_hook(
    node_fn: Callable[..., Any],
) -> Callable[..., Any]:
    """
    节点 Hook 装饰器：包装 LangGraph 节点函数。

    在节点执行前/后/异常时分别输出「相呼应」的结构化日志，并同步任务状态：

        >>> [task_id] node_xxx 开始执行
        <<< [task_id] node_xxx 执行完成，耗时 1234ms
        !!! [task_id] node_xxx 执行失败：{error}

    同时更新 task_utils 中的 running/done 列表（供前端轮询）与耗时指标。

    :param node_fn: LangGraph 节点函数，入参 state dict，出参 dict（局部更新）
    :return: 包装后的节点函数
    """
    node_name = node_fn.__name__

    @functools.wraps(node_fn)
    def wrapper(state: StateT) -> StateT:
        task_id = state.get("task_id") or state.get("session_id") or "unknown"
        is_stream = bool(state.get("is_stream"))

        add_running_task(task_id, node_name, is_stream)
        logger.info(f">>> [{task_id}] {node_name} 开始执行")
        start = time.perf_counter()

        try:
            result = node_fn(state)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            _record_metric(node_name, elapsed_ms)
            update_task_status(task_id, TASK_STATUS_FAILED, push_queue=is_stream)
            logger.exception(f"!!! [{task_id}] {node_name} 执行失败，耗时 {elapsed_ms:.0f}ms：{e}")
            raise  # 重新抛出，交给 LangGraph 的 retry_policy / error_handler 处理

        elapsed_ms = (time.perf_counter() - start) * 1000
        _record_metric(node_name, elapsed_ms)
        add_done_task(task_id, node_name, is_stream)
        logger.info(f"<<< [{task_id}] {node_name} 执行完成，耗时 {elapsed_ms:.0f}ms")
        return result

    return wrapper


def default_retry_policy(
    max_attempts: int = 3,
    retry_on: Optional[Sequence[Type[Exception]]] = None,
) -> RetryPolicy:
    """
    默认节点重试策略：指数退避 + 抖动，最多重试 max_attempts-1 次。

    适用于调用外部服务（LLM / MinerU / Milvus / MCP）的节点；
    纯内存/本地计算节点不建议开启（重试无意义且掩盖真实错误）。

    :param max_attempts: 总尝试次数（含首次），默认 3
    :param retry_on: 触发重试的异常类型；默认 (TimeoutError, ConnectionError, OSError)
    :return: langgraph RetryPolicy 实例
    """
    if retry_on is None:
        retry_on = (TimeoutError, ConnectionError, OSError)

    return RetryPolicy(
        initial_interval=0.5,   # 首次重试前等待 0.5s
        backoff_factor=2.0,     # 每次翻倍
        max_interval=30.0,      # 上限 30s
        max_attempts=max_attempts,
        jitter=True,            # 加抖动避免惊群
        retry_on=tuple(retry_on),
    )
