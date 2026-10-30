#!/usr/bin/env bash
# 下载并校验模型权重（默认 ./models）
set -e
DEST="${1:-./models}"
REV="a483e9e6cbd595906af30beda3187c2663a1118c"
FILE="Qwen3.6-35B-A3B-UD-Q5_K_M.gguf"
SHA="c13ce26253ea334df472bd8fbd2d6da66d8a41195c17f6fcbf44c4d20ece0932"
URL="https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/${REV}/${FILE}"
# 文件大小：26,456,194,016 字节（24.6 GiB）；单卡 32 GB 约束内实测 27.81 GB
mkdir -p "$DEST"
[ -f "$DEST/$FILE" ] || curl -L --retry 3 -o "$DEST/$FILE" "$URL"
echo "$SHA  $DEST/$FILE" | sha256sum -c -
