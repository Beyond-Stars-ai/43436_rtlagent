# syntax=docker/dockerfile:1
# 43436_rtlagent —— 基于赛事方官方基础镜像构建
#   官方基镜像：fpgachina-rtlhls（rocm10-2026.1-r3，Radeon Cloud 私有 registry）
#   内含 Vivado/Vitis 2026.1、llama.cpp(/opt/llamacpp)、ROCm 10.0、python3
#   本方案额外依赖：Node 22 + @deepseek-ai/dsh 0.2.1-rc.2（约 +900 MB）
#   构建前须把权重放进构建上下文：models/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf（21 GB）
# BASE_IMAGE 与官方 example/Dockerfile 完全一致（含完整 digest，64 位）
# 官方说明：该镜像在平台私有 registry 内，外部无法拉取；实例上也没有 docker，
# 因此队伍不自行构建本文件；赛事方仅在"容器重建回退模式"下用它（API_CONTRACT §8）。
ARG BASE_IMAGE=fpgachina-rtlhls@sha256:a7cf627e11708e8a6dcd9de689ff934f7b450b79369ebc7bb0217e8b54d81e56
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    EDA_TMP=/var/tmp/eda AGENT_TMP=/var/tmp SELFTEST_TMP=/var/tmp \
    XILINXD_LICENSE_FILE=2100@vivado-lic.fpgachina.svc.cluster.local \
    FLEXLM_TIMEOUT=300000000 \
    ROCBLAS_TENSILE_LIBPATH=/opt/llamacpp/rocblas/library \
    MODEL_FILE=/opt/models/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
    DSH_HOME=/opt/dsh-home \
    PATH=/opt/node/bin:$PATH \
    FPGACHINA_TOKEN=""

WORKDIR /app

# ---- 权重 ----
# 权重来源三选一：
#   A) 构建上下文 models/ 内已有（推荐：构建前 ./fetch_model.sh ./models）
#   B) 构建机可联网：docker build --build-arg FETCH_MODEL=1
#   C) 都不满足：镜像不烤权重，运行期由 serve/start.sh 从 /workspace/models 读取
ARG FETCH_MODEL=0
COPY models/ /opt/models/

# ---- Node 22（离线：已 vendor） + DSH（构建期联网装依赖）----
COPY vendor/node.tar.xz /tmp/node.tar.xz
RUN mkdir -p /opt/node && tar xf /tmp/node.tar.xz -C /opt/node --strip-components=1 && rm /tmp/node.tar.xz \
 && ln -sf /opt/node/bin/node /usr/local/bin/node \
 && ln -sf /opt/node/bin/npm  /usr/local/bin/npm
# dsh 有 ~90 个依赖，只能从 registry 装（构建期需要网络；基础镜像本身也要从 registry 拉）
# 先试官方 registry，失败则回退到 npmmirror（国内网络更稳）
RUN npm install -g @deepseek-ai/dsh@0.2.1-rc.2 --no-audit --no-fund \
 || npm install -g @deepseek-ai/dsh@0.2.1-rc.2 --no-audit --no-fund --registry=https://registry.npmmirror.com \
 || (apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
     curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs && \
     npm install -g @deepseek-ai/dsh@0.2.1-rc.2 --no-audit --no-fund --registry=https://registry.npmmirror.com)

# ---- 方案文件 ----
COPY run.sh run_baseline.sh baseline.py fetch_model.sh manifest.json REPORT.md README.md ./
COPY agent/ agent/
COPY serve/ serve/
COPY model/ model/
COPY skill/ skill/

# ---- DSH profile / 技能 / 插件：统一由 agent/setup_dsh.sh 生成，避免两处漂移 ----
RUN DSH_HOME=/opt/dsh-home bash agent/setup_dsh.sh \
 && mkdir -p /var/tmp/eda \
 && chmod +x run.sh run_baseline.sh serve/start.sh fetch_model.sh agent/setup_dsh.sh

# ---- 权重兜底：FETCH_MODEL=1 时构建期下载并校验；否则只提示，不中断构建 ----
RUN set -e; \
    GGUF=/opt/models/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf; \
    if [ ! -f "$GGUF" ] && [ "$FETCH_MODEL" = "1" ]; then \
      echo "fetching weights at build time (FETCH_MODEL=1)"; bash /app/fetch_model.sh /opt/models; \
    fi; \
    if [ -f "$GGUF" ]; then echo "OK: weights baked in ($GGUF)"; \
    else echo "WARN: no weights baked in; runtime will look for /workspace/models or MODEL_FILE"; fi

EXPOSE 7860 8000
# 与官方 example 一致：不设 entrypoint。评测走 HTTP（serve/start.sh 起服务后退出），
# 容器重建回退模式下由赛事方自行执行 serve/start.sh 与 run.sh。
# 本地自测示例：docker run -it -v /workspace/models:/workspace/models <image> \
#                 bash -lc "serve/start.sh && ./run.sh /path/to/task /tmp/out"
CMD ["/bin/bash"]

