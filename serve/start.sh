#!/usr/bin/env bash
# 常驻进程：llama-server(8000) + 投题服务(7860)
set -o pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LOG_DIR="${SERVE_LOG_DIR:-${AGENT_TMP:-/tmp}/serve-logs}"; mkdir -p "$LOG_DIR"

# 权重查找顺序：显式 MODEL_FILE > 镜像内 /opt/models > 持久盘 /workspace/models
#               > 实例临时目录 /var/tmp/models_35b（fetch_model.sh 的默认落点）
#               > 任意 /var/tmp 下的同名文件（持久盘被清空时的兜底）
MODEL_NAME="${MODEL_FILENAME:-Qwen3.6-35B-A3B-UD-Q5_K_M.gguf}"
if [ -z "${MODEL_FILE:-}" ]; then
  for c in "/opt/models/$MODEL_NAME" "/workspace/models/$MODEL_NAME" \
           "/var/tmp/models_q5/$MODEL_NAME" \
           "/var/tmp/models_35b/$MODEL_NAME" "/tmp/models_35b/$MODEL_NAME"; do
    [ -f "$c" ] && MODEL_FILE="$c" && break
  done
fi
if [ -z "${MODEL_FILE:-}" ]; then
  for c in $(ls -1 /var/tmp/*/"$MODEL_NAME" /var/tmp/"$MODEL_NAME" 2>/dev/null | head -1); do
    [ -f "$c" ] && MODEL_FILE="$c"
  done
fi
MODEL_FILE="${MODEL_FILE:-/opt/models/$MODEL_NAME}"

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

[ -f "$MODEL_FILE" ] || { echo "FATAL: model not found: $MODEL_FILE"; exit 1; }
echo "model: $MODEL_FILE"

if ! curl -sf -m 2 http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
  setsid /opt/llamacpp/llama-server -m "$MODEL_FILE" --host 127.0.0.1 --port 8000 \
    -ngl 999 -c "$MAX_LEN" --jinja --alias qwen36-35b \
    --reasoning off --chat-template-kwargs '{"enable_thinking": false}' \
    > "$LOG_DIR/llama.log" 2>&1 &
  for i in $(seq 1 120); do curl -sf -m 2 http://127.0.0.1:8000/v1/models >/dev/null 2>&1 && break; sleep 2; done
fi
curl -sf -m 3 http://127.0.0.1:8000/v1/models >/dev/null || { echo "FATAL: llama-server not ready"; exit 1; }

# 投题服务由官方 serve/run_api.sh 负责（带 2 秒自动重启，服务短暂中断不会让隧道被收回）
if curl -sf -m 2 http://127.0.0.1:7860/v1/health -H "Authorization: Bearer ${FPGACHINA_TOKEN:-probe}" >/dev/null 2>&1; then
  echo "READY"            # 推理服务与投题服务都在运行
else
  echo "READY_INFERENCE"  # 仅推理服务就绪，下一步执行: ./serve/run_api.sh start
fi
