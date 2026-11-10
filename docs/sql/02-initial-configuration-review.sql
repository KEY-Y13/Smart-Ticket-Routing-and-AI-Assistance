-- REVIEW ONLY; NOT EXECUTED. Use only a dedicated newly migrated database.
INSERT INTO dispatch_mutex (id) VALUES (1);
INSERT INTO sla_policies (policy_code, version, priority, response_seconds, resolution_seconds, warning_seconds, active, created_at) VALUES ('initial-natural-hours', 1, 0, 900, 14400, 300, 1, UTC_TIMESTAMP(6));
INSERT INTO sla_policies (policy_code, version, priority, response_seconds, resolution_seconds, warning_seconds, active, created_at) VALUES ('initial-natural-hours', 1, 1, 3600, 43200, 600, 1, UTC_TIMESTAMP(6));
INSERT INTO sla_policies (policy_code, version, priority, response_seconds, resolution_seconds, warning_seconds, active, created_at) VALUES ('initial-natural-hours', 1, 2, 14400, 172800, 1800, 1, UTC_TIMESTAMP(6));
INSERT INTO sla_policies (policy_code, version, priority, response_seconds, resolution_seconds, warning_seconds, active, created_at) VALUES ('initial-natural-hours', 1, 3, 43200, 432000, 3600, 1, UTC_TIMESTAMP(6));
