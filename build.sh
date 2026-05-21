#!/bin/bash
# 开启“遇到错误立即退出”的模式
set -e

# --- 配置区 ---
PORT=8000
IMAGE_NAME="docker.io/library/model-lite-ep-deploy:24.3.0"
PROXY_URL="${http_proxy:-}"

echo -e "\033[32m[INFO] 正在启动本地 HTTP 下载服务器 (端口: $PORT)...\033[0m"
# 将日志输出到 /dev/null 保持终端整洁
python3 -m http.server $PORT > /dev/null 2>&1 &
SERVER_PID=$!

# --- 核心安全机制 ---
# 无论脚本正常结束、发生错误还是被 Ctrl+C 中断，都会自动触发执行 kill
trap "echo -e '\033[33m[INFO] 正在清理本地 HTTP 服务器 (PID: $SERVER_PID)...\033[0m'; kill $SERVER_PID" EXIT

# 稍微等待1秒确保端口已成功绑定
sleep 1

# 缓存失效机制
TIMESTAMP=$(date +%s)

echo -e "\033[32m[INFO] 开始使用 Podman 构建镜像: ${IMAGE_NAME}\033[0m"

# 关键修改：
# 1. 移除 --add-host host.docker.internal:host-gateway
# 2. 增加 --network host
# 3. 将 SOURCE_URL 修改为 localhost
docker buildx build \
    --platform linux/arm64 \
    --network host \
    --build-arg SOURCE_URL="http://localhost:${PORT}" \
    --build-arg PROXY="${PROXY_URL}" \
    --build-arg CACHE_BUST="${TIMESTAMP}" \
    -t ${IMAGE_NAME} \
    -f Dockerfile \
    .

echo -e "\033[32m[INFO] 镜像构建成功！\033[0m"
