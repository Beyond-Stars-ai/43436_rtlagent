#!/usr/bin/env python3
"""DSH 运行时封装：把一次求解映射成「DSH headless 会话 + 会话日志转 trace」。

官方契约（`docs/API_CONTRACT.md`）：
    run.sh <input_dir> <output_dir>
      <input_dir>/prompt.txt   -> 题面
      <output_dir>/solution.v  -> 产出（可为空，但必须存在）
      <output_dir>/trace.jsonl -> 每次工具调用一行（必需）

本文件只做三件事，不含任何题目逻辑：
    1. 把工作区约定 `agent/AGENTS.md` 放进 Output 目录（DSH 从 cwd 读取）
    2. 以 headless 模式调用 DSH，由模型自己决定读写与编译顺序
    3. 把 DSH 的会话事件流转换成官方 trace 格式
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEADLINE_S = int(os.environ.get("AGENT_DEADLINE_S", "840"))


PROFILE_REL = os.path.join("profiles", "headless", "cordis.patch.yml")
EXPECTED_DSH_VERSION = "0.2.1-rc.2"   # 版本锁：上游改版可能改变 agent 行为，禁止漂移


def _has_profile(home: str) -> bool:
    """profile 存在才算可用——只剩空目录会让 DSH 回落默认模板（连云 API）。"""
    return bool(home) and os.path.isfile(os.path.join(home, PROFILE_REL))


def resolve_dsh_home() -> str:
    """确定 DSH_HOME。

    顺序：显式环境变量 > 镜像内 /opt/dsh-home > 持久盘 <tools>/dsh-home。
    镜像内优先：正式评测只有 /opt 这一份，开发机上的 /workspace 副本不应覆盖它。
    每个候选都要求 profile 真实存在，否则跳过（空目录会导致 DSH 连 DeepSeek 云 API）。
    """
    env_home = os.environ.get("DSH_HOME")
    if env_home:
        return env_home
    tools = os.environ.get("TOOLS_ROOT", "/workspace/tools")
    candidates = ["/opt/dsh-home", os.path.join(tools, "dsh-home")]
    for c in candidates:
        if _has_profile(c):
            return c
    return candidates[0]


def _env():
    env = dict(os.environ)
    tools = os.environ.get("TOOLS_ROOT", "/workspace/tools")
    node_bin = os.path.join(tools, "node", "bin")
    env["DSH_HOME"] = resolve_dsh_home()
    env.setdefault("LOCAL_LLM_KEY", "sk-local")
    env.setdefault("EDA_TMP", "/var/tmp/eda")
    prefix = node_bin if os.path.isdir(node_bin) else "/opt/node/bin"
    env["PATH"] = prefix + ":" + env.get("PATH", "")
    env.setdefault("RTL_AGENT_DIR", HERE)
    os.makedirs(env["EDA_TMP"], exist_ok=True)
    return env


def dsh_version(env) -> str:
    try:
        p = subprocess.run(["dsh", "--version"], env=env, timeout=20,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return (p.stdout or "").strip()
    except Exception:
        return "unknown"


def _build_agents(prompt: str, out_dir: str, fallback_path: str) -> str:
    """按题拼装 AGENTS.md；任何异常或越界都回退到固定版（2,071 B）。"""
    mode = os.environ.get("RTL_PROMPT_MODE", "v4")
    if mode == "v4":
        try:
            p4 = os.path.join(HERE, "prompt4")
            if p4 not in sys.path:
                sys.path.insert(0, p4)
            import compose as _v4
            text, picked = _v4.fit(prompt or "")
            if text and 300 <= len(text.encode()) <= 2200:
                try:
                    with open(os.path.join(out_dir, "prompt_plan.json"), "w", encoding="utf-8") as f:
                        json.dump({"variant": "v4", "checks": picked, "bytes": len(text.encode())}, f, ensure_ascii=False)
                except Exception:
                    pass
                return text
        except Exception:
            pass
    if mode == "blocks":
        try:
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            import fit_prompt  # 零依赖版
            text, plan = fit_prompt.fit(prompt or "")
            if text and 300 <= len(text.encode()) <= 1900:
                try:
                    with open(os.path.join(out_dir, "prompt_plan.json"), "w", encoding="utf-8") as f:
                        json.dump(plan, f, ensure_ascii=False, indent=1)
                except Exception:
                    pass
                return text
        except Exception:
            pass
    try:
        with open(fallback_path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def run_dsh(prompt: str, out_dir: str) -> int:
    """把题面交给 DSH headless；不做任何提示词改写（所有检查在插件里）。"""
    agents_md = os.path.join(HERE, "AGENTS.md")
    # 题面副本：spec-oracle 插件据此生成波形表自测台（无表则不生成）
    try:
        with open(os.path.join(out_dir, ".spec_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt or "")
    except Exception:
        pass

    if os.path.exists(agents_md):
        base = _build_agents(prompt, out_dir, agents_md)
        with open(os.path.join(out_dir, "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write(base)

    instruction = (
        prompt
        + "\n\n把最终 Verilog 代码写入 "
        + os.path.join(out_dir, "solution.v")
        + "，只写这一个文件；不要写测试台。"
    )
    env_used = _env()
    cmd = ["dsh", "--profile", "headless", instruction]
    try:
        p = subprocess.run(
            cmd,
            cwd=out_dir,
            env=env_used,
            timeout=DEADLINE_S,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        with open(os.path.join(out_dir, "dsh.log"), "w", encoding="utf-8") as f:
            f.write("[agent] DSH_HOME=%s\n" % env_used.get("DSH_HOME"))
            f.write("[agent] DSH_VERSION=%s (expected %s)\n" % (dsh_version(env_used), EXPECTED_DSH_VERSION))
            f.write(p.stdout or "")
        return p.returncode
    except subprocess.TimeoutExpired:
        with open(os.path.join(out_dir, "dsh.log"), "a", encoding="utf-8") as f:
            f.write("\n[agent] deadline reached, killed\n")
        return 124


def ensure_outputs(out_dir: str):
    sol = os.path.join(out_dir, "solution.v")
    if not os.path.exists(sol):
        open(sol, "w").close()
    tr = os.path.join(out_dir, "trace.jsonl")
    if not os.path.exists(tr):
        open(tr, "w").close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()

    os.makedirs(a.output, exist_ok=True)
    prompt_path = os.path.join(a.input, "prompt.txt")
    prompt = open(prompt_path, encoding="utf-8", errors="replace").read() if os.path.exists(prompt_path) else ""

    rc = run_dsh(prompt, a.output)

    # 会话日志 -> 官方 trace.jsonl
    tracer = os.path.join(HERE, "dsh_trace.py")
    subprocess.run([sys.executable, tracer, a.input, a.output],
                   env=_env(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ensure_outputs(a.output)

    if rc != 0:
        print(f"agent exited {rc}, emitted whatever we have", file=sys.stderr)
    return 0  # 契约：永不非零退出


if __name__ == "__main__":
    sys.exit(main())



