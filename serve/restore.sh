#!/usr/bin/env bash
# ============================================================================
# 实例重启后一键恢复（/var/tmp 被清空的情形）
#
#   bash serve/restore.sh
#
# 做四件事：
#   1. 找权重；找不到就按 MODEL.md 声明的地址下载 Q5_K_M 到 /var/tmp/models_q5
#      （/workspace 只有 25 GiB，Q5 的 24.64 GiB 放不下，只能落脚 /var/tmp）
#   2. 起推理服务（serve/start.sh，llama-server :8000）
#   3. 起投题服务（serve/run_api.sh start，uvicorn :7860，后台+自动重启）
#   4. 自检并打印端点与隧道提示
#
# 注意：步骤 1 需要联网。评测期间是断网沙箱，所以**评测期间不要重启实例**；
#       若在评测期间重启，本脚本会因下载失败而报错，此时无解（官方规则亦如此）。
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT" || exit 1

GGUF_NAME="${MODEL_FILENAME:-Qwen3.6-35B-A3B-UD-Q5_K_M.gguf}"
MODEL_DEST="${MODEL_DEST:-/var/tmp/models_q5}"
PORT_API="${API_PORT:-7860}"
PORT_LLM="${LLM_PORT:-8000}"

say() { echo "[restore $(date -u +%H:%M:%SZ)] $*"; }

echo "=================================================================="
echo " 实例重启后恢复 —— $GGUF_NAME"
echo "=================================================================="

# ---- 1. 权重 ----
say "1/4 检查权重"
FOUND=""
for c in "${MODEL_FILE:-}" "/opt/models/$GGUF_NAME" "/workspace/models/$GGUF_NAME" \
         "$MODEL_DEST/$GGUF_NAME" "/var/tmp/models_35b/$GGUF_NAME" "/tmp/models_35b/$GGUF_NAME"; do
  [ -n "$c" ] && [ -f "$c" ] && FOUND="$c" && break
done
if [ -z "$FOUND" ]; then
  for c in $(ls -1 /var/tmp/*/"$GGUF_NAME" 2>/dev/null | head -1); do
    [ -f "$c" ] && FOUND="$c"
  done
fi

if [ -n "$FOUND" ]; then
  SZ=$(stat -c%s "$FOUND" 2>/dev/null || echo 0)
  say "    已有权重: $FOUND ($(python3 -c "print(f'{$SZ/1024**3:.2f}')" 2>/dev/null || echo ?) GiB)"
else
  say "    未找到权重 -> 下载到 $MODEL_DEST（约 24.6 GiB，需要联网）"
  mkdir -p "$MODEL_DEST" || { say "    !! 无法创建 $MODEL_DEST"; exit 1; }
  if bash "$ROOT/fetch_model.sh" "$MODEL_DEST"; then
    FOUND="$MODEL_DEST/$GGUF_NAME"
    say "    下载完成并通过 sha256 校验"
  else
    say "    !! 权重下载失败"
    say "    可能的原因为离线环境。评测期间重启实例后无法自行恢复，需联系赛事方。"
    exit 1
  fi
fi
export MODEL_FILE="$FOUND"

# ---- 2. 推理服务 ----
say "2/4 启动推理服务（llama-server :$PORT_LLM）"
if curl -sf -m 3 "http://127.0.0.1:$PORT_LLM/v1/models" >/dev/null 2>&1; then
  say "    已在运行"
else
  bash "$ROOT/serve/start.sh" || { say "    !! start.sh 失败"; exit 1; }
fi
curl -sf -m 5 "http://127.0.0.1:$PORT_LLM/v1/models" >/dev/null || { say "    !! 推理服务未就绪"; exit 1; }
say "    就绪"

# ---- 3. 投题服务 ----
say "3/4 启动投题服务（uvicorn :$PORT_API）"
bash "$ROOT/serve/run_api.sh" start || { say "    !! run_api.sh start 失败"; exit 1; }

# ---- 4. 自检 ----
say "4/4 自检"
TOKEN="${FPGACHINA_TOKEN:-}"
FAIL=0

printf "    %-22s" "模型文件"
[ -f "$MODEL_FILE" ] && echo "OK  $MODEL_FILE" || { echo "FAIL"; FAIL=1; }

printf "    %-22s" "llama-server :$PORT_LLM"
curl -sf -m 5 "http://127.0.0.1:$PORT_LLM/v1/models" >/dev/null 2>&1 \
  && echo "OK" || { echo "FAIL"; FAIL=1; }

printf "    %-22s" "投题服务 :$PORT_API"
if [ -n "$TOKEN" ]; then
  H=$(curl -s -m 5 -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT_API/v1/health" 2>/dev/null)
  [ -n "$H" ] && echo "OK  $H" || { echo "FAIL"; FAIL=1; }
else
  C=$(curl -s -o /dev/null -w '%{http_code}' -m 5 --noproxy '*' "http://127.0.0.1:$PORT_API/v1/health" 2>/dev/null)
  [ "$C" = "401" ] && echo "OK  (401，未设 FPGACHINA_TOKEN)" || { echo "FAIL http=$C"; FAIL=1; }
fi

printf "    %-22s" "不带令牌应 401"
C=$(curl -s -o /dev/null -w '%{http_code}' -m 5 --noproxy '*' "http://127.0.0.1:$PORT_API/v1/health" 2>/dev/null)
[ "$C" = "401" ] && echo "OK" || { echo "FAIL http=$C"; FAIL=1; }

echo
if [ "$FAIL" = "0" ]; then
  echo "=================================================================="
  echo " 恢复完成。"
  echo
  echo " 若隧道仍未建立（新容器），执行："
  echo "   env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \\"
  echo "       /var/run/secrets/frp-self-service/install"
  echo "   env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \\"
  echo "       \"\$HOME/.local/bin/rc-tunnel\" expose --port $PORT_API"
  echo "   \"\$HOME/.local/bin/rc-tunnel\" status"
  echo
  echo " 网址变化后须重新填写报送表单。"
  echo "=================================================================="
  exit 0
else
  echo "恢复未完全成功，请查看上面 FAIL 项与 $LOG_DIR 下的日志。"
  exit 1
fi
