"""NginxとGunicornを使用する本番環境用設定。"""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403


def required_environment_list(name):
    values = [
        value.strip()
        for value in os.environ.get(name, "").split(",")
        if value.strip()
    ]
    if not values:
        raise ImproperlyConfigured(
            f"本番環境では{name}の設定が必要です。"
        )
    return values


try:
    SECRET_KEY = os.environ["ELECTION_SECRET_KEY"]
except KeyError as exc:
    raise ImproperlyConfigured(
        "本番環境ではELECTION_SECRET_KEYの設定が必要です。"
    ) from exc

DEBUG = False
ALLOWED_HOSTS = required_environment_list(
    "ELECTION_ALLOWED_HOSTS"
)

if not DATABASES["default"]["PASSWORD"]:  # noqa: F405
    raise ImproperlyConfigured(
        "本番環境ではELECTION_DB_PASSWORDの設定が必要です。"
    )

if not os.environ.get("ELECTION_EMAIL_HOST"):
    raise ImproperlyConfigured(
        "本番環境ではELECTION_EMAIL_HOSTの設定が必要です。"
    )

STATIC_ROOT = BASE_DIR / "staticfiles"  # noqa: F405

SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https",
)
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(
    os.environ.get("ELECTION_SECURE_HSTS_SECONDS", "3600")
)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ELECTION_CSRF_TRUSTED_ORIGINS",
        "",
    ).split(",")
    if origin.strip()
]

