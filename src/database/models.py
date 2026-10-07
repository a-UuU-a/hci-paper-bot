from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PaperRecord(Base):
    __tablename__ = "papers"
    __table_args__ = (
        CheckConstraint("year between 1900 and 2200", name="papers_year_check"),
        CheckConstraint("citation_count >= 0", name="papers_citation_count_check"),
        Index("papers_venue_year_idx", "venue", "year"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list, server_default=text("'[]'")
    )
    abstract: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str] = mapped_column(Text)
    year: Mapped[int] = mapped_column(Integer)
    doi: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    citation_count: Mapped[int | None] = mapped_column(Integer)
    topics: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list, server_default=text("'[]'")
    )
    source: Mapped[str] = mapped_column(Text, default="dblp", server_default="dblp")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    recommendations: Mapped[list["RecommendationRecord"]] = relationship(back_populates="paper")


class RecommendationRecord(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        CheckConstraint("status in ('sent', 'failed')", name="recommendations_status_check"),
        CheckConstraint(
            "(status = 'sent' and sent_at is not null) or (status = 'failed' and sent_at is null)",
            name="recommendations_sent_at_check",
        ),
        Index(
            "recommendations_sent_idx",
            "paper_id",
            postgresql_where=text("status = 'sent'"),
            sqlite_where=text("status = 'sent'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    paper_id: Mapped[str] = mapped_column(Text, ForeignKey("papers.id"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    channel: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paper: Mapped[PaperRecord] = relationship(back_populates="recommendations")
