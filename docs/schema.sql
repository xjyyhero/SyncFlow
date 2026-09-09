-- Week 1 design proposal; not a production migration.
-- MySQL 8.4, all application timestamps must be written in UTC.
CREATE TABLE IF NOT EXISTS jobs (
  id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
  filename VARCHAR(255) NOT NULL,
  storage_key VARCHAR(255) NOT NULL,
  file_sha256 CHAR(64) CHARACTER SET ascii NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'QUEUED',
  total_count INT UNSIGNED NOT NULL,
  processed_count INT UNSIGNED NOT NULL DEFAULT 0,
  success_count INT UNSIGNED NOT NULL DEFAULT 0,
  failed_count INT UNSIGNED NOT NULL DEFAULT 0,
  checkpoint INT UNSIGNED NOT NULL DEFAULT 0,
  attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
  dispatch_pending BOOLEAN NOT NULL DEFAULT TRUE,
  cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
  next_retry_at DATETIME(3) NULL,
  lease_expires_at DATETIME(3) NULL,
  idempotency_key VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NULL,
  idempotency_expires_at DATETIME(3) NULL,
  last_error_code VARCHAR(64) NULL,
  last_error_message VARCHAR(512) NULL,
  started_at DATETIME(3) NULL,
  finished_at DATETIME(3) NULL,
  created_at DATETIME(3) NOT NULL,
  updated_at DATETIME(3) NOT NULL,
  UNIQUE KEY uq_jobs_idempotency (idempotency_key),
  KEY ix_jobs_created (created_at, id),
  KEY ix_jobs_status_created (status, created_at, id),
  KEY ix_jobs_dispatch (dispatch_pending, status, next_retry_at),
  KEY ix_jobs_lease (status, lease_expires_at),
  KEY ix_jobs_key_expiry (idempotency_expires_at),
  CONSTRAINT ck_jobs_status CHECK (status IN ('QUEUED','RUNNING','SUCCESS','PARTIAL_SUCCESS','FAILED','RETRY_WAIT','CANCELLED')),
  CONSTRAINT ck_jobs_counts CHECK (total_count > 0 AND processed_count = success_count + failed_count AND processed_count <= total_count AND checkpoint = processed_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS records (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  job_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `row_number` INT UNSIGNED NOT NULL,
  external_id VARCHAR(64) COLLATE utf8mb4_0900_bin NOT NULL,
  name VARCHAR(255) NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  record_date DATE NOT NULL,
  created_at DATETIME(3) NOT NULL,
  updated_at DATETIME(3) NOT NULL,
  UNIQUE KEY uq_records_external (job_id, external_id),
  UNIQUE KEY uq_records_row (job_id, `row_number`),
  CONSTRAINT ck_records_amount CHECK (amount >= 0),
  CONSTRAINT ck_records_row CHECK (`row_number` > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS job_errors (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  job_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `row_number` INT UNSIGNED NOT NULL,
  code VARCHAR(64) NOT NULL,
  message VARCHAR(512) NOT NULL,
  field VARCHAR(64) NULL,
  created_at DATETIME(3) NOT NULL,
  updated_at DATETIME(3) NOT NULL,
  UNIQUE KEY uq_errors_row (job_id, `row_number`),
  CONSTRAINT ck_errors_row CHECK (`row_number` > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS job_attempts (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  job_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  attempt_no INT UNSIGNED NOT NULL,
  outcome VARCHAR(24) NOT NULL DEFAULT 'RUNNING',
  error_code VARCHAR(64) NULL,
  error_message VARCHAR(512) NULL,
  started_at DATETIME(3) NOT NULL,
  finished_at DATETIME(3) NULL,
  created_at DATETIME(3) NOT NULL,
  updated_at DATETIME(3) NOT NULL,
  UNIQUE KEY uq_attempts_no (job_id, attempt_no),
  CONSTRAINT ck_attempt_no CHECK (attempt_no > 0),
  CONSTRAINT ck_attempt_outcome CHECK (outcome IN ('RUNNING','SUCCESS','PARTIAL_SUCCESS','FAILED','CANCELLED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
