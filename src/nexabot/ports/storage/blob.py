"""Binary object storage port."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredBlob:
    storage_key: str
    size_bytes: int
    checksum_sha256: str
    content_type: str


class BlobStorage(Protocol):
    async def put(
        self,
        *,
        owner_id: str,
        filename: str,
        content_type: str,
        chunks: AsyncIterator[bytes],
    ) -> StoredBlob:
        """Store binary content and return metadata."""

    async def open(self, storage_key: str) -> AsyncIterator[bytes]:
        """Stream stored content."""

    async def delete(self, storage_key: str) -> None:
        """Delete stored content."""
