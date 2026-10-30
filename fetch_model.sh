#!/usr/bin/env bash
# ============================================================================
# 下载并校验模型权重
#
#   ./fetch_model.sh [DEST]        # 默认 ./models
#
# 权重不进 git 仓库（24.6 GiB）。MODEL.md 声明的地址与 revision 即此处常量。
# 先用官方 HuggingFace，不通则回退到 hf-mirror 镜像；8 段并行，最后校验 sha256。
# ============================================================================
set -uo pipefail

DEST="${1:-./models}"
REV="a483e9e6cbd595906af30beda3187c2663a1118c"
FILE="Qwen3.6-35B-A3B-UD-Q5_K_M.gguf"
SHA="c13ce26253ea334df472bd8fbd2d6da66d8a41195c17f6fcbf44c4d20ece0932"
SIZE=26456194016
# 单卡 32 GB 约束内实测 27.81 GB（25.90 GiB）

NSEG="${NSEG:-8}"
mkdir -p "$DEST" || { echo "fetch_model: 无法创建 $DEST"; exit 1; }
TARGET="$DEST/$FILE"

say() { echo "[fetch_model] $*"; }

# ---- 已经就位就只校验 ----
if [ -f "$TARGET" ]; then
  HAVE=$(stat -c%s "$TARGET" 2>/dev/null || echo 0)
  if [ "$HAVE" -eq "$SIZE" ]; then
    if echo "$SHA  $TARGET" | sha256sum -c - >/dev/null 2>&1; then
      say "已存在且 sha256 校验通过: $TARGET"
      exit 0
    fi
    say "已存在但 sha256 不匹配，重新下载"
  else
    say "已存在但大小不符（$HAVE / $SIZE），重新下载"
  fi
  rm -f "$TARGET"
fi

WORK="$(mktemp -d "${TMPDIR:-/var/tmp}/fetch_model.XXXXXX")" || { echo "fetch_model: mktemp 失败"; exit 1; }
trap 'rm -rf "$WORK"' EXIT INT TERM

urls=(
  "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/${REV}/${FILE}"
  "https://hf-mirror.com/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/${REV}/${FILE}"
)

download_parallel() {
  local url="$1" dir="$2"
  local chunk=$(( (SIZE + NSEG - 1) / NSEG ))
  local i s e
  mkdir -p "$dir"
  for i in $(seq 0 $((NSEG-1))); do
    s=$((i*chunk)); e=$((s+chunk-1)); [ "$e" -ge "$SIZE" ] && e=$((SIZE-1))
    curl -sL --connect-timeout 25 --max-time 5400 -r "$s-$e" -o "$dir/part$i" "$url" 2>/dev/null &
  done
  wait
  local got=0 g
  for i in $(seq 0 $((NSEG-1))); do
    g=$(stat -c%s "$dir/part$i" 2>/dev/null || echo 0)
    got=$((got+g))
  done
  [ "$got" -eq "$SIZE" ]
}

ok=0
for url in "${urls[@]}"; do
  host=$(echo "$url" | sed -E 's#https?://([^/]+)/.*#\1#')
  say "尝试 $host （${NSEG} 段并行）"
  rm -rf "$WORK/seg"; mkdir -p "$WORK/seg"
  if download_parallel "$url" "$WORK/seg"; then
    say "  下载完整，合并…"
    cat "$WORK/seg"/part* > "$TARGET" 2>/dev/null
    if [ "$(stat -c%s "$TARGET" 2>/dev/null || echo 0)" -eq "$SIZE" ]; then ok=1; break; fi
  else
    say "  $host 下载不完整，换下一个源"
  fi
  rm -f "$TARGET"
done

if [ "$ok" -ne 1 ]; then
  say "所有源均失败（离线环境无法下载）"
  say "请从 MODEL.md 声明的地址手工下载该文件，放到 $DEST/"
  exit 1
fi

say "校验 sha256…"
if echo "$SHA  $TARGET" | sha256sum -c - >/dev/null 2>&1; then
  say "校验通过: $TARGET ($(python3 -c "print(f'{$SIZE/1024**3:.2f}')" 2>/dev/null || echo ?) GiB)"
  exit 0
fi
say "sha256 校验失败，删除产物"
rm -f "$TARGET"
exit 1
