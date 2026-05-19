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
        
        self.debug_mode = os.getenv("EMAIL_DEBUG", "false").lower() == "true"
        
        if self.debug_mode:
            logger.debug(f"[EMAIL DEBUG] SMTP Configuration: "
                        f"host={self.smtp_server}, port={self.smtp_port}, "
                        f"user={'YES' if self.username else 'NO_USER'}, "
                        f"hasPassword={'YES' if self.password else 'NO'}")
        
        if self.is_configured:
            logger.info(f"[EMAIL INIT] EmailService configured with server={self.smtp_server}:{self.smtp_port}, sender={self.sender_email}")
        else:
            logger.warning("[EMAIL INIT] EmailService is not fully configured (missing SMTP_SERVER or SENDER_EMAIL)")

    @property
    def is_configured(self) -> bool:
        """Check if SMTP is configured."""
        return bool(self.smtp_server and self.sender_email)

    def _get_smtp_connection(self):
        """Create and return appropriate SMTP connection based on port configuration."""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        if self.smtp_port == 25:
            if self.debug_mode:
                logger.debug("[EMAIL DEBUG] Using port 25 - Plain SMTP")
            
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            if self.debug_mode:
                server.set_debuglevel(1)
                
            if self.debug_mode:
                try:
                    response = server.noop()
                    logger.debug(f"[EMAIL SMTP] NOOP response: {response}")
                    try:
                        vrfy_response = server.verify(self.sender_email)
                        logger.debug(f"[EMAIL SMTP] VRFY sender response: {vrfy_response}")
                    except Exception as vrfy_e:
                        logger.debug(f"[EMAIL SMTP] VRFY sender not supported: {vrfy_e}")
                except Exception as e:
                    logger.warning(f"[EMAIL SMTP] NOOP failed: {e}")

            if self.username and self.password:
                try:
                    server.starttls(context=context)
                    server.login(self.username, self.password)
                except Exception as e:
                    logger.warning(f"[EMAIL SMTP] STARTTLS/AUTH failed on port 25, continuing without: {e}")

            return server

        elif self.smtp_port == 465:
            if self.debug_mode:
                logger.debug("[EMAIL DEBUG] Using port 465 - SMTP with SSL")
            server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, context=context)
            if self.username and self.password:
                server.login(self.username, self.password)
            return server
            
        elif self.smtp_port == 587:
            if self.debug_mode:
                logger.debug("[EMAIL DEBUG] Using port 587 - SMTP with STARTTLS")
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls(context=context)
            if self.username and self.password:
                server.login(self.username, self.password)
            return server
            
        else:
            if self.debug_mode:
                logger.debug(f"[EMAIL DEBUG] Using port {self.smtp_port} - Default configuration")
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            if self.username and self.password:
                server.login(self.username, self.password)
            return server

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
            body_text: Plain text body
            docx_files: List of (file_path, display_name) tuples
            
        Returns: True if sent successfully, False otherwise
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
            msg["Return-Path"] = self.sender_email
            msg["X-Mailer"] = "TimSumV3"
            msg["X-Priority"] = "3"
            msg["Message-ID"] = f"<{int(time.time())}.{random.randint(1000,9999)}@timsumv3>"
            msg["MIME-Version"] = "1.0"
            
            # Alternative text/html parts
            msg_alternative = MIMEMultipart('alternative')
            msg_alternative.attach(MIMEText(body_text, "plain", "utf-8"))
            
            html_body = self._html_template(body_text)
            msg_alternative.attach(MIMEText(html_body, "html", "utf-8"))
            
            msg.attach(msg_alternative)

            # Attachments
            for file_path, display_name in docx_files:
                with Path(file_path).open("rb") as f:
                    part = MIMEBase("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                
                safe_filename = f"{display_name}.docx"
                try:
                    encoded_filename = Header(safe_filename, "utf-8").encode()
                except UnicodeEncodeError:
                    encoded_filename = safe_filename.encode('ascii', 'ignore').decode('ascii')
                
                part.add_header("Content-Disposition", f'attachment; filename="{encoded_filename}"')
                msg.attach(part)

            with self._get_smtp_connection() as server:
                message = msg.as_string()
                if self.debug_mode:
                    logger.debug(f"[EMAIL DEBUG] Message size: {len(message)} bytes")
                    
                smtp_result = server.sendmail(self.sender_email, recipient_email, message)
                if smtp_result:
                    logger.warning(f"[EMAIL DEBUG] Some recipients rejected: {smtp_result}")

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
        body_html: str = None,
    ) -> bool:
        """Send a simple email without attachments."""
        if not self.is_configured:
            logger.warning("Email not configured — skipping send")
            return False

        try:
            msg = MIMEMultipart('alternative')
            msg["From"] = f"TimSum <{self.sender_email}>"
            msg["To"] = recipient_email
            msg["Subject"] = f"[TimSum] {subject}"
            msg["Reply-To"] = self.sender_email
            msg["Return-Path"] = self.sender_email
            msg["X-Mailer"] = "TimSumV3"
            msg["X-Priority"] = "3"
            msg["Message-ID"] = f"<{int(time.time())}.{random.randint(1000,9999)}@timsumv3>"
            msg["MIME-Version"] = "1.0"
            
            msg.attach(MIMEText(body_text, "plain", "utf-8"))
            
            if body_html:
                msg.attach(MIMEText(body_html, "html", "utf-8"))
            else:
                html_body = self._html_template(body_text)
                msg.attach(MIMEText(html_body, "html", "utf-8"))

            with self._get_smtp_connection() as server:
                server.sendmail(self.sender_email, recipient_email, msg.as_string())

            logger.info(f"Simple email sent successfully to {recipient_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send simple email to {recipient_email}: {e}")
            return False

    def _html_template(self, body_text: str) -> str:
        """Create a professional HTML email template."""
        body_html = body_text.replace('\n', '<br>')
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
{body_html}
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
