"""
Email Service — ported from TimSumV2ToV3.

Sends DOCX results via email with HTML template.
All config comes from environment variables; service is optional —
if SMTP_SERVER is not set, email functions gracefully return errors.
"""

import os
import smtplib
import ssl
import time
import random
import logging
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)


class EmailService:
    """SMTP email service with DOCX attachment support."""

    def __init__(
        self,
        smtp_server: str = "",
        smtp_port: int = 25,
        username: str = "",
        password: str = "",
        sender_email: str = "",
    ) -> None:
        self.smtp_server = smtp_server or os.getenv("SMTP_SERVER", "")
        self.smtp_port = smtp_port or int(os.getenv("SMTP_PORT", "25"))
        self.username = username or os.getenv("EMAIL_USERNAME", "")
        self.password = password or os.getenv("EMAIL_PASSWORD", "")
        self.sender_email = sender_email or os.getenv("SENDER_EMAIL", "")

    @property
    def is_configured(self) -> bool:
        """Check if SMTP is configured."""
        return bool(self.smtp_server and self.sender_email)

    def _get_smtp_connection(self):
        """Create SMTP connection based on port."""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        if self.smtp_port == 465:
            server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, context=context)
            if self.username and self.password:
                server.login(self.username, self.password)
        elif self.smtp_port == 587:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls(context=context)
            if self.username and self.password:
                server.login(self.username, self.password)
        else:
            # Port 25 or other — plain SMTP
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            if self.username and self.password:
                try:
                    server.starttls(context=context)
                    server.login(self.username, self.password)
                except Exception:
                    pass  # Continue without auth for port 25

        return server

    def send_email_with_attachments(
        self,
        recipient_email: str,
        subject: str,
        body_text: str,
        docx_files: list[tuple[str, str]],
    ) -> bool:
        """
        Send email with DOCX attachments.
        
        Args:
            recipient_email: Recipient email address
            subject: Email subject
            body_text: Plain text body
            docx_files: List of (file_path, display_name) tuples
        
        Returns: True if sent successfully
        """
        if not self.is_configured:
            logger.warning("Email not configured — skipping send")
            return False

        try:
            msg = MIMEMultipart('mixed')
            msg["From"] = f"TimSum <{self.sender_email}>"
            msg["To"] = recipient_email
            msg["Subject"] = f"[TimSum] {subject}"
            msg["Reply-To"] = self.sender_email
            msg["X-Mailer"] = "TimSumV3"
            msg["Message-ID"] = f"<{int(time.time())}.{random.randint(1000,9999)}@timsumv3>"

            # Text + HTML body
            alt = MIMEMultipart('alternative')
            alt.attach(MIMEText(body_text, "plain", "utf-8"))
            alt.attach(MIMEText(self._html_template(body_text), "html", "utf-8"))
            msg.attach(alt)

            # Attachments
            for file_path, display_name in docx_files:
                with Path(file_path).open("rb") as f:
                    part = MIMEBase("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                safe_name = f"{display_name}.docx"
                part.add_header("Content-Disposition", f'attachment; filename="{safe_name}"')
                msg.attach(part)

            with self._get_smtp_connection() as server:
                server.sendmail(self.sender_email, recipient_email, msg.as_string())

            logger.info(f"Email sent to {recipient_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False

    def _html_template(self, body_text: str) -> str:
        """Simple HTML email template."""
        body_html = body_text.replace('\n', '<br>')
        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>TimSum</title></head>
<body style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;color:#333;padding:20px;">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#fff;">
<tr><td style="padding:20px;text-align:center;background:#f8f9fa;border-bottom:2px solid #007bff;">
<h1 style="margin:0;color:#007bff;font-size:24px;">TimSum V3</h1>
<p style="margin:5px 0 0;color:#666;">Transcription & Summarization System</p>
</td></tr>
<tr><td style="padding:30px;">
<div style="margin:20px 0;padding:15px;background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;">
{body_html}
</div>
</td></tr>
<tr><td style="padding:20px;text-align:center;background:#f8f9fa;border-top:1px solid #dee2e6;font-size:12px;color:#6c757d;">
<p>อีเมลนี้ถูกส่งจากระบบ TimSum V3 โดยอัตโนมัติ</p>
</td></tr>
</table></body></html>"""
