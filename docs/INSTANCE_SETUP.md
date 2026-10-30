# 实例部署与恢复（10/31 当天用）

本文覆盖三种情形：**实例正常**、**实例重启（/var/tmp 被清）**、**持久盘被清空**。
所有命令均按《初赛提交要求》第二部分的口径执行。

---

## 0. 一句话结论

服务只需要三样东西：**仓库代码** + **Q5 权重（24.64 GiB）** + **Node22/DSH 运行时**。

三者都可从零重建：

| 组成 | 从哪来 |
|---|---|
| 代码 | 仓库（Gitee/GitHub 私有仓克隆） |
| 权重 | `fetch_model.sh`（HuggingFace，回退 hf-mirror；8 段并行 + sha256 校验） |
| Node22 + DSH | `agent/setup_dsh.sh`（`start.sh` 检测到缺失会自动调用） |

> **权重位置**：`/workspace` 只有 25 GiB，而 Q5 权重 24.64 GiB **放不下**（放完会超 0.57 GiB）。
> 因此权重落在 `/var/tmp/models_q5`（1.7 TB），代价是**实例重启后要重新下载（约 40 秒 ~ 2 分钟）**。
> 这正是官方 FAQ Q6 允许的「放不下时可以放在 /workspace 以外，但每次重启后要重新下载」。

---

## 1. 正常启动（实例已就绪）

严格按官方顺序：

```bash
source /workspace/env.sh
cd /workspace/submission/43436_rtlagent

./serve/start.sh            # 启动推理服务（llama-server :8000），权重缺失会自动下载；就绪打印 READY
./serve/run_api.sh start    # 启动投题服务（uvicorn :7860，后台运行 + 退出后自动重启）

# 自检：应返回 401
curl -s -o /dev/null -w "%{http_code}\n" --noproxy '*' http://127.0.0.1:7860/v1/health

# 带令牌自检：应返回 ready:true, track:rtl
curl -s -H "Authorization: Bearer $FPGACHINA_TOKEN" http://127.0.0.1:7860/v1/health
```

建立公网隧道（去掉代理变量，否则隧道连不上）：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
    /var/run/secrets/frp-self-service/install
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
    "$HOME/.local/bin/rc-tunnel" expose --port 7860
sleep 20
"$HOME/.local/bin/rc-tunnel" status
grep -E '^\s*serverAddr' /root/.local/state/rc-tunnel/frpc.toml   # 须为 rc-tun-cn.anruicloud.com
```

`status` 的 `URL:` 一行即公网网址，报送到表单。

---

## 2. 实例重启（/var/tmp 也被清空，最常见）

**一条命令**：

```bash
source /workspace/env.sh
cd /workspace/submission/43436_rtlagent
bash serve/restore.sh
```

`restore.sh` 依次做：

1. 找权重；找不到就 `fetch_model.sh` 下载 Q5 到 `/var/tmp/models_q5`（8 段并行 + sha256 校验）
2. 起推理服务（内部调 `serve/start.sh`）
3. 起投题服务（内部调 `serve/run_api.sh start`）
4. 打印四项自检（权重文件 / 8000 / 7860 带令牌 / 7860 不带令牌应 401）

然后按第 1 节重建隧道并重新报送网址。

### 也可以只跑官方命令

`serve/start.sh` 已内置自动下载，所以官方两步就够：

```bash
source /workspace/env.sh
cd /workspace/submission/43436_rtlagent
./serve/start.sh            # 权重缺失 -> 自动下载 -> 起推理 -> 起投题
./serve/run_api.sh start    # 幂等确认
```

关闭自动下载（离线环境手工放权重时）：

```bash
NO_FETCH=1 ./serve/start.sh
```

改权重的落点：

```bash
MODEL_DEST=/your/path ./serve/start.sh
```

### 权重查找顺序（`start.sh` 内置）

```
MODEL_FILE（显式指定）
  > /opt/models/            （镜像内，Dockerfile 构建时烘进去）
  > /workspace/models/      （持久盘，但 Q5 放不下）
  > /var/tmp/models_q5/     （本方案的实际落点）
  > /var/tmp/models_35b/  > /tmp/models_35b/
  > /var/tmp/*/<同名文件>   （兜底 glob）
```

---

## 3. 持久盘被清空（/workspace 没了）

```bash
cd /workspace
git clone <私有仓库地址> 43436_rtlagent
cd 43436_rtlagent
source /workspace/env.sh

bash serve/restore.sh       # 权重下载 + 两个服务 + 自检
# 然后重建隧道（见第 1 节）
```

---

## 4. 自检清单（启动后 30 秒内完成）

| 检查 | 命令 | 期望 |
|---|---|---|
| 权重文件 | `ls -lh /var/tmp/models_q5/` | 24.64 GiB（26,456,194,016 B） |
| 推理服务 | `curl -s localhost:8000/v1/models` | 返回 `qwen36-35b` |
| 投题服务（无令牌） | `curl -s -o /dev/null -w '%{http_code}' localhost:7860/v1/health` | `401` |
| 投题服务（带令牌） | `curl -s -H "Authorization: Bearer $FPGACHINA_TOKEN" localhost:7860/v1/health` | `{"ready":true,"track":"rtl","model":"Qwen3.6-35B-A3B","vram_gb":约26}` |
| 投题接口 | `curl -X POST ... /v1/solve -d '{}'` | `422`（参数校验，说明路由可达） |
| 隧道 | `"$HOME/.local/bin/rc-tunnel" status` | `Status: active` |
| serverAddr | `grep serverAddr /root/.local/state/rc-tunnel/frpc.toml` | `rc-tun-cn.anruicloud.com` |
| 外部连通 | 在自己电脑上 `curl -H "Authorization: Bearer <令牌>" <网址>/v1/health` | `ready:true`，`track` 为 `rtl` |

---

## 5. 注意

- **服务必须绑 `127.0.0.1`**，对外由平台 `rc-tunnel` 转发；绑 `0.0.0.0` 平台不接管。
- **只暴露 7860 一个端口**，8000 由智能体经 `127.0.0.1` 内部调用。
- **投题服务必须用 `run_api.sh start` 起**，不要在终端前台或 notebook 单元格里跑。
- **权重不要放进 git**；`/workspace` 只有 25 GiB，放 24.64 GiB 的 Q5 会超配额。
- **评测期间不要重启实例**。重启后 `/var/tmp` 清空，权重需联网重下；而评测是断网沙箱，
  重下会失败 → 该时段题目按 L0 计。官方规则亦如此。
- **评测期间不得修改方案代码、技能包或权重**（改了就与封存版本不一致）。
- 实例重启后 `rc-tunnel` 会被清除，需重新 `install`；重新 `expose` 会得到**新网址**，
  必须重新填写报送表单。
