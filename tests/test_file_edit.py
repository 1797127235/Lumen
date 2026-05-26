"""file_edit 工具测试。"""

import pytest

from lib.tools.files import _strip_line_prefixes, replace


class TestReplace:
    def test_exact(self):
        assert replace("hello world", "world", "python") == "hello python"

    def test_line_trimmed(self):
        # Replacer 只定位原文区间，replace() 整块替换为 new_string
        # LLM 需要在 new_string 中带好正确缩进
        assert replace("  hello\n  world", "hello\nworld", "a\nb") == "a\nb"

    def test_not_found(self):
        with pytest.raises(ValueError, match="Could not find"):
            replace("hello", "xyz", "abc")

    def test_multiple(self):
        with pytest.raises(ValueError, match="multiple matches"):
            replace("x x", "x", "y")

    def test_replace_all(self):
        assert replace("x x", "x", "y", replace_all=True) == "y y"

    def test_indent_flex(self):
        # Replacer 只定位原文区间，replace() 整块替换为 new_string
        # LLM 需要在 new_string 中带好正确缩进
        content = "    def a():\n        pass"
        assert replace(content, "def a():\n    pass", "def b():\n    pass") == "def b():\n    pass"


class TestStripPrefixes:
    def test_with(self):
        assert _strip_line_prefixes("1: a\n2: b") == "a\nb"

    def test_without(self):
        assert _strip_line_prefixes("a\nb") == "a\nb"


class TestFileEdit:
    @pytest.mark.asyncio
    async def test_new_file(self, tmp_path):
        class D:
            workspace_root = tmp_path

        from lib.tools.files import _file_edit

        await _file_edit({"file_path": "x.txt", "old_string": "", "new_string": "hi"}, D())
        assert (tmp_path / "x.txt").read_text() == "hi"

    @pytest.mark.asyncio
    async def test_new_file_exists_error(self, tmp_path):
        class D:
            workspace_root = tmp_path

        (tmp_path / "exists.txt").write_text("original")
        from lib.tools.files import _file_edit

        result = await _file_edit({"file_path": "exists.txt", "old_string": "", "new_string": "hi"}, D())
        assert "已存在" in str(result)

    @pytest.mark.asyncio
    async def test_with_prefix(self, tmp_path):
        class D:
            workspace_root = tmp_path

        (tmp_path / "a.py").write_text("DEBUG = False\n", encoding="utf-8", newline="")
        from lib.tools.files import _file_edit

        await _file_edit(
            {
                "file_path": "a.py",
                "old_string": "1: DEBUG = False",
                "new_string": "DEBUG = True",
            },
            D(),
        )
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "DEBUG = True\n"

    @pytest.mark.asyncio
    async def test_bom(self, tmp_path):
        class D:
            workspace_root = tmp_path

        p = tmp_path / "bom.txt"
        p.write_bytes(b"\xef\xbb\xbfhello")
        from lib.tools.files import _file_edit

        await _file_edit({"file_path": "bom.txt", "old_string": "hello", "new_string": "world"}, D())
        assert p.read_bytes() == b"\xef\xbb\xbfworld"

    @pytest.mark.asyncio
    async def test_crlf(self, tmp_path):
        class D:
            workspace_root = tmp_path

        p = tmp_path / "crlf.txt"
        p.write_text("a\r\nb\r\nc", encoding="utf-8", newline="")
        from lib.tools.files import _file_edit

        await _file_edit({"file_path": "crlf.txt", "old_string": "b", "new_string": "x"}, D())
        assert b"\r\n" in p.read_bytes()
