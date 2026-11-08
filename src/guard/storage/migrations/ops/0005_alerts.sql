-- Every alert delivery attempt, successful or not. Errors are stored with amounts, tokens and
-- URLs already removed.
CREATE TABLE alert_delivery (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,                 -- weekly | daily | snapshot | heartbeat | test
  urgency TEXT NOT NULL,              -- info | warn | urgent
  channel TEXT NOT NULL,              -- telegram | ntfy | none
  ok INTEGER NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL
);

-- Alerts that must go out once only (the missed-weekly-run alert), so a restart does not repeat it.
CREATE TABLE alert_once (
  key TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);
