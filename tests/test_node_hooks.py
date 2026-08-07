"""节点 Hook 工具单元测试：相呼应日志、任务状态、耗时指标、异常传播。"""
import time

import pytest

from app.core import node_hooks
from app.core.node_hooks import node_hook, default_retry_policy
from app.utils import task_utils


class TestNodeHook:
    def setup_method(self):
        # 每个用例前清理任务状态与指标，避免相互污染
        node_hooks._NODE_METRICS.clear()
        task_utils._tasks_running_list.clear()
        task_utils._tasks_done_list.clear()
        task_utils._tasks_status.clear()

    def test_success_updates_state_and_metrics(self):
        @node_hook
        def fake_node(state):
            state["done"] = True
            return state

        state = {"task_id": "t1"}
        out = fake_node(state)

        assert out["done"] is True
        # 任务状态：运行中列表已清空，done 列表包含节点
        assert "fake_node" in task_utils.get_done_task_list("t1")
        assert task_utils.get_running_task_list("t1") == []
        # 指标：1 次调用，耗时 > 0
        metrics = node_hooks.get_node_metrics()
        assert metrics["fake_node"]["count"] == 1
        assert metrics["fake_node"]["total_ms"] > 0

    def test_failure_re_raises_and_marks_failed(self):
        @node_hook
        def bad_node(state):
            raise ConnectionError("network down")

        state = {"task_id": "t2"}
        with pytest.raises(ConnectionError):
            bad_node(state)

        assert task_utils.get_task_status("t2") == task_utils.TASK_STATUS_FAILED
        # done 列表不应包含失败的节点
        assert "bad_node" not in task_utils.get_done_task_list("t2")

    def test_uses_session_id_when_no_task_id(self):
        @node_hook
        def q_node(state):
            return state

        q_node({"session_id": "s1", "is_stream": False})
        assert "q_node" in task_utils.get_done_task_list("s1")

    def test_preserves_return_value_and_input(self):
        @node_hook
        def partial_node(state):
            # 模拟查询链路节点：只返回局部更新
            return {"answer": "hi"}

        out = partial_node({"session_id": "s2"})
        assert out == {"answer": "hi"}

    def test_hook_on_non_dict_error_path(self):
        """异常路径下 wrapper 不应吞掉非网络异常（如 ValueError）"""

        @node_hook
        def val_node(state):
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            val_node({"task_id": "t3"})


class TestDefaultRetryPolicy:
    def test_default_values(self):
        policy = default_retry_policy()
        assert policy.max_attempts == 3
        assert policy.initial_interval == 0.5
        assert policy.backoff_factor == 2.0
        assert policy.max_interval == 30.0
        assert policy.jitter is True

    def test_custom_attempts(self):
        policy = default_retry_policy(max_attempts=5)
        assert policy.max_attempts == 5

    def test_custom_retry_on(self):
        policy = default_retry_policy(retry_on=(TimeoutError,))
        assert policy.retry_on == (TimeoutError,)

    def test_retry_on_contains_network_exceptions(self):
        policy = default_retry_policy()
        assert TimeoutError in policy.retry_on
        assert ConnectionError in policy.retry_on
        assert OSError in policy.retry_on
