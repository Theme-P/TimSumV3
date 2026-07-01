"""Encrypt legacy user PII and rotate existing ciphertext safely.

Examples (inside the backend container):
  python scripts/migrate_encrypt_pii.py              # preflight/dry run
  python scripts/migrate_encrypt_pii.py --apply      # encrypt/update records
  python scripts/migrate_encrypt_pii.py --finalize   # verify and drop old index
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.encryption import PIIEncryptionError, PIIEncryptor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Encrypt/rotate user PII in MongoDB")
    parser.add_argument("--apply", action="store_true", help="write encrypted fields")
    parser.add_argument(
        "--finalize",
        action="store_true",
        help="require a clean migration and remove legacy full email indexes",
    )
    parser.add_argument("--batch-size", type=int, default=200)
    return parser.parse_args()


def _preflight(collection, encryptor: PIIEncryptor) -> tuple[int, int]:
    """Return total/migration counts and reject normalized duplicate emails."""
    total = 0
    pending = 0
    email_owners: dict[str, object] = {}
    for document in collection.find({}):
        total += 1
        decrypted = encryptor.decrypt_user_document(document)
        email = decrypted.get("email")
        if email:
            blind_index = encryptor.blind_index(email)
            previous = email_owners.get(blind_index)
            if previous is not None and previous != document["_id"]:
                raise PIIEncryptionError(
                    "Duplicate emails after case-insensitive normalization: "
                    f"user IDs {previous} and {document['_id']}"
                )
            email_owners[blind_index] = document["_id"]
        if encryptor.user_document_needs_migration(document):
            pending += 1
    return total, pending


def _flush(collection, operations: list[UpdateOne]) -> int:
    if not operations:
        return 0
    result = collection.bulk_write(operations, ordered=False)
    count = result.modified_count
    operations.clear()
    return count


def _finalize(collection, encryptor: PIIEncryptor) -> None:
    plaintext_count = 0
    stale_count = 0
    for document in collection.find({}):
        if encryptor.user_document_needs_migration(document):
            stale_count += 1
        if isinstance(document.get("email"), str):
            plaintext_count += 1
    if stale_count or plaintext_count:
        raise PIIEncryptionError(
            f"Cannot finalize: {stale_count} users need migration and "
            f"{plaintext_count} plaintext emails remain"
        )

    for name, info in collection.index_information().items():
        if info.get("key") == [("email", 1)]:
            collection.drop_index(name)
            print(f"Dropped legacy email index: {name}")

    collection.create_index(
        "email_bidx",
        unique=True,
        partialFilterExpression={"email_bidx": {"$type": "string"}},
        name="email_bidx_unique",
    )
    print("Finalization complete. Set PII_ALLOW_LEGACY_PLAINTEXT=false and restart.")


def main() -> int:
    load_dotenv()
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")

    try:
        encryptor = PIIEncryptor.from_env(require_enabled=True)
    except (PIIEncryptionError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    uri = os.getenv("MONGO_CONNECTION_STRING", "mongodb://mongo:27017")
    db_name = os.getenv("MONGO_DB_NAME", "timsumv3")
    client = MongoClient(uri, tz_aware=True)
    client.admin.command("ping")
    collection = client[db_name].user

    try:
        total, pending = _preflight(collection, encryptor)
        print(
            f"Preflight OK: total={total}, pending={pending}, "
            f"active_key_version={encryptor.active_version}"
        )
        if pending and not args.apply:
            print("Dry run only. Re-run with --apply to write changes.")
        elif pending:
            operations: list[UpdateOne] = []
            modified = 0
            for document in collection.find({}):
                if not encryptor.user_document_needs_migration(document):
                    continue
                update_fields = encryptor.encrypted_update_fields(
                    document, reencrypt=True
                )
                update_fields["pii_encryption_version"] = encryptor.active_version
                update_fields["pii_migrated_at"] = datetime.now(timezone.utc)
                operations.append(
                    UpdateOne({"_id": document["_id"]}, {"$set": update_fields})
                )
                if len(operations) >= args.batch_size:
                    modified += _flush(collection, operations)
            modified += _flush(collection, operations)
            print(f"Migration complete: modified={modified}")

            _, remaining = _preflight(collection, encryptor)
            if remaining:
                raise PIIEncryptionError(
                    f"Post-migration verification failed: {remaining} users remain"
                )
            print("Post-migration verification passed.")

        if args.finalize:
            if pending and not args.apply:
                raise PIIEncryptionError("Use --apply with --finalize when records are pending")
            _finalize(collection, encryptor)
    except (PIIEncryptionError, BulkWriteError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
