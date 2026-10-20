-- The Cherry Store's packages now live on GitHub (Cherry-Systems/cherry-store). Cherry Cloud only keeps
-- accounts, and holds Cherry-account uploads until the store's workflow collects them.
DROP TABLE IF EXISTS versions;
DROP TABLE IF EXISTS packages;
DROP TABLE IF EXISTS reports;
DELETE FROM blobs WHERE key LIKE 'p/%';
UPDATE usage SET bytes = 0;

CREATE TABLE uploads (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  request TEXT NOT NULL,
  size INTEGER NOT NULL,
  chunks INTEGER NOT NULL,
  -- uploading -> waiting (sent to GitHub) -> done | failed
  state TEXT NOT NULL DEFAULT 'uploading',
  message TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX uploads_state ON uploads(state, updated_at);
