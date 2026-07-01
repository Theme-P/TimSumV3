import base64
import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.encryption import PIIEncryptionError, PIIEncryptor
from app.services.mongo import MongoService


def key(byte: int) -> bytes:
    return bytes([byte]) * 32


class TestPIIEncryptor(unittest.TestCase):
    def setUp(self):
        self.encryptor = PIIEncryptor(
            keys={1: key(1), 2: key(2)},
            active_version=2,
            blind_index_key=key(3),
            enabled=True,
        )

    def test_round_trip_and_random_nonce(self):
        first = self.encryptor.encrypt("สมชาย", context="user:1:first_name")
        second = self.encryptor.encrypt("สมชาย", context="user:1:first_name")

        self.assertNotEqual(first["ciphertext"], second["ciphertext"])
        self.assertEqual(first["version"], 2)
        self.assertEqual(
            self.encryptor.decrypt(first, context="user:1:first_name"),
            "สมชาย",
        )

    def test_context_prevents_ciphertext_swapping(self):
        encrypted = self.encryptor.encrypt("secret", context="user:1:first_name")
        with self.assertRaises(PIIEncryptionError):
            self.encryptor.decrypt(encrypted, context="user:2:first_name")

    def test_tampering_is_detected(self):
        encrypted = self.encryptor.encrypt("secret", context="user:1:email")
        raw = bytearray(base64.urlsafe_b64decode(encrypted["ciphertext"]))
        raw[0] ^= 1
        encrypted["ciphertext"] = base64.urlsafe_b64encode(raw).decode("ascii")

        with self.assertRaises(PIIEncryptionError):
            self.encryptor.decrypt(encrypted, context="user:1:email")

    def test_email_blind_index_is_normalized(self):
        self.assertEqual(
            self.encryptor.blind_index(" User@Example.COM "),
            self.encryptor.blind_index("user@example.com"),
        )

    def test_user_document_encryption_and_decryption(self):
        document = {
            "_id": "abc123",
            "email": " User@Example.com ",
            "username": "สมชาย ใจดี",
            "first_name": "สมชาย",
            "organization": None,
            "role": "user",
        }
        encrypted = self.encryptor.encrypt_user_document(document)

        self.assertTrue(self.encryptor.is_encrypted(encrypted["email"]))
        self.assertTrue(self.encryptor.is_encrypted(encrypted["first_name"]))
        self.assertIn("email_bidx", encrypted)
        self.assertEqual(encrypted["role"], "user")

        decrypted = self.encryptor.decrypt_user_document(encrypted)
        self.assertEqual(decrypted["email"], "user@example.com")
        self.assertEqual(decrypted["username"], "สมชาย ใจดี")
        self.assertIsNone(decrypted["organization"])

    def test_old_key_version_can_be_rotated(self):
        old = PIIEncryptor(
            keys={1: key(1)},
            active_version=1,
            blind_index_key=key(3),
            enabled=True,
        )
        document = old.encrypt_user_document({"_id": "1", "email": "a@example.com"})
        self.assertTrue(self.encryptor.user_document_needs_migration(document))

        rotated = self.encryptor.encrypt_user_document(document, reencrypt=True)
        self.assertEqual(rotated["email"]["version"], 2)
        self.assertEqual(
            self.encryptor.decrypt_user_document(rotated)["email"],
            "a@example.com",
        )

    def test_disabled_mode_preserves_legacy_values(self):
        disabled = PIIEncryptor(enabled=False)
        document = disabled.encrypt_user_document(
            {"_id": "1", "email": " User@Example.COM ", "first_name": "A"}
        )
        self.assertEqual(document["email"], "user@example.com")
        self.assertEqual(document["first_name"], "A")


class TestMongoPIIIntegration(unittest.TestCase):
    def setUp(self):
        self.encryptor = PIIEncryptor(
            keys={1: key(1)},
            active_version=1,
            blind_index_key=key(2),
            enabled=True,
            allow_legacy_plaintext=True,
        )
        self.service = MongoService.__new__(MongoService)
        self.service.pii = self.encryptor

    def test_email_query_uses_blind_index_and_legacy_fallback(self):
        query = self.service._user_email_query(" User@Example.COM ")
        self.assertEqual(query["$or"][0]["email_bidx"], self.encryptor.blind_index("user@example.com"))
        self.assertEqual(query["$or"][1], {"email": "user@example.com"})

    def test_reset_token_is_hashed_before_storage(self):
        class FakeCollection:
            def __init__(self):
                self.deleted_query = None
                self.document = None

            def delete_many(self, query):
                self.deleted_query = query

            def insert_one(self, document):
                self.document = document

        collection = FakeCollection()
        self.service.db = SimpleNamespace(password_reset=collection)
        self.service.create_password_reset_token(
            "user-id",
            "raw-reset-token",
            datetime.now(timezone.utc),
        )

        self.assertEqual(collection.deleted_query, {"user_id": "user-id"})
        self.assertNotIn("token", collection.document)
        self.assertNotIn("email", collection.document)
        self.assertEqual(len(collection.document["token_hash"]), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
