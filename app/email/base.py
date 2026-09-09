"""
EmailSender interface. Each backend (SMTP, Graph) implements send() and
validates its own required config in __init__ — see app/email/__init__.py
for how config.EMAIL_PROVIDER picks a backend.
"""
from abc import ABC, abstractmethod


class EmailError(Exception):
    pass


class EmailSender(ABC):
    @abstractmethod
    def send(self, subject: str, body_html: str, attachment_path: str | None = None) -> None:
        ...


def require_config(name: str, value: str) -> str:
    if not value:
        raise EmailError(f"Missing required env var: {name}. Check your .env file.")
    return value
