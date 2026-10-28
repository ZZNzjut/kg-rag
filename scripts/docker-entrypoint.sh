#!/bin/sh
# 容器入口：数据卷为空时自动 seed，再启动服务。
set -e
DATA_DIR="${KGRAG_DATA:-/app/data}"
if [ ! -d "$DATA_DIR/vectors" ] || [ -z "$(ls -A "$DATA_DIR/vectors" 2>/dev/null)" ]; then
  echo "[entrypoint] 检测到空数据目录，执行 seed..."
  python scripts/seed.py
fi
exec python app.py --host 0.0.0.0 --port "${PORT:-8000}"