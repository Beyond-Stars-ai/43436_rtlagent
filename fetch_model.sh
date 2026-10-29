#!/usr/bin/env bash
# 下载并校验模型权重（默认 ./models）
set -e
DEST="${1:-./models}"
REV="a483e9e6cbd595906af30beda3187c2663a1118c"
FILE="Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
SHA="ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61"
URL="https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/${REV}/${FILE}"
mkdir -p "$DEST"
[ -f "$DEST/$FILE" ] || curl -L --retry 3 -o "$DEST/$FILE" "$URL"
echo "$SHA  $DEST/$FILE" | sha256sum -c -
