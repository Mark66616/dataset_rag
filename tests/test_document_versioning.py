"""P0.3 文档版本化单元测试：发布节点状态切换、残留清理、检索过滤。

通过 fake Milvus client 验证，不依赖真实 Milvus：
- publish_version: staging→active、旧版本 active→superseded
- mark_document_failed: 残留 staging→failed
- step_3_clean_old_data: 按 document_id 清非 active，不删 active
- 检索过滤表达式含 index_status == "active"
"""
import pytest

from app.import_process.agent.nodes import node_publish_version as npv
from app.import_process.agent.nodes import node_import_milvus as nim


class FakeMilvus:
    """最小 fake MilvusClient：内存存储，支持 query/upsert/delete/has_collection。"""

    def __init__(self):
        # rows: list[dict]，含 chunk_id/index_status/document_id/document_version
        self.rows = []
        self._next_id = 1

    def has_collection(self, collection_name=None):
        return True

    def query(self, collection_name=None, filter="", output_fields=None, **kwargs):
        return [r for r in self.rows if self._match(r, filter)]

    def upsert(self, collection_name=None, data=None, **kwargs):
        for row in data:
            # 找到同 chunk_id 的行并更新字段
            for existing in self.rows:
                if existing["chunk_id"] == row["chunk_id"]:
                    existing.update(row)
                    break
            else:
                row["chunk_id"] = self._next_id
                self._next_id += 1
                self.rows.append(row)
        return {"upsert_count": len(data)}

    def insert(self, collection_name=None, data=None, **kwargs):
        inserted = []
        for row in data:
            r = dict(row)
            r["chunk_id"] = self._next_id
            self._next_id += 1
            self.rows.append(r)
            inserted.append(r["chunk_id"])
        return {"insert_count": len(data), "ids": inserted}

    def delete(self, collection_name=None, filter="", **kwargs):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not self._match(r, filter)]
        return {"delete_count": before - len(self.rows)}

    def flush(self, collection_name=None):
        return None

    @staticmethod
    def _match(row, expr):
        """极简 filter 解析：仅支持本项目用到的 and/==/!= 组合。"""
        # 示例: document_id == "doc1" and index_status != "active"
        if " and " in expr:
            return all(FakeMilvus._match(row, part.strip()) for part in expr.split(" and "))
        if "==" in expr:
            field, val = expr.split("==")
            field, val = field.strip(), val.strip().strip('"')
            return str(row.get(field)) == val
        if "!=" in expr:
            field, val = expr.split("!=")
            field, val = field.strip(), val.strip().strip('"')
            return str(row.get(field)) != val
        return True


def _make_row(chunk_id, doc_id, version, status, item_name="H3C ER2100"):
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "document_version": version,
        "index_status": status,
        "item_name": item_name,
        "content": "测试内容",
    }


class TestPublishVersion:
    def test_promote_staging_to_active(self):
        client = FakeMilvus()
        client.rows = [
            _make_row(1, "doc_a", 1, "active"),
            _make_row(2, "doc_a", 2, "staging"),
            _make_row(3, "doc_a", 2, "staging"),
        ]
        stats = npv.publish_version("doc_a", 2, client=client)
        # v2 的两条 staging 被 promote 为 active；v1 的旧 active 被 supersede
        assert stats["promoted"] == 2
        assert stats["superseded"] == 1
        v2 = [r for r in client.rows if r["document_version"] == 2]
        assert all(r["index_status"] == "active" for r in v2)
        v1 = [r for r in client.rows if r["document_version"] == 1]
        assert v1[0]["index_status"] == "superseded"

    def test_old_active_superseded(self):
        client = FakeMilvus()
        client.rows = [
            _make_row(1, "doc_a", 1, "active"),
            _make_row(2, "doc_a", 2, "staging"),
        ]
        stats = npv.publish_version("doc_a", 2, client=client)
        assert stats["superseded"] == 1
        v1 = [r for r in client.rows if r["document_version"] == 1]
        assert v1[0]["index_status"] == "superseded"
        v2 = [r for r in client.rows if r["document_version"] == 2]
        assert v2[0]["index_status"] == "active"

    def test_different_doc_untouched(self):
        """不同 document_id 的数据互不干扰（修复同商品名互删）。"""
        client = FakeMilvus()
        client.rows = [
            _make_row(1, "doc_a", 1, "active"),
            _make_row(2, "doc_b", 1, "active", item_name="H3C ER2100"),  # 同商品名不同文档
        ]
        npv.publish_version("doc_a", 2, client=client, document_version=None) if False else None
        # 发布 doc_a 的 v1(active)→ 无 staging,不报错;doc_b 不受影响
        stats = npv.publish_version("doc_a", 1, client=client)
        assert stats["promoted"] == 0
        doc_b = [r for r in client.rows if r["document_id"] == "doc_b"]
        assert doc_b[0]["index_status"] == "active"

    def test_empty_document_id_raises(self):
        with pytest.raises(ValueError):
            npv.publish_version("", 1, client=FakeMilvus())


class TestMarkDocumentFailed:
    def test_staging_marked_failed(self):
        client = FakeMilvus()
        client.rows = [
            _make_row(1, "doc_a", 1, "active"),
            _make_row(2, "doc_a", 2, "staging"),
        ]
        npv.mark_document_failed("doc_a", client=client)
        statuses = {r["chunk_id"]: r["index_status"] for r in client.rows}
        assert statuses[1] == "active"  # 旧版本不受影响
        assert statuses[2] == "failed"


class TestCleanOldData:
    def test_clean_non_active_only(self):
        client = FakeMilvus()
        client.rows = [
            _make_row(1, "doc_a", 1, "active"),
            _make_row(2, "doc_a", 2, "staging"),   # 上次失败的残留
            _make_row(3, "doc_a", 1, "superseded"),
            _make_row(4, "doc_b", 1, "active"),    # 其他文档不受影响
        ]
        nim._clear_non_active_by_document_id(client, "kb_chunks", "doc_a")
        remaining = client.rows
        assert {r["chunk_id"] for r in remaining} == {1, 4}  # 只留两个 active

    def test_clean_skips_when_no_doc_id(self):
        client = FakeMilvus()
        nim._clear_non_active_by_document_id(client, "kb_chunks", "")  # 不应报错


class TestRetrievalFilter:
    def test_search_embedding_filter_has_active(self):
        src = open("app/query_process/agent/nodes/node_search_embedding.py").read()
        assert 'index_status == "active"' in src

    def test_hyde_filter_has_active(self):
        src = open("app/query_process/agent/nodes/node_search_embedding_hyde.py").read()
        assert 'index_status == "active"' in src


class TestNodeEntryDocId:
    def test_generates_uuid_when_missing(self):
        from app.import_process.agent.nodes.node_entry import node_entry

        state = {"task_id": "t1", "local_file_path": "/tmp/测试文档.pdf"}
        out = node_entry(state)
        assert out["document_id"]  # 生成了 UUID
        assert out["document_version"] == 1

    def test_keeps_passed_document_id(self):
        from app.import_process.agent.nodes.node_entry import node_entry

        state = {
            "task_id": "t2",
            "local_file_path": "/tmp/测试文档.pdf",
            "document_id": "doc_fixed_123",
            "document_version": 3,
        }
        out = node_entry(state)
        assert out["document_id"] == "doc_fixed_123"
        assert out["document_version"] == 3
