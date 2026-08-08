"""P1.3 任务取消与超时治理单元测试。

覆盖（不依赖真实 Milvus/MongoDB）：
- cancel_task_by_id / is_task_cancelled：状态流转与边界
- node_hook 取消检查：已取消任务跳过节点、抛 TaskCancelledError、状态保持 cancelled
- node_hook 超时：慢节点超时抛 TimeoutError、状态 failed；正常节点不受影响
"""
import time

import pytest

from app.core.node_hooks import node_hook, TaskCancelledError
from app.utils import task_utils


class TestCancelTaskUtils:
    def test_cancel_sets_status(self):
        task_utils.update_task_status("tc1", task_utils.TASK_STATUS_PROCESSING)
        assert task_utils.cancel_task_by_id("tc1") is True
        assert task_utils.is_task_cancelled("tc1") is True
        assert task_utils.get_task_status("tc1") == task_utils.TASK_STATUS_CANCELLED

    def test_cancel_completed_returns_false(self):
        task_utils.update_task_status("tc2", task_utils.TASK_STATUS_COMPLETED)
        assert task_utils.cancel_task_by_id("tc2") is False
        assert not task_utils.is_task_cancelled("tc2")

    def test_cancel_missing_returns_false(self):
        assert task_utils.cancel_task_by_id("no_such_task") is False

    def test_cancel_already_cancelled_returns_false(self):
        task_utils.update_task_status("tc3", task_utils.TASK_STATUS_PROCESSING)
        assert task_utils.cancel_task_by_id("tc3") is True
        assert task_utils.cancel_task_by_id("tc3") is False  # 二次取消无效


class TestNodeHookCancel:
    def test_cancelled_task_skips_node(self):
        """任务已取消时，节点 hook 应跳过执行并抛 TaskCancelledError。"""
        calls = {"n": 0}

        def my_node(state):
            calls["n"] += 1
            return dict(state)

        wrapped = node_hook(my_node)
        task_utils.update_task_status("t_cancel", task_utils.TASK_STATUS_PROCESSING)
        task_utils.cancel_task_by_id("t_cancel")

        with pytest.raises(TaskCancelledError):
            wrapped({"task_id": "t_cancel"})
        assert calls["n"] == 0  # 节点函数未被调用
        assert task_utils.get_task_status("t_cancel") == task_utils.TASK_STATUS_CANCELLED  # 保持 cancelled

    def test_normal_task_runs_node(self):
        calls = {"n": 0}

        def my_node(state):
            calls["n"] += 1
            state["x"] = 42
            return dict(state)

        wrapped = node_hook(my_node)
        task_utils.update_task_status("t_normal", task_utils.TASK_STATUS_PROCESSING)
        result = wrapped({"task_id": "t_normal"})
        assert calls["n"] == 1
        assert result["x"] == 42


class TestNodeHookTimeout:
    def test_timeout_raises_and_marks_failed(self):
        def slow_node(state):
            time.sleep(5)
            return dict(state)

        wrapped = node_hook(slow_node, timeout=0.2)
        task_utils.update_task_status("t_to", task_utils.TASK_STATUS_PROCESSING)

        start = time.time()
        with pytest.raises(TimeoutError):
            wrapped({"task_id": "t_to"})
        assert time.time() - start < 3  # 不应等满 5s
        assert task_utils.get_task_status("t_to") == task_utils.TASK_STATUS_FAILED

    def test_fast_node_with_timeout_ok(self):
        def fast_node(state):
            state["ok"] = True
            return dict(state)

        wrapped = node_hook(fast_node, timeout=2.0)
        task_utils.update_task_status("t_fast", task_utils.TASK_STATUS_PROCESSING)
        result = wrapped({"task_id": "t_fast"})
        assert result["ok"] is True
        assert task_utils.get_task_status("t_fast") != task_utils.TASK_STATUS_FAILED
