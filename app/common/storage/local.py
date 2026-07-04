from __future__ import annotations

import os
import uuid
from pathlib import Path

from werkzeug.utils import secure_filename

from app.common.storage.base import StorageProvider, StoredFile


class LocalStorageProvider(StorageProvider):
    def __init__(self, root: str, public_base_url: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base_url = public_base_url.rstrip("/")

    def save(self, namespace: str, filename: str, content: bytes, content_type: str) -> StoredFile:
        clean_name = secure_filename(filename) or "file"
        storage_key = f"{namespace}/{uuid.uuid4()}-{clean_name}"
        file_path = self.root / storage_key
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        return StoredFile(
            storage_key=storage_key,
            file_url=f"{self.public_base_url}/api/v1/system/storage/{storage_key}",
            size=len(content),
            mime_type=content_type,
        )

    def open_bytes(self, storage_key: str) -> bytes:
        file_path = self.root / storage_key
        return file_path.read_bytes()
