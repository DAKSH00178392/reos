import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


ENCRYPTED_PREFIX = 'enc:v1:'

SENSITIVE_FIELDS = {
    'leads': {
        'clientName', 'phone', 'location', 'locationArea', 'locationCity',
        'source', 'assignedTo', 'notes', 'closedBy', 'closedByType',
        'closedPropertyName', 'closeReason',
    },
    'clients': {
        'name', 'phone', 'email', 'req', 'location', 'locationArea',
        'locationCity', 'interest', 'source',
    },
    'properties': {
        'location', 'locationArea', 'locationCity', 'owner', 'ophone',
        'desc', 'listedBy', 'brokerName', 'brokerPhone', 'amenities',
    },
    'followups': {'clientName', 'action', 'notes'},
    'meetings': {'clientName', 'property', 'notes', 'outcome'},
    'shortlist': {'clientName', 'propertyName'},
    'activities': {'clientName', 'message'},
    'brokers': {'name', 'phone', 'area', 'types', 'rating'},
}


def _derive_key(secret):
    digest = hashlib.sha256(secret.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def get_cipher():
    key = os.environ.get('REOS_ENCRYPTION_KEY', '').strip()
    if key:
        try:
            return Fernet(key.encode('utf-8'))
        except Exception as exc:
            raise RuntimeError('REOS_ENCRYPTION_KEY must be a valid Fernet key.') from exc

    key_file = Path(__file__).with_name('.reos_encryption_key')
    if key_file.exists():
        return Fernet(key_file.read_text(encoding='utf-8').strip().encode('utf-8'))

    generated_key = Fernet.generate_key().decode('utf-8')
    key_file.write_text(generated_key, encoding='utf-8')
    return Fernet(generated_key.encode('utf-8'))

    fallback_secret = os.environ.get('REOS_SECRET_KEY', 'change-this-secret-before-hosting')
    return Fernet(_derive_key(fallback_secret))


def encrypt_value(value):
    if value is None:
        return None
    value = str(value)
    if not value or value.startswith(ENCRYPTED_PREFIX):
        return value
    token = get_cipher().encrypt(value.encode('utf-8')).decode('utf-8')
    return ENCRYPTED_PREFIX + token


def decrypt_value(value):
    if not isinstance(value, str) or not value.startswith(ENCRYPTED_PREFIX):
        return value
    token = value[len(ENCRYPTED_PREFIX):]
    try:
        return get_cipher().decrypt(token.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        return value


def encrypt_payload(table, payload):
    fields = SENSITIVE_FIELDS.get(table, set())
    return {
        key: encrypt_value(value) if key in fields else value
        for key, value in payload.items()
    }


def decrypt_record(table, record):
    if not record:
        return record
    fields = SENSITIVE_FIELDS.get(table, set())
    return {
        key: decrypt_value(value) if key in fields else value
        for key, value in record.items()
    }


def decrypt_records(table, records):
    return [decrypt_record(table, record) for record in records]
