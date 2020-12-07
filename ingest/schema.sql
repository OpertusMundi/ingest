DROP TABLE IF EXISTS tickets;

CREATE TABLE tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket TEXT NOT NULL,
  idempotent_key TEXT,
  request TEXT NOT NULL,
  status INTEGER DEFAULT 0,
  success INTEGER,
  execution_time REAL,
  requested_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  result text,
  rows INTEGER,
  comment text
);

CREATE UNIQUE INDEX idx_tickets_ticket
ON tickets (ticket);

CREATE UNIQUE INDEX idx_tickets_idempotent_key
ON tickets (idempotent_key);
