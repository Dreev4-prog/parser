#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "DT Vinted Session Capture (Mac)"
echo "Будет создан локальный .vinted_capture_env; пароль Vinted скрипт не видит."
if ! command -v python3 >/dev/null 2>&1; then
  echo "Не найден python3. Установи Python 3 и запусти файл снова."
  read -r
  exit 1
fi
if [ ! -d .vinted_capture_env ]; then
  python3 -m venv .vinted_capture_env
fi
source .vinted_capture_env/bin/activate
python -m pip -q install --upgrade pip
python -m pip -q install "playwright==1.61.0"
python capture_vinted_session.py
status=$?
echo ""
read -r -p "Нажми Enter, чтобы закрыть окно..."
exit $status
