"""Create exactly one privileged TimSum user as a one-time operation.

Examples (inside the backend container):

    python scripts/create_admin.py \
      --role superadmin --username "Operations" --email ops@example.com

    python scripts/create_admin.py \
      --role admin --username "Support" --email support@example.com \
      --password-file /run/secrets/timsum_admin_password

Passwords never have command-line or environment defaults.  By default the
script prompts on a TTY twice; automation should use a mounted secret file.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from app.models.user import Quota, User
from app.models.package import ADMIN_PACKAGE, SUPERADMIN_PACKAGE
from app.services.mongo import MongoService
from app.services.security import validate_password


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create one TimSum admin or superadmin account",
    )
    parser.add_argument("--role", choices=("admin", "superadmin"), required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--password-file",
        type=Path,
        help="Read the password from a mounted secret file instead of prompting",
    )
    return parser


def _read_password(password_file: Path | None) -> str:
    if password_file is not None:
        try:
            password = password_file.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise ValueError(f"Cannot read password secret file: {exc}") from exc
    else:
        if not sys.stdin.isatty():
            raise ValueError(
                "Interactive password input requires a TTY; use --password-file"
            )
        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise ValueError("Passwords do not match")

    return validate_password(password, reject_bootstrap_defaults=True)


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise ValueError("A valid email address is required")
    return email


def _create_user(
    mongo: MongoService,
    *,
    username: str,
    email: str,
    password: str,
    role: str,
) -> None:
    if mongo.get_user_by_email(email):
        raise ValueError(f"A user already exists for {email}")

    user = User(
        username=username,
        email=email,
        password=password,
        role=role,
        status="approved",
    )
    quota = Quota(
        user_id=user.id,
        value1=100,
        value2=100,
        value3=100,
        value4=100,
    )

    package_template = ADMIN_PACKAGE if role == "admin" else SUPERADMIN_PACKAGE
    package_data = dict(package_template)
    package_data["limits"] = dict(package_template["limits"])
    package_data["is_active"] = True
    package_id = mongo.seed_package_if_missing(package_data)

    try:
        mongo.create_user(user)
        mongo.create_quota(quota)
        mongo.assign_user_package(
            str(user.id),
            package_id,
            assigned_by="bootstrap",
            source="bootstrap",
        )
    except Exception:
        # Compensate only records created for this invocation so the one-time
        # command can be retried safely after an infrastructure failure.
        mongo.db.user_package.delete_many({"user_id": user.id})
        mongo.db.quota.delete_many({"user_id": user.id})
        mongo.db.user.delete_one({"_id": user.id})
        raise
    print(f"Created {role} account for {email}")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = _parser()
    args = parser.parse_args(argv)

    try:
        username = args.username.strip()
        if not username:
            raise ValueError("Username must not be empty")
        email = _normalize_email(args.email)
        password = _read_password(args.password_file)

        mongo = MongoService(
            uri=os.getenv("MONGO_CONNECTION_STRING", "mongodb://mongo:27017"),
            db_name=os.getenv("MONGO_DB_NAME", "timsumv3"),
        )
        _create_user(
            mongo,
            username=username,
            email=email,
            password=password,
            role=args.role,
        )
    except ValueError as exc:
        parser.error(str(exc))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
