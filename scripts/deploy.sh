#!/usr/bin/env bash
set -Eeuo pipefail

# コードの取得・切り替え後にrootで実行する。
# 配置場所が異なる場合は環境変数で上書きできる。
ELECTION_ROOT="${ELECTION_ROOT:-/opt/election}"
SOURCE_DIR="${ELECTION_SOURCE_DIR:-${ELECTION_ROOT}/src}"
VENV_DIR="${ELECTION_VENV_DIR:-${ELECTION_ROOT}/.venv}"
ENV_FILE="${ELECTION_ENV_FILE:-${ELECTION_ROOT}/etc/election.env}"
SERVICE_NAME="${ELECTION_SERVICE_NAME:-election}"
APP_USER="${ELECTION_APP_USER:-election}"

if [[ ! -r "${ENV_FILE}" ]]; then
    echo "環境変数ファイルを読み込めません: ${ENV_FILE}" >&2
    exit 1
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Python仮想環境がありません: ${VENV_DIR}" >&2
    exit 1
fi

cd "${SOURCE_DIR}"

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

export DJANGO_SETTINGS_MODULE=config.settings.production

"${VENV_DIR}/bin/pip" install --requirement requirements.txt
"${VENV_DIR}/bin/python" manage.py check --deploy
"${VENV_DIR}/bin/python" manage.py migrate --noinput
"${VENV_DIR}/bin/python" manage.py collectstatic --noinput

chown -R "${APP_USER}:${APP_USER}" "${SOURCE_DIR}/staticfiles"
systemctl restart "${SERVICE_NAME}.service"
systemctl --no-pager --full status "${SERVICE_NAME}.service"

