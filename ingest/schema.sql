DROP TABLE IF EXISTS tickets;

CREATE TABLE tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket TEXT NOT NULL,
  status INTEGER DEFAULT 0,
  success INTEGER,
  execution_time INTEGER,
  requested_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  result text,
  comment text
);

CREATE UNIQUE INDEX idx_tickets_ticket
ON tickets (ticket);
