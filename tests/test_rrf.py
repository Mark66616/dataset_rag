"""RRF（倒数排名融合）算法单元测试。"""
from app.query_process.agent.nodes.node_rrf import reciprocal_rank_fusion, _as_entity_list


def _docs(*ids):
    return [{"chunk_id": i} for i in ids]


class TestReciprocalRankFusion:
    def test_single_source_keeps_order(self):
        docs = _docs("a", "b", "c")
        merged = reciprocal_rank_fusion([(docs, 1.0)])
        assert [d["chunk_id"] for d, _ in merged] == ["a", "b", "c"]

    def test_common_item_ranks_higher(self):
        # a 仅在第一路 rank1；b 在两路分别 rank2/rank1，融合分应更高
        merged = reciprocal_rank_fusion(
            [(_docs("a", "b", "c"), 1.0), (_docs("b", "c", "d"), 1.0)]
        )
        ids = [d["chunk_id"] for d, _ in merged]
        assert ids[0] == "b"

    def test_weight_influences_ranking(self):
        # 高权重来源的第一名应胜过低权重来源的第一名
        merged = reciprocal_rank_fusion(
            [(_docs("x"), 2.0), (_docs("y"), 0.1)]
        )
        assert merged[0][0]["chunk_id"] == "x"

    def test_dedup(self):
        merged = reciprocal_rank_fusion(
            [(_docs("a", "b"), 1.0), (_docs("b", "a"), 1.0)]
        )
        ids = [d["chunk_id"] for d, _ in merged]
        assert len(ids) == len(set(ids)) == 2

    def test_max_results_truncates(self):
        merged = reciprocal_rank_fusion([(_docs("a", "b", "c", "d"), 1.0)], max_results=2)
        assert len(merged) == 2

    def test_empty_sources(self):
        assert reciprocal_rank_fusion([]) == []

    def test_items_missing_id_skipped(self):
        merged = reciprocal_rank_fusion([([{"content": "no id"}], 1.0)])
        assert merged == []


class TestAsEntityList:
    def test_flat_dict_passthrough(self):
        out = _as_entity_list([{"chunk_id": 1, "content": "x"}])
        assert out == [{"chunk_id": 1, "content": "x"}]

    def test_nested_entity_extracted(self):
        out = _as_entity_list([{"entity": {"chunk_id": 1}, "distance": 0.9, "id": 99}])
        assert out[0]["chunk_id"] == 1
        assert out[0]["score"] == 0.9

    def test_empty_list(self):
        assert _as_entity_list([]) == []

    def test_none_values_filtered(self):
        assert _as_entity_list([None, {}, {"chunk_id": 2}]) == [{"chunk_id": 2}]
