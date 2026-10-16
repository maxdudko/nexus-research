"""Research-scoped source identity and indexing state."""

import enum
import re
import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from nexus.models.base import Base

CONTENT_HASH_LENGTH = 64
URI_MAX_LENGTH = 2048
DISPLAY_NAME_MAX_LENGTH = 300
PDF_URI_PREFIX = "pdf:"
CONTENT_HASH = re.compile(r"^[0-9a-f]{64}$")
ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


class SourceType(StrEnum):
    """Where the source bytes came from."""

    WEB = "WEB"
    PDF = "PDF"


class SourceStatus(StrEnum):
    """Indexing lifecycle. INDEXED means the last Qdrant upsert succeeded."""

    PENDING = "PENDING"
    INDEXING = "INDEXING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Persist enum values. SQLAlchemy otherwise stores the member names."""
    return [str(member.value) for member in enum_cls]


class Source(Base):
    """One document inside a research scope.

    ``content_hash`` is the last digest whose vectors were accepted. For a PDF
    it is also the identity, so a claim stores it before the attempt succeeds.
    ``indexing_content_hash`` is only the attempt in progress. Web identity is
    ``(research_id, uri)``. PDF identity is ``(research_id, content_hash)`` and
    ``uri`` is ``pdf:{content_hash}``, never a filesystem path.
    """

    __tablename__ = "sources"
    __table_args__ = (
        Index(
            "uq_sources_web_identity",
            "research_id",
            "uri",
            unique=True,
            postgresql_where=text("type = 'WEB'"),
        ),
        Index(
            "uq_sources_pdf_identity",
            "research_id",
            "content_hash",
            unique=True,
            postgresql_where=text("type = 'PDF'"),
        ),
        CheckConstraint(
            "chunk_count >= 0",
            name="ck_sources_chunk_count_nonnegative",
        ),
        CheckConstraint(
            "status <> 'INDEXED' OR content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sources_indexed_hash",
        ),
        CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sources_content_hash_hex",
        ),
        CheckConstraint(
            "indexing_content_hash IS NULL OR indexing_content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sources_indexing_hash_hex",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$'",
            name="ck_sources_error_code",
        ),
        CheckConstraint(
            "type <> 'PDF' OR uri = 'pdf:' || content_hash",
            name="ck_sources_pdf_uri",
        ),
        CheckConstraint(
            "type <> 'WEB' OR uri NOT LIKE 'pdf:%'",
            name="ck_sources_web_uri",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    research_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    type: Mapped[SourceType] = mapped_column(
        Enum(
            SourceType,
            name="source_type",
            native_enum=True,
            values_callable=_enum_values,
        ),
    )
    uri: Mapped[str] = mapped_column(String(URI_MAX_LENGTH))
    display_name: Mapped[str | None] = mapped_column(String(DISPLAY_NAME_MAX_LENGTH))
    content_hash: Mapped[str | None] = mapped_column(String(CONTENT_HASH_LENGTH))
    indexing_content_hash: Mapped[str | None] = mapped_column(
        String(CONTENT_HASH_LENGTH),
    )
    indexing_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[SourceStatus] = mapped_column(
        Enum(
            SourceStatus,
            name="source_status",
            native_enum=True,
            values_callable=_enum_values,
        ),
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
