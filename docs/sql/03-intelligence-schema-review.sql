SET SESSION default_storage_engine=InnoDB;

SET NAMES utf8mb4;

-- Running upgrade 0001_core -> 0002_intelligence

CREATE TABLE knowledge_indexes (
    generation VARCHAR(32) NOT NULL, 
    manifest_hash VARCHAR(64) NOT NULL, 
    active BOOL NOT NULL, 
    chunk_count INTEGER NOT NULL, 
    model_revision VARCHAR(100) NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_knowledge_indexes PRIMARY KEY (id), 
    CONSTRAINT uq_knowledge_indexes_generation UNIQUE (generation)
);

CREATE TABLE knowledge_documents (
    document_key VARCHAR(100) NOT NULL, 
    revision INTEGER NOT NULL, 
    title VARCHAR(200) NOT NULL, 
    content TEXT NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    source VARCHAR(200) NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    valid_from DATETIME(6) NOT NULL, 
    valid_until DATETIME(6), 
    author_id BIGINT NOT NULL, 
    reviewer_id BIGINT, 
    review_note TEXT, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    version INTEGER NOT NULL, 
    CONSTRAINT pk_knowledge_documents PRIMARY KEY (id), 
    CONSTRAINT ck_knowledge_documents_status CHECK (status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')), 
    CONSTRAINT ck_knowledge_documents_revision CHECK (revision > 0), 
    CONSTRAINT fk_knowledge_documents_author_id_users FOREIGN KEY(author_id) REFERENCES users (id), 
    CONSTRAINT fk_knowledge_documents_reviewer_id_users FOREIGN KEY(reviewer_id) REFERENCES users (id), 
    CONSTRAINT uq_knowledge_documents_document_key UNIQUE (document_key, revision)
);

CREATE INDEX ix_knowledge_status_valid ON knowledge_documents (status, valid_from, valid_until);

CREATE TABLE knowledge_chunks (
    document_id BIGINT NOT NULL, 
    position INTEGER NOT NULL, 
    text TEXT NOT NULL, 
    text_hash VARCHAR(64) NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_knowledge_chunks PRIMARY KEY (id), 
    CONSTRAINT fk_knowledge_chunks_document_id_knowledge_documents FOREIGN KEY(document_id) REFERENCES knowledge_documents (id), 
    CONSTRAINT uq_knowledge_chunks_document_id UNIQUE (document_id, position)
);

CREATE TABLE ai_invocations (
    ticket_id BIGINT NOT NULL, 
    actor_id BIGINT NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    model VARCHAR(100) NOT NULL, 
    `usage` JSON NOT NULL, 
    error_code VARCHAR(50), 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_ai_invocations PRIMARY KEY (id), 
    CONSTRAINT fk_ai_invocations_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id), 
    CONSTRAINT fk_ai_invocations_ticket_id_tickets FOREIGN KEY(ticket_id) REFERENCES tickets (id)
);

CREATE TABLE model_analyses (
    ticket_id BIGINT NOT NULL, 
    input_version INTEGER NOT NULL, 
    model_version VARCHAR(100) NOT NULL, 
    result JSON NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_model_analyses PRIMARY KEY (id), 
    CONSTRAINT fk_model_analyses_ticket_id_tickets FOREIGN KEY(ticket_id) REFERENCES tickets (id), 
    CONSTRAINT uq_model_analyses_ticket_id UNIQUE (ticket_id, input_version)
);

CREATE TABLE ai_suggestions (
    ticket_id BIGINT NOT NULL, 
    actor_id BIGINT NOT NULL, 
    input_version INTEGER NOT NULL, 
    context_hash VARCHAR(64) NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    body TEXT NOT NULL, 
    raw_output JSON NOT NULL, 
    evidence JSON NOT NULL, 
    facts JSON NOT NULL, 
    model_version VARCHAR(100) NOT NULL, 
    prompt_version VARCHAR(50) NOT NULL, 
    latency_ms INTEGER NOT NULL, 
    `usage` JSON NOT NULL, 
    sent_message_id BIGINT, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    version INTEGER NOT NULL, 
    CONSTRAINT pk_ai_suggestions PRIMARY KEY (id), 
    CONSTRAINT ck_ai_suggestions_status CHECK (status IN ('PENDING_REVIEW','ACCEPTED','EDITED','REJECTED','SENT')), 
    CONSTRAINT fk_ai_suggestions_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id), 
    CONSTRAINT fk_ai_suggestions_sent_message_id_ticket_messages FOREIGN KEY(sent_message_id) REFERENCES ticket_messages (id), 
    CONSTRAINT fk_ai_suggestions_ticket_id_tickets FOREIGN KEY(ticket_id) REFERENCES tickets (id)
);

CREATE INDEX ix_ai_suggestions_ticket_id ON ai_suggestions (ticket_id);

CREATE TABLE sla_alerts (
    cycle_id BIGINT NOT NULL, 
    kind VARCHAR(30) NOT NULL, 
    due_at DATETIME(6) NOT NULL, 
    acknowledged_by BIGINT, 
    acknowledged_at DATETIME(6), 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_sla_alerts PRIMARY KEY (id), 
    CONSTRAINT fk_sla_alerts_acknowledged_by_users FOREIGN KEY(acknowledged_by) REFERENCES users (id), 
    CONSTRAINT fk_sla_alerts_cycle_id_ticket_sla_cycles FOREIGN KEY(cycle_id) REFERENCES ticket_sla_cycles (id), 
    CONSTRAINT uq_sla_alerts_cycle_id UNIQUE (cycle_id, kind)
);

CREATE TABLE ai_feedback (
    suggestion_id BIGINT NOT NULL, 
    reviewer_id BIGINT NOT NULL, 
    decision VARCHAR(20) NOT NULL, 
    final_body TEXT NOT NULL, 
    reason TEXT NOT NULL, 
    approved_for_training BOOL NOT NULL, 
    knowledge_error BOOL NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_ai_feedback PRIMARY KEY (id), 
    CONSTRAINT fk_ai_feedback_reviewer_id_users FOREIGN KEY(reviewer_id) REFERENCES users (id), 
    CONSTRAINT fk_ai_feedback_suggestion_id_ai_suggestions FOREIGN KEY(suggestion_id) REFERENCES ai_suggestions (id), 
    CONSTRAINT uq_ai_feedback_suggestion_id UNIQUE (suggestion_id)
);

ALTER TABLE job_tasks ADD COLUMN lease_token VARCHAR(32);

UPDATE alembic_version SET version_num='0002_intelligence' WHERE alembic_version.version_num = '0001_core';

