CREATE TABLE run (
  run_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                 -- weekly | daily | snapshot
  slot TEXT NOT NULL,                 -- the scheduled slot this run answers, e.g. 2026-11-11
  status TEXT NOT NULL,               -- ok | stopped | failed
  stop_reason TEXT,
  inputs_hash TEXT,
  config_version TEXT,
  code_version TEXT,
  payload TEXT,                       -- canonical JSON: inputs, signals, ladder state, advice
  started_at TEXT NOT NULL,
  finished_at TEXT,
  UNIQUE (kind, slot)
);

CREATE TABLE decision_journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES run (run_id),
  advice_ref TEXT,
  decision TEXT NOT NULL CHECK (decision IN ('followed', 'changed', 'ignored')),
  note TEXT,
  recorded_at TEXT NOT NULL,
  supersedes INTEGER REFERENCES decision_journal (id)
);

CREATE TRIGGER decision_journal_no_update BEFORE UPDATE ON decision_journal
BEGIN SELECT RAISE(ABORT, 'decision_journal is append-only'); END;

CREATE TRIGGER decision_journal_no_delete BEFORE DELETE ON decision_journal
BEGIN SELECT RAISE(ABORT, 'decision_journal is append-only'); END;
