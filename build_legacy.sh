#!/bin/bash

# --- 1. 脚本环境配置 ---
# 遇到错误立即退出，确保流程安全
set -e

# --- 2. 自定义参数配置 ---
PORT=8000
IMAGE_NAME="docker.io/library/model-lite-ep-deploy:25.0.0"
OUTPUT_FILE="model-lite-deploy-legacy.tar"
PROXY_URL="${http_proxy:-}"
TIMESTAMP=$(date +%s)
BUILDER_NAME="legacy-builder"

echo -e "\033[32m[1/5] 启动本地下载服务器 (端口: $PORT)...\033[0m"
# 在后台启动 Python HTTP 服务器用于构建过程中下载资源
python3 -m http.server $PORT > /dev/null 2>&1 &
SERVER_PID=$!

# 核心清理机制：脚本退出时（无论成功失败）关闭服务器并切回默认构建器
cleanup() {
    echo -e "\n\033[33m[INFO] 正在清理环境...\033[0m"
    if [ -n "$SERVER_PID" ]; then kill $SERVER_PID || true; fi
    docker buildx use default || true
}
trap cleanup EXIT

# 等待服务器启动
sleep 2

# --- 3. 准备构建驱动 (关键步骤) ---
# 默认的 'docker' 驱动不支持直接导出兼容性 tar 包，必须使用 'docker-container' 驱动
echo -e "\033[32m[2/5] 配置 Docker Buildx 兼容性驱动...\033[0m"

if ! docker buildx inspect $BUILDER_NAME > /dev/null 2>&1; then
    echo "[INFO] 创建新的构建器实例: $BUILDER_NAME"
    # 创建支持高级导出功能的构建器
    docker buildx create --name $BUILDER_NAME --driver docker-container --driver-opt network=host --use
else
    echo "[INFO] 使用已存在的构建器实例: $BUILDER_NAME"
    docker buildx use $BUILDER_NAME
fi

# 启动构建器实例
docker buildx inspect --bootstrap

# --- 4. 执行构建与导出 (核心兼容性配置) ---
echo -e "\033[32m[3/5] 开始构建并导出兼容性镜像包 (ARM64)...\033[0m"
echo "[HINT] 正在强制使用 gzip 压缩和 V2 Manifest 格式以兼容旧版本 Docker"

# 参数解释：
# --output type=docker: 强制导出为旧版 Docker 镜像格式（非 OCI）
# dest: 指定导出的路径
# compression=gzip: 解决高版本默认 zstd 导致低版本 load 失败的问题
# provenance=false: 移除 BuildKit 特有的元数据，防止旧版解析 JSON 报错
docker buildx build \
    --platform linux/arm64 \
    --network host \
    --build-arg SOURCE_URL="http://localhost:${PORT}" \
    --build-arg PROXY="${PROXY_URL}" \
    --build-arg CACHE_BUST="${TIMESTAMP}" \
    -t "${IMAGE_NAME}" \
    --output "type=docker,dest=./${OUTPUT_FILE},compression=gzip,provenance=false" \
    .

# --- 5. 完成与验证 ---
echo -e "\033[32m[4/5] 构建成功！\033[0m"
if [ -f "./${OUTPUT_FILE}" ]; then
    FILE_SIZE=$(du -sh ./${OUTPUT_FILE} | cut -f1)
    echo -e "\033[34m[DONE] 离线镜像包已生成: ./${OUTPUT_FILE} (大小: $FILE_SIZE)\033[0m"
    
    echo -e "\033[32m[5/5] 验证镜像格式...\033[0m"
    # 验证是否存在 manifest.json，这是传统 Docker 格式的标志
    if tar -tf "./${OUTPUT_FILE}" | grep -q "manifest.json"; then
        echo "[SUCCESS] 格式校验通过：检测到标准的 manifest.json"
    else
        echo "\033[31m[WARNING] 未检测到 manifest.json，请检查构建输出日志\033[0m"
    fi
fi

echo -e "\n\033[32m后续操作提示：\033[0m"
echo "1. 将 $OUTPUT_FILE 拷贝到低版本目标机器"
echo "2. 执行命令：docker load -i $OUTPUT_FILE"
