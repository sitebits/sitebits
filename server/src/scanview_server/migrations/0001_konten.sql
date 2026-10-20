CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  is_admin INTEGER NOT NULL DEFAULT 0,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  last_login_at INTEGER
);
CREATE TABLE passkeys (
  id TEXT PRIMARY KEY,               -- credential id, base64url
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  public_key BLOB NOT NULL,
  sign_count INTEGER NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  last_used_at INTEGER
);
CREATE TABLE passwords (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  hash TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE invites (
  token_hash TEXT PRIMARY KEY,
  email TEXT NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('neu', 'zuruecksetzen')),
  grants TEXT NOT NULL DEFAULT '{}', -- JSON {projectId: rolle}
  is_admin INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER REFERENCES users(id),
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER
);
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX sessions_user ON sessions(user_id);
CREATE TABLE project_roles (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('betrachter', 'bearbeiter')),
  PRIMARY KEY (user_id, project_id)
);
CREATE TABLE project_meta (
  project_id TEXT PRIMARY KEY,
  title TEXT,
  description TEXT,
  updated_by INTEGER REFERENCES users(id),
  updated_at INTEGER NOT NULL
);
CREATE TABLE challenges (
  id TEXT PRIMARY KEY,
  challenge BLOB NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('registrieren', 'anmelden')),
  invite_hash TEXT,
  user_id INTEGER,
  expires_at INTEGER NOT NULL
);
CREATE TABLE throttle (
  key TEXT PRIMARY KEY,
  failures INTEGER NOT NULL,
  locked_until INTEGER NOT NULL DEFAULT 0
);
