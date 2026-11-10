SET SESSION default_storage_engine=InnoDB;

SET NAMES utf8mb4;

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

