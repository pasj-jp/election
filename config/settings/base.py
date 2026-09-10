"""開発環境と本番環境で共通のDjango設定。"""

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent.parent


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "election",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("ELECTION_DB_NAME", "election"),
        "USER": os.environ.get("ELECTION_DB_USER", "election"),
        "PASSWORD": os.environ.get("ELECTION_DB_PASSWORD", ""),
        "HOST": os.environ.get("ELECTION_DB_HOST", "localhost"),
        "PORT": os.environ.get("ELECTION_DB_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        ),
    },
]

LANGUAGE_CODE = "ja"
TIME_ZONE = "Asia/Tokyo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("ELECTION_EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("ELECTION_EMAIL_PORT", "465"))
EMAIL_HOST_USER = os.environ.get("ELECTION_EMAIL_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("ELECTION_EMAIL_PASSWORD", "")
EMAIL_USE_SSL = (
    os.environ.get("ELECTION_EMAIL_USE_SSL", "true").lower()
    == "true"
)
EMAIL_USE_TLS = (
    os.environ.get("ELECTION_EMAIL_USE_TLS", "false").lower()
    == "true"
)
EMAIL_TIMEOUT = 30
DEFAULT_FROM_EMAIL = os.environ.get(
    "ELECTION_FROM_EMAIL",
    "vote@pasj.jp",
)

