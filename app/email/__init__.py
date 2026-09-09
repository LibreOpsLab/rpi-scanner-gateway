from app.config import config
from app.email.base import EmailError, EmailSender

__all__ = ["get_email_sender", "EmailSender", "EmailError"]


def get_email_sender() -> EmailSender | None:
    """Returns None when EMAIL_PROVIDER=none — callers skip the email step
    entirely rather than treating a missing sender as an error."""
    provider = config.EMAIL_PROVIDER
    if provider == "none":
        return None
    if provider == "smtp":
        from app.email.smtp_sender import SmtpSender
        return SmtpSender()
    if provider == "graph":
        from app.email.graph_sender import GraphSender
        return GraphSender()
    raise EmailError(f"Unknown EMAIL_PROVIDER: {provider!r} (expected 'smtp', 'graph', or 'none')")
