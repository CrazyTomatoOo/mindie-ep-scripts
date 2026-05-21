# 使用官方 LTS 镜像
FROM docker.io/openeuler/openeuler:24.03-lts

# 1. 声明参数与环境变量 (合并 ARG/ENV 减少层数)
ARG PROXY
ENV PIP_PROGRESS_BAR=off \
    INPUT_USER_CONFIG="/home/modellite/conf/user_config.json" \
    OUTPUT_USER_CONFIG="/home/modellite/kubernetes_deploy_scripts/user_config.json" \
    LANG=en_US.UTF-8

# 2. 系统配置、依赖安装、用户创建 (全部合并为一个 RUN 层)
# 这样做可以确保所有中间产生的缓存和临时文件在同一层被清理
RUN set -ex && \
    ln -sf /usr/share/zoneinfo/UTC /etc/localtime && \
    sed -i "s/TMOUT=300/TMOUT=0/g" /etc/bashrc && \
    ulimit -u 8192 && \
    echo "* soft nproc 65535" >> /etc/security/limits.conf && \
    echo "* hard nproc 65535" >> /etc/security/limits.conf && \
    echo "sslverify=false" >> /etc/dnf/dnf.conf && \
    if [ -n "$PROXY" ]; then export http_proxy=$PROXY https_proxy=$PROXY; fi && \
    dnf install -y --nobest shadow-utils git vim wget curl jq python3-pip && \
    wget --no-check-certificate https://github.com/krallin/tini/releases/download/v0.19.0/tini-arm64 -O /usr/local/bin/tini && \
    wget --no-check-certificate https://dl.k8s.io/release/v1.31.1/bin/linux/arm64/kubectl -O /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/tini /usr/local/bin/kubectl && \
    ln -s /usr/lib64/libpython3.7m.so.1.0 /usr/lib64/libpython3.7m.so || true && \
    groupadd modelengine -g 2000 && \
    useradd -d /home/modellite -u 200 -g 2000 -m -s /bin/bash modellite && \
    mkdir -p /home/modellite/scripts /home/modellite/conf /home/modellite/kubernetes_deploy_scripts && \
    dnf clean all && \
    rm -rf /var/cache/dnf

# 接收构建脚本传进来的参数
ARG SOURCE_URL
ARG CACHE_BUST

# 设置工作目录
WORKDIR /home/modellite/kubernetes_deploy_scripts

# 核心：单层完成代码拉取与权限固化
RUN echo "Cache Busting: $CACHE_BUST" && \
    if [ -n "$PROXY" ]; then export http_proxy=$PROXY https_proxy=$PROXY; fi && \
    pip3 config set global.index-url https://mirrors.huaweicloud.com/repository/pypi/simple && \
    pip3 config set global.trusted-host mirrors.huaweicloud.com && \
    pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir ruamel.yaml psutil urllib3 jsonpath-ng kubernetes ply pyyaml && \
    mkdir -p /home/modellite/kubernetes_deploy_scripts /home/modellite/scripts /home/modellite/conf && \
    wget -r -np -nH --cut-dirs=0 -R "index.html*" --no-check-certificate \
         "${SOURCE_URL}/kubernetes_deploy_scripts/" -P /home/modellite/ && \
    wget --no-check-certificate "${SOURCE_URL}/start.py" -P /home/modellite/scripts/ && \
    wget --no-check-certificate "${SOURCE_URL}/env_mapping.json" -P /home/modellite/scripts/ && \
    chown -R modellite:modelengine /home/modellite && \
    find /home/modellite/kubernetes_deploy_scripts -type d -exec chmod 750 {} + && \
    find /home/modellite/kubernetes_deploy_scripts -type f -exec chmod 640 {} + && \
    chmod 750 /home/modellite/scripts/start.py

# 5. 切换到非 root 用户
USER modellite

# 6. 设置启动入口点
ENTRYPOINT ["tini", "--"]
CMD python3 /home/modellite/scripts/start.py "${INPUT_USER_CONFIG}" --mapping "/home/modellite/scripts/env_mapping.json" --output "${OUTPUT_USER_CONFIG}" --execute
