import argparse
import getpass
import sys

from werkzeug.security import generate_password_hash

from database import init_db, insert_db, query_db, update_db


def require_password(value=None):
    password = value or getpass.getpass('Password: ')
    confirm = getpass.getpass('Confirm password: ') if value is None else value
    if password != confirm:
        raise SystemExit('Passwords do not match.')
    if len(password) < 8:
        raise SystemExit('Password must be at least 8 characters.')
    return password


def list_users(_args):
    users = query_db("SELECT id, username, role, region, createdAt FROM users ORDER BY id")
    if not users:
        print('No users found.')
        return
    for user in users:
        region = user.get('region') or 'all'
        print(f"{user['id']}: {user['username']} ({user['role']}, region={region}) created={user.get('createdAt') or '-'}")


def add_user(args):
    password = require_password(args.password)
    try:
        user_id = insert_db(
            "INSERT INTO users(username, password_hash, role, region, createdAt) VALUES(?,?,?,?,datetime('now'))",
            (args.username, generate_password_hash(password), args.role, args.region),
        )
    except Exception as exc:
        raise SystemExit(f"Could not add user: {exc}") from exc
    print(f"Added user {args.username} with id {user_id}.")


def reset_password(args):
    user = query_db("SELECT id FROM users WHERE username=?", (args.username,), one=True)
    if not user:
        raise SystemExit(f"User not found: {args.username}")
    password = require_password(args.password)
    update_db(
        "UPDATE users SET password_hash=? WHERE username=?",
        (generate_password_hash(password), args.username),
    )
    print(f"Password reset for {args.username}.")


def set_region(args):
    user = query_db("SELECT id FROM users WHERE username=?", (args.username,), one=True)
    if not user:
        raise SystemExit(f"User not found: {args.username}")
    update_db(
        "UPDATE users SET region=? WHERE username=?",
        (args.region, args.username),
    )
    print(f"Region set for {args.username}: {args.region or 'all'}")


def delete_user(args):
    user = query_db("SELECT id FROM users WHERE username=?", (args.username,), one=True)
    if not user:
        raise SystemExit(f"User not found: {args.username}")
    update_db("DELETE FROM users WHERE username=?", (args.username,))
    print(f"Deleted user {args.username}.")


def build_parser():
    parser = argparse.ArgumentParser(description='Manage REOS login users.')
    subparsers = parser.add_subparsers(dest='command', required=True)

    list_parser = subparsers.add_parser('list', help='List users')
    list_parser.set_defaults(func=list_users)

    add_parser = subparsers.add_parser('add', help='Add a user')
    add_parser.add_argument('username')
    add_parser.add_argument('--role', default='staff')
    add_parser.add_argument('--region', default='', help='Region scope, e.g. "Ahmedabad Region" or "Vapi Region". Empty means all regions.')
    add_parser.add_argument('--password', help='Password value. Omit to enter securely.')
    add_parser.set_defaults(func=add_user)

    reset_parser = subparsers.add_parser('reset-password', help='Reset a user password')
    reset_parser.add_argument('username')
    reset_parser.add_argument('--password', help='Password value. Omit to enter securely.')
    reset_parser.set_defaults(func=reset_password)

    region_parser = subparsers.add_parser('set-region', help='Set user region scope')
    region_parser.add_argument('username')
    region_parser.add_argument('--region', default='', help='Region scope. Empty means all regions.')
    region_parser.set_defaults(func=set_region)

    delete_parser = subparsers.add_parser('delete', help='Delete a user')
    delete_parser.add_argument('username')
    delete_parser.set_defaults(func=delete_user)

    return parser


def main(argv=None):
    init_db()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main(sys.argv[1:])
