from __future__ import annotations

from io import BytesIO
from urllib.parse import urlparse
import uuid

import boto3
from botocore.config import Config
from werkzeug.utils import secure_filename

from app.common.storage.base import StoredFile


class B2StorageProvider:
    def __init__(
        self,
        *,
        endpoint_url: str,
        region_name: str,
        bucket_name: str,
        key_id: str,
        app_key: str,
        public_base_url: str,
        key_prefix: str = "",
    ) -> None:
        self.endpoint_url = endpoint_url
        self.region_name = region_name or self._infer_region_from_endpoint(endpoint_url)
        self.bucket_name = bucket_name
        self.public_base_url = public_base_url.rstrip("/")
        self.key_prefix = key_prefix.strip("/")
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=key_id,
            aws_secret_access_key=app_key,
            region_name=self.region_name,
            config=Config(signature_version="s3v4"),
        )

    @staticmethod
    def _infer_region_from_endpoint(endpoint_url: str) -> str:
        host = urlparse(endpoint_url).hostname or ""
        parts = host.split(".")
        if len(parts) >= 3 and parts[0] == "s3":
            return parts[1]
        return "us-east-005"

    def _qualify_key(self, namespace: str, filename: str) -> str:
        clean_name = secure_filename(filename) or "file"
        relative_key = f"{namespace.strip('/')}/{uuid.uuid4()}-{clean_name}".strip("/")
        if not self.key_prefix:
            return relative_key
        return f"{self.key_prefix}/{relative_key}"

    def save(self, namespace: str, filename: str, content: bytes, content_type: str) -> StoredFile:
        storage_key = self._qualify_key(namespace, filename)
        self.s3_client.upload_fileobj(
            BytesIO(content),
            self.bucket_name,
            storage_key,
            ExtraArgs={"ContentType": content_type},
        )
        return StoredFile(
            storage_key=storage_key,
            file_url=f"{self.public_base_url}/api/v1/system/storage/{storage_key}",
            size=len(content),
            mime_type=content_type,
        )

    def open_bytes(self, storage_key: str) -> bytes:
        response = self.s3_client.get_object(Bucket=self.bucket_name, Key=storage_key)
        return response["Body"].read()
