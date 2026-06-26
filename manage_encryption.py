from crypto_utils import ENCRYPTED_PREFIX, SENSITIVE_FIELDS, encrypt_value
from database import get_db, init_db


def table_columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def encrypt_existing_data():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    total_updates = 0

    for table, sensitive_fields in SENSITIVE_FIELDS.items():
        columns = table_columns(cursor, table)
        fields = [field for field in sensitive_fields if field in columns]
        if not fields:
            continue

        cursor.execute(f"SELECT id, {', '.join(fields)} FROM {table}")
        rows = cursor.fetchall()
        for row in rows:
            updates = {}
            for field in fields:
                value = row[field]
                if isinstance(value, str) and value and not value.startswith(ENCRYPTED_PREFIX):
                    updates[field] = encrypt_value(value)
            if updates:
                assignments = ', '.join(f"{field}=?" for field in updates)
                args = list(updates.values()) + [row['id']]
                cursor.execute(f"UPDATE {table} SET {assignments} WHERE id=?", args)
                total_updates += 1

    conn.commit()
    conn.close()
    print(f"Encrypted sensitive data in {total_updates} row(s).")


if __name__ == '__main__':
    encrypt_existing_data()
