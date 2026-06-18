from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        self.session.flush()
        return entity

    def get(self, entity_id: int) -> ModelT | None:
        return self.session.get(self.model, entity_id)

    def list(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        statement = select(self.model).limit(limit).offset(offset)
        return list(self.session.scalars(statement))

    def latest_for_lead(self, lead_id: int) -> ModelT | None:
        statement = (
            select(self.model)
            .where(self.model.lead_id == lead_id)  # type: ignore[attr-defined]
            .order_by(self.model.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        return self.session.scalar(statement)

    def exists_for_lead(self, lead_id: int) -> bool:
        statement = (
            select(self.model.id)  # type: ignore[attr-defined]
            .where(self.model.lead_id == lead_id)  # type: ignore[attr-defined]
            .limit(1)
        )
        return self.session.scalar(statement) is not None

    def delete(self, entity: ModelT) -> None:
        self.session.delete(entity)
