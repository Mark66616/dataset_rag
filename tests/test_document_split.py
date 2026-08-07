"""文档切分节点纯逻辑单元测试（不依赖外部服务）。"""
from app.import_process.agent.nodes.node_document_split import (
    step_2_split_by_titles,
    step_3_handle_no_title,
    _split_long_section,
    _merge_short_sections,
)


class TestSplitByTitles:
    def test_basic_sections(self):
        content = "# 标题1\n内容1\n## 标题2\n内容2"
        sections, title_count, _ = step_2_split_by_titles(content, "test")
        assert title_count == 2
        assert len(sections) == 2
        assert sections[0]["title"] == "# 标题1"
        assert sections[1]["title"] == "## 标题2"

    def test_code_block_titles_ignored(self):
        content = "```\n# 不是标题\n```\n# 真标题\n正文"
        sections, title_count, _ = step_2_split_by_titles(content, "test")
        # 代码块内的 # 不计入标题数，真标题计入
        assert title_count == 1
        titles = [s["title"] for s in sections]
        assert "# 真标题" in titles
        assert "# 不是标题" not in titles

    def test_file_title_attached(self):
        _, _, _ = None, None, None
        sections, _, _ = step_2_split_by_titles("# A\n正文", "myfile")
        assert sections[0]["file_title"] == "myfile"


class TestHandleNoTitle:
    def test_no_title_wraps_whole_content(self):
        sections, cnt, _ = step_2_split_by_titles("没有标题的正文内容", "test")
        out = step_3_handle_no_title("没有标题的正文内容", sections, cnt, "test")
        assert len(out) == 1
        assert out[0]["title"] == "无标题"
        assert "正文" in out[0]["content"]


class TestSplitLongSection:
    def test_short_section_unchanged(self):
        sec = {"title": "短标题", "content": "短内容", "file_title": "f"}
        out = _split_long_section(sec, max_length=2000)
        assert len(out) == 1
        assert out[0]["content"] == "短内容"

    def test_long_section_split(self):
        long_text = "段落一。" * 100
        sec = {"title": "长标题", "content": long_text, "file_title": "f"}
        out = _split_long_section(sec, max_length=200)
        assert len(out) > 1
        # 每个子块长度不超过上限
        for chunk in out:
            assert len(chunk["content"]) <= 200

    def test_part_numbering(self):
        long_text = "内容。" * 200
        out = _split_long_section({"title": "T", "content": long_text}, max_length=100)
        parts = [c["part"] for c in out]
        assert parts == list(range(1, len(out) + 1))


class TestMergeShortSections:
    def test_merge_same_parent_short(self):
        sections = [
            {"title": "T-1", "content": "很短", "parent_title": "T", "part": 1},
            {"title": "T-2", "content": "也很短", "parent_title": "T", "part": 2},
        ]
        out = _merge_short_sections(sections, min_length=50)
        assert len(out) == 1
        assert "很短" in out[0]["content"]
        assert "也很短" in out[0]["content"]

    def test_do_not_merge_different_parents(self):
        sections = [
            {"title": "A-1", "content": "很短", "parent_title": "A", "part": 1},
            {"title": "B-1", "content": "也很短", "parent_title": "B", "part": 1},
        ]
        out = _merge_short_sections(sections, min_length=50)
        assert len(out) == 2

    def test_empty_input(self):
        assert _merge_short_sections([]) == []
