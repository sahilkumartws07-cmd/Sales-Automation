from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        dict[str, Any]: dict[str, Any],
        list[dict[str, Any]]: list[dict[str, Any]],
    }


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class IdMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class TableNameMixin:
    @declared_attr.directive
    def __tablename__(cls) -> str:
        name = cls.__name__
        chars: list[str] = []
        for index, char in enumerate(name):
            if char.isupper() and index:
                chars.append("_")
            chars.append(char.lower())
        return "".join(chars) + "s"
