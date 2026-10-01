"""``uv run app create-admin --email X`` and ``uv run app seed-demo``."""

import argparse
import getpass
import sys

from app.auth import passwords
from app.config import Settings
from app.contracts.outcomes import AppError, Outcome
from app.db import connect, db_path, init_db

DEMO_USERS = (("analyst1@example.com", "analyst"), ("analyst2@example.com", "analyst"),
              ("admin@example.com", "admin"))


class _DemoSettings(Settings):  # reads DEMO_PASSWORD from the env or .env like other settings
    demo_password: str = ""


def create_admin(email: str) -> None:
    password = getpass.getpass("Admin password: ")
    if password != getpass.getpass("Repeat password: "):
        raise AppError(Outcome.invalid_input, "Passwords do not match.")
    init_db(db_path())
    with connect(db_path()) as conn:
        passwords.create_user(conn, email, password, "admin")
    print(f"Created admin {email}")


def seed_demo() -> None:
    password = _DemoSettings().demo_password
    if not password:
        raise AppError(Outcome.invalid_input, "Set DEMO_PASSWORD before seeding demo accounts.")
    init_db(db_path())
    with connect(db_path()) as conn:
        for email, role in DEMO_USERS:
            if passwords.user_exists(conn, email):
                print(f"Skipped {email} (already exists)")
            else:
                passwords.create_user(conn, email, password, role)
                print(f"Created {role} {email}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create-admin").add_argument("--email", required=True)
    sub.add_parser("seed-demo")
    args = parser.parse_args(argv)
    try:
        if args.command == "create-admin":
            create_admin(args.email)
        else:
            seed_demo()
    except AppError as e:
        print(f"Error: {e.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
