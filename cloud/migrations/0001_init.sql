-- Cherry Cloud schema.

CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  email TEXT,
  created_at INTEGER NOT NULL,
  banned INTEGER NOT NULL DEFAULT 0
);

-- Publishers sign requests and packages with Ed25519 keys; there are no passwords.
CREATE TABLE user_keys (
  key_id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  public_key TEXT NOT NULL,
  label TEXT,
  created_at INTEGER NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX user_keys_user ON user_keys(user_id);

CREATE TABLE packages (
  name TEXT PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id),
  summary TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  homepage TEXT NOT NULL DEFAULT '',
  license TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'native',
  icon TEXT NOT NULL DEFAULT '',
  latest_version TEXT,
  downloads INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  taken_down INTEGER NOT NULL DEFAULT 0,
  takedown_reason TEXT
);
CREATE INDEX packages_updated ON packages(updated_at);

CREATE TABLE versions (
  name TEXT NOT NULL REFERENCES packages(name),
  version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'uploading',
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  signature TEXT NOT NULL,
  key_id TEXT NOT NULL,
  manifest TEXT NOT NULL,
  chunks INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  scan_status TEXT NOT NULL DEFAULT 'pending',
  scan_detail TEXT,
  yanked INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (name, version)
);
CREATE INDEX versions_scan ON versions(scan_status, status);

CREATE TABLE reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  version TEXT,
  reason TEXT NOT NULL,
  details TEXT NOT NULL DEFAULT '',
  reporter TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0
);

-- Encrypted file transfers. The server only ever sees ciphertext; the key never leaves the user.
CREATE TABLE transfers (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  declared_size INTEGER NOT NULL,
  stored_size INTEGER NOT NULL DEFAULT 0,
  chunks INTEGER NOT NULL DEFAULT 0,
  complete INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX transfers_expires ON transfers(expires_at);

-- Phone <-> new PC hand-off of a transfer code (encrypted to the PC's one-time key).
CREATE TABLE pairs (
  id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE nonces (
  sig TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL
);

CREATE TABLE rate (
  key TEXT PRIMARY KEY,
  count INTEGER NOT NULL
);

-- Blob storage for STORAGE="d1" (D1 rows are limited to 2 MB, so blobs are split into parts).
CREATE TABLE blobs (
  key TEXT NOT NULL,
  part INTEGER NOT NULL,
  data BLOB NOT NULL,
  PRIMARY KEY (key, part)
);

CREATE TABLE usage (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  bytes INTEGER NOT NULL
);
INSERT INTO usage (id, bytes) VALUES (1, 0);
