from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class StoredFile:
    storage_key: str
    file_url: str
    size: int
    mime_type: str


class StorageProvider(Protocol):
    def save(self, namespace: str, filename: str, content: bytes, content_type: str) -> StoredFile: ...

    def open_bytes(self, storage_key: str) -> bytes: ...
