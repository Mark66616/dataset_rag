"""
pytest 全局配置。

关键：在导入任何 app 模块之前，把 MONGO_URL / MILVUS_URL 指向立即拒绝连接的地址，
并收紧连接/选择超时（ms 级），避免模块级连接尝试在测试中等待默认的 20-30s。
load_dotenv 默认不覆盖已存在的环境变量，因此这里的设置优先于 .env。
"""
import os

os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300&connectTimeoutMS=300",
)
os.environ.setdefault("MILVUS_URL", "http://127.0.0.1:1")
os.environ.setdefault("MINIO_ENDPOINT", "127.0.0.1:1")
