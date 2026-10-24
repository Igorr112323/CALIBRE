"""Relational schema for the CALIBRE catalogue and its editorial history."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Machine(Base):
    __tablename__ = "machines"
    __table_args__ = (
        UniqueConstraint("code", name="uq_machines_code"),
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_machines_status"),
        CheckConstraint("version >= 0", name="ck_machines_version"),
        Index("ix_machines_publication", "status", "is_demo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(160), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    machine_type: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tractor_requirements: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    documents: Mapped[list["SourceDocument"]] = relationship(back_populates="machine", cascade="all, delete-orphan")
    characteristics: Mapped[list["MachineCharacteristic"]] = relationship(back_populates="machine", cascade="all, delete-orphan")
    nodes: Mapped[list["Node"]] = relationship(back_populates="machine", cascade="all, delete-orphan")
    problems: Mapped[list["Problem"]] = relationship(back_populates="machine", cascade="all, delete-orphan")
    photos: Mapped[list["Photo"]] = relationship(back_populates="machine", cascade="all, delete-orphan")
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(back_populates="machine", cascade="all, delete-orphan")


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (UniqueConstraint("machine_id", "sha256", name="uq_source_machine_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str | None] = mapped_column(String(16))
    page_count: Mapped[int | None] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(String(800), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    machine: Mapped[Machine] = relationship(back_populates="documents")


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("source_document_id", "page_number", name="uq_document_page_number"),
        CheckConstraint("page_number >= 1", name="ck_document_page_number"),
        Index("ix_document_pages_quality", "source_document_id", "text_quality"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    extracted_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(32), default="text", nullable=False)
    text_quality: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    ocr_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    rendered_sha256: Mapped[str | None] = mapped_column(String(64))
    source_document: Mapped[SourceDocument] = relationship()


class MachineCharacteristic(Base):
    __tablename__ = "machine_characteristics"
    __table_args__ = (CheckConstraint("status IN ('draft', 'published')", name="ck_characteristic_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    display_value: Mapped[str] = mapped_column(String(240), nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id", ondelete="SET NULL"))
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_quote: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    machine: Mapped[Machine] = relationship(back_populates="characteristics")


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        UniqueConstraint("machine_id", "number", name="uq_node_number_per_machine"),
        CheckConstraint("status IN ('ai_draft', 'review', 'verified', 'published', 'rejected')", name="ck_nodes_status"),
        CheckConstraint("map_x IS NULL OR (map_x >= 0 AND map_x <= 1)", name="ck_nodes_map_x"),
        CheckConstraint("map_y IS NULL OR (map_y >= 0 AND map_y <= 1)", name="ck_nodes_map_y"),
        Index("ix_nodes_status", "machine_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ai_draft", nullable=False)
    map_x: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    map_y: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    machine: Mapped[Machine] = relationship(back_populates="nodes")
    adjustments: Mapped[list["Adjustment"]] = relationship(back_populates="node", cascade="all, delete-orphan")
    mistakes: Mapped[list["CommonMistake"]] = relationship(back_populates="node", cascade="all, delete-orphan")
    photos: Mapped[list["Photo"]] = relationship(back_populates="node")
    reviews: Mapped[list["DepartmentReview"]] = relationship(back_populates="node", cascade="all, delete-orphan")
    related_links: Mapped[list["NodeLink"]] = relationship(
        foreign_keys="NodeLink.node_id", back_populates="node", cascade="all, delete-orphan"
    )


class NodeLink(Base):
    __tablename__ = "node_links"
    __table_args__ = (
        UniqueConstraint("node_id", "related_node_id", name="uq_node_link_pair"),
        CheckConstraint("node_id != related_node_id", name="ck_node_link_not_self"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    related_node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    relation_label: Mapped[str] = mapped_column(String(160), default="Связанный узел", nullable=False)
    node: Mapped[Node] = relationship(foreign_keys=[node_id], back_populates="related_links")
    related_node: Mapped[Node] = relationship(foreign_keys=[related_node_id])


class Adjustment(Base):
    __tablename__ = "adjustments"
    __table_args__ = (
        CheckConstraint("status IN ('ai_draft', 'review', 'verified', 'published', 'rejected')", name="ck_adjustments_status"),
        Index("ix_adjustments_status", "node_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    instructions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    tool: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ai_draft", nullable=False)
    node: Mapped[Node] = relationship(back_populates="adjustments")
    norms: Mapped[list["Norm"]] = relationship(back_populates="adjustment", cascade="all, delete-orphan")
    reviews: Mapped[list["DepartmentReview"]] = relationship(back_populates="adjustment", cascade="all, delete-orphan")


class Norm(Base):
    __tablename__ = "norms"
    __table_args__ = (
        CheckConstraint("source_kind IN ('document', 'department_measurement')", name="ck_norm_source_kind"),
        CheckConstraint("review_status IN ('unverified', 'verified', 'rejected', 'forced_accept')", name="ck_norm_review_status"),
        CheckConstraint(
            "(source_kind = 'document' AND source_document_id IS NOT NULL AND source_page_start IS NOT NULL AND source_page_start >= 1 AND length(trim(source_quote)) > 0) OR "
            "(source_kind = 'department_measurement' AND length(trim(measured_by)) > 0 AND measured_at IS NOT NULL)",
            name="ck_norm_has_source",
        ),
        CheckConstraint("review_status != 'forced_accept' OR length(trim(force_comment)) > 0", name="ck_norm_force_comment"),
        CheckConstraint("review_status != 'rejected' OR length(trim(rejection_reason)) > 0", name="ck_norm_rejection_reason"),
        CheckConstraint("source_page_end IS NULL OR (source_page_start IS NOT NULL AND source_page_end >= source_page_start)", name="ck_norm_source_page_range"),
        CheckConstraint("minimum_value IS NULL OR maximum_value IS NULL OR minimum_value <= maximum_value", name="ck_norm_min_max"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    adjustment_id: Mapped[int] = mapped_column(ForeignKey("adjustments.id", ondelete="CASCADE"), nullable=False, index=True)
    value_original: Mapped[str] = mapped_column(String(240), nullable=False)
    unit_original: Mapped[str | None] = mapped_column(String(80))
    source_kind: Mapped[str] = mapped_column(String(32), default="document", nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id", ondelete="RESTRICT"))
    source_page_start: Mapped[int | None] = mapped_column(Integer)
    source_page_end: Mapped[int | None] = mapped_column(Integer)
    source_quote: Mapped[str | None] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    minimum_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    maximum_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    tolerance_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    review_status: Mapped[str] = mapped_column(String(24), default="unverified", nullable=False)
    measured_by: Mapped[str | None] = mapped_column(String(240))
    measured_at: Mapped[date | None] = mapped_column(Date)
    force_comment: Mapped[str | None] = mapped_column(Text)
    adjustment: Mapped[Adjustment] = relationship(back_populates="norms")
    source_document: Mapped[SourceDocument | None] = relationship()


class CommonMistake(Base):
    __tablename__ = "common_mistakes"

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    consequence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    node: Mapped[Node] = relationship(back_populates="mistakes")


class Problem(Base):
    __tablename__ = "problems"
    __table_args__ = (
        CheckConstraint("status IN ('ai_draft', 'review', 'verified', 'published', 'rejected')", name="ck_problems_status"),
        Index("ix_problems_status", "machine_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(320), nullable=False)
    symptom: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ai_draft", nullable=False)
    machine: Mapped[Machine] = relationship(back_populates="problems")
    steps: Mapped[list["ProblemStep"]] = relationship(back_populates="problem", cascade="all, delete-orphan", order_by="ProblemStep.step_number")


class ProblemStep(Base):
    __tablename__ = "problem_steps"
    __table_args__ = (UniqueConstraint("problem_id", "step_number", name="uq_problem_step_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    linked_node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id", ondelete="SET NULL"))
    problem: Mapped[Problem] = relationship(back_populates="steps")
    linked_node: Mapped[Node | None] = relationship()


class Photo(Base):
    __tablename__ = "photos"
    __table_args__ = (
        CheckConstraint("usage_rights IN ('own', 'manufacturer_permission', 'internal_only')", name="ck_photos_rights"),
        CheckConstraint("photo_kind IN ('node', 'machine_scheme', 'candidate')", name="ck_photos_kind"),
        CheckConstraint("NOT (usage_rights = 'internal_only' AND published = 1)", name="ck_internal_photo_not_published"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id", ondelete="SET NULL"), index=True)
    storage_path: Mapped[str] = mapped_column(String(800), nullable=False)
    caption: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    photo_kind: Mapped[str] = mapped_column(String(32), default="node", nullable=False)
    usage_rights: Mapped[str] = mapped_column(String(32), default="internal_only", nullable=False)
    allow_training: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    machine: Mapped[Machine] = relationship(back_populates="photos")
    node: Mapped[Node | None] = relationship(back_populates="photos")


class Synonym(Base):
    __tablename__ = "synonyms"
    __table_args__ = (UniqueConstraint("term", "synonym", name="uq_synonym_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    synonym: Mapped[str] = mapped_column(String(120), nullable=False)


class DepartmentReview(Base):
    __tablename__ = "department_reviews"
    __table_args__ = (
        CheckConstraint("verdict IN ('confirmed', 'corrected', 'rejected')", name="ck_review_verdict"),
        CheckConstraint("(node_id IS NOT NULL AND adjustment_id IS NULL) OR (node_id IS NULL AND adjustment_id IS NOT NULL)", name="ck_review_target"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    adjustment_id: Mapped[int | None] = mapped_column(ForeignKey("adjustments.id", ondelete="CASCADE"), index=True)
    expert_name: Mapped[str] = mapped_column(String(240), nullable=False)
    reviewed_at: Mapped[date] = mapped_column(Date, nullable=False)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    node: Mapped[Node | None] = relationship(back_populates="reviews")
    adjustment: Mapped[Adjustment | None] = relationship(back_populates="reviews")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity_time", "entity_type", "entity_id", "changed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    author_name: Mapped[str | None] = mapped_column(String(240))
    before_data: Mapped[dict | None] = mapped_column(JSON)
    after_data: Mapped[dict | None] = mapped_column(JSON)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("client_event_id", name="uq_feedback_client_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_event_id: Mapped[str | None] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    helpful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class ExpertQuestion(Base):
    __tablename__ = "expert_questions"
    __table_args__ = (
        CheckConstraint("status IN ('new', 'in_progress', 'answered', 'closed')", name="ck_question_status"),
        UniqueConstraint("client_event_id", name="uq_question_client_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_event_id: Mapped[str | None] = mapped_column(String(80))
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id", ondelete="SET NULL"), index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="new", nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id", ondelete="SET NULL"), index=True)
    model_name: Mapped[str | None] = mapped_column(String(160))
    prompt_version: Mapped[str | None] = mapped_column(String(80))
    stage: Mapped[str] = mapped_column(String(80), default="queued", nullable=False)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    raw_response: Mapped[dict | list | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    machine: Mapped[Machine] = relationship(back_populates="analysis_runs")


class Gap(Base):
    __tablename__ = "gaps"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id", ondelete="SET NULL"), index=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
