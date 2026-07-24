"""
Email Service — ported from TimSumV2ToV3.

Sends DOCX results via email with HTML template.
All config comes from environment variables; service is optional —
if SMTP_SERVER is not set, email functions gracefully return errors.

Security notes:
- SSL cert verification is enabled by default.
  Set SMTP_SKIP_VERIFY=true only for internal/self-signed servers.
- HTML body is escaped before injection into the template (XSS prevention).
- Connection timeout is set to 10 s to prevent indefinite hangs.
"""

import html
import os
import smtplib
import ssl
import time
import uuid
import logging
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

_SMTP_CONNECT_TIMEOUT = 10  # seconds — prevents indefinite hang on unresponsive servers


class EmailService:
    """SMTP email service with DOCX attachment support."""

    def __init__(
        self,
        smtp_server: str = "",
        smtp_port: int | None = None,
        username: str = "",
        password: str = "",
        sender_email: str = "",
        smtp_secure: str | None = None,
    ) -> None:
        self.smtp_server = smtp_server or os.getenv("SMTP_SERVER", "")
        configured_port = os.getenv("SMTP_PORT", "").strip()
        self.smtp_port = (
            smtp_port
            if smtp_port is not None
            else int(configured_port or "25")
        )
        if not 1 <= self.smtp_port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535")
        self.username = username or os.getenv("EMAIL_USERNAME", "")
        self.password = password or os.getenv("EMAIL_PASSWORD", "")
        self.sender_email = sender_email or os.getenv("SENDER_EMAIL", "")
        self.smtp_secure = (
            smtp_secure if smtp_secure is not None else os.getenv("SMTP_SECURE", "")
        ).strip().lower()

        self.debug_mode = os.getenv("EMAIL_DEBUG", "false").lower() == "true"

        # Allow skipping cert verification for internal/self-signed SMTP servers.
        # Disabled by default — must be explicitly opted in.
        self._skip_verify = os.getenv("SMTP_SKIP_VERIFY", "false").lower() == "true"

        if self.debug_mode:
            logger.debug(
                "[EMAIL DEBUG] SMTP Configuration: "
                f"host={self.smtp_server}, port={self.smtp_port}, "
                f"user={'YES' if self.username else 'NO_USER'}, "
                f"hasPassword={'YES' if self.password else 'NO'}, "
                f"secure={self.smtp_secure or 'auto'}, "
                f"skipVerify={self._skip_verify}"
            )

        if self.is_configured:
            logger.info(
                f"[EMAIL INIT] EmailService configured with "
                f"server={self.smtp_server}:{self.smtp_port}, sender={self.sender_email}"
            )
        else:
            logger.warning(
                "[EMAIL INIT] EmailService is not fully configured "
                "(missing SMTP_SERVER or SENDER_EMAIL)"
            )

    @property
    def is_configured(self) -> bool:
        """Check if minimum SMTP config is present."""
        return bool(self.smtp_server and self.sender_email)

    def _make_ssl_context(self) -> ssl.SSLContext:
        """Build SSL context.

        Cert verification is enabled by default.
        Set SMTP_SKIP_VERIFY=true to allow self-signed/internal server certs.
        """
        if self._skip_verify:
            # Intentionally insecure — only for internal SMTP servers
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            logger.warning(
                "[EMAIL SSL] Certificate verification DISABLED (SMTP_SKIP_VERIFY=true). "
                "Use only for internal/self-signed servers."
            )
            return context
        # Secure default: verify server certificate
        return ssl.create_default_context()

    def _get_smtp_connection(self):
        """Create and return appropriate SMTP connection based on security mode."""
        context = self._make_ssl_context()
        security_mode = self._resolve_security_mode()

        if security_mode == "ssl":
            if self.debug_mode:
                logger.debug("[EMAIL DEBUG] Using SMTP_SSL (implicit TLS)")
            server = smtplib.SMTP_SSL(
                self.smtp_server, self.smtp_port,
                context=context,
                timeout=_SMTP_CONNECT_TIMEOUT,
            )
            server.ehlo()
            if self.username and self.password:
                server.login(self.username, self.password)
            return server

        if security_mode == "starttls":
            if self.debug_mode:
                logger.debug("[EMAIL DEBUG] Using SMTP with STARTTLS")
            server = smtplib.SMTP(
                self.smtp_server, self.smtp_port,
                timeout=_SMTP_CONNECT_TIMEOUT,
            )
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()  # Re-identify after STARTTLS upgrade
            if self.username and self.password:
                server.login(self.username, self.password)
            return server

        # Plain SMTP (port 25 / internal relay)
        if self.debug_mode:
            logger.debug("[EMAIL DEBUG] Using plain SMTP")
        server = smtplib.SMTP(
            self.smtp_server, self.smtp_port,
            timeout=_SMTP_CONNECT_TIMEOUT,
        )
        if self.debug_mode:
            server.set_debuglevel(1)
        server.ehlo()
        if self.username and self.password:
            server.login(self.username, self.password)
        return server

    def _resolve_security_mode(self) -> str:
        """Resolve SMTP security mode from SMTP_SECURE env var with port-based fallback."""
        if self.smtp_secure in {"true", "ssl", "smtps", "465"}:
            return "ssl"
        if self.smtp_secure in {"starttls", "tls", "587"}:
            return "starttls"
        if self.smtp_secure in {"false", "none", "plain", "0", "no", "off"}:
            return "plain"
        # Port-based auto-detection when SMTP_SECURE is not set
        if self.smtp_port == 465:
            return "ssl"
        if self.smtp_port == 587:
            return "starttls"
        return "plain"

    def _make_message_id(self) -> str:
        """Generate a RFC 5322-compliant Message-ID using the sender domain."""
        domain = self.sender_email.split("@")[-1] if "@" in self.sender_email else self.smtp_server
        return f"<{int(time.time())}.{uuid.uuid4().hex[:8]}@{domain}>"

    def send_email_with_attachments(
        self,
        recipient_email: str,
        subject: str,
        body_text: str,
        docx_files: list[tuple[str, str]],
    ) -> bool:
        """
        Send an email with multiple DOCX file attachments.

        Args:
            recipient_email: Recipient email address
            subject: Email subject
            body_text: Plain text body (will be HTML-escaped before rendering)
            docx_files: List of (file_path, display_name) tuples

        Returns: True if sent successfully, False otherwise
        """
        if not self.is_configured:
            logger.warning("Email not configured — skipping send")
            return False

        try:
            msg = MIMEMultipart("mixed")
            msg["From"] = f"TimSum <{self.sender_email}>"
            msg["To"] = recipient_email
            msg["Subject"] = f"[TimSum] {subject}"
            msg["Reply-To"] = self.sender_email
            msg["Return-Path"] = self.sender_email
            msg["X-Mailer"] = "TimSumV3"
            msg["X-Priority"] = "3"
            msg["Message-ID"] = self._make_message_id()
            msg["MIME-Version"] = "1.0"

            # Multipart/alternative carries plain text + HTML
            msg_alternative = MIMEMultipart("alternative")
            msg_alternative.attach(MIMEText(body_text, "plain", "utf-8"))
            html_body = self._html_template(body_text)
            msg_alternative.attach(MIMEText(html_body, "html", "utf-8"))
            msg.attach(msg_alternative)

            # DOCX attachments
            for file_path, display_name in docx_files:
                with Path(file_path).open("rb") as f:
                    part = MIMEBase(
                        "application",
                        "vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                    part.set_payload(f.read())
                encoders.encode_base64(part)

                safe_filename = f"{display_name}.docx"
                try:
                    encoded_filename = Header(safe_filename, "utf-8").encode()
                except UnicodeEncodeError:
                    encoded_filename = safe_filename.encode("ascii", "ignore").decode("ascii")

                part.add_header("Content-Disposition", f'attachment; filename="{encoded_filename}"')
                msg.attach(part)

            with self._get_smtp_connection() as server:
                message = msg.as_string()
                if self.debug_mode:
                    logger.debug(f"[EMAIL DEBUG] Message size: {len(message)} bytes")

                smtp_result = server.sendmail(self.sender_email, recipient_email, message)
                if smtp_result:
                    logger.warning(f"[EMAIL] Some recipients rejected: {smtp_result}")

            logger.info(f"Email sent successfully to {recipient_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email to {recipient_email}: {e}")
            return False

    def send_simple_email(
        self,
        recipient_email: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> bool:
        """Send a simple email without attachments."""
        if not self.is_configured:
            logger.warning("Email not configured — skipping send")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"TimSum <{self.sender_email}>"
            msg["To"] = recipient_email
            msg["Subject"] = f"[TimSum] {subject}"
            msg["Reply-To"] = self.sender_email
            msg["Return-Path"] = self.sender_email
            msg["X-Mailer"] = "TimSumV3"
            msg["X-Priority"] = "3"
            msg["Message-ID"] = self._make_message_id()
            msg["MIME-Version"] = "1.0"

            msg.attach(MIMEText(body_text, "plain", "utf-8"))

            rendered_html = body_html if body_html else self._html_template(body_text)
            msg.attach(MIMEText(rendered_html, "html", "utf-8"))

            with self._get_smtp_connection() as server:
                server.sendmail(self.sender_email, recipient_email, msg.as_string())

            logger.info(f"Simple email sent successfully to {recipient_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send simple email to {recipient_email}: {e}")
            return False

    def _html_template(self, body_text: str) -> str:
        """Render body_text into a professional HTML email template.

        body_text is HTML-escaped before injection to prevent XSS.
        Newlines are converted to <br> after escaping.
        """
        # Escape HTML special chars first, then convert newlines → <br>
        escaped = html.escape(body_text).replace("\n", "<br>")
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>TimSum - Document Processing</title>
</head>
<body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #333333; margin: 0; padding: 20px;">

<table width="100%" cellpadding="0" cellspacing="0" style="max-width: 600px; margin: 0 auto; background-color: #ffffff;">
<tr>
<td style="padding: 20px; text-align: center; background-color: #f8f9fa; border-bottom: 2px solid #007bff;">
<h1 style="margin: 0; color: #007bff; font-size: 24px;">TimSum V3</h1>
<p style="margin: 5px 0 0 0; color: #666666;">Document Summarization System</p>
</td>
</tr>
<tr>
<td style="padding: 30px;">

<table width="100%" cellpadding="15" cellspacing="0" style="background-color: #f8f9fa; border: 1px solid #dee2e6; margin: 20px 0;">
<tr>
<td>
<div style="margin-top: 10px; padding: 10px; background-color: #ffffff; border-radius: 4px;">
{escaped}
</div>
</td>
</tr>
</table>

<table width="100%" cellpadding="15" cellspacing="0" style="background-color: #d1ecf1; border: 1px solid #bee5eb; margin: 20px 0;">
<tr>
<td>
<p style="margin: 0; color: #0c5460; font-size: 14px;">
<strong>หมายเหตุ:</strong> กรุณาตรวจสอบเอกสารที่แนบมา และหากมีข้อสงสัยกรุณาติดต่อ TimSum Support
</p>
</td>
</tr>
</table>

</td>
</tr>
<tr>
<td style="padding: 20px; text-align: center; background-color: #f8f9fa; border-top: 1px solid #dee2e6; font-size: 12px; color: #6c757d;">

<p>อีเมลนี้ถูกส่งจากระบบ TimSum โดยอัตโนมัติ</p>

<div style="margin-top: 20px; padding-top: 15px; border-top: 1px solid #dee2e6;">
<p style="margin: 2px 0;"><strong>TimSum</strong></p>
</div>

</td>
</tr>
</table>

</body>
</html>"""
