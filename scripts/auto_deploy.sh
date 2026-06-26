#!/usr/bin/env bash
set -e

echo "============================================================"
echo "🚀 Bắt đầu quy trình Auto Deploy (Git Push & Docker Rebuild)"
echo "============================================================"

# Đảm bảo đang ở thư mục gốc của dự án
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"

# 1. Kiểm tra thay đổi bằng git status
echo "🔍 Kiểm tra trạng thái git..."
if [ -z "$(git status --porcelain)" ]; then
    echo "✅ Không có thay đổi mã nguồn nào cần commit."
else
    echo "📝 Phát hiện thay đổi mã nguồn. Tiến hành commit và push..."
    git add .
    git commit -m "auto: update UI and console panels [skip ci]" || true
    
    # Push lên nhánh dev-agent
    echo "⬆️ Đang đẩy code lên nhánh dev-agent..."
    git push origin dev-agent || echo "⚠️ Cảnh báo: Push thất bại (có thể do chưa setup remote hoặc quyền truy cập), tiếp tục build image..."
fi

# 2. Rebuild image và khởi động lại container UI
echo "🐳 Đang tiến hành build lại Docker image cho observation-web..."
docker compose -f docker-compose.agentic-ui.yml build observation-web

echo "🔄 Đang khởi động lại container observation-web (không gián đoạn các service khác)..."
docker compose -f docker-compose.agentic-ui.yml up -d --no-deps observation-web

echo "============================================================"
echo "🎉 Quá trình Auto Deploy đã hoàn tất thành công!"
echo "🌐 Truy cập giao diện tại: http://localhost:3003"
echo "============================================================"
