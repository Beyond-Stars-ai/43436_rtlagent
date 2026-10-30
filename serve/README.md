# serve/ — 推理服务与投题接口

两个常驻进程，都只绑 `127.0.0.1`：

```
 8000   serve/start.sh    推理服务（llama.cpp，ROCm）        不对外
 7860   serve/run_api.sh  投题接口（serve_api.py，官方 watchdog）  经 rc-tunnel 对外
```

**启动顺序不能颠倒**：先推理服务（`start.sh`），再投题接口（`run_api.sh`），最后才 `expose`。
平台每 10 秒探一次 `127.0.0.1:7860`，连续约 1 分钟不通就收回隧道，网址随即作废。

---

## 1. 开发期：不需要 GPU

```bash
export LLM_BACKEND=openai
export LLM_BASE_URL=https://api.deepseek.com/v1     # 或任意 OpenAI 兼容端点
export LLM_API_KEY=sk-xxxxxxxx
export LLM_MODEL=deepseek-coder
```

此时不必启动 `start.sh`，智能体直接访问所配置的端点。注意：商用 API 与本地小模型在输出
长度、指令遵循、代码格式稳定性上差异明显，围绕前者调好的提示词换到后者常常直接失效，
**尽早用本地权重跑一次**。

## 2. 正式提交：本地权重（llama.cpp）

```bash
source /workspace/env.sh                      # FPGACHINA_TOKEN（+ MODEL_NAME）
export MODEL_FILE=/workspace/models/Qwen3.6-35B-A3B-UD-Q5_K_M.gguf   # 可省略，start.sh 会自动查找
./serve/start.sh                              # ① 推理服务；就绪打印 READY
```

`start.sh` 的权重查找顺序：

```
MODEL_FILE > /opt/models/ > /workspace/models/ > /var/tmp/models_q5/ > /var/tmp/models_35b/
          > 任意 /var/tmp/*/<同名文件>
```

上下文长度**从 `manifest.json` 的 `context.max_model_len` 读取**（当前 49152），
避免"声明与实现漂移"。实测占用 25.9 GiB（单卡 32 GB 约束内）。

## 3. 起投题接口（官方 watchdog：`serve/run_api.sh`）

```bash
export FPGACHINA_TOKEN=<赛事方发的令牌>
export MODEL_NAME=Qwen3.6-35B-A3B          # 必须与 model/MODEL.md 声明一致

./serve/run_api.sh start                   # ② 后台运行 + 退出 2 秒自动重启
./serve/run_api.sh status
./serve/run_api.sh stop                    # 主动停止（平时不要用）

curl -s -o /dev/null -w "%{http_code}\n" --noproxy '*' http://127.0.0.1:7860/v1/health   # 应 401
```

**为什么必须用 `run_api.sh` 而不是直接前台 `uvicorn`**：隧道随时可能因一次崩溃被永久收回。
`run_api.sh` 用 `setsid` 脱离终端，并在 API 退出后 2 秒内重启，把中断压到隧道收回阈值
（约 1 分钟）以下 —— 这是"服务重启、网址不变"的唯一手段。

## 4. 建立隧道

```bash
/var/run/secrets/frp-self-service/install
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
    "$HOME/.local/bin/rc-tunnel" expose --port 7860     # 去掉代理变量，否则隧道连不上
"$HOME/.local/bin/rc-tunnel" status                     # URL: 一行即公网网址
```

## 5. 自测

```bash
curl -H "Authorization: Bearer $FPGACHINA_TOKEN" http://localhost:7860/v1/health
# {"ready":true,"track":"rtl","model":"Qwen3.6-35B-A3B","vram_gb":26.0}

# 投一道题：立即返回 job_id，之后轮询直到 done
curl -X POST -H "Authorization: Bearer $FPGACHINA_TOKEN" -H "Content-Type: application/json" \
     -d '{"task_id":"t1","nonce":"n1","mode":"agent","prompt":"...","interface":"","deadline_s":900}' \
     http://localhost:7860/v1/solve
curl -H "Authorization: Bearer $FPGACHINA_TOKEN" http://localhost:7860/v1/jobs/<job_id>
```

`track` 必须为 `"rtl"`，赛事方投题前会核对，不符即拒投。

---

## 6. 几处容易踩的地方

**显存按 32 GB 预算，而不是按卡的容量。** 验证机的 W7900 有 48 GB 物理显存，但赛题上限是
**单卡 32 GB**。llama.cpp 按实际加载量占用，本方案实测 25.9 GiB；若换成 vLLM 一类预分配
框架，须显式限制 `--gpu-memory-utilization`，并在提交前用 `rocm-smi --showmeminfo vram`
实测稳态。

**绑 `127.0.0.1`，不是 `0.0.0.0`。** `rc-tunnel` 只接管环回口上的 HTTP 服务。绑 `0.0.0.0`
时服务本身是活的，但外面永远打不进来，在赛事方那边看起来和挂掉没有区别，而失败不重投。

**只暴露 7860 一个端口。** 一个 notebook 同时只能暴露一个端口；8000 由智能体经
`127.0.0.1` 内部调用，不要暴露。

**令牌检查不是可选项。** 平台给出的公网 URL 不带业务层鉴权，没有令牌检查，任何人拿到网址
都能投题。

**中间文件落本地目录。** `AGENT_TMP` 默认 `/tmp`；实例上是 4 GB 内存盘，请设为 `/var/tmp`。
`/workspace` 是 NFS，Vivado 综合产生的大量小文件写上去极慢 —— 这不是优化建议。

**服务要能连续跑数百次请求不重启。** 评测期间挂掉就是 L0，赛事方不重投。每题之后要清理
临时目录与子进程，别把磁盘写满。

**投题是异步的。** `POST /v1/solve` 立即返回任务编号，题在后台解，赛事方每 2 秒查询。
平台入口会切断 60 秒内无响应的请求，所以不能让请求挂着等解题完成。

**自己先超时，别让赛事方超时。** `serve_api.py` 在 `deadline_s - 10` 秒时对 `run.sh`
整个进程组发 SIGTERM，5 秒后 SIGKILL，返回已有最好结果。按进程组结束，是因为只杀
`run.sh` 时它启动的智能体与 Vivado 会继续跑，和下一题抢 GPU/CPU。

**常驻进程只从 `start.sh` / `run_api.sh` 起。** 容器重建这条退路下，赛事方只运行
`start.sh` 再逐题调用 `run.sh`，不启动 `serve_api.py`；超时信号发给 `run.sh` 的进程组，
由 `run.sh` 在后台带起的常驻进程会随之结束。详见 `docs/API_CONTRACT.md` 第八节。

**进程要自己回收。** 平台实例的 1 号进程是 JupyterLab，不回收孤儿进程，被结束的子进程会
变僵尸占进程号。`serve_api.py` 启动时把自己设为回收者（`PR_SET_CHILD_SUBREAPER`），每题
结束回收该题全部进程；换自己的服务时这一段要保留。详见 `docs/API_CONTRACT.md` 第十节。

---

## 7. 跨代架构的已知问题（验证 gfx1100 → 决赛 gfx1201）

1. **跨卡张量并行与流水并行在 gfx1201 上不可用**，无 XGMI，集合通信库无法可靠启动；
   四卡推荐每卡一个实例、四路并发。
2. **FP8 存在「硬件有、软件未必用得上」**：部分框架缺少平台检测，会把 FP8 权重静默反量化，
   算力浪费且无告警。
3. **部分算子库在 RDNA 4 上不工作**，须显式关闭。
4. **gfx1100 上做 FP8 量化只省显存、不获加速**（无 FP8 矩阵指令），验证阶段的量化收益不能
   直接外推到决赛。
