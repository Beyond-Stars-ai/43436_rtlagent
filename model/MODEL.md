# 模型声明

| 项 | 值 |
| :--- | :--- |
| 模型 | Qwen3.6-35B-A3B（MoE，总 35B / 每 token 激活 3B） |
| 权重仓库 | https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF |
| **revision** | **`a483e9e6cbd595906af30beda3187c2663a1118c`**（main 分支 commit 哈希） |
| 文件 | `Qwen3.6-35B-A3B-UD-Q5_K_M.gguf` |
| 文件大小 | 22,134,528,992 字节（20.6 GiB） |
| 文件 sha256 | `c13ce26253ea334df472bd8fbd2d6da66d8a41195c17f6fcbf44c4d20ece0932` |
| 量化 | GGUF UD-Q5_K_M（unsloth Dynamic，约 5.6 bit/参数） |
| 实测显存 | **25.90 GiB / 27.81 GB @49152 上下文**（`rocm-smi --showmeminfo vram` 实测；`/v1/health` 回报 26.0 左右）；单卡 32 GB 约束内 |
| 实测生成速度 | 53.7 tok/s（700 token 连续生成，W7900） |
| 推理后端 | llama.cpp `/opt/llamacpp/llama-server`，`-ngl 999` |
| 上下文长度 | **49152**（本方案交付口径；`manifest.json` 的 `context.max_model_len` 与 `serve/start.sh` 启动参数均为此值） |
| 单次输出上限 | **16384**（与 `baseline.py` 同口径；`manifest.json` 的 `context.max_output_tokens` 已同步为 16384） |
| 思考模式 | 关闭：`--reasoning off` + `chat_template_kwargs.enable_thinking=false` |
| 辅助模型 | 无（评分细则 2.13） |
| 微调 | 无，直接使用公开权重 |

## 获取与校验

```bash
# 1) 下载（约 21 GB）
./fetch_model.sh ./models

# 2) 或从 HF 按 revision 精确下载
python3 - <<'PY'
from huggingface_hub import hf_hub_download
p = hf_hub_download("unsloth/Qwen3.6-35B-A3B-GGUF",
                    "Qwen3.6-35B-A3B-UD-Q5_K_M.gguf",
                    revision="a483e9e6cbd595906af30beda3187c2663a1118c",
                    local_dir="models")
print(p)
PY

# 3) 校验文件完整性
sha256sum models/Qwen3.6-35B-A3B-UD-Q5_K_M.gguf
# 期望 c13ce26253ea334df472bd8fbd2d6da66d8a41195c17f6fcbf44c4d20ece0932
```

权重不入 Git（`.gitignore` 已排除 `*.gguf`）；构建镜像时须放在构建上下文的 `models/` 目录。

## 为什么选 MoE 而非 Dense

同卡（W7900 48 GB）实测：

| 模型 | 权重 | 显存 | 生成速度 |
| :--- | ---: | ---: | ---: |
| Qwen3.6-27B Dense Q4_K_M | 16.8 GB | 18.9 GB | 18.3 tok/s |
| **Qwen3.6-35B-A3B MoE Q5_K_M** | 26 GB | **27.8 GB** | **约 50 tok/s（2.7×）** |

MoE 每 token 只激活 3B 参数，速度高约 2.7 倍；全库 156 题实测得分也更高（Q5 0.7910 vs Q4 0.7577）。

