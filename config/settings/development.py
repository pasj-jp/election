"""ローカル開発用設定。"""

from .base import *  # noqa: F403


SECRET_KEY = "django-insecure-development-only-key"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

# ローカル開発では、外部データベースを用意せず起動できるようSQLiteを使う。
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

# 開発中の誤送信を防ぎ、メール本文をコンソールへ表示する。
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
