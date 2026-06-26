# REOS

Real Estate Operating System is a Flask-based CRM for real-estate teams managing leads, clients, properties, meetings, follow-ups, shortlists, brokers, archives, and operational backups.

## Features

- Lead, client, property, broker, meeting, and follow-up management
- Property matching and client shortlisting workflows
- Pipeline, dashboard, analytics, archive, and backup screens
- Region, city, creator, and assignment filters
- Login-protected CRM UI and REST APIs
- Hashed passwords, CSRF protection, login throttling, audit logs, and soft archive
- Optional encryption for sensitive CRM fields
- Admin-only exports and backup/restore tools
- Optional OpenAI-powered assistant when `OPENAI_API_KEY` is configured

## Tech Stack

- Python
- Flask
- SQLite
- HTML, CSS, and vanilla JavaScript
- Gunicorn-compatible WSGI entrypoint

## Repository Safety

This repository is intended to contain application source code only.

The following runtime data is ignored and should not be committed:

- `reos.db`
- `.reos_encryption_key`
- `backups/`
- `__pycache__/`
- `.env`

Anyone with both the database and encryption key can read CRM data through the app, so keep those files private and backed up securely.

## Quick Start

Install dependencies:

```bash
pip install -r requirements.txt
```

Start locally on Windows PowerShell:

```powershell
$env:REOS_ADMIN_PASSWORD="choose-a-strong-password"
python app.py
```

Open:

```text
http://localhost:5000/login
```

On first startup, REOS creates the initial admin user from:

- `REOS_ADMIN_USER`, default `admin`
- `REOS_ADMIN_PASSWORD`, required

## Configuration

Use `env.example` as the reference for environment variables.

Common local settings:

```powershell
$env:REOS_HOST="127.0.0.1"
$env:REOS_PORT="5000"
$env:REOS_DEBUG="0"
$env:REOS_ADMIN_PASSWORD="choose-a-strong-password"
python app.py
```

Production-style settings:

```bash
export REOS_SECRET_KEY="a-long-random-secret"
export REOS_ENCRYPTION_KEY="a-fernet-encryption-key"
export REOS_ADMIN_USER="admin"
export REOS_ADMIN_PASSWORD="a-strong-password"
export REOS_COOKIE_SECURE=1
export REOS_ENABLE_HSTS=1
export REOS_ALLOWED_HOSTS="crm.yourcompany.com"
gunicorn -w 2 -b 127.0.0.1:5000 wsgi:app
```

Generate an encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep `REOS_ENCRYPTION_KEY` private. If it is lost, encrypted CRM data cannot be recovered.

## User Management

Manage staff logins from the command line:

```bash
python manage_users.py add staff1
python manage_users.py list
python manage_users.py reset-password staff1
python manage_users.py delete staff1
```

Users can also change their own password from the sidebar after logging in.

## Data Protection

Sensitive CRM fields can be encrypted before storage in SQLite, including:

- Client names, phone numbers, email, and notes
- Owner and broker contact details
- Follow-up and meeting notes
- Activity messages

Encrypt existing plaintext records after setting `REOS_ENCRYPTION_KEY`:

```bash
python manage_encryption.py
```

Delete actions use soft archive by setting `deletedAt` and `deletedBy`; normal screens and exports hide archived records.

Restore archived records:

```powershell
python manage_archive.py list clients
python manage_archive.py restore clients 8
```

## Backups

Global admins can create downloadable backups from the web app in the **Backups** screen. Automatic daily backups are also supported while the app is in use.

Backup ZIP files are stored in `backups/` and include:

- `reos.db`
- `.reos_encryption_key`

Keep backup ZIP files private.

## Project Structure

```text
app.py                  Flask app, routes, APIs, auth, exports, backups
database.py             SQLite connection helpers and schema setup
crypto_utils.py         Sensitive-field encryption helpers
static/REOS.html        Main CRM interface
static/login.html       Login page
static/change_password.html
manage_users.py         CLI user management
manage_encryption.py    CLI encryption migration
manage_archive.py       CLI archive tools
wsgi.py                 Production WSGI entrypoint
env.example             Environment variable reference
```

## Security Checklist

Before hosting:

- Set a strong `REOS_SECRET_KEY`
- Set `REOS_ADMIN_PASSWORD`
- Set and back up `REOS_ENCRYPTION_KEY`
- Keep `REOS_DEBUG=0`
- Use HTTPS
- Enable secure cookies and HSTS
- Restrict `REOS_ALLOWED_HOSTS`
- Keep `reos.db`, backups, and encryption keys out of Git

## License

No open-source license has been added. All rights are reserved unless a license is added later.
