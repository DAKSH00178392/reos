import argparse
import sys
from datetime import datetime

from crypto_utils import decrypt_records
from database import init_db, insert_db, query_db, update_db


ARCHIVABLE_TABLES = {'leads', 'clients', 'properties', 'followups', 'meetings', 'shortlist', 'brokers'}


def require_table(table):
    if table not in ARCHIVABLE_TABLES:
        raise SystemExit(f"Unknown table '{table}'. Use one of: {', '.join(sorted(ARCHIVABLE_TABLES))}")


def label_for(table, row):
    if table == 'clients':
        return f"{row.get('name') or '-'} | {row.get('phone') or '-'} | {row.get('location') or '-'}"
    if table == 'leads':
        return f"{row.get('clientName') or '-'} | {row.get('phone') or '-'} | {row.get('status') or '-'}"
    if table == 'properties':
        return f"{row.get('name') or '-'} | {row.get('location') or '-'} | {row.get('status') or '-'}"
    if table == 'meetings':
        return f"{row.get('clientName') or '-'} | {row.get('property') or '-'} | {row.get('date') or '-'}"
    if table == 'followups':
        return f"{row.get('clientName') or '-'} | {row.get('action') or '-'} | {row.get('dueDate') or '-'}"
    if table == 'brokers':
        return f"{row.get('name') or '-'} | {row.get('phone') or '-'} | {row.get('area') or '-'}"
    if table == 'shortlist':
        return f"{row.get('clientName') or '-'} | {row.get('propertyName') or '-'}"
    return str(row.get('id'))


def list_archived(args):
    require_table(args.table)
    rows = decrypt_records(args.table, query_db(
        f"SELECT * FROM {args.table} WHERE deletedAt IS NOT NULL ORDER BY deletedAt DESC, id DESC"
    ))
    if not rows:
        print(f"No archived {args.table}.")
        return
    for row in rows:
        print(f"{row['id']}: {label_for(args.table, row)} | archived={row.get('deletedAt') or '-'} by={row.get('deletedBy') or '-'}")


def restore_archived(args):
    require_table(args.table)
    row = query_db(
        f"SELECT * FROM {args.table} WHERE id=? AND deletedAt IS NOT NULL",
        (args.id,),
        one=True,
    )
    if not row:
        raise SystemExit(f"Archived {args.table} record not found: {args.id}")
    update_db(f"UPDATE {args.table} SET deletedAt=NULL, deletedBy=NULL WHERE id=?", (args.id,))
    insert_db(
        "INSERT INTO audit_logs(tableName, recordId, action, username, message, createdAt) VALUES(?,?,?,?,?,?)",
        (args.table, args.id, 'restore', 'cli', f"{args.table} restored from command line", datetime.utcnow().isoformat(timespec='seconds')),
    )
    restored = [args.id]
    if args.table == 'clients':
        client = decrypt_records('clients', [row])[0]
        leads = decrypt_records('leads', query_db("SELECT id, clientId, clientName, phone FROM leads WHERE deletedAt IS NOT NULL"))
        for lead in leads:
            same_id = client.get('id') and str(lead.get('clientId') or '') == str(client.get('id'))
            same_phone = client.get('phone') and str(lead.get('phone') or '').strip().casefold() == str(client.get('phone') or '').strip().casefold()
            same_name = client.get('name') and str(lead.get('clientName') or '').strip().casefold() == str(client.get('name') or '').strip().casefold()
            if same_id or same_phone or same_name:
                update_db("UPDATE leads SET deletedAt=NULL, deletedBy=NULL WHERE id=?", (lead['id'],))
                insert_db(
                    "INSERT INTO audit_logs(tableName, recordId, action, username, message, createdAt) VALUES(?,?,?,?,?,?)",
                    ('leads', lead['id'], 'restore', 'cli', f"Lead restored with client #{args.id}", datetime.utcnow().isoformat(timespec='seconds')),
                )
                restored.append(f"lead:{lead['id']}")
    print(f"Restored {args.table} record {args.id}. Related restored: {', '.join(map(str, restored[1:])) or 'none'}.")


def build_parser():
    parser = argparse.ArgumentParser(description='List and restore archived REOS records.')
    subparsers = parser.add_subparsers(dest='command', required=True)

    list_parser = subparsers.add_parser('list', help='List archived records')
    list_parser.add_argument('table')
    list_parser.set_defaults(func=list_archived)

    restore_parser = subparsers.add_parser('restore', help='Restore an archived record')
    restore_parser.add_argument('table')
    restore_parser.add_argument('id', type=int)
    restore_parser.set_defaults(func=restore_archived)

    return parser


def main(argv=None):
    init_db()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main(sys.argv[1:])
