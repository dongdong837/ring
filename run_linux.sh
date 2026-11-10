#!/usr/bin/env bash
# Linux / macOS 启动脚本
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    exec python3 mic_wake_monitor.py "$@"
elif command -v python >/dev/null 2>&1; then
    exec python mic_wake_monitor.py "$@"
else
    echo "[错误] 没找到 python3，请先安装 Python 3.7+"
    exit 1
fi
