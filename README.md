# 43436_rtlagent —— RTL 本地智能体方案

> 赛道：2026 FPGA 创新设计大赛 · AMD 3.1 RTL/HLS 本地智能体设计赛道
> 智能体运行时：DeepSeek Harness（DSH）0.2.1-rc.2 headless
> 模型：Qwen3.6-35B-A3B（MoE，GGUF UD-Q4_K_M）

## 1. 目录结构

```
43436_rtlagent/
├── Dockerfile            官方基镜像 + Node 22 + DSH 0.2.1-rc.2
├── manifest.json         模型 / 上下文 / 生成 / 服务 / 智能体声明
├── run.sh                单题入口：读题 -> solution.v + trace.jsonl
├── run_baseline.sh       赛事方基线入口（原样）
├── baseline.py           赛事方基线（原样，不可修改）
├── requirements.txt      投题服务依赖
├── fetch_model.sh        下载并校验权重（含 revision 与 sha256）
├── agent/                智能体实现
│   ├── main.py           DSH headless 封装 + trace 转换
│   ├── dsh_trace.py      DSH 会话事件 -> 官方 trace.jsonl
│   ├── AGENTS.md         工作区约定（模块名/接口/语法/编译验证）
│   └── setup_dsh.sh      运行时安装脚本（重启后可用）
├── skill/                技能包
│   ├── rtl-module-interface-contract/
│   └── verilog-2001-syntax/
├── serve/                常驻服务
│   ├── start.sh          llama-server(8000) + 投题服务(7860)
│   └── serve_api.py      /v1/health /v1/solve /v1/jobs
└── model/MODEL.md        权重地址、revision、量化、显存、速度
```

## 2. 方案概述

```
prompt.txt --> run.sh          : DSH 智能体（AGENTS.md + xvlog 编译回路）--> solution.v + trace.jsonl
prompt.txt --> run_baseline.sh : 官方基线（单次直出，不重试、不调工具）   --> solution.v
                     ^ 同一 llama-server、同一 generation 段
```

`run.sh` 只做三件事：把 `agent/AGENTS.md` 放进输出目录、以 headless 模式调用 DSH、
把会话事件流转换成官方 trace 格式。**题目逻辑全部由模型完成**，不含任何硬编码答案。

## 3. 结果（156 题全库，单采样）

判定口径与官方一致：`L1 可编译 → L2 仿真通过 → L3 可综合`，逐级门控。

| | 方案（32K） | 基线（T2） |
| :--- | ---: | ---: |
| L3 可综合 | **72.4%（113/156）** | — |
| L1 可编译 | 26.3%（41/156） | — |
| L0 未通过 | 1.3%（2/156） | — |
| **题集得分** | **0.7769** | **0.6321** |
| 平均单题墙钟 | 约 150 s | — |

分段：基础题 1.000 / 中题 0.893 / K-map 段 0.753 / FSM 段 0.813 / 难段 0.483。

归一化增益 g = 0.3936 → 能力 23.31 + 增益 26.2 = 49.5 / 70（另加代价 10 与工程质量 20）。
详见 `REPORT.md`。

## 4. 复现

```bash
# 1) 取权重（21 GB，三选一，详见下）
./fetch_model.sh ./models
# 1b) 构建（Node 22 已 vendor；dsh 的 ~90 个依赖仍需构建机联网从 npm 装）
#     注意：拉取基础镜像本身就需要网络，因此"构建期联网、运行期断网"是官方口径。
docker build -t 43436_rtlagent .
#     若构建机可联网且不想预放权重：docker build --build-arg FETCH_MODEL=1 -t 43436_rtlagent .

# 权重来源三选一（运行时断网，均不需要网络）：
#   A) 构建前 ./fetch_model.sh ./models      → 权重烤进镜像（推荐）
#   B) --build-arg FETCH_MODEL=1            → 构建期下载并校验 sha256
#   C) 都不做                                → 运行期从 /workspace/models 读取（持久盘预置）

# 2) 启动常驻服务
export FPGACHINA_TOKEN=<赛事方发放>
./serve/start.sh          # 就绪后打印 READY

# 3) 单题自测
./run.sh          /path/to/task /tmp/out_agent
./run_baseline.sh /path/to/task /tmp/out_base
```

硬件：单卡 32 GB 以内（实测 22.9 GB）、Vivado 2026.1、llama.cpp（基镜像自带）、Node 22（镜像内安装）。

## 技能包构成（避免误读）

本方案的领域知识分布在两处，合计构成本队技能包：

| 位置 | 内容 | 生效方式 |
| :--- | :--- | :--- |
| `agent/prompt4/` | **16 条**自查条目（kmap/kmapdc/truthtable/reverse/priority/sumwidth/count/fsm/onehot/mealy/seqdet/edge/concat/adder/ca/wave）+ base/pitfalls/check_stable/fixed | 按题面关键词路由，拼进每题提示词（1.7~2.3 KB） |
| `skill/` | 1 个 DSH 技能 `verilog-2001-syntax`（另 1 个 `rtl-module-interface-contract` 以 `.disabled` 保留，见 `skill/README.md`） | 模型用 `skill` 工具按需加载 |

报告 §5 的"技能清单/提炼过程/有效性验证"对应 `agent/prompt4/` 这批条目。

## 5. 合规说明

| 条款 | 做法 |
| :--- | :--- |
| 断网（2.3、2.9） | 禁用 DSH 的 `web`/`tool-web`/`web-*` 五个插件；智能体不访问任何外部地址 |
| 不用非 AMD 工具 | 只调用 `xvlog`/`xelab`/`xsim`/`synth_design` |
| 确定性检查合规（2.11） | 编译验证由模型在 bash 中主动执行，脚本只做 trace 格式转换 |
| 基线不可改（2.4） | `baseline.py`/`run_baseline.sh` 原样复制，未作任何修改 |
| 单次输出上限 | 16384，与基线同口径；上下文 **32768**（交付口径，声明于 manifest 与 MODEL.md） |

> 实例部署/恢复（含持久盘被清空、实例重启两种极端情况）见 [`docs/INSTANCE_SETUP.md`](./docs/INSTANCE_SETUP.md)。

## 6. 本队部署方式（C 方案：权重走持久盘）与构建验证

验证实例的持久盘 `/workspace` 配额 25 GB，放不下 21 GB 权重；权重由 `fetch_model.sh` 下到
`/var/tmp/models_35b/`（实例存活期内有效）。`serve/start.sh` 的查找顺序为
`MODEL_FILE` > `/opt/models/` > `/workspace/models/`，因此在实例上用任一方式指向真实文件即可：
设置 `MODEL_FILE`，或在 `/workspace/models/` 建同名软链（本队实测用后者，`start.sh` 默认查找即可命中）。

- **镜像内不烤权重**：`models/` 里只有 `.gitkeep`，`docker build` 会打印一行 WARN 并继续（不中断构建）；
- **运行期**由 `serve/start.sh` 按顺序查找：`MODEL_FILE` > `/opt/models/` > `/workspace/models/`；
- **容器内看不到宿主目录**：用 C 方案运行镜像时必须挂载，例如
  `docker run -v /workspace/models:/workspace/models ...`；否则 `serve/start.sh` 会以
  `FATAL: model not found` 退出（已实测，报错信息明确）。
- 若需把权重烤进镜像（A 方案）或构建期下载（B 方案），见上文第 4 节。

### 构建验证（两步，已实测）

| 验证 | 做法 | 结果 |
| :--- | :--- | :--- |
| ① 步骤与实跑一致 | 在 Radeon 实例上原生执行 Dockerfile 的全部步骤，跑完 156 题 | ✅ 0.7769 |
| ② 模拟构建（联网，npm 装 dsh） | 把 `BASE_IMAGE` 换成公开的 `ubuntu:24.04`、**其余行一字不改**，在真实 docker 中构建 | ✅ **20/20 步骤通过**；容器内 `node v22.23.3`、`dsh 0.2.1-rc.2`、`defaultContextWindow: 32768`、技能包与插件就位；`baseline.py` sha `17aaa609d4…` 与官方一致 |
| ③ 模拟构建（**断网**，依赖已 vendor） | 同上，但把 dsh 依赖树整体打进 `vendor/`，`docker build --network=none` | ✅ **19/19 步骤通过**，证明"零外网"路径也可行（该 84 MB vendor 为可选项，最终未随包提交，以控制仓库体积） |

说明：官方基础镜像位于平台私有 registry，外部无法拉取，官方要求"队伍不自行构建、只保持 `BASE_IMAGE` 与步骤一致"；上述 ② 用于验证**我们新增的步骤**本身无误。
