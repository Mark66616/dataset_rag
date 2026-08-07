"""工具函数单元测试：Milvus 字符串转义、稀疏向量归一化。"""
import numpy as np

from app.utils.escape_milvus_string_utils import escape_milvus_string
from app.utils.normalize_sparse_vector import normalize_sparse_vector


class TestEscapeMilvusString:
    def test_plain_string_unchanged(self):
        assert escape_milvus_string("华为P60") == "华为P60"

    def test_double_quote_escaped(self):
        assert escape_milvus_string('a"b') == 'a\\"b'

    def test_backslash_escaped(self):
        assert escape_milvus_string("a\\b") == "a\\\\b"

    def test_newline_tab_replaced_by_space(self):
        assert escape_milvus_string("a\nb\tc\rd") == "a b c d"

    def test_none_returns_empty(self):
        assert escape_milvus_string(None) == ""

    def test_non_string_coerced(self):
        assert escape_milvus_string(123) == "123"

    def test_round_trip_usable_in_filter_expr(self):
        # 转义后的字符串可以直接拼进 Milvus filter 表达式而不破坏语法
        escaped = escape_milvus_string('H3C "ER2100" 路由器\n说明')
        assert '"' not in escaped.replace('\\"', "")
        assert "\n" not in escaped


class TestNormalizeSparseVector:
    def test_l2_norm_is_one(self):
        vec = {1: 3.0, 2: 4.0}
        out = normalize_sparse_vector(vec)
        values = np.array(list(out.values()))
        assert abs(np.linalg.norm(values) - 1.0) < 1e-9

    def test_keys_preserved(self):
        out = normalize_sparse_vector({10: 2.0, 20: 2.0})
        assert set(out.keys()) == {10, 20}

    def test_empty_returns_as_is(self):
        assert normalize_sparse_vector({}) == {}

    def test_none_returns_none(self):
        assert normalize_sparse_vector(None) is None

    def test_zero_norm_returns_original(self):
        vec = {1: 0.0, 2: 0.0}
        assert normalize_sparse_vector(vec) == vec

    def test_ratio_preserved(self):
        out = normalize_sparse_vector({1: 3.0, 2: 4.0})
        assert abs(out[1] / out[2] - 3.0 / 4.0) < 1e-9
