"""P0.4 MinIO 上传辅助函数单元测试。

通过 monkeypatch 模拟 minio_utils 的客户端，验证：
- upload_file: 成功返回 True、客户端不可用降级 False、上传异常降级 False
- get_object_url: 成功返回 URL、客户端不可用返回空串
不依赖真实 MinIO。
"""
import pytest

import app.clients.minio_utils as mu


class TestUploadFile:
    def test_upload_success(self, monkeypatch):
        calls = {}

        class FakeClient:
            def fput_object(self, bucket_name, object_name, file_path, content_type):
                calls["bucket"] = bucket_name
                calls["object"] = object_name
                calls["path"] = file_path
                calls["ctype"] = content_type

        monkeypatch.setattr(mu, "minio_client", FakeClient())
        monkeypatch.setattr(mu.minio_config, "bucket_name", "kb-test")
        assert mu.upload_file("/tmp/a.pdf", "kb/task1/a.pdf", "application/pdf") is True
        assert calls["bucket"] == "kb-test"
        assert calls["object"] == "kb/task1/a.pdf"
        assert calls["path"] == "/tmp/a.pdf"
        assert calls["ctype"] == "application/pdf"

    def test_upload_client_none_degrades(self, monkeypatch):
        monkeypatch.setattr(mu, "minio_client", None)
        assert mu.upload_file("/tmp/a.pdf", "kb/task1/a.pdf") is False

    def test_upload_exception_degrades(self, monkeypatch):
        class BoomClient:
            def fput_object(self, **kwargs):
                raise RuntimeError("minio down")

        monkeypatch.setattr(mu, "minio_client", BoomClient())
        assert mu.upload_file("/tmp/a.pdf", "kb/task1/a.pdf") is False


class TestGetObjectUrl:
    def test_url_success(self, monkeypatch):
        class FakeClient:
            def presigned_get_object(self, bucket_name, object_name, expires):
                return f"http://minio/{bucket_name}/{object_name}?expires={expires}"

        monkeypatch.setattr(mu, "minio_client", FakeClient())
        monkeypatch.setattr(mu.minio_config, "bucket_name", "kb-test")
        url = mu.get_object_url("kb/task1/a.pdf", expires_seconds=600)
        assert url.startswith("http://minio/kb-test/kb/task1/a.pdf")

    def test_url_client_none_returns_empty(self, monkeypatch):
        monkeypatch.setattr(mu, "minio_client", None)
        assert mu.get_object_url("kb/task1/a.pdf") == ""
