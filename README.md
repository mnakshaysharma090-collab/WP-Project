# CivicPulse

A civic issue reporting site: citizens report problems in their area, track them by complaint ID, and administrators update their status.

## Project layout

```
index.html, explore.html, track.html, report.html, login.html, admin.html, ...
style.css, script.js          front end (plain HTML, CSS and JavaScript)
image/, cityscene.jpg, ...    site images
backend/server.py             API server + SQLite database (Python standard library only)
backend/mysql_setup.sql       one-time MySQL database and user setup
backend/civicpulse.db         SQLite file, only used without DATABASE_URL (not included)
uploads/                      citizen photos, created automatically (not included)
```

## Requirements

- Python 3.8 or newer
- For MySQL: a MySQL 8.0+ or MariaDB 10.4+ server, and the PyMySQL driver (`pip install pymysql`)

Without `DATABASE_URL` the server uses a local SQLite file, which is handy for development.

## Set up MySQL

1. Create the database and a user. Edit the password in `backend/mysql_setup.sql` first, then run it as a MySQL administrator:

   ```bash
   mysql -u root -p < backend/mysql_setup.sql
   ```

2. Install the driver:

   ```bash
   pip install pymysql
   ```

3. Point the server at the database. Passwords containing special characters must be URL-encoded (for example `@` becomes `%40`).

   macOS / Linux:
   ```bash
   export DATABASE_URL='mysql://civicpulse:change-this-password@localhost:3306/civicpulse'
   ```

   Windows (PowerShell):
   ```powershell
   $env:DATABASE_URL = 'mysql://civicpulse:change-this-password@localhost:3306/civicpulse'
   ```

## Run it

```bash
python3 backend/server.py
```

Open http://localhost:8000 in your browser. On the first run the server creates the tables and loads six sample issues. The startup message shows which database it connected to.

## Create an administrator

```bash
python3 backend/server.py create-admin you@example.com
```

You will be asked for a password (at least 8 characters). To script this, set `CIVICPULSE_ADMIN_PASSWORD` first.

Then sign in at http://localhost:8000/login.html. Administrators are sent to `admin.html`, where they can change an issue's status and add a note that citizens will see on the Track page.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | Port to listen on |
| `HOST` | `127.0.0.1` | Address to bind. Use `0.0.0.0` to allow other devices on your network |
| `DATABASE_URL` | (unset, so SQLite is used) | MySQL connection, e.g. `mysql://user:password@host:3306/civicpulse` |
| `CIVICPULSE_DB` | `backend/civicpulse.db` | SQLite file, used only when `DATABASE_URL` is not set |
| `CIVICPULSE_ADMIN_PASSWORD` | (prompt) | Admin password for `create-admin` |

## API

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/issues?category=&status=&q=` | anyone | List issues, with optional filters |
| GET | `/api/issues/CP-1001` | anyone | One issue with its status history |
| POST | `/api/issues` | anyone | Report an issue (optional photo as a data URL) |
| GET | `/api/stats` | anyone | Counts for the Home page |
| POST | `/api/auth/register` | anyone | Create a citizen account |
| POST | `/api/auth/login` | anyone | Sign in (sets a session cookie) |
| POST | `/api/auth/logout` | signed in | Sign out |
| GET | `/api/auth/me` | anyone | Current user, or null |
| PATCH | `/api/admin/issues/CP-1001` | admin | Change status and add a note |

## Notes for deployment

- The server is a single Python process. That's fine for a pilot or a small city. For heavy traffic, run it behind a proper web server such as Nginx with Gunicorn or a similar WSGI setup.
- Serve the site over HTTPS in production and add the `Secure` flag to the session cookie (see `session_cookie` in `server.py`).
- Back up the MySQL database (for example with `mysqldump civicpulse > backup.sql`) and the `uploads/` folder regularly.
- Login attempts are not rate-limited yet. Add that before a public launch.
