CREATE TABLE snapshot (
  account_id TEXT NOT NULL,
  day TEXT NOT NULL,                  -- local calendar day, ISO date
  platform TEXT NOT NULL,
  taken_at TEXT NOT NULL,
  cash TEXT NOT NULL,                 -- Decimal as text
  total TEXT NOT NULL,
  PRIMARY KEY (account_id, day)
);

CREATE TABLE snapshot_holding (
  account_id TEXT NOT NULL,
  day TEXT NOT NULL,
  isin TEXT NOT NULL,
  platform TEXT NOT NULL,
  ticker TEXT NOT NULL,
  name TEXT NOT NULL,
  quantity TEXT NOT NULL,
  price TEXT NOT NULL,
  value_gbp TEXT NOT NULL,
  excluded INTEGER NOT NULL CHECK (excluded IN (0, 1)),
  PRIMARY KEY (account_id, day, isin),
  FOREIGN KEY (account_id, day) REFERENCES snapshot (account_id, day) ON DELETE CASCADE
);

CREATE TABLE snapshot_gap (
  account_id TEXT NOT NULL,
  day TEXT NOT NULL,                  -- a day with no snapshot; never backfilled
  PRIMARY KEY (account_id, day)
);

CREATE TABLE raw_history (
  kind TEXT NOT NULL CHECK (kind IN ('orders', 'dividends', 'transactions')),
  account_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  payload TEXT NOT NULL,              -- the item as received, JSON
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (kind, account_id, external_id)
);
