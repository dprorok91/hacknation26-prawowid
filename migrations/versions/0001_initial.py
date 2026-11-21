"""Create the baseline durable analysis, source, and job schema."""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    op.execute(
        """
        CREATE TABLE analyses (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            input_hash VARCHAR(64) NOT NULL,
            status VARCHAR(19) DEFAULT 'queued' NOT NULL,
            input_type VARCHAR(4) NOT NULL,
            input_redacted TEXT,
            pipeline_version TEXT NOT NULL,
            corpus_snapshot TEXT,
            cache_key VARCHAR(64),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            finished_at TIMESTAMP WITH TIME ZONE,
            expires_at TIMESTAMP WITH TIME ZONE,
            error_code TEXT,
            response_json JSONB,
            PRIMARY KEY (id),
            CONSTRAINT analysis_url_cache_only CHECK (cache_key IS NULL OR input_type = 'url'),
            CONSTRAINT analysis_status CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'needs_clarification')),
            CONSTRAINT analysis_input_type CHECK (input_type IN ('url', 'text')),
            UNIQUE (cache_key)
        )
        """
    )
    op.execute("CREATE INDEX ix_analyses_expires_at ON analyses (expires_at)")
    op.execute("CREATE INDEX ix_analyses_input_pipeline ON analyses (input_hash, pipeline_version)")
    op.execute(
        """
        CREATE TABLE events (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            event_key TEXT NOT NULL,
            label TEXT NOT NULL,
            start_at TIMESTAMP WITH TIME ZONE,
            end_at TIMESTAMP WITH TIME ZONE,
            language VARCHAR(16) DEFAULT 'pl' NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (event_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE jobs (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            kind TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            generation INTEGER DEFAULT '0' NOT NULL,
            state VARCHAR(9) DEFAULT 'queued' NOT NULL,
            priority INTEGER DEFAULT '100' NOT NULL,
            attempts INTEGER DEFAULT '0' NOT NULL,
            lease_owner TEXT,
            lease_until TIMESTAMP WITH TIME ZONE,
            run_after TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            payload_json JSONB NOT NULL,
            error_code TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (kind, dedupe_key, generation),
            CONSTRAINT job_nonnegative_counters CHECK (generation >= 0 AND attempts >= 0),
            CONSTRAINT live_job_lease_required CHECK (state != 'leased' OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL)),
            CONSTRAINT job_state CHECK (state IN ('queued', 'leased', 'completed', 'dead'))
        )
        """
    )
    op.execute("CREATE INDEX ix_jobs_state_run_after_priority ON jobs (state, run_after, priority)")
    op.execute(
        """
        CREATE TABLE sources (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            domain TEXT NOT NULL,
            display_name TEXT NOT NULL,
            source_type VARCHAR(9) NOT NULL,
            policy_url TEXT,
            license_label TEXT,
            enabled BOOLEAN DEFAULT false NOT NULL,
            approved_at TIMESTAMP WITH TIME ZONE,
            PRIMARY KEY (id),
            UNIQUE (domain),
            CONSTRAINT source_type CHECK (source_type IN ('news', 'primary', 'factcheck'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE audit_events (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            analysis_id UUID,
            event_type TEXT NOT NULL,
            at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            source_ids_json JSONB,
            model_name TEXT,
            latency_ms INTEGER,
            tokens_in INTEGER,
            tokens_out INTEGER,
            estimated_cost_usd NUMERIC(14, 8),
            redacted_metadata_json JSONB,
            PRIMARY KEY (id),
            FOREIGN KEY(analysis_id) REFERENCES analyses (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE claims (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            analysis_id UUID NOT NULL,
            text TEXT NOT NULL,
            kind VARCHAR(17) NOT NULL,
            verdict VARCHAR(21),
            rationale TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(analysis_id) REFERENCES analyses (id) ON DELETE CASCADE,
            CONSTRAINT claim_kind CHECK (kind IN ('verifiable', 'opinion', 'non_claim', 'unsupported_scope')),
            CONSTRAINT claim_verdict CHECK (verdict IN ('confirmed', 'false', 'misleading', 'insufficient_evidence', 'not_verifiable'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE documents (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            source_id UUID NOT NULL,
            canonical_url TEXT NOT NULL,
            url_hash VARCHAR(64) NOT NULL,
            title TEXT NOT NULL,
            published_at TIMESTAMP WITH TIME ZONE,
            fetched_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            content_hash VARCHAR(64),
            content_version INTEGER DEFAULT '1' NOT NULL,
            text_license TEXT,
            retrieval_status TEXT DEFAULT 'metadata_only' NOT NULL,
            original_language VARCHAR(16) DEFAULT 'pl' NOT NULL,
            indexed_at TIMESTAMP WITH TIME ZONE,
            expires_at TIMESTAMP WITH TIME ZONE,
            PRIMARY KEY (id),
            CONSTRAINT documents_positive_version CHECK (content_version >= 1),
            FOREIGN KEY(source_id) REFERENCES sources (id),
            UNIQUE (canonical_url)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_documents_source_published ON documents (source_id, published_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_documents_title_fts ON documents USING gin (to_tsvector('simple', title))"
    )
    op.execute(
        """
        CREATE TABLE document_versions (
            document_id UUID NOT NULL,
            content_version INTEGER NOT NULL,
            content_hash VARCHAR(64),
            title TEXT NOT NULL,
            published_at TIMESTAMP WITH TIME ZONE,
            fetched_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            text_license TEXT,
            retrieval_status TEXT NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE,
            PRIMARY KEY (document_id, content_version),
            CONSTRAINT document_versions_positive_version CHECK (content_version >= 1),
            FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE event_documents (
            event_id UUID NOT NULL,
            document_id UUID NOT NULL,
            match_score FLOAT NOT NULL,
            event_match_reason TEXT NOT NULL,
            PRIMARY KEY (event_id, document_id),
            FOREIGN KEY(event_id) REFERENCES events (id) ON DELETE CASCADE,
            FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE narrative_frames (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            analysis_id UUID NOT NULL,
            document_id UUID NOT NULL,
            emphasis_json JSONB NOT NULL,
            omission_comparison_json JSONB,
            highlighted_passage_ids JSONB NOT NULL,
            uncertainty_text TEXT,
            PRIMARY KEY (id),
            UNIQUE (analysis_id, document_id),
            FOREIGN KEY(analysis_id) REFERENCES analyses (id) ON DELETE CASCADE,
            FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE passages (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            document_id UUID NOT NULL,
            content_version INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            text TEXT NOT NULL,
            source_span_hash VARCHAR(64) NOT NULL,
            embedding VECTOR(384),
            embedding_model_id TEXT,
            PRIMARY KEY (id),
            FOREIGN KEY(document_id, content_version) REFERENCES document_versions (document_id, content_version) ON DELETE CASCADE,
            UNIQUE (document_id, content_version, ordinal),
            CONSTRAINT passage_valid_offsets CHECK (ordinal >= 0 AND char_start >= 0 AND char_end > char_start),
            CONSTRAINT passage_embedding_has_model CHECK ((embedding IS NULL) = (embedding_model_id IS NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_passages_text_fts ON passages USING gin (to_tsvector('simple', coalesce(text, '')))"
    )
    op.execute(
        """
        CREATE TABLE evidence (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            claim_id UUID NOT NULL,
            passage_id UUID NOT NULL,
            relation VARCHAR(8) NOT NULL,
            exact_quote TEXT NOT NULL,
            validation_passed BOOLEAN DEFAULT false NOT NULL,
            checked_at TIMESTAMP WITH TIME ZONE,
            PRIMARY KEY (id),
            FOREIGN KEY(claim_id) REFERENCES claims (id) ON DELETE CASCADE,
            FOREIGN KEY(passage_id) REFERENCES passages (id) ON DELETE CASCADE,
            CONSTRAINT evidence_relation CHECK (relation IN ('supports', 'refutes', 'context'))
        )
        """
    )


def downgrade() -> None:
    op.drop_table("evidence")
    op.drop_table("passages")
    op.drop_table("narrative_frames")
    op.drop_table("event_documents")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("claims")
    op.drop_table("audit_events")
    op.drop_table("sources")
    op.drop_table("jobs")
    op.drop_table("events")
    op.drop_table("analyses")
    # The extension can already exist and be shared with other schemas.
