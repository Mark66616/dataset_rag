# 导入Python内置模块
import os
import json
from datetime import timedelta
# 导入MinIO官方Python SDK核心类
from minio import Minio
# 项目内部配置与日志
from app.conf.minio_config import minio_config
from app.core.logger import logger

# 全局MinIO客户端对象，初始化后供全项目调用
minio_client = None

try:
    # 初始化MinIO客户端实例
    minio_client = Minio(
        endpoint=minio_config.endpoint,
        access_key=minio_config.access_key,
        secret_key=minio_config.secret_key,
        secure=False  # 内网/本地部署用HTTP，公网部署需改为True并配置SSL
    )
    bucket_name = minio_config.bucket_name

    # 检查存储桶是否存在，不存在则自动创建
    if not minio_client.bucket_exists(bucket_name):
        logger.info(f"MinIO存储桶[{bucket_name}]不存在，开始创建")
        minio_client.make_bucket(bucket_name)
        logger.info(f"MinIO存储桶[{bucket_name}]创建成功")
    else:
        logger.info(f"MinIO存储桶[{bucket_name}]已存在，无需重复创建")

    # 配置存储桶公网只读策略：允许匿名用户通过URL直接访问桶内文件
    bucket_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},  # *表示所有匿名用户（S3兼容标识）
            "Action": ["s3:GetObject"],   # 仅授权文件获取/访问操作
            "Resource": [f"arn:aws:s3:::{bucket_name}/*"]
        }]
    }
    minio_client.set_bucket_policy(bucket_name, json.dumps(bucket_policy))
    logger.info(f"MinIO存储桶[{bucket_name}]已配置公网只读策略，支持匿名URL访问")

except Exception as e:
    # 捕获初始化异常，记录错误日志并置空客户端
    logger.error(f"MinIO客户端初始化失败，错误信息：{str(e)}", exc_info=True)
    minio_client = None


def get_minio_client():
    """
    获取全局初始化的MinIO客户端实例
    :return: 已初始化的Minio对象 / None（初始化失败时）
    """
    return minio_client


def upload_file(local_path: str, object_key: str, content_type: str = "application/octet-stream") -> bool:
    """
    上传本地文件到 MinIO（P0.4）。

    设计要点：
    - 复用全局 minio_client；客户端未初始化（初始化失败）时降级为 False，不抛出；
    - object_key 建议格式：{prefix}/{task_id}/{filename}，见 file_import_service 调用处；
    - 上传失败仅记录日志并返回 False，不阻塞主流程（本地文件仍是权威副本）。

    :param local_path: 本地文件绝对路径
    :param object_key: MinIO 对象键（桶内路径）
    :param content_type: 对象的 Content-Type
    :return: 上传成功返回 True；失败/客户端不可用返回 False
    """
    if minio_client is None:
        logger.warning(f"MinIO上传跳过（客户端未初始化）：{object_key}")
        return False
    try:
        bucket_name = minio_config.bucket_name
        minio_client.fput_object(
            bucket_name=bucket_name,
            object_name=object_key,
            file_path=local_path,
            content_type=content_type,
        )
        logger.info(f"MinIO上传成功：{bucket_name}/{object_key}")
        return True
    except Exception as e:
        logger.error(f"MinIO上传失败：{object_key} | 错误：{str(e)}", exc_info=True)
        return False


def get_object_url(object_key: str, expires_seconds: int = 3600) -> str:
    """
    生成 MinIO 对象的临时访问 URL（预签名，默认 1 小时有效）。

    设计要点：
    - 使用 presigned GET URL，避免把 bucket 长期公网暴露；
    - 客户端不可用时返回空字符串（调用方自行降级）。

    :param object_key: MinIO 对象键
    :param expires_seconds: URL 有效期秒数，默认 3600（1 小时）
    :return: 预签名 URL；失败/客户端不可用返回空字符串
    """
    if minio_client is None:
        logger.warning(f"MinIO签名URL生成跳过（客户端未初始化）：{object_key}")
        return ""
    try:
        bucket_name = minio_config.bucket_name
        url = minio_client.presigned_get_object(
            bucket_name=bucket_name,
            object_name=object_key,
            expires=timedelta(seconds=expires_seconds),
        )
        return url
    except Exception as e:
        logger.error(f"MinIO签名URL生成失败：{object_key} | 错误：{str(e)}", exc_info=True)
        return ""