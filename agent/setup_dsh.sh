#!/usr/bin/env bash
# 准备 DSH 运行时与 profile。分两层：
#   - 二进制：优先用镜像内 /opt/node；否则从持久盘快照恢复；再否则下载+安装
#   - 配置：profile（本地 llama-server + 禁网工具）与技能包，写到 DSH_HOME
# 开发期 DSH_HOME 放持久盘（/workspace/tools/dsh-home），重启不丢；镜像内默认 /opt/dsh-home。
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TOOLS="${TOOLS_ROOT:-/workspace/tools}"
NODE_HOME="$TOOLS/node"
DSH_HOME_DIR="${DSH_HOME:-$TOOLS/dsh-home}"
NODE_SNAP="$TOOLS/node_full.tgz"
DSH_VER="${DSH_VER:-0.2.1-rc.2}"

mkdir -p "$TOOLS"

# ---- 1. Node ----
if [ ! -x /opt/node/bin/node ]; then
  if [ -f "$NODE_SNAP" ]; then
    echo "restore node from $NODE_SNAP"
    mkdir -p /opt && tar xzf "$NODE_SNAP" -C /opt --no-same-owner 2>/dev/null || true
  fi
fi
if [ ! -x /opt/node/bin/node ]; then
  echo "download node"
  V="${NODE_VER:-v22.23.4}"
  for u in "https://npmmirror.com/mirrors/node/$V/node-$V-linux-x64.tar.xz" \
           "https://nodejs.org/dist/$V/node-$V-linux-x64.tar.xz"; do
    rm -f /tmp/n.tar.xz
    curl -sSL --max-time 180 -o /tmp/n.tar.xz "$u" || true
    [ "$(stat -c%s /tmp/n.tar.xz 2>/dev/null || echo 0)" -gt 10000000 ] && break
  done
  mkdir -p /opt/node && tar xf /tmp/n.tar.xz -C /opt/node --strip-components=1 --no-same-owner
  rm -f /tmp/n.tar.xz
fi
export PATH="/opt/node/bin:$PATH"
for b in node npm npx; do ln -sf "/opt/node/bin/$b" "/usr/local/bin/$b" 2>/dev/null || true; done

# ---- 2. DSH ----
if ! command -v dsh >/dev/null 2>&1; then
  echo "install dsh $DSH_VER"
  npm install -g "@deepseek-ai/dsh@${DSH_VER}" --no-audit --no-fund \
      --registry=https://registry.npmmirror.com >/dev/null 2>&1
fi
ln -sf /opt/node/bin/dsh /usr/local/bin/dsh 2>/dev/null || true

# 持久盘快照（下次重启秒恢复）
if [ ! -f "$NODE_SNAP" ] && [ -d /opt/node ]; then
  tar czf "$NODE_SNAP" -C /opt node --no-same-owner 2>/dev/null || true
fi

# ---- 3. profile 与技能包 ----
mkdir -p "$DSH_HOME_DIR/profiles/headless" "$DSH_HOME_DIR/skills"
cp -r "$HERE/../skill/." "$DSH_HOME_DIR/skills/" 2>/dev/null || true
cat > "$DSH_HOME_DIR/profiles/headless/cordis.patch.yml" <<'YML'
- id: llm-pi-ai
  config:
    providers:
      local:
        api: openai-completions
        baseURL: http://127.0.0.1:8000/v1
        apiKeyEnv: LOCAL_LLM_KEY
        displayName: local-llama
        defaultContextWindow: 32768
        models:
          - id: qwen36-35b
- id: agent-default-model
  config:
    provider: local
    model: qwen36-35b
# ── 断网合规：禁用全部联网工具 ──
- id: web
  disabled: true
- id: tool-web
  disabled: true
- id: web-search-deepseek
  disabled: true
- id: web-deepseek-api
  disabled: true
- id: web-fetch-http
  disabled: true
# ── 禁止运行时自更新/自装插件（防上游改版） ──
- id: plugin-manager
  disabled: true
- id: tool-plugin-manager
  disabled: true
- id: hmr
  disabled: true
# ── 断网合规（关键）：DSH 默认带 OTel 遥测，会往外部 collector 发日志 ──
# session-telemetry-otel 的 exporter.url 写死为
#   https://dsh-otel-collector.deepseeksvc.com/v1/logs
# 出流量非零即判违规（SCORING 2.9/2.10），必须物理禁掉。
- id: otel
  disabled: true
- id: session-telemetry-otel
  disabled: true
# 云端账户/包清单类插件：本方案只用本地 llama-server，不需要
- id: llm-deepseek-account
  disabled: true
- id: plugin-package-inventory-deepseek
  disabled: true

# ── 工具裁剪：RTL 单题任务用不到多智能体/长期任务/图像/计划模式 ──
# 实测 2600 份 trace：workflow 与 subagent 各仅被调用 1 次，
# 二者描述共 4761 字符；裁剪后每轮请求少约 2600 token。
- id: tool-workflow
  disabled: true
- id: workflow-ptc
  disabled: true
- id: tool-subagent
  disabled: true
- id: tool-subagent-fork
  disabled: true
- id: tool-subagent-list-agents
  disabled: true
- id: tool-subagent-control
  disabled: true
- id: subagent
  disabled: true
- id: subagent-spawn-in-process
  disabled: true
- id: subagent-fork-in-process
  disabled: true
- id: tool-jobs
  disabled: true
- id: jobs
  disabled: true
- id: tool-goal
  disabled: true
- id: goal
  disabled: true
- id: goal-round-driver
  disabled: true
- id: command-goal
  disabled: true
- id: plan-mode
  disabled: true
- id: image-offload
  disabled: true

# ── 本地插件（检查器，不生成 RTL）──
- insert:
    - id: rtl-guard
      name: 'dsh-rtl-guard'
YML


# ---- 3b. 本地 cordis 插件（dsh-rtl-guard：合并版检查器）----
# 插件必须装在 $DSH_HOME/node_modules 下：ESM 从 profile 目录向上找 node_modules。
PLUG_SRC="$HERE/plugins"
if [ -d "$PLUG_SRC" ]; then
  mkdir -p "$DSH_HOME_DIR/node_modules"
  for d in "$PLUG_SRC"/*/; do
    [ -d "$d" ] || continue
    name="$(basename "$d")"
    rm -rf "$DSH_HOME_DIR/node_modules/$name"
    cp -r "$d" "$DSH_HOME_DIR/node_modules/$name"
    echo "installed plugin: $name"
  done
fi

# ---- 4. 版本锁：装到的版本必须与声明一致，否则报错退出 ----
GOT="$(dsh --version 2>&1 | tr -d '[:space:]')"
if [ "$GOT" != "$DSH_VER" ]; then
  echo "FATAL: dsh 版本不符（期望 $DSH_VER，实际 $GOT）" >&2
  exit 1
fi
npm config set update-notifier false >/dev/null 2>&1 || true
echo "DSH ready: $GOT | DSH_HOME=$DSH_HOME_DIR"



