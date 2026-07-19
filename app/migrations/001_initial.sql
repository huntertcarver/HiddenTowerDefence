-- Apply with the production migration command before deploying the first revision.
-- All timestamps are stored as RFC 3339 strings so the same model can run in
-- SQLite locally and Cloud Spanner in production.

CREATE TABLE RuntimeState (
  state_key STRING(128) NOT NULL,
  state_value STRING(128) NOT NULL
) PRIMARY KEY (state_key);

CREATE TABLE SourceItems (
  id STRING(256) NOT NULL,
  payload JSON NOT NULL,
  created_at STRING(64) NOT NULL
) PRIMARY KEY (id);

CREATE TABLE EventSequence (
  name STRING(128) NOT NULL,
  next_id INT64 NOT NULL
) PRIMARY KEY (name);

CREATE TABLE Events (
  id INT64 NOT NULL,
  event_type STRING(128) NOT NULL,
  source_item_id STRING(256),
  trust_state STRING(32),
  payload JSON NOT NULL,
  occurred_at STRING(64) NOT NULL
) PRIMARY KEY (id);

CREATE TABLE Approvals (
  id STRING(64) NOT NULL,
  source_item_id STRING(256) NOT NULL,
  action STRING(128) NOT NULL,
  arguments JSON NOT NULL,
  status STRING(32) NOT NULL,
  created_at STRING(64) NOT NULL,
  resolved_at STRING(64)
) PRIMARY KEY (id);

CREATE INDEX ApprovalsByStatus ON Approvals(status, created_at);

CREATE TABLE Incidents (
  id STRING(64) NOT NULL,
  source_item_id STRING(256) NOT NULL,
  severity STRING(32) NOT NULL,
  summary STRING(MAX) NOT NULL,
  created_at STRING(64) NOT NULL,
  acknowledged_at STRING(64)
) PRIMARY KEY (id);

CREATE TABLE TriageResults (
  source_item_id STRING(256) NOT NULL,
  payload JSON NOT NULL,
  created_at STRING(64) NOT NULL
) PRIMARY KEY (source_item_id);
