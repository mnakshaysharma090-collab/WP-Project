-- Run once as a MySQL administrator (for example: mysql -u root -p < backend/mysql_setup.sql)
-- Change the password before running.

CREATE DATABASE IF NOT EXISTS civicpulse
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'civicpulse'@'localhost' IDENTIFIED BY 'change-this-password';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, REFERENCES, ALTER ON civicpulse.* TO 'civicpulse'@'localhost';
FLUSH PRIVILEGES;

-- The tables are created automatically the first time the server starts.
