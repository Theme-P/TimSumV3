import hashlib
import importlib.util
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from bson import ObjectId
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from starlette.requests import Request

from app.core.auth import get_current_user
from app.models.user import User
from app.routers.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    forgot_password,
    login,
    reset_password,
)
from app.routers.consent import ConsentSubmitRequest, consent_policy_hash
from app.routers.user import ChangePasswordRequest
from app.services.email_service import EmailService
from app.services.rate_limit import LOGIN_IP, RedisRateLimiter, enforce_rate_limit
from app.services.passwords import PasswordManager
from app.services.security import (
    SecurityConfigurationError,
    build_frontend_url,
    get_client_ip,
    get_public_frontend_url,
    validate_password,
)


def _request(*, client="203.0.113.20", headers=None, app=None):
    header_items = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": header_items,
            "client": (client, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "app": app,
        }
    )


class SecurityHelperTests(unittest.TestCase):
    def test_public_frontend_url_is_operator_controlled_and_normalized(self):
        with patch.dict(
            os.environ,
            {"PUBLIC_FRONTEND_URL": "https://meet.example.test/app/", "APP_ENV": "production"},
            clear=True,
        ):
            self.assertEqual(get_public_frontend_url(), "https://meet.example.test/app")
            self.assertEqual(
                build_frontend_url("/reset-password", {"token": "a+b&c"}),
                "https://meet.example.test/app/reset-password?token=a%2Bb%26c",
            )

    def test_public_frontend_url_rejects_http_in_production(self):
        with patch.dict(
            os.environ,
            {"PUBLIC_FRONTEND_URL": "http://meet.example.test", "APP_ENV": "production"},
            clear=True,
        ):
            with self.assertRaises(SecurityConfigurationError):
                get_public_frontend_url()

    def test_public_frontend_url_rejects_invalid_port(self):
        with patch.dict(
            os.environ,
            {"PUBLIC_FRONTEND_URL": "https://meet.example.test:not-a-port"},
            clear=True,
        ):
            with self.assertRaises(SecurityConfigurationError):
                get_public_frontend_url()

    def test_forwarded_ip_is_ignored_unless_peer_is_trusted(self):
        request = _request(headers={"X-Forwarded-For": "198.51.100.40"})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_client_ip(request), "203.0.113.20")

        request = _request(
            client="10.0.0.2",
            headers={"X-Forwarded-For": "198.51.100.40"},
        )
        with patch.dict(os.environ, {"TRUSTED_PROXY_CIDRS": "10.0.0.0/24"}, clear=True):
            self.assertEqual(get_client_ip(request), "198.51.100.40")

    def test_bootstrap_password_rejects_short_and_published_pattern(self):
        with self.assertRaises(ValueError):
            validate_password("short")
        with self.assertRaises(ValueError):
            validate_password(
                "TimSum-New-Admin-Password",
                reject_bootstrap_defaults=True,
            )
        self.assertEqual(
            validate_password("a-long-unique-password"),
            "a-long-unique-password",
        )


class _FakeRedis:
    def __init__(self):
        self.counts = {}

    def eval(self, _script, _key_count, key, window_seconds):
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], int(window_seconds)]


class RateLimitTests(unittest.TestCase):
    def test_limit_is_enforced_and_key_does_not_expose_identifier(self):
        redis = _FakeRedis()
        limiter = RedisRateLimiter(redis_client=redis)

        for _ in range(LOGIN_IP.limit):
            allowed, _ = limiter.hit(LOGIN_IP, "person@example.test")
            self.assertTrue(allowed)
        allowed, retry_after = limiter.hit(LOGIN_IP, "person@example.test")

        self.assertFalse(allowed)
        self.assertEqual(retry_after, LOGIN_IP.window_seconds)
        self.assertNotIn("person@example.test", next(iter(redis.counts)))

    def test_http_429_includes_retry_after(self):
        redis = _FakeRedis()
        limiter = RedisRateLimiter(redis_client=redis)
        request = _request()
        for _ in range(LOGIN_IP.limit):
            enforce_rate_limit(request, LOGIN_IP, "same-client", limiter=limiter)

        with self.assertRaises(HTTPException) as context:
            enforce_rate_limit(request, LOGIN_IP, "same-client", limiter=limiter)

        self.assertEqual(context.exception.status_code, 429)
        self.assertEqual(
            context.exception.headers["Retry-After"],
            str(LOGIN_IP.window_seconds),
        )

    def test_backend_failure_is_fail_closed(self):
        class BrokenRedis:
            def eval(self, *_args):
                raise ConnectionError("redis unavailable")

        limiter = RedisRateLimiter(redis_client=BrokenRedis())
        with self.assertRaises(HTTPException) as context:
            enforce_rate_limit(_request(), LOGIN_IP, "client", limiter=limiter)

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.headers["Retry-After"], "5")


class AuthVersionTests(unittest.TestCase):
    def _authorize(self, token_version, stored_version=3, deletion_pending=False):
        user_id = ObjectId()
        secret = "test-secret-with-enough-entropy-for-unit-tests"
        payload = {"id": str(user_id), "role": "user"}
        if token_version is not None:
            payload["ver"] = token_version
        token = jwt.encode(payload, secret, algorithm="HS256")

        mongo = SimpleNamespace(
            get_user_document_by_id=lambda *_args, **_kwargs: {
                "_id": user_id,
                "username": "User",
                "email": "person@example.test",
                "role": "user",
                "status": "approved",
                "auth_version": stored_version,
                "deletion_pending": deletion_pending,
            }
        )
        app = SimpleNamespace(state=SimpleNamespace(mongo_service=mongo))
        request = _request(app=app)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with patch.dict(os.environ, {"JWT_SECRET_KEY": secret}, clear=True):
            return get_current_user(request, credentials)

    def test_matching_auth_version_is_returned(self):
        user = self._authorize(3)
        self.assertEqual(user.auth_version, 3)

    def test_missing_or_stale_auth_version_is_rejected(self):
        for token_version in (None, 2):
            with self.subTest(token_version=token_version):
                with self.assertRaises(HTTPException) as context:
                    self._authorize(token_version)
                self.assertEqual(context.exception.status_code, 401)

    def test_deletion_pending_user_is_rejected(self):
        with self.assertRaises(HTTPException) as context:
            self._authorize(3, deletion_pending=True)
        self.assertEqual(context.exception.status_code, 403)


class LoginTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_token_contains_current_auth_version(self):
        user = User(
            username="User",
            email="person@example.test",
            password="unused-password-value",
            auth_version=7,
        )
        mongo = SimpleNamespace(authenticate_user=lambda *_args: user)
        secret = "test-secret-with-enough-entropy-for-unit-tests"

        with (
            patch.dict(os.environ, {"JWT_SECRET_KEY": secret}, clear=True),
            patch("app.routers.auth.enforce_rate_limits"),
        ):
            response = await login(
                LoginRequest(
                    email="person@example.test",
                    password="a-login-password",
                ),
                SimpleNamespace(),
                mongo,
            )

        payload = jwt.decode(response["token"], secret, algorithms=["HS256"])
        self.assertEqual(payload["ver"], 7)

    async def test_failed_login_does_not_lookup_or_disclose_account_status(self):
        mongo = SimpleNamespace(
            authenticate_user=lambda *_args: None,
            get_user_status=lambda *_args: (_ for _ in ()).throw(
                AssertionError("status lookup would enumerate the account")
            ),
        )
        with patch("app.routers.auth.enforce_rate_limits"):
            with self.assertRaises(HTTPException) as context:
                await login(
                    LoginRequest(
                        email="person@example.test",
                        password="wrong-password",
                    ),
                    SimpleNamespace(),
                    mongo,
                )
        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "อีเมลหรือรหัสผ่านไม่ถูกต้อง")


class ConsentValidationTests(unittest.TestCase):
    def test_consent_values_are_strict_booleans_and_keys_are_exact(self):
        valid = ConsentSubmitRequest(
            consents={"privacy_policy": True, "data_processing": True}
        )
        self.assertTrue(valid.consents.privacy_policy)

        for invalid in (
            {"privacy_policy": "true", "data_processing": True},
            {"privacy_policy": True, "data_processing": True, "unknown": True},
            {"privacy_policy": True},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    ConsentSubmitRequest(consents=invalid)

    def test_policy_hash_is_stable_and_content_addressed(self):
        first = consent_policy_hash("privacy_policy")
        second = consent_policy_hash("privacy_policy")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, consent_policy_hash("data_processing"))


class EmailConfigurationTests(unittest.TestCase):
    def test_smtp_port_comes_from_environment_when_not_explicit(self):
        env = {
            "SMTP_SERVER": "smtp.example.test",
            "SMTP_PORT": "587",
            "SENDER_EMAIL": "noreply@example.test",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(EmailService().smtp_port, 587)
            self.assertEqual(EmailService(smtp_port=2525).smtp_port, 2525)

        env["SMTP_PORT"] = ""
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(EmailService().smtp_port, 25)


@unittest.skipUnless(importlib.util.find_spec("argon2"), "argon2-cffi is not installed")
class PasswordHashingTests(unittest.TestCase):
    def test_new_hashes_use_argon2id(self):
        manager = PasswordManager()
        encoded, salt = manager.hash("a-long-unique-password")

        self.assertTrue(encoded.startswith("$argon2id$"))
        self.assertIsNone(salt)
        self.assertTrue(manager.verify("a-long-unique-password", encoded).valid)
        self.assertFalse(manager.verify("incorrect-password", encoded).valid)

    def test_legacy_pbkdf2_is_verified_and_upgraded(self):
        manager = PasswordManager()
        password = "a-legacy-long-password"
        salt = "legacy-salt"
        stored_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        ).hex()

        result = manager.verify(password, stored_hash, salt)

        self.assertTrue(result.valid)
        self.assertTrue(result.needs_rehash)
        self.assertTrue(result.upgraded_hash.startswith("$argon2id$"))
        self.assertIsNone(result.upgraded_salt)

    def test_wrong_legacy_password_is_not_upgraded(self):
        manager = PasswordManager()
        salt = "legacy-salt"
        stored_hash = hashlib.pbkdf2_hmac(
            "sha256",
            b"correct-password",
            salt.encode("utf-8"),
            100_000,
        ).hex()

        result = manager.verify("incorrect-password", stored_hash, salt)

        self.assertFalse(result.valid)
        self.assertFalse(result.needs_rehash)


class PasswordResetTests(unittest.IsolatedAsyncioTestCase):
    async def test_forgot_password_ignores_host_and_origin_headers(self):
        user = User(
            username="User",
            email="person@example.test",
            password="unused-password-value",
        )
        sent = {}
        email_service = SimpleNamespace(
            is_configured=True,
            send_simple_email=lambda **kwargs: sent.update(kwargs),
        )

        class Mongo:
            def get_user_by_email(self, _email):
                return user

            def create_password_reset_token(self, *_args):
                return None

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(email_service=email_service)),
            headers={"origin": "https://evil.example", "host": "evil.example"},
        )
        with (
            patch.dict(
                os.environ,
                {"PUBLIC_FRONTEND_URL": "https://trusted.example", "APP_ENV": "production"},
                clear=True,
            ),
            patch("app.routers.auth.enforce_rate_limits"),
        ):
            await forgot_password(
                ForgotPasswordRequest(email="person@example.test"),
                request,
                Mongo(),
            )

        self.assertIn("https://trusted.example/reset-password?token=", sent["body_text"])
        self.assertNotIn("evil.example", sent["body_text"])

    async def test_reset_uses_one_time_consume_interface(self):
        user = User(
            username="User",
            email="person@example.test",
            password="unused-password-value",
        )

        class Mongo:
            def __init__(self):
                self.consumed = []
                self.invalidated_users = []
                self.updated = []

            def consume_password_reset_token(self, token):
                self.consumed.append(token)
                return {"user_id": str(user.id)}

            def get_user_by_id(self, _user_id):
                return user

            def delete_password_reset_tokens_for_user(self, user_id):
                self.invalidated_users.append(user_id)

            def update_user_password(self, user_id, password):
                self.updated.append((user_id, password))

        mongo = Mongo()
        reset_token = "t" * 43
        with patch("app.routers.auth.enforce_rate_limits"):
            await reset_password(
                ResetPasswordRequest(
                    token=reset_token,
                    new_password="a-new-long-password",
                ),
                SimpleNamespace(),
                mongo,
            )

        self.assertEqual(mongo.consumed, [reset_token])
        self.assertEqual(mongo.invalidated_users, [str(user.id), str(user.id)])
        self.assertEqual(mongo.updated, [(str(user.id), "a-new-long-password")])


class ChangePasswordValidationTests(unittest.TestCase):
    def test_profile_password_change_uses_twelve_character_policy(self):
        with self.assertRaises(ValidationError):
            ChangePasswordRequest(
                current_password="old-password",
                new_password="only-eight",
            )

        request = ChangePasswordRequest(
            current_password="old-password",
            new_password="a-new-long-password",
        )
        self.assertEqual(request.new_password, "a-new-long-password")


if __name__ == "__main__":
    unittest.main()
