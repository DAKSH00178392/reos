# REOS - Real Estate Operating System

This is the primary app for the `tpcrm` workspace.

A professional, intelligent Real Estate CRM mapped to a Flask backend with an SQLite database.

## Prerequisites
- Python 3.x
- pip

## Installation & Running

Install dependencies:
```bash
pip install -r requirements.txt
```

Run locally on your PC:
```bash
set REOS_ADMIN_PASSWORD=choose-a-strong-password
python app.py
```
Then open [http://localhost:5000/login](http://localhost:5000/login) in your browser.

On first startup, the app creates the initial admin user from `REOS_ADMIN_USER` or `admin` and requires `REOS_ADMIN_PASSWORD`.

## Web Server Settings

For office network access, set `REOS_HOST=0.0.0.0` and open the main PC/server IP from other computers.

For production hosting:
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

On Windows PowerShell for local testing:
```powershell
$env:REOS_HOST="127.0.0.1"
$env:REOS_PORT="5000"
$env:REOS_DEBUG="0"
python app.py
```

## Users

Create staff logins from the command line:
```bash
python manage_users.py add staff1
python manage_users.py list
python manage_users.py reset-password staff1
python manage_users.py delete staff1
```

Users can also change their own password from the sidebar after logging in.

## Data Encryption

Sensitive CRM fields are encrypted before they are stored in SQLite, then decrypted only when the logged-in app reads them.

Generate a production encryption key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set that value as `REOS_ENCRYPTION_KEY` before starting the app. Keep it private and backed up. If this key is lost, encrypted CRM data cannot be recovered.

Encrypt existing plaintext records after setting the key:
```bash
python manage_encryption.py
```

Sensitive encrypted fields include client names, phone numbers, email, notes, owner/broker contact details, follow-up notes, meeting notes, and activity messages.

## Security Controls

The app includes:
- Login-required protection for every CRM page and API.
- Hashed passwords.
- Login attempt throttling.
- CSRF protection for forms and API writes.
- Soft archive instead of permanent delete for CRM records.
- Audit log entries for create, update, and archive actions.
- Admin-only CSV/XLSX exports for decrypted CRM data.
- Same-origin API behavior by default; CORS is disabled unless `REOS_CORS_ORIGINS` is set.
- Security headers for frame, content-type, referrer, and browser permission restrictions.
- Optional HTTPS-only cookies and HSTS for production.
- Request body size limit via `REOS_MAX_CONTENT_LENGTH`.
- Optional host allow-list via `REOS_ALLOWED_HOSTS`.

Before hosting, set a strong `REOS_SECRET_KEY`, change all default passwords, keep `REOS_DEBUG=0`, and run behind HTTPS.

## Data Safety

Delete buttons archive records by setting `deletedAt` and `deletedBy`; normal screens and exports hide archived records. This prevents accidental data loss while keeping old rows recoverable from the database.

Global admins can create a downloadable backup from the web app: open **Backups** in the sidebar, then click **Create Backup**. The app also creates one automatic backup per day while the system is in use. Backup ZIP files are stored in `reos/backups/` and include both:
- `reos.db`
- `.reos_encryption_key`

Keep backup ZIP files private. Anyone with both the database and encryption key can read CRM data through the app.

For database migration, open **Backups** as the global admin and use **Upload DB**. Upload accepts a REOS backup `.zip` or a SQLite `.db` file. The app validates the database and creates a safety backup before replacing the current database.

Properties that are linked to meetings, shortlists, or closed deals are protected from archive through the property delete button. Clients with active leads are also protected.

Audit rows are stored in `audit_logs` and can be read by admins through `/api/audit`.

List archived clients from PowerShell:
```powershell
python manage_archive.py list clients
```

Restore one archived client:
```powershell
python manage_archive.py restore clients 8
```

Admins can also restore from the web app: open **Archive** in the sidebar, choose the record type, then click **Restore**.
Restoring a client also restores archived leads that match by client ID, phone number, or client name.

The web API restore route is `POST /api/clients/<id>/restore`.

Client archive is blocked when an active lead still matches by client ID, phone number, or client name.
Lead archive also archives the matching client when that client has no other active leads. If the client has another active lead, only the selected lead is archived.

## Region Filters

Leads, clients, and properties support region/city/creator filters in the web UI. New records store:
- `region`
- `createdBy`

Regions are auto-derived from location for common nearby areas, for example Ahmedabad/Sanand/Gandhinagar as `Ahmedabad Region` and Vapi/Daman/Silvassa/Valsad as `Vapi Region`. Older records still appear in filters using derived region/city from their saved location, but their creator may show as `Unknown` if the record was created before `createdBy` tracking existed.

Entry forms include a Region dropdown. Default regions are Ahmedabad Region, Vapi Region, Surat Region, Shared, and Other. Users can press the `+` button beside the Region field to add a new shared region to the dropdown list.

## Workspace Note

Ongoing work should happen in `reos/`. Older prototype files were moved under the workspace archive folder for reference.

## Architecture
- `app.py`: Flask application providing REST APIs for Leads, Clients, Properties, Followups, Meetings, and Brokers.
- `database.py`: Handles all database connections and schema creation/query execution via SQLite.
- `static/REOS.html`: The fully-featured UI that interacts with the `app.py` APIs using asynchronous fetch calls. It replaces the old prototype files.
- `manage_users.py`: Command-line staff login management.
- `manage_encryption.py`: Encrypts existing sensitive plaintext data.
- `wsgi.py`: Production WSGI entrypoint for Gunicorn/Nginx hosting.
