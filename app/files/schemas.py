from __future__ import annotations

from app.common.schemas.base import ApiSchema


class FileMetadataInput(ApiSchema):
    title: str
    file_category: str
