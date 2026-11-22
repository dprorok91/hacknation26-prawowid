"""PostgreSQL tables used by the persistence adapters."""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()


def _id():
    return sa.Column(
        "id", sa.Uuid(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _fk(name, target, *, nullable=False, ondelete=None):
    return sa.Column(
        name, sa.Uuid(as_uuid=False), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable
    )


def _at(name, *, default=False, nullable=True):
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.func.now() if default else None,
        nullable=nullable,
    )


def _choice(name, *values):
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


sources = sa.Table(
    "sources",
    metadata,
    _id(),
    sa.Column("domain", sa.Text, nullable=False, unique=True),
    sa.Column("display_name", sa.Text, nullable=False),
    sa.Column(
        "source_type", _choice("source_type", "news", "primary", "factcheck"), nullable=False
    ),
    sa.Column("policy_url", sa.Text),
    sa.Column("license_label", sa.Text),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    _at("approved_at"),
)
documents = sa.Table(
    "documents",
    metadata,
    _id(),
    _fk("source_id", "sources.id"),
    sa.Column("canonical_url", sa.Text, nullable=False, unique=True),
    sa.Column("url_hash", sa.String(64), nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    _at("published_at"),
    _at("fetched_at", default=True, nullable=False),
    sa.Column("content_hash", sa.String(64)),
    sa.Column("content_version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("text_license", sa.Text),
    sa.Column("retrieval_status", sa.Text, nullable=False, server_default="metadata_only"),
    sa.Column("original_language", sa.String(16), nullable=False, server_default="pl"),
    _at("indexed_at"),
    _at("expires_at"),
    sa.CheckConstraint("content_version >= 1", name="documents_positive_version"),
)
document_versions = sa.Table(
    "document_versions",
    metadata,
    _fk("document_id", "documents.id", ondelete="CASCADE"),
    sa.Column("content_version", sa.Integer, nullable=False),
    sa.Column("content_hash", sa.String(64)),
    sa.Column("title", sa.Text, nullable=False),
    _at("published_at"),
    _at("fetched_at", default=True, nullable=False),
    sa.Column("text_license", sa.Text),
    sa.Column("retrieval_status", sa.Text, nullable=False),
    _at("expires_at"),
    sa.PrimaryKeyConstraint("document_id", "content_version"),
    sa.CheckConstraint("content_version >= 1", name="document_versions_positive_version"),
)
passages = sa.Table(
    "passages",
    metadata,
    _id(),
    sa.Column("document_id", sa.Uuid(as_uuid=False), nullable=False),
    sa.Column("content_version", sa.Integer, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("char_start", sa.Integer, nullable=False),
    sa.Column("char_end", sa.Integer, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("source_span_hash", sa.String(64), nullable=False),
    sa.Column("embedding", Vector(384)),
    sa.Column("embedding_model_id", sa.Text),
    sa.ForeignKeyConstraint(
        ["document_id", "content_version"],
        ["document_versions.document_id", "document_versions.content_version"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("document_id", "content_version", "ordinal"),
    sa.CheckConstraint(
        "ordinal >= 0 AND char_start >= 0 AND char_end > char_start", name="passage_valid_offsets"
    ),
    sa.CheckConstraint(
        "(embedding IS NULL) = (embedding_model_id IS NULL)", name="passage_embedding_has_model"
    ),
)
events = sa.Table(
    "events",
    metadata,
    _id(),
    sa.Column("event_key", sa.Text, nullable=False, unique=True),
    sa.Column("label", sa.Text, nullable=False),
    _at("start_at"),
    _at("end_at"),
    sa.Column("language", sa.String(16), nullable=False, server_default="pl"),
    _at("created_at", default=True, nullable=False),
)
event_documents = sa.Table(
    "event_documents",
    metadata,
    _fk("event_id", "events.id", ondelete="CASCADE"),
    _fk("document_id", "documents.id", ondelete="CASCADE"),
    sa.Column("match_score", sa.Float, nullable=False),
    sa.Column("event_match_reason", sa.Text, nullable=False),
    sa.PrimaryKeyConstraint("event_id", "document_id"),
)
analyses = sa.Table(
    "analyses",
    metadata,
    _id(),
    sa.Column("input_hash", sa.String(64), nullable=False),
    sa.Column(
        "status",
        _choice(
            "analysis_status", "queued", "processing", "completed", "failed", "needs_clarification"
        ),
        nullable=False,
        server_default="queued",
    ),
    sa.Column("input_type", _choice("analysis_input_type", "url", "text"), nullable=False),
    sa.Column("input_redacted", sa.Text),
    sa.Column("pipeline_version", sa.Text, nullable=False),
    sa.Column("corpus_snapshot", sa.Text),
    sa.Column("cache_key", sa.String(64), unique=True),
    sa.Column("delete_token_hash", sa.String(64)),
    _at("created_at", default=True, nullable=False),
    _at("updated_at", default=True, nullable=False),
    _at("finished_at"),
    _at("expires_at"),
    sa.Column("error_code", sa.Text),
    sa.Column("response_json", JSONB),
    sa.CheckConstraint("cache_key IS NULL OR input_type = 'url'", name="analysis_url_cache_only"),
)
claims = sa.Table(
    "claims",
    metadata,
    _id(),
    _fk("analysis_id", "analyses.id", ondelete="CASCADE"),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column(
        "kind",
        _choice("claim_kind", "verifiable", "opinion", "non_claim", "unsupported_scope"),
        nullable=False,
    ),
    sa.Column(
        "verdict",
        _choice(
            "claim_verdict",
            "confirmed",
            "false",
            "misleading",
            "insufficient_evidence",
            "not_verifiable",
        ),
    ),
    sa.Column("rationale", sa.Text),
    _at("created_at", default=True, nullable=False),
)
evidence = sa.Table(
    "evidence",
    metadata,
    _id(),
    _fk("claim_id", "claims.id", ondelete="CASCADE"),
    _fk("passage_id", "passages.id", ondelete="CASCADE"),
    sa.Column(
        "relation", _choice("evidence_relation", "supports", "refutes", "context"), nullable=False
    ),
    sa.Column("exact_quote", sa.Text, nullable=False),
    sa.Column("validation_passed", sa.Boolean, nullable=False, server_default=sa.false()),
    _at("checked_at"),
)
narrative_frames = sa.Table(
    "narrative_frames",
    metadata,
    _id(),
    _fk("analysis_id", "analyses.id", ondelete="CASCADE"),
    _fk("document_id", "documents.id", ondelete="CASCADE"),
    sa.Column("emphasis_json", JSONB, nullable=False),
    sa.Column("omission_comparison_json", JSONB),
    sa.Column("highlighted_passage_ids", JSONB, nullable=False),
    sa.Column("uncertainty_text", sa.Text),
    sa.UniqueConstraint("analysis_id", "document_id"),
)
jobs = sa.Table(
    "jobs",
    metadata,
    _id(),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("dedupe_key", sa.Text, nullable=False),
    sa.Column("generation", sa.Integer, nullable=False, server_default="0"),
    sa.Column(
        "state",
        _choice("job_state", "queued", "leased", "completed", "dead"),
        nullable=False,
        server_default="queued",
    ),
    sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("lease_owner", sa.Text),
    _at("lease_until"),
    _at("run_after", default=True, nullable=False),
    sa.Column("payload_json", JSONB, nullable=False),
    sa.Column("error_code", sa.Text),
    _at("created_at", default=True, nullable=False),
    _at("updated_at", default=True, nullable=False),
    sa.UniqueConstraint("kind", "dedupe_key", "generation"),
    sa.CheckConstraint("generation >= 0 AND attempts >= 0", name="job_nonnegative_counters"),
    sa.CheckConstraint(
        "state != 'leased' OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
        name="live_job_lease_required",
    ),
)
audit_events = sa.Table(
    "audit_events",
    metadata,
    _id(),
    _fk("analysis_id", "analyses.id", nullable=True, ondelete="SET NULL"),
    sa.Column("event_type", sa.Text, nullable=False),
    _at("at", default=True, nullable=False),
    sa.Column("source_ids_json", JSONB),
    sa.Column("model_name", sa.Text),
    sa.Column("latency_ms", sa.Integer),
    sa.Column("tokens_in", sa.Integer),
    sa.Column("tokens_out", sa.Integer),
    sa.Column("estimated_cost_usd", sa.Numeric(14, 8)),
    sa.Column("redacted_metadata_json", JSONB),
)

discovery_cache = sa.Table(
    "discovery_cache",
    metadata,
    sa.Column("cache_key", sa.String(64), primary_key=True),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("results_json", JSONB, nullable=False),
    _at("created_at", default=True, nullable=False),
    _at("expires_at", nullable=False),
)

provider_daily_usage = sa.Table(
    "provider_daily_usage",
    metadata,
    sa.Column("provider", sa.Text, primary_key=True),
    sa.Column("day_utc", sa.Date, primary_key=True),
    sa.Column("credits_used", sa.Integer, nullable=False, server_default="0"),
    sa.CheckConstraint("credits_used >= 0", name="provider_usage_nonnegative"),
)

sa.Index("ix_discovery_cache_expires_at", discovery_cache.c.expires_at)

sa.Index("ix_documents_source_published", documents.c.source_id, documents.c.published_at.desc())
sa.Index("ix_jobs_state_run_after_priority", jobs.c.state, jobs.c.run_after, jobs.c.priority)
sa.Index("ix_analyses_input_pipeline", analyses.c.input_hash, analyses.c.pipeline_version)
sa.Index("ix_analyses_expires_at", analyses.c.expires_at)
sa.Index(
    "ix_passages_text_fts",
    sa.text("to_tsvector('simple', coalesce(text, ''))"),
    postgresql_using="gin",
    _table=passages,
)
sa.Index(
    "ix_documents_title_fts",
    sa.text("to_tsvector('simple', title)"),
    postgresql_using="gin",
    _table=documents,
)
