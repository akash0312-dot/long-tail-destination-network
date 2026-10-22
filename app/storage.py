"""Files out: S3-compatible storage (SeaweedFS in compose) or local filesystem (tests). Returns download links."""
import json
from pathlib import Path
from typing import Protocol

from app.config import get_settings


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> str: ...
    def get_bytes(self, key: str) -> bytes: ...
    def url_for(self, key: str) -> str: ...
    def ready(self) -> bool: ...


class LocalStorage:
    def __init__(self, root: str, public_base_url: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base_url = public_base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return self.url_for(key)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def url_for(self, key: str) -> str:
        return f"{self.public_base_url}/api/v1/files/{key}"

    def ready(self) -> bool:
        return self.root.exists()


class S3Storage:
    def __init__(self, s):
        import boto3
        from botocore.config import Config

        cfg = Config(s3={"addressing_style": "path"}, signature_version="s3v4", retries={"max_attempts": 3})
        common = dict(aws_access_key_id=s.s3_access_key or "anonymous",
                      aws_secret_access_key=s.s3_secret_key or "anonymous", region_name=s.s3_region, config=cfg)
        self.client = boto3.client("s3", endpoint_url=s.s3_endpoint_url, **common)
        self.public_client = boto3.client("s3", endpoint_url=s.s3_public_endpoint_url or s.s3_endpoint_url, **common)
        self.bucket = s.s3_bucket
        self.ttl = s.download_url_ttl_s
        self.proxy_base = f"{s.public_base_url.rstrip('/')}/api/v1/files" if s.storage_links == "proxy" else None
        self._bucket_ok = False

    def _ensure_bucket(self):
        if self._bucket_ok:
            return
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)
        self._bucket_ok = True

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        self._ensure_bucket()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return self.url_for(key)

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def url_for(self, key: str) -> str:
        if self.proxy_base:
            return f"{self.proxy_base}/{key}"
        return self.public_client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.ttl)

    def ready(self) -> bool:
        try:
            self._ensure_bucket()
            return True
        except Exception:
            return False


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        _storage = S3Storage(s) if s.storage_backend == "s3" else LocalStorage(s.storage_local_dir, s.public_base_url)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None


def put_json(key: str, obj) -> str:
    return get_storage().put_bytes(key, json.dumps(obj, indent=2, default=str).encode(), "application/json")
