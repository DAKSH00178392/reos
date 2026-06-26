# Contributing

This project is maintained as a focused CRM application. Keep changes small, practical, and easy to review.

## Local Setup

```bash
pip install -r requirements.txt
```

```powershell
$env:REOS_ADMIN_PASSWORD="choose-a-strong-password"
python app.py
```

## Guidelines

- Do not commit `reos.db`, `.reos_encryption_key`, backups, cache files, or `.env` files.
- Keep user-facing CRM workflows simple and direct.
- Prefer small commits with clear messages.
- Update `README.md` when setup, security, or deployment behavior changes.
- Test login, dashboard loading, and the touched workflow before pushing.

## Suggested Checks

```bash
python -m py_compile app.py database.py crypto_utils.py manage_users.py manage_encryption.py manage_archive.py wsgi.py
```

For frontend edits in `static/REOS.html`, check that the embedded JavaScript parses before committing.
