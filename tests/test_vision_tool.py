"""Vision 工具测试"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lib.tools.vision import _detect_mime, _get_vl_config, _image_read, create_vision_tools


class TestDetectMime:
    """测试 _detect_mime 函数"""

    def test_detect_png(self, tmp_path: Path) -> None:
        """测试 PNG 格式检测"""
        png_file = tmp_path / "test.png"
        # PNG 魔数
        png_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        assert _detect_mime(png_file) == "image/png"

    def test_detect_jpeg(self, tmp_path: Path) -> None:
        """测试 JPEG 格式检测"""
        jpeg_file = tmp_path / "test.jpg"
        # JPEG 魔数
        jpeg_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        assert _detect_mime(jpeg_file) == "image/jpeg"

    def test_detect_gif87a(self, tmp_path: Path) -> None:
        """测试 GIF87a 格式检测"""
        gif_file = tmp_path / "test.gif"
        gif_file.write_bytes(b"GIF87a" + b"\x00" * 100)
        assert _detect_mime(gif_file) == "image/gif"

    def test_detect_gif89a(self, tmp_path: Path) -> None:
        """测试 GIF89a 格式检测"""
        gif_file = tmp_path / "test.gif"
        gif_file.write_bytes(b"GIF89a" + b"\x00" * 100)
        assert _detect_mime(gif_file) == "image/gif"

    def test_detect_bmp(self, tmp_path: Path) -> None:
        """测试 BMP 格式检测"""
        bmp_file = tmp_path / "test.bmp"
        bmp_file.write_bytes(b"BM" + b"\x00" * 100)
        assert _detect_mime(bmp_file) == "image/bmp"

    def test_detect_webp(self, tmp_path: Path) -> None:
        """测试 WebP 格式检测"""
        webp_file = tmp_path / "test.webp"
        # RIFF....WEBP
        webp_file.write_bytes(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100)
        assert _detect_mime(webp_file) == "image/webp"

    def test_detect_riff_not_webp(self, tmp_path: Path) -> None:
        """测试 RIFF 但不是 WebP 的情况"""
        riff_file = tmp_path / "test.avi"
        # RIFF 但后面不是 WEBP
        riff_file.write_bytes(b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 100)
        assert _detect_mime(riff_file) is None

    def test_detect_unsupported_format(self, tmp_path: Path) -> None:
        """测试不支持的格式"""
        txt_file = tmp_path / "test.txt"
        txt_file.write_bytes(b"Hello, world!")
        assert _detect_mime(txt_file) is None

    def test_detect_empty_file(self, tmp_path: Path) -> None:
        """测试空文件"""
        empty_file = tmp_path / "empty.png"
        empty_file.write_bytes(b"")
        assert _detect_mime(empty_file) is None

    def test_detect_nonexistent_file(self, tmp_path: Path) -> None:
        """测试不存在的文件"""
        nonexistent = tmp_path / "nonexistent.png"
        assert _detect_mime(nonexistent) is None


class TestGetVlConfig:
    """测试 _get_vl_config 函数"""

    @patch("core.config.load_user_config")
    @patch("core.config.get_settings")
    @patch("core.config.build_llm_call_params")
    def test_vl_not_configured_reuse_main_model(
        self,
        mock_build_params: MagicMock,
        mock_get_settings: MagicMock,
        mock_load_config: MagicMock,
    ) -> None:
        """测试 VL 未配置时复用主模型"""
        mock_load_config.return_value = {}  # 无 VL 配置
        mock_get_settings.return_value = MagicMock()
        mock_build_params.return_value = {
            "model": "dashscope/qwen-plus",
            "api_key": "test-key",
            "base_url": "https://api.dashscope.com",
        }

        config = _get_vl_config()

        assert config["is_main_model"] is True
        assert config["model"] == "dashscope/qwen-plus"
        assert config["api_key"] == "test-key"

    @patch("core.config.load_user_config")
    @patch("core.config.get_settings")
    @patch("core.config.build_llm_call_params")
    def test_vl_configured_use_vl_model(
        self,
        mock_build_params: MagicMock,
        mock_get_settings: MagicMock,
        mock_load_config: MagicMock,
    ) -> None:
        """测试 VL 已配置时使用 VL 模型"""
        mock_load_config.return_value = {
            "vl_provider": "dashscope",
            "vl_model": "qwen-vl-max",
            "vl_api_key": "vl-key",
            "vl_base_url": "https://vl-api.com",
        }
        mock_get_settings.return_value = MagicMock()
        mock_build_params.return_value = {
            "model": "dashscope/qwen-vl-max",
            "api_key": "vl-key",
            "base_url": "https://vl-api.com",
        }

        config = _get_vl_config()

        assert config["is_main_model"] is False
        assert config["model"] == "dashscope/qwen-vl-max"
        assert config["api_key"] == "vl-key"

        # 验证 build_llm_call_params 被正确调用
        mock_build_params.assert_called_once_with(
            model="qwen-vl-max",
            provider="dashscope",
            api_key="vl-key",
            base_url="https://vl-api.com",
        )

    @patch("core.config.load_user_config")
    @patch("core.config.get_settings")
    @patch("core.config.build_llm_call_params")
    def test_vl_partial_config_fallback_to_main(
        self,
        mock_build_params: MagicMock,
        mock_get_settings: MagicMock,
        mock_load_config: MagicMock,
    ) -> None:
        """测试 VL 部分配置（只有 provider 没有 model）时回退到主模型"""
        mock_load_config.return_value = {
            "vl_provider": "dashscope",
            # 缺少 vl_model
        }
        mock_get_settings.return_value = MagicMock()
        mock_build_params.return_value = {
            "model": "dashscope/qwen-plus",
            "api_key": "test-key",
            "base_url": "https://api.dashscope.com",
        }

        config = _get_vl_config()

        assert config["is_main_model"] is True


class TestImageRead:
    """测试 _image_read 工具函数"""

    @pytest.mark.asyncio
    async def test_missing_file_path(self) -> None:
        """测试缺少 file_path 参数"""
        result = await _image_read({}, MagicMock())
        assert "❌" in str(result)
        assert "请提供 file_path" in str(result)

    @pytest.mark.asyncio
    async def test_empty_file_path(self) -> None:
        """测试空 file_path 参数"""
        result = await _image_read({"file_path": "  "}, MagicMock())
        assert "❌" in str(result)
        assert "请提供 file_path" in str(result)

    @pytest.mark.asyncio
    async def test_file_not_exists(self, tmp_path: Path) -> None:
        """测试文件不存在"""
        deps = MagicMock()
        deps.workspace_root = str(tmp_path)

        with patch("lib.tools.files._resolve_read", return_value=(str(tmp_path / "nonexistent.png"), None)):
            result = await _image_read({"file_path": "nonexistent.png"}, deps)
            assert "❌" in str(result)
            assert "文件不存在" in str(result)

    @pytest.mark.asyncio
    async def test_path_is_directory(self, tmp_path: Path) -> None:
        """测试路径是目录"""
        deps = MagicMock()
        deps.workspace_root = str(tmp_path)

        with patch("lib.tools.files._resolve_read", return_value=(str(tmp_path), None)):
            result = await _image_read({"file_path": "."}, deps)
            assert "❌" in str(result)
            assert "路径不是文件" in str(result)

    @pytest.mark.asyncio
    async def test_unsupported_format(self, tmp_path: Path) -> None:
        """测试不支持的图片格式"""
        deps = MagicMock()
        deps.workspace_root = str(tmp_path)

        # 创建一个文本文件
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("Hello")

        with patch("lib.tools.files._resolve_read", return_value=(str(txt_file), None)):
            result = await _image_read({"file_path": "test.txt"}, deps)
            assert "❌" in str(result)
            assert "不支持的图片格式" in str(result)

    @pytest.mark.asyncio
    async def test_no_vl_api_key(self, tmp_path: Path) -> None:
        """测试未配置 VL API Key"""
        deps = MagicMock()
        deps.workspace_root = str(tmp_path)

        # 创建一个 PNG 文件（使用 PIL 生成有效的 PNG）
        png_file = tmp_path / "test.png"
        try:
            from PIL import Image

            img = Image.new("RGB", (10, 10), color="red")
            img.save(png_file)
        except ImportError:
            # 如果 Pillow 未安装，创建一个简单的 PNG 文件
            png_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        with (
            patch("lib.tools.files._resolve_read", return_value=(str(png_file), None)),
            patch("lib.tools.vision._get_vl_config", return_value={"api_key": "", "model": "test"}),
            patch("lib.tools.vision._detect_mime", return_value="image/png"),
            patch("lib.tools.vision._encode_image_data_uri", return_value="data:image/png;base64,dGVzdA=="),
        ):
            result = await _image_read({"file_path": "test.png"}, deps)
            assert "❌" in str(result)
            assert "未配置 VL 模型 API Key" in str(result)


class TestCreateVisionTools:
    """测试 create_vision_tools 函数"""

    def test_returns_tool_list(self) -> None:
        """测试返回工具列表"""
        tools = create_vision_tools()
        assert len(tools) == 1

    def test_tool_definition(self) -> None:
        """测试工具定义"""
        tools = create_vision_tools()
        tool = tools[0]

        assert tool.name == "image_read"
        assert "图片" in tool.description
        assert tool.read_only is True
        assert tool.meta.always_on is True
        assert tool.meta.risk == "read-only"
        assert "图片" in tool.meta.search_hint

    def test_input_schema(self) -> None:
        """测试输入 schema"""
        tools = create_vision_tools()
        tool = tools[0]
        schema = tool.input_schema

        assert schema["type"] == "object"
        assert "file_path" in schema["properties"]
        assert "prompt" in schema["properties"]
        assert schema["required"] == ["file_path"]
