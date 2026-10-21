"""文档存储抽象：MinIO（默认）或本地文件系统（离线开发/测试）。

原始文档、页面图片统一按 storage_key 存放，数据库中只保存 key 与 sha256（文档 §20.1）。
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import io
from abc import ABC, abstractmethod
from pathlib import Path

from .config import get_settings


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Storage(ABC):
    """对象存储接口。"""

    backend: str = "base"

    @abstractmethod
    async def ensure_ready(self) -> None:
        """确保存储可用（MinIO 会创建 bucket）。"""

    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """写入对象，返回 storage_key。"""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """读取对象。"""

    async def public_url(self, key: str) -> str | None:
        """可选的直链（MinIO 匿名读 / 本地静态挂载）。"""
        return None


class LocalStorage(Storage):
    """本地文件系统存储，落在 data/_storage 下。"""

    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError(f"illegal storage key: {key}")
        return p

    async def ensure_ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        return key

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def public_url(self, key: str) -> str | None:
        return f"/storage/{key}"


class MinioStorage(Storage):
    """MinIO 对象存储。"""

    backend = "minio"

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool = False) -> None:
        from minio import Minio

        self.bucket = bucket
        self.endpoint = endpoint
        self.secure = secure
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    async def ensure_ready(self) -> None:
        def _ensure() -> None:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)

        await asyncio.to_thread(_ensure)

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        def _put() -> None:
            self._client.put_object(
                self.bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )

        await asyncio.to_thread(_put)
        return key

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            resp = self._client.get_object(self.bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        return await asyncio.to_thread(_get)

    async def public_url(self, key: str) -> str | None:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}/{self.bucket}/{key}"


@functools.lru_cache(maxsize=1)
def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend.lower() == "local":
        return LocalStorage(settings.local_storage_dir)
    try:
        return MinioStorage(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket,
            secure=settings.minio_secure,
        )
    except Exception:  # noqa: BLE001 - 无 MinIO 时退回本地，保证开发可用
        return LocalStorage(settings.local_storage_dir)


def reset_storage() -> None:
    get_storage.cache_clear()
