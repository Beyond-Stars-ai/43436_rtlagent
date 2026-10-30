# 实例部署与恢复（10/31 当天用）

本文覆盖三种情形：**实例正常**、**持久盘被清空**、**实例重启（/var/tmp 也被清）**。

## 0. 一句话结论

服务只需要三样东西：**仓库代码** + **21 GB 权重** + **Node22/DSH 运行时**。
三者都可从零重建：代码来自 GitHub，权重用 `fetch_model.sh` 下，运行时由 `agent/setup_dsh.sh` 装。

## 1. 正常启动（实例已就绪）

```bash
cd /workspace/submission/43436_rtlagent      # 或 git clone 到任意可写目录
export FPGACHINA_TOKEN=<赛事方令牌>
bash serve/start.sh                          # ① 推理服务，打印 READY
bash serve/run_api.sh start                  # ② 投题服务（7860，自动重启）
curl -s localhost:7860/v1/health -H "Authorization: Bearer $FPGACHINA_TOKEN"
# 期望 {"ready":true,"track":"rtl","model":"Qwen3.6-35B-A3B","vram_gb":21.6}
```

## 2. 持久盘被清空（/workspace 没了）

```bash
cd /workspace
git clone https://github.com/Beyond-Stars-ai/43436_rtlagent.git
cd 43436_rtlagent
./fetch_model.sh /var/tmp/models_35b          # 21 GB，约 10~20 分钟；落到 /var/tmp 是因为持久盘只有 25 GB
export FPGACHINA_TOKEN=<令牌>
bash serve/start.sh
bash serve/run_api.sh start
```

`serve/start.sh` 的权重查找顺序（已内置）：

```
MODEL_FILE > /opt/models/ > /workspace/models/ > /var/tmp/models_35b/ > /var/tmp/*/<同名文件>
```

因此**不需要**再建软链；若权重落在别处，用 `MODEL_FILE=/path/to.gguf bash serve/start.sh` 指定即可。

## 3. 实例重启（/var/tmp 也没了，/opt 复原为基镜像）

`serve/start.sh` 会自动检测 DSH：

```bash
# 若 dsh 不在 PATH，start.sh 内部会执行：
DSH_HOME=$PWD/.dsh-home bash agent/setup_dsh.sh    # 装 Node22（npmmirror）+ dsh 0.2.1-rc.2 + 生成 profile
```

所以重启后仍然只要：

```bash
git clone ... && cd 43436_rtlagent
./fetch_model.sh /var/tmp/models_35b
export FPGACHINA_TOKEN=<令牌>
bash serve/start.sh
bash serve/run_api.sh start
```

## 4. 自检清单（启动后 30 秒内完成）

| 检查 | 命令 | 期望 |
| :--- | :--- | :--- |
| 模型文件 | `ls -lh $(grep -m1 'model:' /tmp/serve-logs/llama.log 2>/dev/null) 2>/dev/null || ls -lh /var/tmp/models_35b` | 21 GB |
| llama | `curl -s localhost:8000/v1/models` | 返回 `qwen36-35b` |
| 健康 | `curl -s localhost:7860/v1/health -H "Authorization: Bearer $TOKEN"` | `ready:true, track:rtl` |
| 鉴权 | 不带令牌请求 | HTTP 401 |
| 投题 | `curl -X POST localhost:7860/v1/solve -d '{"task_id":"t","nonce":"n","mode":"agent","prompt":"...","interface":"","deadline_s":900}'` | 202 + `job_id` |

## 5. 注意

- 服务必须绑 `127.0.0.1`，对外由平台 `rc-tunnel` 转发；端点网址报给赛事方。
- 权重不要放进 git；`/workspace` 只有 25 GB，放 21 GB 权重后容易触发配额问题。
- 评测期间不得修改方案代码、技能包与权重（改了就与封存版本不一致）。
