-- REVIEW ONLY: not executed. No DROP/DELETE/TRUNCATE.
-- Select the dedicated NEW empty database before applying.
SET SESSION default_storage_engine=InnoDB;

SET NAMES utf8mb4;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001_core

CREATE TABLE business_groups (
    code VARCHAR(50) NOT NULL, 
    name VARCHAR(100) NOT NULL, 
    enabled BOOL NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    version INTEGER NOT NULL, 
    CONSTRAINT pk_business_groups PRIMARY KEY (id), 
    CONSTRAINT uq_business_groups_code UNIQUE (code)
);

CREATE TABLE dispatch_mutex (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    CONSTRAINT pk_dispatch_mutex PRIMARY KEY (id)
);

CREATE TABLE job_tasks (
    type VARCHAR(50) NOT NULL, 
    dedupe_key VARCHAR(160) NOT NULL, 
    payload JSON NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    attempts INTEGER NOT NULL, 
    available_at DATETIME(6) NOT NULL, 
    lease_until DATETIME(6), 
    last_error TEXT, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_job_tasks PRIMARY KEY (id), 
    CONSTRAINT uq_job_tasks_dedupe_key UNIQUE (dedupe_key)
);

CREATE INDEX ix_jobs_pending ON job_tasks (status, available_at, id);

CREATE TABLE products (
    sku VARCHAR(64) NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_products PRIMARY KEY (id), 
    CONSTRAINT uq_products_sku UNIQUE (sku)
);

CREATE TABLE sla_policies (
    policy_code VARCHAR(50) NOT NULL, 
    version INTEGER NOT NULL, 
    priority INTEGER NOT NULL, 
    response_seconds INTEGER NOT NULL, 
    resolution_seconds INTEGER NOT NULL, 
    warning_seconds INTEGER NOT NULL, 
    active BOOL NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_sla_policies PRIMARY KEY (id), 
    CONSTRAINT ck_sla_policies_times CHECK (response_seconds > 0 AND resolution_seconds >= response_seconds AND warning_seconds >= 0 AND warning_seconds < response_seconds), 
    CONSTRAINT uq_sla_policies_policy_code UNIQUE (policy_code, version, priority)
);

CREATE TABLE users (
    login VARCHAR(100) NOT NULL, 
    password_hash VARCHAR(255) NOT NULL, 
    `role` VARCHAR(16) NOT NULL, 
    enabled BOOL NOT NULL, 
    auth_version INTEGER NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    version INTEGER NOT NULL, 
    CONSTRAINT pk_users PRIMARY KEY (id), 
    CONSTRAINT ck_users_role CHECK (role IN ('CUSTOMER','AGENT','ADMIN')), 
    CONSTRAINT uq_users_login UNIQUE (login)
);

CREATE TABLE admin_events (
    actor_id BIGINT NOT NULL, 
    action VARCHAR(50) NOT NULL, 
    target_type VARCHAR(50) NOT NULL, 
    target_id BIGINT NOT NULL, 
    detail JSON NOT NULL, 
    request_id VARCHAR(64) NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_admin_events PRIMARY KEY (id), 
    CONSTRAINT fk_admin_events_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id)
);

CREATE TABLE agent_profiles (
    user_id BIGINT NOT NULL, 
    online BOOL NOT NULL, 
    capacity INTEGER NOT NULL, 
    active_load INTEGER NOT NULL, 
    last_assigned_at DATETIME(6), 
    version INTEGER NOT NULL, 
    CONSTRAINT pk_agent_profiles PRIMARY KEY (user_id), 
    CONSTRAINT ck_agent_profiles_capacity CHECK (capacity > 0 AND active_load >= 0 AND active_load <= capacity), 
    CONSTRAINT fk_agent_profiles_user_id_users FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE group_members (
    group_id BIGINT NOT NULL, 
    user_id BIGINT NOT NULL, 
    can_handle BOOL NOT NULL, 
    can_view_business BOOL NOT NULL, 
    CONSTRAINT pk_group_members PRIMARY KEY (group_id, user_id), 
    CONSTRAINT fk_group_members_group_id_business_groups FOREIGN KEY(group_id) REFERENCES business_groups (id), 
    CONSTRAINT fk_group_members_user_id_users FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE idempotency_keys (
    actor_id BIGINT NOT NULL, 
    operation VARCHAR(100) NOT NULL, 
    `key` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL, 
    request_hash VARCHAR(64) NOT NULL, 
    response_snapshot JSON NOT NULL, 
    expires_at DATETIME(6) NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_idempotency_keys PRIMARY KEY (id), 
    CONSTRAINT fk_idempotency_keys_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id), 
    CONSTRAINT uq_idempotency_keys_actor_id UNIQUE (actor_id, operation, `key`)
);

CREATE TABLE orders (
    order_no VARCHAR(64) NOT NULL, 
    customer_id BIGINT NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    total_amount NUMERIC(12, 2) NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_orders PRIMARY KEY (id), 
    CONSTRAINT fk_orders_customer_id_users FOREIGN KEY(customer_id) REFERENCES users (id), 
    CONSTRAINT uq_orders_order_no UNIQUE (order_no)
);

CREATE INDEX ix_orders_customer_created ON orders (customer_id, created_at);

CREATE TABLE routing_rules (
    category INTEGER NOT NULL, 
    group_id BIGINT NOT NULL, 
    `rank` INTEGER NOT NULL, 
    enabled BOOL NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_routing_rules PRIMARY KEY (id), 
    CONSTRAINT ck_routing_rules_category CHECK (category >= 0 AND category <= 5), 
    CONSTRAINT fk_routing_rules_group_id_business_groups FOREIGN KEY(group_id) REFERENCES business_groups (id), 
    CONSTRAINT uq_routing_rules_category UNIQUE (category, group_id)
);

CREATE INDEX ix_routing_category_enabled_rank ON routing_rules (category, enabled, `rank`);

CREATE TABLE order_items (
    order_id BIGINT NOT NULL, 
    product_id BIGINT NOT NULL, 
    line_no INTEGER NOT NULL, 
    product_name_snapshot VARCHAR(200) NOT NULL, 
    quantity INTEGER NOT NULL, 
    unit_price NUMERIC(12, 2) NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_order_items PRIMARY KEY (id), 
    CONSTRAINT ck_order_items_quantity_price CHECK (quantity > 0 AND unit_price >= 0), 
    CONSTRAINT fk_order_items_order_id_orders FOREIGN KEY(order_id) REFERENCES orders (id), 
    CONSTRAINT fk_order_items_product_id_products FOREIGN KEY(product_id) REFERENCES products (id), 
    CONSTRAINT uq_order_items_order_id UNIQUE (order_id, line_no)
);

CREATE TABLE payments (
    order_id BIGINT NOT NULL, 
    record_no VARCHAR(64) NOT NULL, 
    amount NUMERIC(12, 2) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    occurred_at DATETIME(6) NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_payments PRIMARY KEY (id), 
    CONSTRAINT fk_payments_order_id_orders FOREIGN KEY(order_id) REFERENCES orders (id), 
    CONSTRAINT uq_payments_record_no UNIQUE (record_no)
);

CREATE INDEX ix_payments_order_id ON payments (order_id);

CREATE TABLE refunds (
    order_id BIGINT NOT NULL, 
    record_no VARCHAR(64) NOT NULL, 
    amount NUMERIC(12, 2) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    reason TEXT NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_refunds PRIMARY KEY (id), 
    CONSTRAINT fk_refunds_order_id_orders FOREIGN KEY(order_id) REFERENCES orders (id), 
    CONSTRAINT uq_refunds_record_no UNIQUE (record_no)
);

CREATE INDEX ix_refunds_order_id ON refunds (order_id);

CREATE TABLE shipments (
    order_id BIGINT NOT NULL, 
    tracking_no VARCHAR(100) NOT NULL, 
    carrier VARCHAR(50) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_shipments PRIMARY KEY (id), 
    CONSTRAINT fk_shipments_order_id_orders FOREIGN KEY(order_id) REFERENCES orders (id)
);

CREATE INDEX ix_shipments_order_id ON shipments (order_id);

CREATE TABLE tickets (
    ticket_no VARCHAR(64) NOT NULL, 
    customer_id BIGINT NOT NULL, 
    order_id BIGINT, 
    subject VARCHAR(200) NOT NULL, 
    category INTEGER, 
    priority INTEGER NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    assignee_id BIGINT, 
    analysis_status VARCHAR(32) NOT NULL, 
    input_version INTEGER NOT NULL, 
    ended_at DATETIME(6), 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    version INTEGER NOT NULL, 
    CONSTRAINT pk_tickets PRIMARY KEY (id), 
    CONSTRAINT ck_tickets_status CHECK (status IN ('NEW','PENDING_ASSIGNMENT','PROCESSING','WAITING_CUSTOMER','WAITING_EXTERNAL','RESOLVED','CLOSED')), 
    CONSTRAINT ck_tickets_category CHECK (category IS NULL OR category BETWEEN 0 AND 5), 
    CONSTRAINT ck_tickets_priority CHECK (priority BETWEEN 0 AND 3), 
    CONSTRAINT fk_tickets_assignee_id_users FOREIGN KEY(assignee_id) REFERENCES users (id), 
    CONSTRAINT fk_tickets_customer_id_users FOREIGN KEY(customer_id) REFERENCES users (id), 
    CONSTRAINT fk_tickets_order_id_orders FOREIGN KEY(order_id) REFERENCES orders (id), 
    CONSTRAINT uq_tickets_ticket_no UNIQUE (ticket_no)
);

CREATE INDEX ix_tickets_assignee_status_created ON tickets (assignee_id, status, created_at);

CREATE INDEX ix_tickets_customer_created ON tickets (customer_id, created_at, id);

CREATE INDEX ix_tickets_queue ON tickets (status, priority, created_at, id);

CREATE TABLE ticket_assignments (
    ticket_id BIGINT NOT NULL, 
    agent_id BIGINT NOT NULL, 
    group_id BIGINT NOT NULL, 
    started_at DATETIME(6) NOT NULL, 
    ended_at DATETIME(6), 
    reason TEXT NOT NULL, 
    rule_id BIGINT NOT NULL, 
    active_ticket_id BIGINT GENERATED ALWAYS AS (CASE WHEN ended_at IS NULL THEN ticket_id ELSE NULL END) STORED, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_ticket_assignments PRIMARY KEY (id), 
    CONSTRAINT fk_ticket_assignments_agent_id_users FOREIGN KEY(agent_id) REFERENCES users (id), 
    CONSTRAINT fk_ticket_assignments_group_id_business_groups FOREIGN KEY(group_id) REFERENCES business_groups (id), 
    CONSTRAINT fk_ticket_assignments_rule_id_routing_rules FOREIGN KEY(rule_id) REFERENCES routing_rules (id), 
    CONSTRAINT fk_ticket_assignments_ticket_id_tickets FOREIGN KEY(ticket_id) REFERENCES tickets (id), 
    CONSTRAINT uq_ticket_assignments_active_ticket_id UNIQUE (active_ticket_id)
);

CREATE TABLE ticket_events (
    ticket_id BIGINT NOT NULL, 
    actor_id BIGINT, 
    event_type VARCHAR(50) NOT NULL, 
    old_value JSON NOT NULL, 
    new_value JSON NOT NULL, 
    reason TEXT NOT NULL, 
    request_id VARCHAR(64) NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_ticket_events PRIMARY KEY (id), 
    CONSTRAINT fk_ticket_events_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id), 
    CONSTRAINT fk_ticket_events_ticket_id_tickets FOREIGN KEY(ticket_id) REFERENCES tickets (id)
);

CREATE INDEX ix_ticket_events_ticket_id ON ticket_events (ticket_id);

CREATE TABLE ticket_messages (
    ticket_id BIGINT NOT NULL, 
    author_id BIGINT NOT NULL, 
    visibility VARCHAR(16) NOT NULL, 
    body TEXT NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_ticket_messages PRIMARY KEY (id), 
    CONSTRAINT ck_ticket_messages_visibility CHECK (visibility IN ('PUBLIC','INTERNAL')), 
    CONSTRAINT fk_ticket_messages_author_id_users FOREIGN KEY(author_id) REFERENCES users (id), 
    CONSTRAINT fk_ticket_messages_ticket_id_tickets FOREIGN KEY(ticket_id) REFERENCES tickets (id)
);

CREATE INDEX ix_ticket_messages_ticket_id ON ticket_messages (ticket_id);

CREATE TABLE ticket_sla_cycles (
    ticket_id BIGINT NOT NULL, 
    cycle_no INTEGER NOT NULL, 
    policy_id BIGINT NOT NULL, 
    policy_snapshot JSON NOT NULL, 
    started_at DATETIME(6) NOT NULL, 
    response_due_at DATETIME(6) NOT NULL, 
    resolution_due_at DATETIME(6) NOT NULL, 
    first_response_at DATETIME(6), 
    resolved_at DATETIME(6), 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_ticket_sla_cycles PRIMARY KEY (id), 
    CONSTRAINT fk_ticket_sla_cycles_policy_id_sla_policies FOREIGN KEY(policy_id) REFERENCES sla_policies (id), 
    CONSTRAINT fk_ticket_sla_cycles_ticket_id_tickets FOREIGN KEY(ticket_id) REFERENCES tickets (id), 
    CONSTRAINT uq_ticket_sla_cycles_ticket_id UNIQUE (ticket_id, cycle_no)
);

CREATE INDEX ix_ticket_sla_cycles_resolution_due_at ON ticket_sla_cycles (resolution_due_at);

CREATE INDEX ix_ticket_sla_cycles_response_due_at ON ticket_sla_cycles (response_due_at);

INSERT INTO alembic_version (version_num) VALUES ('0001_core');

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

-- Running upgrade 0002_intelligence -> 0003_reviews

CREATE TABLE review_collections (
    code VARCHAR(100) NOT NULL, 
    title VARCHAR(200) NOT NULL, 
    kind VARCHAR(30) NOT NULL, 
    provenance TEXT NOT NULL, 
    source_sha256 VARCHAR(64) NOT NULL, 
    imported_by BIGINT NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_review_collections PRIMARY KEY (id), 
    CONSTRAINT ck_review_collections_kind CHECK (kind IN ('CLASSIFICATION','KNOWLEDGE','RETRIEVAL','LLM')), 
    CONSTRAINT fk_review_collections_imported_by_users FOREIGN KEY(imported_by) REFERENCES users (id), 
    CONSTRAINT uq_review_collections_code UNIQUE (code)
);

CREATE TABLE review_items (
    collection_id BIGINT NOT NULL, 
    item_key VARCHAR(100) NOT NULL, 
    payload JSON NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    version INTEGER NOT NULL, 
    CONSTRAINT pk_review_items PRIMARY KEY (id), 
    CONSTRAINT ck_review_items_status CHECK (status IN ('PENDING','APPROVED','REJECTED')), 
    CONSTRAINT fk_review_items_collection_id_review_collections FOREIGN KEY(collection_id) REFERENCES review_collections (id), 
    CONSTRAINT uq_review_items_collection_id UNIQUE (collection_id, item_key)
);

CREATE INDEX ix_review_items_collection_status_id ON review_items (collection_id, status, id);

CREATE TABLE review_snapshots (
    collection_id BIGINT NOT NULL, 
    created_by BIGINT NOT NULL, 
    source_sha256 VARCHAR(64) NOT NULL, 
    snapshot_sha256 VARCHAR(64) NOT NULL, 
    `rows` JSON NOT NULL, 
    decision_ids JSON NOT NULL, 
    reason TEXT NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_review_snapshots PRIMARY KEY (id), 
    CONSTRAINT fk_review_snapshots_collection_id_review_collections FOREIGN KEY(collection_id) REFERENCES review_collections (id), 
    CONSTRAINT fk_review_snapshots_created_by_users FOREIGN KEY(created_by) REFERENCES users (id), 
    CONSTRAINT uq_review_snapshots_snapshot_sha256 UNIQUE (snapshot_sha256)
);

CREATE TABLE model_releases (
    snapshot_id BIGINT NOT NULL, 
    created_by BIGINT NOT NULL, 
    manifest_ref VARCHAR(500) NOT NULL, 
    fingerprints JSON NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    scope VARCHAR(10) NOT NULL, 
    reviewed_by BIGINT, 
    review_reason TEXT, 
    package_ref VARCHAR(500), 
    package_sha256 VARCHAR(64), 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    version INTEGER NOT NULL, 
    CONSTRAINT pk_model_releases PRIMARY KEY (id), 
    CONSTRAINT ck_model_releases_scope CHECK (scope IN ('SHADOW','LIVE')), 
    CONSTRAINT ck_model_releases_status CHECK (status IN ('CANDIDATE','APPROVED','REJECTED','REVOKED')), 
    CONSTRAINT fk_model_releases_created_by_users FOREIGN KEY(created_by) REFERENCES users (id), 
    CONSTRAINT fk_model_releases_reviewed_by_users FOREIGN KEY(reviewed_by) REFERENCES users (id), 
    CONSTRAINT fk_model_releases_snapshot_id_review_snapshots FOREIGN KEY(snapshot_id) REFERENCES review_snapshots (id)
);

CREATE TABLE review_decisions (
    item_id BIGINT NOT NULL, 
    item_version INTEGER NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    reviewer_id BIGINT NOT NULL, 
    decision VARCHAR(20) NOT NULL, 
    annotation JSON NOT NULL, 
    reason TEXT NOT NULL, 
    id BIGINT NOT NULL AUTO_INCREMENT, 
    created_at DATETIME(6) NOT NULL, 
    CONSTRAINT pk_review_decisions PRIMARY KEY (id), 
    CONSTRAINT ck_review_decisions_decision CHECK (decision IN ('APPROVED','REJECTED')), 
    CONSTRAINT fk_review_decisions_item_id_review_items FOREIGN KEY(item_id) REFERENCES review_items (id), 
    CONSTRAINT fk_review_decisions_reviewer_id_users FOREIGN KEY(reviewer_id) REFERENCES users (id), 
    CONSTRAINT uq_review_decisions_item_id UNIQUE (item_id, item_version)
);

UPDATE alembic_version SET version_num='0003_reviews' WHERE alembic_version.version_num = '0002_intelligence';

