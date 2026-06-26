# Security Policy

## Sensitive Data

Do not commit runtime CRM data or secrets to this repository.

Keep these files private:

- `reos.db`
- `.reos_encryption_key`
- `backups/`
- `.env`

If a token, password, database, backup, or encryption key is exposed, revoke or rotate it immediately.

## Production Requirements

Before hosting REOS:

- Set `REOS_SECRET_KEY`
- Set `REOS_ADMIN_PASSWORD`
- Set and securely back up `REOS_ENCRYPTION_KEY`
- Keep `REOS_DEBUG=0`
- Serve the app over HTTPS
- Set `REOS_COOKIE_SECURE=1`
- Set `REOS_ENABLE_HSTS=1`
- Restrict `REOS_ALLOWED_HOSTS`

## Reporting

Report security issues privately to the repository owner. Do not open public issues containing secrets, database contents, credentials, or customer data.
