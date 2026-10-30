#!/usr/bin/env bash
# 常驻进程：llama-server(8000) + 投题服务(7860)
set -o pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# 运行时 PATH：官方流程只 source /workspace/env.sh，它不设 PATH；
# 这里自带，保证 python3（含 uvicorn）、rocm-smi、Vivado、node 都能找到。
export PATH="/opt/python/bin:/tools/Xilinx/2026.1/Vivado/bin:/tools/Xilinx/2026.1/Vitis/bin:/opt/node/bin:/opt/llamacpp:${PATH:-/usr/local/bin:/usr/bin:/bin}"
LOG_DIR="${SERVE_LOG_DIR:-${AGENT_TMP:-/tmp}/serve-logs}"; mkdir -p "$LOG_DIR"

# 权重查找顺序：显式 MODEL_FILE > 镜像内 /opt/models > 持久盘 /workspace/models
#               > 实例临时目录 /var/tmp/models_35b（fetch_model.sh 的默认落点）
#               > 任意 /var/tmp 下的同名文件（持久盘被清空时的兜底）
GGUF_NAME="${MODEL_FILENAME:-Qwen3.6-35B-A3B-UD-Q5_K_M.gguf}"
# 注意：不要用 MODEL_NAME 存文件名——env.sh 已 export MODEL_NAME（声明名），
# 覆盖它会让 /v1/health 的 model 字段变成文件名。
if [ -z "${MODEL_FILE:-}" ]; then
  for c in "/opt/models/$GGUF_NAME" "/workspace/models/$GGUF_NAME" \
           "/var/tmp/models_q5/$GGUF_NAME" \
           "/var/tmp/models_35b/$GGUF_NAME" "/tmp/models_35b/$GGUF_NAME"; do
    [ -f "$c" ] && MODEL_FILE="$c" && break
  done
fi
if [ -z "${MODEL_FILE:-}" ]; then
  for c in $(ls -1 /var/tmp/*/"$GGUF_NAME" /var/tmp/"$GGUF_NAME" 2>/dev/null | head -1); do
    [ -f "$c" ] && MODEL_FILE="$c"
  done
fi
MODEL_FILE="${MODEL_FILE:-/opt/models/$GGUF_NAME}"

# DSH 运行时缺失时自动引导（node + dsh + profile），保证实例被重置后也能起服务
if ! command -v dsh >/dev/null 2>&1; then
  echo "dsh not found -> bootstrap via agent/setup_dsh.sh"
  DSH_HOME="${DSH_HOME:-$ROOT/.dsh-home}" bash "$ROOT/agent/setup_dsh.sh" || true
  export PATH="/opt/node/bin:$PATH"
fi

# 上下文长度以 manifest.json 的 context.max_model_len 为准（评分细则 1.3：队伍自定，须声明）
MAX_LEN="$(python3 - "$ROOT/manifest.json" <<'PYEOF' 2>/dev/null || echo 49152
import json, sys
try:
    print(json.load(open(sys.argv[1]))["context"]["max_model_len"])
except Exception:
    print(49152)
PYEOF
)"
MAX_LEN="${MAX_LEN:-49152}"
export ROCBLAS_TENSILE_LIBPATH="${ROCBLAS_TENSILE_LIBPATH:-/opt/llamacpp/rocblas/library}"
export XILINXD_LICENSE_FILE="${XILINXD_LICENSE_FILE:-2100@vivado-lic.fpgachina.svc.cluster.local}"
export FLEXLM_TIMEOUT="${FLEXLM_TIMEOUT:-600}"
export EDA_TMP="${EDA_TMP:-/var/tmp/eda}"; mkdir -p "$EDA_TMP"

# 权重缺失时自动下载（实例重启后 /var/tmp 被清空的情形）
#   NO_FETCH=1 可禁用；MODEL_DEST 可改落点（默认 /var/tmp/models_q5，
#   因为 /workspace 只有 25 GiB，装不下 24.64 GiB 的 Q5 权重）
if [ ! -f "$MODEL_FILE" ]; then
  echo "model not found: $MODEL_FILE"
  if [ "${NO_FETCH:-0}" = "1" ]; then
    echo "FATAL: 权重缺失且 NO_FETCH=1（离线环境请手工放置权重）"
    exit 1
  fi
  FETCH_DEST="${MODEL_DEST:-/var/tmp/models_q5}"
  echo "-> 自动下载到 $FETCH_DEST（约 24.6 GiB，需要联网）"
  if bash "$ROOT/fetch_model.sh" "$FETCH_DEST"; then
    MODEL_FILE="$FETCH_DEST/$GGUF_NAME"
  fi
fi
[ -f "$MODEL_FILE" ] || { echo "FATAL: model not found: $MODEL_FILE"; exit 1; }
echo "model: $MODEL_FILE"

if ! curl -sf -m 2 http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
  # --parallel 1：智能体串行跑单题，单槽独占整份 KV，避免 4 槽共用池的碎片
  # （曾出现 "failed to find free space in the KV cache" 导致 L0）
  setsid /opt/llamacpp/llama-server -m "$MODEL_FILE" --host 127.0.0.1 --port 8000 \
    -ngl 999 -c "$MAX_LEN" --parallel 1 --jinja --alias qwen36-35b \
    --reasoning off --chat-template-kwargs '{"enable_thinking": false}' \
    > "$LOG_DIR/llama.log" 2>&1 &
  for i in $(seq 1 120); do curl -sf -m 2 http://127.0.0.1:8000/v1/models >/dev/null 2>&1 && break; sleep 2; done
fi
curl -sf -m 3 http://127.0.0.1:8000/v1/models >/dev/null || { echo "FATAL: llama-server not ready"; exit 1; }

# 投题服务交给 run_api.sh（官方要求：./serve/run_api.sh start，后台运行、退出后自动重启）
bash "$HERE/run_api.sh" start || { echo "FATAL: serve_api not ready"; exit 1; }
echo READY





