"""任务持久化(task_store)与 Checkpointer 封装单元测试。

通过 monkeypatch 模拟 MongoDB，验证：
- task_store 的 upsert/load/delete/list 行为与降级逻辑
- checkpointer.get_checkpointer 的懒加载与失败降级
不依赖真实 MongoDB 连接。
"""
import pytest


class TestTaskStore:
    def setup_method(self):
        import app.clients.task_store as ts

        self.ts = ts

    def _fake_collection(self, monkeypatch, docs=None):
        """构造一个最小 fake collection，替换 _get_collection。"""
        store = {"docs": docs if docs is not None else {}}

        class FakeCollection:
            def create_index(self, keys, **kwargs):
                store["indexes"] = store.get("indexes", []) + [keys]

            def update_one(self, filt, update, upsert=False):
                task_id = filt["task_id"]
                merged = dict(store["docs"].get(task_id, {}))
                merged.update(update.get("$set", {}))
                merged.update(update.get("$setOnInsert", {}))
                store["docs"][task_id] = merged
                return None

            def find_one(self, filt):
                return store["docs"].get(filt.get("task_id"))

            def delete_one(self, filt):
                store["docs"].pop(filt.get("task_id"), None)
                return None

            def find(self, *args, **kwargs):
                class Cursor:
                    def __init__(self, data):
                        self._data = data

                    def sort(self, *a, **k):
                        return Cursor(self._data)

                    def limit(self, n):
                        return self._data[:n]

                    def __iter__(self):
                        return iter(self._data)

                return Cursor(list(store["docs"].values()))

        monkeypatch.setattr(self.ts, "_get_collection", lambda: FakeCollection())
        return store

    def test_upsert_and_load(self, monkeypatch):
        store = self._fake_collection(monkeypatch)
        assert self.ts.upsert_task("t1", status="processing", done_list=["node_entry"], running_list=["node_pdf_to_md"])
        doc = self.ts.load_task("t1")
        assert doc["task_id"] == "t1"
        assert doc["status"] == "processing"
        assert doc["done_list"] == ["node_entry"]
        assert doc["expire_at"] > doc["updated_at"]  # TTL 字段

    def test_upsert_overwrites_same_task(self, monkeypatch):
        store = self._fake_collection(monkeypatch)
        self.ts.upsert_task("t1", status="processing", done_list=[], running_list=[])
        self.ts.upsert_task("t1", status="completed", done_list=["node_entry"], running_list=[])
        doc = self.ts.load_task("t1")
        assert doc["status"] == "completed"
        assert doc["done_list"] == ["node_entry"]

    def test_load_missing_returns_none(self, monkeypatch):
        self._fake_collection(monkeypatch)
        assert self.ts.load_task("nope") is None

    def test_delete(self, monkeypatch):
        store = self._fake_collection(monkeypatch)
        self.ts.upsert_task("t1", status="pending", done_list=[], running_list=[])
        assert self.ts.delete_task("t1") is True
        assert self.ts.load_task("t1") is None

    def test_list_tasks_sorted(self, monkeypatch):
        self._fake_collection(monkeypatch)
        self.ts.upsert_task("t1", status="completed", done_list=[], running_list=[])
        self.ts.upsert_task("t2", status="processing", done_list=[], running_list=[])
        tasks = self.ts.list_tasks(limit=10)
        assert {t["task_id"] for t in tasks} == {"t1", "t2"}

    def test_mongo_failure_degrades(self, monkeypatch):
        """Mongo 抛异常时，upsert 返回 False 且不抛出。"""

        def boom(*args, **kwargs):
            raise RuntimeError("connection lost")

        monkeypatch.setattr(self.ts, "_get_collection", boom)
        assert self.ts.upsert_task("t1", status="pending", done_list=[], running_list=[]) is False
        assert self.ts.load_task("t1") is None
        assert self.ts.delete_task("t1") is False
        assert self.ts.list_tasks() == []

    def test_ensure_indexes_degrades(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("connection lost")

        monkeypatch.setattr(self.ts, "_get_collection", boom)
        # 不应抛出
        self.ts.ensure_indexes()


class TestCheckpointer:
    def test_returns_none_on_failure(self, monkeypatch):
        """Mongo 连接失败时 get_checkpointer 返回 None（降级）。"""
        import app.clients.checkpointer as cp

        monkeypatch.setattr(cp, "_checkpointer", None)

        def boom():
            raise RuntimeError("no mongo")

        monkeypatch.setattr(cp, "get_history_mongo_tool", boom)
        assert cp.get_checkpointer() is None

    def test_lazy_singleton_cached(self, monkeypatch):
        """成功初始化后，再次调用返回同一实例（单例缓存）。"""
        import app.clients.checkpointer as cp

        monkeypatch.setattr(cp, "_checkpointer", None)

        class FakeTool:
            client = object()
            db_name = "kb_test"

        calls = {"n": 0}

        def fake_tool():
            calls["n"] += 1
            return FakeTool()

        monkeypatch.setattr(cp, "get_history_mongo_tool", fake_tool)

        class FakeSaver:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        import sys
        import types

        fake_mod = types.ModuleType("langgraph.checkpoint.mongodb")
        fake_mod.MongoDBSaver = FakeSaver
        monkeypatch.setitem(sys.modules, "langgraph.checkpoint.mongodb", fake_mod)

        saver1 = cp.get_checkpointer()
        saver2 = cp.get_checkpointer()
        assert saver1 is saver2
        assert calls["n"] == 1  # 只连接一次
        assert saver1.kwargs["db_name"] == "kb_test"
        assert saver1.kwargs["ttl"] == cp.DEFAULT_CHECKPOINT_TTL_SECONDS
