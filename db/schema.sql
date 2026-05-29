CREATE TABLE IF NOT EXISTS daily_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date TEXT NOT NULL UNIQUE,
  regime TEXT NOT NULL,
  pulse_updated_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL REFERENCES daily_snapshots(id) ON DELETE CASCADE,
  signal_type TEXT NOT NULL CHECK (signal_type IN ('momentum', 'quant')),
  symbol TEXT NOT NULL,
  etf TEXT,
  price REAL,
  sma50 REAL,
  beta REAL,
  quant_score REAL,
  rsi REAL,
  signal_label TEXT,
  sector TEXT,
  industry TEXT,
  UNIQUE(snapshot_id, signal_type, symbol)
);

CREATE INDEX IF NOT EXISTS idx_daily_signals_symbol ON daily_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_daily_signals_snapshot ON daily_signals(snapshot_id);

CREATE TABLE IF NOT EXISTS trade_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_type TEXT NOT NULL CHECK (signal_type IN ('momentum', 'quant')),
  symbol TEXT NOT NULL,
  entry_date TEXT NOT NULL,
  entry_price REAL NOT NULL,
  exit_date TEXT,
  exit_price REAL,
  last_price REAL,
  return_pct REAL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(signal_type, symbol, entry_date)
);

CREATE INDEX IF NOT EXISTS idx_trade_positions_status ON trade_positions(status, signal_type);
