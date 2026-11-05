"""
Outbound email for profile-claim verification.

Deliberately stdlib-only -- the app has no mail extension and this sends two
kinds of message. When ENABLE_EMAIL is false (the default, and the case in
local development) the message is written to the app log instead of sent, so
the verification flow is still walkable without an SMTP server.
"""
import smtplib
from email.message import EmailMessage

from flask import current_app, url_for


def _send(to_address, subject, body):
    """
    Send one plain-text message.

    Returns True if it was handed to an SMTP server, False if it was logged
    instead. Never raises: a verification email failing to send must not break
    the request that triggered it -- the user can always ask for a resend.
    """
    if not current_app.config.get('ENABLE_EMAIL'):
        current_app.logger.info(
            'ENABLE_EMAIL is false -- not sending %r to %s. Body:\n%s',
            subject, to_address, body
        )
        return False

    host = current_app.config.get('SMTP_HOST')
    if not host:
        current_app.logger.error('ENABLE_EMAIL is true but SMTP_HOST is unset; '
                                 'cannot email %s', to_address)
        return False

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = current_app.config['MAIL_FROM']
    message['To'] = to_address
    message.set_content(body)

    try:
        with smtplib.SMTP(host, current_app.config['SMTP_PORT'], timeout=15) as smtp:
            if current_app.config.get('SMTP_USE_TLS'):
                smtp.starttls()
            username = current_app.config.get('SMTP_USERNAME')
            if username:
                smtp.login(username, current_app.config.get('SMTP_PASSWORD') or '')
            smtp.send_message(message)
        return True
    except Exception as exc:  # noqa: BLE001 - see docstring
        current_app.logger.error('Failed to email %s: %s', to_address, exc)
        return False


def send_verification_email(user):
    """Issue a fresh token and email the verification link. Caller commits."""
    token = user.new_email_verify_token()
    link = url_for('verify_email', token=token, _external=True)

    body = (
        f"Hi {user.resume_name},\n\n"
        "Confirm this address to finish setting up your RegattaResume account "
        "and publish a sailing resume:\n\n"
        f"{link}\n\n"
        f"The link is good for {current_app.config['EMAIL_VERIFY_TOKEN_MAX_AGE_HOURS']} "
        "hours. If you did not create this account, you can ignore this email.\n"
    )

    sent = _send(user.email, 'Confirm your RegattaResume email', body)
    return sent, link


def send_claim_notification(user, claim):
    """Tell a sailor their claim went through."""
    body = (
        f"Hi {user.resume_name},\n\n"
        f"You've claimed the profile for {claim.profile_name} on RegattaResume.\n\n"
        "You can now build a resume from your results, and add any regattas we "
        "missed:\n\n"
        f"{url_for('resume_builder', _external=True)}\n\n"
        "If this wasn't you, reply to this email and we'll undo it.\n"
    )
    return _send(user.email, 'Profile claimed on RegattaResume', body)
