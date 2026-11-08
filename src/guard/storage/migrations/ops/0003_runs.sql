CREATE TABLE ladder_state (
  week INTEGER PRIMARY KEY,           -- run counter: whole calendar weeks since a fixed Monday
  run_id TEXT,
  state_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE advice (
  run_id TEXT PRIMARY KEY REFERENCES run (run_id),
  payload TEXT NOT NULL,              -- canonical JSON of the advice
  created_at TEXT NOT NULL
);

CREATE TABLE control (
  key TEXT PRIMARY KEY,               -- e.g. paused
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
