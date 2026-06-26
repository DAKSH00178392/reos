import os
import secrets
import time
import sqlite3
import zipfile
import tempfile
from datetime import datetime
from functools import wraps
from pathlib import Path
from flask import Flask, request, jsonify, render_template, Response, redirect, session, url_for, abort, send_file
import io
import csv
import json
import urllib.error
import urllib.request
try:
    from openpyxl import Workbook
    HAS_OPENPYXL = True
except Exception:
    HAS_OPENPYXL = False
import base64
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from crypto_utils import decrypt_records, encrypt_payload
from database import init_db, query_db, insert_db, update_db

app = Flask(__name__, static_folder='static', template_folder='static')
app.secret_key = os.environ.get('REOS_SECRET_KEY', 'change-this-secret-before-hosting')
app.config.update(
    MAX_CONTENT_LENGTH=int(os.environ.get('REOS_MAX_CONTENT_LENGTH', str(100 * 1024 * 1024))),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('REOS_COOKIE_SECURE', '0') == '1',
)
cors_origins = [origin.strip() for origin in os.environ.get('REOS_CORS_ORIGINS', '').split(',') if origin.strip()]
if cors_origins:
    CORS(app, supports_credentials=True, origins=cors_origins)
LOGIN_ATTEMPTS = {}
ARCHIVABLE_TABLES = {'leads', 'clients', 'properties', 'followups', 'meetings', 'shortlist', 'brokers'}
REGION_TABLES = {'leads', 'clients', 'properties', 'brokers'}
APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / 'reos.db'
ENCRYPTION_KEY_PATH = APP_DIR / '.reos_encryption_key'
BACKUP_DIR = APP_DIR / 'backups'
AUTO_BACKUP_MARKER = BACKUP_DIR / '.last_auto_backup'
REQUIRED_DB_TABLES = {'users', 'leads', 'clients', 'properties', 'followups', 'meetings', 'brokers'}

# Initialize database
init_db()


def ensure_default_user():
    existing = query_db("SELECT id FROM users LIMIT 1", one=True)
    if existing:
        return
    username = os.environ.get('REOS_ADMIN_USER', 'admin')
    password = os.environ.get('REOS_ADMIN_PASSWORD')
    if not password:
        raise RuntimeError('Set REOS_ADMIN_PASSWORD before first startup to create the admin user.')
    insert_db(
        "INSERT INTO users(username, password_hash, role, createdAt) VALUES(?,?,?,datetime('now'))",
        (username, generate_password_hash(password), 'admin')
    )


ensure_default_user()


def csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return token


app.jinja_env.globals['csrf_token'] = csrf_token


def wants_json():
    return request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json'


def request_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()


def allowed_host():
    allowed = os.environ.get('REOS_ALLOWED_HOSTS', '').strip()
    if not allowed:
        return True
    host = request.host.split(':', 1)[0].lower()
    allowed_hosts = {item.strip().lower() for item in allowed.split(',') if item.strip()}
    return host in allowed_hosts


def csrf_is_valid():
    expected = session.get('csrf_token')
    supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


@app.before_request
def security_gate():
    if not allowed_host():
        abort(400)

    public_routes = {'login', 'favicon', 'static'}
    if request.endpoint in public_routes:
        return None
    if not session.get('user_id'):
        if wants_json():
            return jsonify({"success": False, "error": "Authentication required"}), 401
        return redirect(url_for('login', next=request.path))
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and request.endpoint != 'login':
        if not csrf_is_valid():
            if wants_json():
                return jsonify({"success": False, "error": "Invalid security token"}), 403
            abort(403)
    return None


@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if request.path == '/' or request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    if os.environ.get('REOS_ENABLE_HSTS', '0') == '1':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped


def current_username():
    return session.get('username') or 'system'


def current_role():
    if session.get('role'):
        return session['role']
    if session.get('user_id'):
        user = query_db("SELECT role FROM users WHERE id=?", (session['user_id'],), one=True)
        if user:
            session['role'] = user.get('role') or 'staff'
            return session['role']
    return 'staff'


def current_region_scope():
    region = session.get('region')
    if region is not None:
        region = str(region).strip()
        return region or None
    if session.get('user_id'):
        user = query_db("SELECT region FROM users WHERE id=?", (session['user_id'],), one=True)
        if user:
            region = (user.get('region') or '').strip()
            session['region'] = region
            return region or None
    return None


def apply_current_region(payload):
    scope = current_region_scope()
    if scope:
        payload['region'] = scope
    return payload


def request_payload(table=None, force_region=False):
    payload = dict(request.json or {})
    if force_region or table in REGION_TABLES:
        payload = apply_current_region(payload)
    return encrypt_payload(table, payload) if table else payload


def same_region(row):
    scope = current_region_scope()
    if not scope:
        return True
    return same_text(effective_region(row), scope)


def effective_region(row):
    explicit = (row.get('region') or '').strip()
    if explicit:
        return explicit
    text = ' '.join(str(row.get(key) or '') for key in ['location', 'locationArea', 'locationCity', 'area']).casefold()
    if any(term in text for term in ['ahmedabad', 'amdavad', 'gandhinagar', 'sanand', 'bavla', 'changodar', 'narol', 'vatva']):
        return 'Ahmedabad Region'
    if any(term in text for term in ['vapi', 'chala', 'chharwada', 'gunjan', 'valsad', 'daman', 'silvassa', 'sarigam', 'bhilad', 'umbergaon']):
        return 'Vapi Region'
    if any(term in text for term in ['surat', 'hazira', 'palsana', 'kim', 'kamrej']):
        return 'Surat Region'
    return explicit


def scoped_entity_rows(table, query, args=()):
    rows = decrypt_records(table, query_db(query, args))
    if table in REGION_TABLES or 'region' in (rows[0].keys() if rows else []):
        return [row for row in rows if same_region(row)]
    return rows


def scoped_datasets(include_deleted=False):
    deleted_clause = "IS NOT NULL" if include_deleted else "IS NULL"
    leads = scoped_entity_rows('leads', f"SELECT * FROM leads WHERE deletedAt {deleted_clause}")
    clients = scoped_entity_rows('clients', f"SELECT * FROM clients WHERE deletedAt {deleted_clause}")
    properties = scoped_entity_rows('properties', f"SELECT * FROM properties WHERE deletedAt {deleted_clause}")
    brokers = scoped_entity_rows('brokers', f"SELECT * FROM brokers WHERE deletedAt {deleted_clause}")

    lead_ids = {str(row.get('id')) for row in leads}
    client_ids = {str(row.get('id')) for row in clients}
    property_ids = {str(row.get('id')) for row in properties}
    client_names = {str(row.get('name') or '').casefold() for row in clients}
    client_names.update(str(row.get('clientName') or '').casefold() for row in leads)
    property_names = {str(row.get('name') or '').casefold() for row in properties}

    followups = decrypt_records('followups', query_db(f"SELECT * FROM followups WHERE deletedAt {deleted_clause}"))
    followups = [row for row in followups if same_region(row) or (not row.get('region') and (
        str(row.get('leadId') or '') in lead_ids
        or str(row.get('clientId') or '') in client_ids
        or str(row.get('clientName') or '').casefold() in client_names
    ))]

    meetings = decrypt_records('meetings', query_db(f"SELECT * FROM meetings WHERE deletedAt {deleted_clause}"))
    meetings = [
        row for row in meetings
        if same_region(row) or (not row.get('region') and (
        str(row.get('leadId') or '') in lead_ids
        or str(row.get('clientId') or '') in client_ids
        or str(row.get('propertyId') or '') in property_ids
        or str(row.get('clientName') or '').casefold() in client_names
        or str(row.get('property') or '').casefold() in property_names
    ))]

    shortlist = decrypt_records('shortlist', query_db(f"SELECT * FROM shortlist WHERE deletedAt {deleted_clause}"))
    shortlist = [
        row for row in shortlist
        if same_region(row) or (not row.get('region') and (
        str(row.get('leadId') or '') in lead_ids
        or str(row.get('clientId') or '') in client_ids
        or str(row.get('propertyId') or '') in property_ids
        or str(row.get('clientName') or '').casefold() in client_names
        or str(row.get('propertyName') or '').casefold() in property_names
    ))]

    activities = decrypt_records('activities', query_db(f"SELECT * FROM activities WHERE deletedAt {deleted_clause} ORDER BY createdAt DESC, id DESC"))
    activities = [
        row for row in activities
        if same_region(row) or (not row.get('region') and (
        str(row.get('leadId') or '') in lead_ids
        or str(row.get('clientId') or '') in client_ids
        or str(row.get('propertyId') or '') in property_ids
        or str(row.get('clientName') or '').casefold() in client_names
    ))]

    return {
        'leads': leads,
        'clients': clients,
        'properties': properties,
        'brokers': brokers,
        'followups': followups,
        'meetings': meetings,
        'shortlist': shortlist,
        'activities': activities,
    }


def scoped_archived_contains(table, record_id):
    if not current_region_scope():
        return True
    return any(str(row.get('id')) == str(record_id) for row in scoped_datasets(include_deleted=True).get(table, []))


def make_backup(label='manual', created_by=None):
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    safe_label = ''.join(ch if ch.isalnum() or ch in {'-', '_'} else '-' for ch in str(label or 'manual')).strip('-') or 'manual'
    base_name = f"reos-backup-{safe_label}-{stamp}"
    temp_db = BACKUP_DIR / f"{base_name}.db"
    zip_path = BACKUP_DIR / f"{base_name}.zip"

    source = sqlite3.connect(DB_PATH)
    target = sqlite3.connect(temp_db)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    manifest = [
        f"createdAt={datetime.now().isoformat(timespec='seconds')}",
        f"createdBy={created_by or current_username()}",
        f"type={safe_label}",
        "contains=reos.db,.reos_encryption_key",
        "restore_note=Keep the database and encryption key together.",
    ]
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(temp_db, 'reos.db')
        if ENCRYPTION_KEY_PATH.exists():
            zf.write(ENCRYPTION_KEY_PATH, '.reos_encryption_key')
        zf.writestr('BACKUP_INFO.txt', '\n'.join(manifest) + '\n')
    temp_db.unlink(missing_ok=True)
    record_audit('backups', None, 'create', f"Backup created: {zip_path.name}")
    return zip_path


def validate_sqlite_db(path):
    try:
        conn = sqlite3.connect(path)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != 'ok':
                return False, 'SQLite integrity check failed'
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = sorted(REQUIRED_DB_TABLES - tables)
            if missing:
                return False, 'Missing required tables: ' + ', '.join(missing)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f'Invalid SQLite database: {exc}'
    return True, ''


def restore_sqlite_db_from_file(source_path, target_path):
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()


def backup_is_due_today():
    if os.environ.get('REOS_AUTO_BACKUP_ENABLED', '1') != '1':
        return False
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        return AUTO_BACKUP_MARKER.read_text(encoding='utf-8').strip() != today
    except FileNotFoundError:
        return True


def mark_auto_backup_done():
    BACKUP_DIR.mkdir(exist_ok=True)
    AUTO_BACKUP_MARKER.write_text(datetime.now().strftime('%Y-%m-%d'), encoding='utf-8')


def maybe_create_daily_backup():
    if not session.get('user_id') or not backup_is_due_today():
        return
    try:
        make_backup(label='auto', created_by='system-auto')
        mark_auto_backup_done()
    except Exception as exc:
        app.logger.exception('Automatic backup failed: %s', exc)


@app.before_request
def automatic_backup_gate():
    if request.endpoint in {'static', 'login', 'favicon'}:
        return None
    if request.path.startswith('/api/backups/upload'):
        return None
    maybe_create_daily_backup()
    return None


def extract_uploaded_database(uploaded_file, work_dir):
    filename = Path(uploaded_file.filename or '').name
    lower_name = filename.lower()
    if not filename:
        raise ValueError('No file selected')

    if lower_name.endswith('.zip'):
        archive_path = work_dir / filename
        uploaded_file.save(archive_path)
        with zipfile.ZipFile(archive_path) as zf:
            names = {Path(name).name: name for name in zf.namelist()}
            db_member = names.get('reos.db')
            if not db_member:
                raise ValueError('Backup ZIP must contain reos.db')
            extracted_db = work_dir / 'uploaded-reos.db'
            with zf.open(db_member) as src, open(extracted_db, 'wb') as dst:
                dst.write(src.read())
            key_member = names.get('.reos_encryption_key')
            extracted_key = None
            if key_member:
                extracted_key = work_dir / '.reos_encryption_key'
                with zf.open(key_member) as src, open(extracted_key, 'wb') as dst:
                    dst.write(src.read())
            return extracted_db, extracted_key, filename

    if lower_name.endswith(('.db', '.sqlite', '.sqlite3')):
        extracted_db = work_dir / 'uploaded-reos.db'
        uploaded_file.save(extracted_db)
        return extracted_db, None, filename

    raise ValueError('Upload a REOS backup .zip or SQLite .db file')


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_role() != 'admin':
            return jsonify({"success": False, "error": "Admin permission required"}), 403
        return view(*args, **kwargs)
    return wrapped


def global_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_role() != 'admin' or current_region_scope():
            if not wants_json():
                abort(403)
            return jsonify({"success": False, "error": "Global admin permission required"}), 403
        return view(*args, **kwargs)
    return wrapped


def record_audit(table, record_id, action, message):
    insert_db(
        "INSERT INTO audit_logs(tableName, recordId, action, username, message, createdAt) VALUES(?,?,?,?,?,?)",
        (table, record_id, action, current_username(), message, datetime.utcnow().isoformat(timespec='seconds')),
    )


def archive_record(table, record_id, message):
    update_db(
        f"UPDATE {table} SET deletedAt=?, deletedBy=? WHERE id=? AND deletedAt IS NULL",
        (datetime.utcnow().isoformat(timespec='seconds'), current_username(), record_id),
    )
    record_audit(table, record_id, 'archive', message)


def restore_record(table, record_id, message):
    if table not in ARCHIVABLE_TABLES:
        return False
    existing = query_db(f"SELECT id FROM {table} WHERE id=? AND deletedAt IS NOT NULL", (record_id,), one=True)
    if not existing:
        return False
    update_db(
        f"UPDATE {table} SET deletedAt=NULL, deletedBy=NULL WHERE id=?",
        (record_id,),
    )
    record_audit(table, record_id, 'restore', message)
    return True


def same_text(left, right):
    return str(left or '').strip().casefold() == str(right or '').strip().casefold()


def lead_matches_client(lead, client):
    client_id = client.get('id')
    client_name = client.get('name')
    client_phone = client.get('phone')
    if client_id and str(lead.get('clientId') or '') == str(client_id):
        return True
    if client_phone and same_text(lead.get('phone'), client_phone):
        return True
    if client_name and same_text(lead.get('clientName'), client_name):
        return True
    return False


def client_has_active_lead(client):
    leads = decrypt_records('leads', query_db("SELECT id, clientId, clientName, phone FROM leads WHERE deletedAt IS NULL"))
    for lead in leads:
        if lead_matches_client(lead, client):
            return lead
    return None


def find_archived_client_leads(client):
    leads = decrypt_records('leads', query_db("SELECT id, clientId, clientName, phone FROM leads WHERE deletedAt IS NOT NULL"))
    matched = []
    for lead in leads:
        if lead_matches_client(lead, client):
            matched.append(lead)
    return matched


def find_active_client_for_lead(lead):
    clients = decrypt_records('clients', query_db("SELECT id, name, phone FROM clients WHERE deletedAt IS NULL"))
    for client in clients:
        if lead_matches_client(lead, client):
            return client
    return None


def client_has_other_active_leads(client, archived_lead_id):
    leads = decrypt_records('leads', query_db("SELECT id, clientId, clientName, phone FROM leads WHERE deletedAt IS NULL"))
    for lead in leads:
        if int(lead.get('id') or 0) == int(archived_lead_id):
            continue
        if lead_matches_client(lead, client):
            return True
    return False


def restore_lead_related_records(lead_id):
    related_labels = {'followups': 'Follow-up', 'meetings': 'Meeting', 'shortlist': 'Shortlist'}
    restored = {}
    for table in ['followups', 'meetings', 'shortlist']:
        rows = query_db(f"SELECT id FROM {table} WHERE leadId=? AND deletedAt IS NOT NULL", (lead_id,))
        restored_ids = []
        for row in rows:
            if restore_record(table, row['id'], f"{related_labels[table]} restored with lead #{lead_id}"):
                restored_ids.append(row['id'])
        if restored_ids:
            restored[table] = restored_ids
    return restored

@app.route('/')
@login_required
def index():
    return render_template('REOS.html', current_user=current_username())


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        ip = request_ip()
        attempts = [ts for ts in LOGIN_ATTEMPTS.get(ip, []) if time.time() - ts < 900]
        LOGIN_ATTEMPTS[ip] = attempts
        if len(attempts) >= 5:
            error = 'Too many login attempts. Try again after 15 minutes.'
            return render_template('login.html', error=error), 429
        user = query_db("SELECT * FROM users WHERE username=?", (username,), one=True)
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            csrf_token()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user.get('role') or 'staff'
            session['region'] = (user.get('region') or '').strip()
            LOGIN_ATTEMPTS.pop(ip, None)
            return redirect(request.args.get('next') or url_for('index'))
        attempts.append(time.time())
        LOGIN_ATTEMPTS[ip] = attempts
        error = 'Invalid username or password.'
    return render_template('login.html', error=error)


@app.route('/change-password', methods=['GET', 'POST'])
@login_required
@global_admin_required
def change_password():
    error = None
    success = None
    if request.method == 'POST':
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        user = query_db("SELECT * FROM users WHERE id=?", (session['user_id'],), one=True)

        if not user or not check_password_hash(user['password_hash'], current_password):
            error = 'Current password is incorrect.'
        elif len(new_password) < 8:
            error = 'New password must be at least 8 characters.'
        elif new_password != confirm_password:
            error = 'New passwords do not match.'
        else:
            update_db(
                "UPDATE users SET password_hash=? WHERE id=?",
                (generate_password_hash(new_password), session['user_id'])
            )
            success = 'Password changed successfully.'

    return render_template('change_password.html', error=error, success=success)


@app.route('/logout', methods=['POST', 'GET'])
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/favicon.ico')
def favicon():
    # 1x1 transparent PNG (served from memory to avoid a missing static file)
    png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    png = base64.b64decode(png_b64)
    return Response(png, mimetype='image/png')

@app.route('/api/data', methods=['GET'])
def get_all_data():
    data = scoped_datasets()
    return jsonify({
        'leads': data['leads'],
        'clients': data['clients'],
        'properties': data['properties'],
        'followups': data['followups'],
        'meetings': data['meetings'],
        'brokers': data['brokers'],
        'shortlist': data['shortlist'],
        'activities': data['activities'],
        'regions': query_db("SELECT * FROM regions ORDER BY name"),
        'workspace': {'user': current_username(), 'region': current_region_scope() or 'all'}
    })


@app.route('/api/users', methods=['GET'])
@global_admin_required
def get_users():
    users = query_db("SELECT id, username, role, region, createdAt FROM users ORDER BY id")
    return jsonify(users)


@app.route('/api/users/<int:id>/password', methods=['PUT'])
@global_admin_required
def reset_user_password(id):
    payload = request.json or {}
    password = payload.get('password') or ''
    confirm = payload.get('confirm') or ''
    if len(password) < 8:
        return jsonify({"success": False, "error": "Password must be at least 8 characters"}), 400
    if password != confirm:
        return jsonify({"success": False, "error": "Passwords do not match"}), 400
    user = query_db("SELECT id, username FROM users WHERE id=?", (id,), one=True)
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404
    update_db("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), id))
    record_audit('users', id, 'password_reset', f"Password reset for {user['username']}")
    return jsonify({"success": True})


def compact_crm_context():
    data = scoped_datasets()
    active_leads = [row for row in data['leads'] if row.get('status') != 'Closed']
    closed_leads = [row for row in data['leads'] if row.get('status') == 'Closed']
    due_followups = [
        row for row in data['followups']
        if not row.get('done') and (not row.get('dueDate') or row.get('dueDate') <= datetime.utcnow().date().isoformat() or row.get('priority') == 'urgent')
    ]
    available_props = [row for row in data['properties'] if row.get('status') == 'Available']
    unavailable_props = [row for row in data['properties'] if row.get('status') != 'Available']
    return {
        'workspace': current_region_scope() or 'all',
        'counts': {
            'activeLeads': len(active_leads),
            'closedLeads': len(closed_leads),
            'clients': len(data['clients']),
            'availableProperties': len(available_props),
            'unavailableProperties': len(unavailable_props),
            'dueFollowUps': len(due_followups),
            'meetings': len(data['meetings']),
        },
        'activeLeads': [{
            'id': row.get('id'),
            'client': row.get('clientName'),
            'status': row.get('status'),
            'priority': row.get('priority'),
            'requirement': f"{row.get('dtype') or ''} {row.get('ptype') or ''} {row.get('config') or ''}".strip(),
            'location': row.get('location'),
            'budget': row.get('budget'),
            'nextAction': row.get('nextAction'),
            'assignedTo': row.get('assignedTo'),
            'lastContact': row.get('lastContact') or row.get('added'),
        } for row in active_leads[:30]],
        'dueFollowUps': [{
            'id': row.get('id'),
            'client': row.get('clientName'),
            'action': row.get('action'),
            'priority': row.get('priority'),
            'dueDate': row.get('dueDate'),
            'assignedTo': row.get('assignedTo'),
        } for row in due_followups[:30]],
        'availableProperties': [{
            'id': row.get('id'),
            'name': row.get('name'),
            'type': row.get('type'),
            'deal': row.get('dtype'),
            'location': row.get('location'),
            'price': row.get('price'),
            'area': row.get('area'),
        } for row in available_props[:30]],
        'dataGaps': [
            {'type': 'closed_lead_no_property', 'leadId': row.get('id'), 'client': row.get('clientName')}
            for row in closed_leads
            if row.get('closedByType') != 'Lost' and not row.get('closedPropertyId')
        ],
    }


def extract_response_text(payload):
    if payload.get('output_text'):
        return payload['output_text']
    parts = []
    for item in payload.get('output', []):
        for content in item.get('content', []):
            if content.get('type') in {'output_text', 'text'} and content.get('text'):
                parts.append(content['text'])
    return '\n'.join(parts).strip()


@app.route('/api/assistant/chat', methods=['POST'])
def assistant_chat():
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not api_key:
        return jsonify({
            "success": False,
            "error": "AI is not configured. Set OPENAI_API_KEY on the server to enable chat."
        }), 400
    question = (request.json or {}).get('message', '').strip()
    if not question:
        return jsonify({"success": False, "error": "Ask a question first."}), 400
    context = compact_crm_context()
    instructions = (
        "You are REOS Assistant for a real estate CRM. Be concise, practical, and action-oriented. "
        "Use only the CRM context provided. Do not invent records. If data is missing, say exactly what is missing. "
        "Return clear next actions for brokers and admins. Never reveal or request passwords."
    )
    body = {
        "model": os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini'),
        "instructions": instructions,
        "input": f"CRM context JSON:\n{json.dumps(context, ensure_ascii=False)}\n\nUser question:\n{question}",
        "max_output_tokens": 700,
        "store": False,
    }
    req = urllib.request.Request(
        'https://api.openai.com/v1/responses',
        data=json.dumps(body).encode('utf-8'),
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        return jsonify({"success": False, "error": f"AI request failed: {detail[:300]}"}), 502
    except Exception as exc:
        return jsonify({"success": False, "error": f"AI request failed: {exc}"}), 502
    answer = extract_response_text(result)
    return jsonify({"success": True, "answer": answer or "No answer returned.", "configured": True})


@app.route('/api/audit', methods=['GET'])
@admin_required
def get_audit_logs():
    return jsonify(query_db("SELECT * FROM audit_logs ORDER BY createdAt DESC, id DESC LIMIT 500"))


@app.route('/api/backups', methods=['GET'])
@global_admin_required
def list_backups():
    BACKUP_DIR.mkdir(exist_ok=True)
    rows = []
    for path in sorted(BACKUP_DIR.glob('reos-backup-*.zip'), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        rows.append({
            "filename": path.name,
            "size": stat.st_size,
            "createdAt": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds'),
        })
    return jsonify(rows)


@app.route('/api/backups', methods=['POST'])
@global_admin_required
def create_backup():
    backup = make_backup()
    return jsonify({"success": True, "filename": backup.name, "size": backup.stat().st_size})


@app.route('/api/backups/<filename>', methods=['GET'])
@global_admin_required
def download_backup(filename):
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.startswith('reos-backup-') or not safe_name.endswith('.zip'):
        return jsonify({"success": False, "error": "Unknown backup file"}), 404
    path = BACKUP_DIR / safe_name
    if not path.exists():
        return jsonify({"success": False, "error": "Backup not found"}), 404
    return send_file(path, as_attachment=True, download_name=safe_name, mimetype='application/zip')


@app.route('/api/backups/upload', methods=['POST'])
@global_admin_required
def upload_backup():
    uploaded = request.files.get('backup')
    if not uploaded:
        return jsonify({"success": False, "error": "Choose a backup ZIP or reos.db file"}), 400

    BACKUP_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=BACKUP_DIR) as temp_name:
        work_dir = Path(temp_name)
        try:
            uploaded_db, uploaded_key, original_name = extract_uploaded_database(uploaded, work_dir)
            valid, error = validate_sqlite_db(uploaded_db)
            if not valid:
                return jsonify({"success": False, "error": error}), 400

            safety_backup = make_backup(label='before-upload-restore', created_by=current_username())
            restore_sqlite_db_from_file(uploaded_db, DB_PATH)
            if uploaded_key and uploaded_key.exists():
                os.replace(uploaded_key, ENCRYPTION_KEY_PATH)
            init_db()
            record_audit('backups', None, 'restore', f"Database uploaded from {original_name}; safety backup: {safety_backup.name}")
            return jsonify({
                "success": True,
                "message": "Database uploaded successfully. Login again if your current session no longer matches the uploaded database.",
                "safetyBackup": safety_backup.name,
                "replacedEncryptionKey": bool(uploaded_key),
            })
        except zipfile.BadZipFile:
            return jsonify({"success": False, "error": "Invalid backup ZIP file"}), 400
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        except Exception as exc:
            app.logger.exception('Database upload failed: %s', exc)
            return jsonify({"success": False, "error": "Database upload failed. Current database was not replaced unless validation completed."}), 500


@app.route('/api/archived/<table>', methods=['GET'])
@admin_required
def get_archived_records(table):
    if table not in ARCHIVABLE_TABLES:
        return jsonify({"success": False, "error": "Unknown archive table"}), 404
    if current_region_scope():
        data = scoped_datasets(include_deleted=True)
        return jsonify(sorted(data.get(table, []), key=lambda row: (row.get('deletedAt') or '', row.get('id') or 0), reverse=True))
    return jsonify(decrypt_records(table, query_db(f"SELECT * FROM {table} WHERE deletedAt IS NOT NULL ORDER BY deletedAt DESC, id DESC")))


@app.route('/api/regions', methods=['GET'])
def get_regions():
    return jsonify(query_db("SELECT * FROM regions ORDER BY name"))


@app.route('/api/regions', methods=['POST'])
def add_region():
    name = (request.json or {}).get('name', '').strip()
    if not name:
        return jsonify({"success": False, "error": "Region name is required"}), 400
    existing = query_db("SELECT id, name FROM regions WHERE lower(name)=lower(?)", (name,), one=True)
    if existing:
        return jsonify({"success": True, "id": existing['id'], "name": existing['name']})
    region_id = insert_db("INSERT INTO regions(name, createdAt) VALUES(?, datetime('now'))", (name,))
    record_audit('regions', region_id, 'create', f"Region created: {name}")
    return jsonify({"success": True, "id": region_id, "name": name})

# --- LEADS ---
@app.route('/api/leads', methods=['GET'])
def get_leads():
    return jsonify(scoped_datasets()['leads'])


@app.route('/api/leads/export', methods=['GET'])
@admin_required
def export_leads():
    fmt = request.args.get('format', 'csv').lower()
    leads = scoped_datasets()['leads']
    # CSV export (default)
    if fmt != 'xlsx':
        si = io.StringIO()
        writer = csv.writer(si)
        if leads:
            headers = list(leads[0].keys())
            writer.writerow(headers)
            for row in leads:
                writer.writerow([row.get(h) for h in headers])
        else:
            writer.writerow(['No leads'])
        output = si.getvalue()
        return Response(output, mimetype='text/csv', headers={"Content-Disposition": "attachment; filename=leads.csv"})

    # XLSX export
    if not HAS_OPENPYXL:
        return jsonify({"success": False, "error": "openpyxl not installed on server"}), 500
    wb = Workbook()
    ws = wb.active
    if leads:
        headers = list(leads[0].keys())
        ws.append(headers)
        for row in leads:
            ws.append([row.get(h) for h in headers])
    else:
        ws.append(['No leads'])
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return Response(bio.read(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={"Content-Disposition": "attachment; filename=leads.xlsx"})


def _perform_export(rows, selected_fields=None, filename_prefix='export', fmt='csv'):
    # rows: list of dicts
    if selected_fields:
        headers = selected_fields
        # project rows
        proj = [{h: r.get(h) for h in headers} for r in rows]
    else:
        headers = list(rows[0].keys()) if rows else []
        proj = rows

    if fmt != 'xlsx':
        si = io.StringIO()
        writer = csv.writer(si)
        if headers:
            writer.writerow(headers)
            for row in proj:
                writer.writerow([row.get(h) for h in headers])
        else:
            writer.writerow(['No data'])
        output = si.getvalue()
        return Response(output, mimetype='text/csv', headers={"Content-Disposition": f"attachment; filename={filename_prefix}.csv"})

    if not HAS_OPENPYXL:
        return jsonify({"success": False, "error": "openpyxl not installed on server"}), 500
    wb = Workbook()
    ws = wb.active
    if headers:
        ws.append(headers)
        for row in proj:
            ws.append([row.get(h) for h in headers])
    else:
        ws.append(['No data'])
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return Response(bio.read(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={"Content-Disposition": f"attachment; filename={filename_prefix}.xlsx"})


@app.route('/api/properties/export', methods=['GET'])
@admin_required
def export_properties():
    fmt = request.args.get('format', 'csv').lower()
    fields = request.args.get('fields')  # comma-separated
    cat = request.args.get('cat')  # comma-separated categories

    # load all properties
    props = scoped_datasets()['properties']
    if not props:
        return _perform_export([], selected_fields=None, filename_prefix='properties', fmt=fmt)

    allowed = list(props[0].keys())
    selected_fields = None
    if fields:
        arr = [f.strip() for f in fields.split(',') if f.strip()]
        # validate
        selected_fields = [f for f in arr if f in allowed]
        if not selected_fields:
            selected_fields = None

    filtered = props
    if cat:
        cats = [c.strip().lower() for c in cat.split(',') if c.strip()]
        filtered = [p for p in props if (p.get('cat') or '').lower() in cats]

    return _perform_export(filtered, selected_fields=selected_fields, filename_prefix='properties', fmt=fmt)


@app.route('/api/clients/export', methods=['GET'])
@admin_required
def export_clients():
    fmt = request.args.get('format', 'csv').lower()
    fields = request.args.get('fields')
    clients = scoped_datasets()['clients']
    if not clients:
        return _perform_export([], selected_fields=None, filename_prefix='clients', fmt=fmt)
    allowed = list(clients[0].keys())
    selected_fields = None
    if fields:
        arr = [f.strip() for f in fields.split(',') if f.strip()]
        selected_fields = [f for f in arr if f in allowed]
        if not selected_fields:
            selected_fields = None
    return _perform_export(clients, selected_fields=selected_fields, filename_prefix='clients', fmt=fmt)

@app.route('/api/leads', methods=['POST'])
def add_lead():
    d = request_payload('leads')
    id = insert_db("""
        INSERT INTO leads(clientId, clientName, phone, category, ptype, canonicalType, dtype, location, locationArea, locationCity, region, createdBy, budget, minBudget, maxBudget, config, minArea, maxArea, areaUnit, bhk, spec, furnishing, powerRequirement, source, assignedTo, lastContact, nextAction, priority, status, notes, added, stageUpdatedAt)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (d.get('clientId'), d.get('clientName'), d.get('phone'), d.get('category'), d.get('ptype'), d.get('canonicalType'), d.get('dtype'), d.get('location'),
          d.get('locationArea'), d.get('locationCity'), d.get('region'), d.get('createdBy') or current_username(), d.get('budget'), d.get('minBudget'), d.get('maxBudget'), d.get('config'),
          d.get('minArea'), d.get('maxArea'), d.get('areaUnit'), d.get('bhk'), d.get('spec'), d.get('furnishing'), d.get('powerRequirement'),
          d.get('source'), d.get('assignedTo'), d.get('lastContact'), d.get('nextAction'), d.get('priority'), d.get('status'), d.get('notes'), d.get('added'), d.get('stageUpdatedAt')))
    record_audit('leads', id, 'create', 'Lead created')
    return jsonify({"success": True, "id": id})

@app.route('/api/leads/<int:id>', methods=['PUT'])
def update_lead(id):
    existing = scoped_entity_rows('leads', "SELECT * FROM leads WHERE id=? AND deletedAt IS NULL", (id,))
    if not existing:
        return jsonify({"success": False, "error": "Lead not found in this region"}), 404
    d = request_payload('leads')
    fields = []
    args = []
    allowed = ['status', 'lastContact', 'nextAction', 'priority', 'assignedTo', 'notes', 'category', 'ptype', 'canonicalType', 'dtype', 'location', 'locationArea', 'locationCity', 'region', 'budget', 'minBudget', 'maxBudget', 'config', 'minArea', 'maxArea', 'areaUnit', 'bhk', 'spec', 'furnishing', 'powerRequirement', 'clientId', 'phone', 'stageUpdatedAt', 'closedBy', 'closedByType', 'closedPropertyId', 'closedPropertyName', 'closedDate', 'closedValue', 'closeReason']
    for field in allowed:
        if field in d:
            fields.append(f"{field}=?")
            args.append(d[field])
    if fields:
        args.append(id)
        update_db(f"UPDATE leads SET {', '.join(fields)} WHERE id=?", tuple(args))
        record_audit('leads', id, 'update', 'Lead updated')
    return jsonify({"success": True})

@app.route('/api/leads/<int:id>', methods=['DELETE'])
def delete_lead(id):
    lead_rows = scoped_entity_rows('leads', "SELECT id, clientId, clientName, phone, region FROM leads WHERE id=? AND deletedAt IS NULL", (id,))
    if not lead_rows:
        return jsonify({"success": False, "error": "Active lead not found"}), 404
    lead = lead_rows[0]
    client = find_active_client_for_lead(lead)
    archive_record('leads', id, 'Lead archived')
    related_labels = {'followups': 'Follow-up', 'meetings': 'Meeting', 'shortlist': 'Shortlist'}
    for table in ['followups', 'meetings', 'shortlist']:
        linked_rows = query_db(f"SELECT id FROM {table} WHERE leadId=? AND deletedAt IS NULL", (id,))
        for row in linked_rows:
            archive_record(table, row['id'], f"{related_labels[table]} archived with lead #{id}")
    archived_client_id = None
    if client and not client_has_other_active_leads(client, id):
        archive_record('clients', client['id'], f"Client archived with lead #{id}")
        archived_client_id = client['id']
        for table in ['followups', 'meetings']:
            linked_rows = query_db(f"SELECT id FROM {table} WHERE clientId=? AND deletedAt IS NULL", (client['id'],))
            for row in linked_rows:
                archive_record(table, row['id'], f"{related_labels[table]} archived with client #{client['id']}")
    return jsonify({"success": True, "archivedClientId": archived_client_id})


@app.route('/api/leads/<int:id>/restore', methods=['POST'])
@admin_required
def restore_lead(id):
    if not scoped_archived_contains('leads', id):
        return jsonify({"success": False, "error": "Archived lead not found in this region"}), 404
    lead_rows = decrypt_records('leads', query_db("SELECT id, clientId FROM leads WHERE id=?", (id,)))
    lead = lead_rows[0] if lead_rows else {}
    if not restore_record('leads', id, 'Lead restored'):
        return jsonify({"success": False, "error": "Archived lead not found"}), 404
    restored_client_id = None
    if lead.get('clientId'):
        client = query_db("SELECT id FROM clients WHERE id=? AND deletedAt IS NOT NULL", (lead.get('clientId'),), one=True)
        if client and restore_record('clients', client['id'], f"Client restored with lead #{id}"):
            restored_client_id = client['id']
    restored_related = restore_lead_related_records(id)
    return jsonify({"success": True, "restoredClientId": restored_client_id, "restoredRelated": restored_related})

# --- CLIENTS ---
@app.route('/api/clients', methods=['GET'])
def get_clients():
    return jsonify(scoped_datasets()['clients'])

@app.route('/api/clients', methods=['POST'])
def add_client():
    d = request_payload('clients')
    id = insert_db("""
        INSERT INTO clients(name, phone, email, budget, req, location, locationArea, locationCity, region, createdBy, interest, source, added)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (d.get('name'), d.get('phone'), d.get('email'), d.get('budget'), d.get('req'), d.get('location'), d.get('locationArea'), d.get('locationCity'), d.get('region'), d.get('createdBy') or current_username(), d.get('interest'), d.get('source'), d.get('added')))
    record_audit('clients', id, 'create', 'Client created')
    return jsonify({"success": True, "id": id})

@app.route('/api/clients/<int:id>', methods=['PUT'])
def update_client(id):
    existing = scoped_entity_rows('clients', "SELECT * FROM clients WHERE id=? AND deletedAt IS NULL", (id,))
    if not existing:
        return jsonify({"success": False, "error": "Client not found in this region"}), 404
    d = request_payload('clients')
    update_db("""
        UPDATE clients 
        SET name=?, phone=?, email=?, budget=?, req=?, location=?, locationArea=?, locationCity=?, region=?, interest=?, source=? 
        WHERE id=?
    """, (d.get('name'), d.get('phone'), d.get('email'), d.get('budget'), d.get('req'), d.get('location'), d.get('locationArea'), d.get('locationCity'), d.get('region'), d.get('interest'), d.get('source'), id))
    record_audit('clients', id, 'update', 'Client updated')
    return jsonify({"success": True})

@app.route('/api/clients/<int:id>', methods=['DELETE'])
def delete_client(id):
    client = scoped_entity_rows('clients', "SELECT * FROM clients WHERE id=? AND deletedAt IS NULL", (id,))
    if not client:
        return jsonify({"success": False, "error": "Active client not found"}), 404
    linked = client_has_active_lead(client[0])
    if linked:
        return jsonify({"success": False, "error": f"Client has active lead #{linked.get('id')}. Archive or close that lead first."}), 409
    archive_record('clients', id, 'Client archived')
    return jsonify({"success": True})


@app.route('/api/clients/<int:id>/restore', methods=['POST'])
@admin_required
def restore_client(id):
    if not scoped_archived_contains('clients', id):
        return jsonify({"success": False, "error": "Archived client not found in this region"}), 404
    client_rows = decrypt_records('clients', query_db("SELECT * FROM clients WHERE id=?", (id,)))
    if not client_rows:
        return jsonify({"success": False, "error": "Client not found"}), 404

    client = client_rows[0]
    client_was_archived = client.get('deletedAt') is not None
    if client_was_archived and not restore_record('clients', id, 'Client restored'):
        return jsonify({"success": False, "error": "Archived client not found"}), 404

    restored_leads = []
    restored_related = {}
    for lead in find_archived_client_leads(client):
        if restore_record('leads', lead['id'], f"Lead restored with client #{id}"):
            restored_leads.append(lead['id'])
            related = restore_lead_related_records(lead['id'])
            if related:
                restored_related[lead['id']] = related

    return jsonify({"success": True, "restoredLeads": restored_leads, "restoredRelated": restored_related})

# --- PROPERTIES ---
@app.route('/api/properties', methods=['GET'])
def get_properties():
    return jsonify(scoped_datasets()['properties'])

@app.route('/api/properties', methods=['POST'])
def add_property():
    d = request_payload('properties')
    id = insert_db("""
        INSERT INTO properties(name, cat, type, canonicalType, dtype, location, locationArea, locationCity, region, createdBy, price, area, areaValue, areaUnit, bhk, furnish, power, powerValue, owner, ophone, desc, status, emoji, added, listedBy, brokerName, brokerPhone, parking, amenities, roadWidth, shedHeight)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (d.get('name'), d.get('cat'), d.get('type'), d.get('canonicalType'), d.get('dtype'), d.get('location'), d.get('locationArea'), d.get('locationCity'),
          d.get('region'), d.get('createdBy') or current_username(), d.get('price'), d.get('area'), d.get('areaValue'), d.get('areaUnit'), d.get('bhk'), d.get('furnish'), d.get('power'), d.get('powerValue'), d.get('owner'), d.get('ophone'), d.get('desc'), d.get('status'), d.get('emoji'), d.get('added'), d.get('listedBy'), d.get('brokerName'), d.get('brokerPhone'), d.get('parking'), d.get('amenities'), d.get('roadWidth'), d.get('shedHeight')))
    record_audit('properties', id, 'create', 'Property created')
    return jsonify({"success": True, "id": id})

@app.route('/api/properties/<int:id>', methods=['PUT'])
def update_property(id):
    existing = scoped_entity_rows('properties', "SELECT * FROM properties WHERE id=? AND deletedAt IS NULL", (id,))
    if not existing:
        return jsonify({"success": False, "error": "Property not found in this region"}), 404
    d = request_payload('properties')
    fields = []
    args = []
    allowed = ['name', 'cat', 'type', 'canonicalType', 'dtype', 'location', 'locationArea', 'locationCity', 'region', 'price', 'area', 'areaValue', 'areaUnit', 'bhk', 'furnish', 'power', 'powerValue', 'owner', 'ophone', 'desc', 'status', 'emoji', 'listedBy', 'brokerName', 'brokerPhone', 'parking', 'amenities', 'roadWidth', 'shedHeight']
    for field in allowed:
        if field in d:
            fields.append(f"{field}=?")
            args.append(d[field])
    if fields:
        args.append(id)
        update_db(f"UPDATE properties SET {', '.join(fields)} WHERE id=?", tuple(args))
        record_audit('properties', id, 'update', 'Property updated')
    return jsonify({"success": True})

@app.route('/api/properties/<int:id>', methods=['DELETE'])
def delete_property(id):
    existing = scoped_entity_rows('properties', "SELECT * FROM properties WHERE id=? AND deletedAt IS NULL", (id,))
    if not existing:
        return jsonify({"success": False, "error": "Property not found in this region"}), 404
    linked_meeting = query_db("SELECT id FROM meetings WHERE propertyId=? AND deletedAt IS NULL LIMIT 1", (id,), one=True)
    linked_shortlist = query_db("SELECT id FROM shortlist WHERE propertyId=? AND deletedAt IS NULL LIMIT 1", (id,), one=True)
    linked_deal = query_db("SELECT id FROM leads WHERE closedPropertyId=? AND deletedAt IS NULL LIMIT 1", (id,), one=True)
    if linked_meeting or linked_shortlist or linked_deal:
        return jsonify({"success": False, "error": "Property is linked to meetings, shortlists, or closed deals. Keep it archived by status instead of deleting."}), 409
    archive_record('properties', id, 'Property archived')
    return jsonify({"success": True})


@app.route('/api/properties/<int:id>/restore', methods=['POST'])
@admin_required
def restore_property(id):
    if not scoped_archived_contains('properties', id):
        return jsonify({"success": False, "error": "Archived property not found in this region"}), 404
    if not restore_record('properties', id, 'Property restored'):
        return jsonify({"success": False, "error": "Archived property not found"}), 404
    return jsonify({"success": True})

# --- FOLLOWUPS ---
@app.route('/api/followups', methods=['GET'])
def get_followups():
    return jsonify(scoped_datasets()['followups'])

@app.route('/api/followups', methods=['POST'])
def add_followup():
    d = request_payload('followups', force_region=True)
    id = insert_db("""
        INSERT INTO followups(clientId, leadId, region, clientName, action, priority, dueDate, dueTime, notes, done, assignedTo, status, completedAt, completedBy, snoozedUntil, createdBy)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (d.get('clientId'), d.get('leadId'), d.get('region'), d.get('clientName'), d.get('action'), d.get('priority'), d.get('dueDate'), d.get('dueTime'), d.get('notes'), d.get('done', 0),
          d.get('assignedTo') or current_username(), d.get('status') or 'pending', d.get('completedAt'), d.get('completedBy'), d.get('snoozedUntil'), d.get('createdBy') or current_username()))
    record_audit('followups', id, 'create', 'Follow-up created')
    return jsonify({"success": True, "id": id})

@app.route('/api/followups/<int:id>', methods=['PUT'])
def update_followup(id):
    if not any(str(row.get('id')) == str(id) for row in scoped_datasets()['followups']):
        return jsonify({"success": False, "error": "Follow-up not found in this region"}), 404
    d = encrypt_payload('followups', request.json or {})
    fields = []
    args = []
    for field in ['done', 'action', 'priority', 'dueDate', 'dueTime', 'notes', 'leadId', 'clientId', 'assignedTo', 'status', 'completedAt', 'completedBy', 'snoozedUntil']:
        if field in d:
            fields.append(f"{field}=?")
            args.append(d[field])
    if fields:
        args.append(id)
        update_db(f"UPDATE followups SET {', '.join(fields)} WHERE id=?", tuple(args))
        record_audit('followups', id, 'update', 'Follow-up updated')
    return jsonify({"success": True})

@app.route('/api/followups/<int:id>', methods=['DELETE'])
def delete_followup(id):
    if not any(str(row.get('id')) == str(id) for row in scoped_datasets()['followups']):
        return jsonify({"success": False, "error": "Follow-up not found in this region"}), 404
    archive_record('followups', id, 'Follow-up archived')
    return jsonify({"success": True})


@app.route('/api/followups/<int:id>/restore', methods=['POST'])
@admin_required
def restore_followup(id):
    if not scoped_archived_contains('followups', id):
        return jsonify({"success": False, "error": "Archived follow-up not found in this region"}), 404
    if not restore_record('followups', id, 'Follow-up restored'):
        return jsonify({"success": False, "error": "Archived follow-up not found"}), 404
    return jsonify({"success": True})

# --- MEETINGS ---
@app.route('/api/meetings', methods=['GET'])
def get_meetings():
    return jsonify(scoped_datasets()['meetings'])

@app.route('/api/meetings', methods=['POST'])
def add_meeting():
    d = request_payload('meetings', force_region=True)
    id = insert_db("""
        INSERT INTO meetings(clientId, leadId, propertyId, region, clientName, property, date, time, type, status, notes, outcome)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
    """, (d.get('clientId'), d.get('leadId'), d.get('propertyId'), d.get('region'), d.get('clientName'), d.get('property'), d.get('date'), d.get('time'), d.get('type'), d.get('status'), d.get('notes'), d.get('outcome')))
    record_audit('meetings', id, 'create', 'Meeting created')
    return jsonify({"success": True, "id": id})

@app.route('/api/meetings/<int:id>', methods=['PUT'])
def update_meeting(id):
    if not any(str(row.get('id')) == str(id) for row in scoped_datasets()['meetings']):
        return jsonify({"success": False, "error": "Meeting not found in this region"}), 404
    d = encrypt_payload('meetings', request.json or {})
    fields = []
    args = []
    for field in ['status', 'outcome', 'notes', 'leadId', 'clientId', 'propertyId', 'date', 'time', 'type']:
        if field in d:
            fields.append(f"{field}=?")
            args.append(d[field])
    if fields:
        args.append(id)
        update_db(f"UPDATE meetings SET {', '.join(fields)} WHERE id=?", tuple(args))
        record_audit('meetings', id, 'update', 'Meeting updated')
    return jsonify({"success": True})

# --- SHORTLIST ---
@app.route('/api/shortlist', methods=['GET'])
def get_shortlist():
    return jsonify(scoped_datasets()['shortlist'])

@app.route('/api/shortlist', methods=['POST'])
def add_shortlist():
    d = request_payload('shortlist', force_region=True)
    id = insert_db("""
        INSERT INTO shortlist(clientId, leadId, propertyId, region, clientName, propertyName, sent, added)
        VALUES(?,?,?,?,?,?,?,?)
    """, (d.get('clientId'), d.get('leadId'), d.get('propertyId'), d.get('region'), d.get('clientName'), d.get('propertyName'), d.get('sent', 0), d.get('added')))
    record_audit('shortlist', id, 'create', 'Shortlist item created')
    return jsonify({"success": True, "id": id})

@app.route('/api/shortlist/<int:id>', methods=['PUT'])
def update_shortlist(id):
    if not any(str(row.get('id')) == str(id) for row in scoped_datasets()['shortlist']):
        return jsonify({"success": False, "error": "Shortlist item not found in this region"}), 404
    d = encrypt_payload('shortlist', request.json or {})
    fields = []
    args = []
    for field in ['sent', 'leadId', 'clientId', 'propertyId']:
        if field in d:
            fields.append(f"{field}=?")
            args.append(d[field])
    if fields:
        args.append(id)
        update_db(f"UPDATE shortlist SET {', '.join(fields)} WHERE id=?", tuple(args))
        record_audit('shortlist', id, 'update', 'Shortlist item updated')
    return jsonify({"success": True})

@app.route('/api/shortlist/<int:id>', methods=['DELETE'])
def delete_shortlist(id):
    if not any(str(row.get('id')) == str(id) for row in scoped_datasets()['shortlist']):
        return jsonify({"success": False, "error": "Shortlist item not found in this region"}), 404
    archive_record('shortlist', id, 'Shortlist item archived')
    return jsonify({"success": True})


@app.route('/api/shortlist/<int:id>/restore', methods=['POST'])
@admin_required
def restore_shortlist(id):
    if not scoped_archived_contains('shortlist', id):
        return jsonify({"success": False, "error": "Archived shortlist item not found in this region"}), 404
    if not restore_record('shortlist', id, 'Shortlist item restored'):
        return jsonify({"success": False, "error": "Archived shortlist item not found"}), 404
    return jsonify({"success": True})

# --- ACTIVITIES ---
@app.route('/api/activities', methods=['GET'])
def get_activities():
    return jsonify(scoped_datasets()['activities'])

@app.route('/api/activities', methods=['POST'])
def add_activity():
    d = request_payload('activities', force_region=True)
    id = insert_db("""
        INSERT INTO activities(clientId, clientName, leadId, propertyId, region, kind, message, createdAt)
        VALUES(?,?,?,?,?,?,?,?)
    """, (d.get('clientId'), d.get('clientName'), d.get('leadId'), d.get('propertyId'), d.get('region'), d.get('kind'), d.get('message'), d.get('createdAt')))
    record_audit('activities', id, 'create', 'Activity created')
    return jsonify({"success": True, "id": id})

@app.route('/api/meetings/<int:id>', methods=['DELETE'])
def delete_meeting(id):
    if not any(str(row.get('id')) == str(id) for row in scoped_datasets()['meetings']):
        return jsonify({"success": False, "error": "Meeting not found in this region"}), 404
    archive_record('meetings', id, 'Meeting archived')
    return jsonify({"success": True})


@app.route('/api/meetings/<int:id>/restore', methods=['POST'])
@admin_required
def restore_meeting(id):
    if not scoped_archived_contains('meetings', id):
        return jsonify({"success": False, "error": "Archived meeting not found in this region"}), 404
    if not restore_record('meetings', id, 'Meeting restored'):
        return jsonify({"success": False, "error": "Archived meeting not found"}), 404
    return jsonify({"success": True})

# --- BROKERS ---
@app.route('/api/brokers', methods=['GET'])
def get_brokers():
    return jsonify(scoped_datasets()['brokers'])

@app.route('/api/brokers', methods=['POST'])
def add_broker():
    d = request_payload('brokers')
    id = insert_db("""
        INSERT INTO brokers(name, phone, region, area, types, deals, rating)
        VALUES(?,?,?,?,?,?,?)
    """, (d.get('name'), d.get('phone'), d.get('region'), d.get('area'), d.get('types'), d.get('deals'), d.get('rating')))
    record_audit('brokers', id, 'create', 'Broker created')
    return jsonify({"success": True, "id": id})

@app.route('/api/brokers/<int:id>', methods=['PUT'])
def update_broker(id):
    existing = scoped_entity_rows('brokers', "SELECT * FROM brokers WHERE id=? AND deletedAt IS NULL", (id,))
    if not existing:
        return jsonify({"success": False, "error": "Broker not found in this region"}), 404
    d = request_payload('brokers')
    allowed = ['name', 'phone', 'region', 'area', 'types', 'deals', 'rating']
    fields = []
    args = []
    for key in allowed:
        if key in d:
            fields.append(f"{key}=?")
            args.append(d.get(key))
    if fields:
        args.append(id)
        update_db(f"UPDATE brokers SET {', '.join(fields)} WHERE id=?", tuple(args))
        record_audit('brokers', id, 'update', 'Broker updated')
    return jsonify({"success": True})

@app.route('/api/brokers/<int:id>', methods=['DELETE'])
def delete_broker(id):
    existing = scoped_entity_rows('brokers', "SELECT * FROM brokers WHERE id=? AND deletedAt IS NULL", (id,))
    if not existing:
        return jsonify({"success": False, "error": "Broker not found in this region"}), 404
    archive_record('brokers', id, 'Broker archived')
    return jsonify({"success": True})


@app.route('/api/brokers/<int:id>/restore', methods=['POST'])
@admin_required
def restore_broker(id):
    if not scoped_archived_contains('brokers', id):
        return jsonify({"success": False, "error": "Archived broker not found in this region"}), 404
    if not restore_record('brokers', id, 'Broker restored'):
        return jsonify({"success": False, "error": "Archived broker not found"}), 404
    return jsonify({"success": True})

if __name__ == '__main__':
    host = os.environ.get('REOS_HOST', '127.0.0.1')
    port = int(os.environ.get('REOS_PORT', '5000'))
    debug = os.environ.get('REOS_DEBUG', '0') == '1'
    app.run(host=host, port=port, debug=debug)
