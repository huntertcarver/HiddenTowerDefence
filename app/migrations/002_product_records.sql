CREATE TABLE SchemaMigrations (
  version INT64 NOT NULL,
  name STRING(256) NOT NULL,
  checksum STRING(64) NOT NULL,
  applied_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true)
) PRIMARY KEY (version);

CREATE TABLE ProductRecords (
  record_type STRING(64) NOT NULL,
  record_id STRING(256) NOT NULL,
  source_item_id STRING(256),
  status STRING(64),
  payload JSON NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
) PRIMARY KEY (record_type, record_id);

CREATE INDEX ProductRecordsByTypeStatus
ON ProductRecords(record_type, status, updated_at DESC);

CREATE INDEX ProductRecordsBySource
ON ProductRecords(source_item_id, record_type, updated_at DESC);

CREATE TABLE HeartbeatLeases (
  name STRING(128) NOT NULL,
  owner_id STRING(128) NOT NULL,
  expires_at TIMESTAMP NOT NULL
) PRIMARY KEY (name);
