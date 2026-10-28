#!/usr/bin/env python3
"""把 DSH 的会话日志转成赛事方要求的 trace.jsonl。

用法：
    dsh_trace.py <input_dir> <output_dir> [--session <sessionDir>]

- 从 $DSH_HOME/sessions/**/session-*/session.v4.jsonl.zstd 读取最近一次会话
- 输出 <output_dir>/trace.jsonl，每行一个 JSON 对象
- 规则：`tool/call` 与 `tool/result` 各输出一行，字段与官方示例对齐
"""
import sys, os, json, glob, time

def load_events(session_dir):
    """读会话目录下的 v4 jsonl（zstd 或明文）"""
    cands = sorted(glob.glob(os.path.join(session_dir, "session*.jsonl*")))
    if not cands:
        return []
    p = cands[0]
    raw = open(p, "rb").read()
    if p.endswith(".zstd"):
        import zstandard
        raw = zstandard.ZstdDecompressor().stream_reader(__import__("io").BytesIO(raw)).read()
    out = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out

def latest_session(dsh_home):
    pats = os.path.join(dsh_home, "sessions", "**", "session-*", "session*.jsonl*")
    files = glob.glob(pats, recursive=True)
    if not files:
        return None
    return max(files, key=os.path.getmtime)

def find_session_for(output_dir):
    """优先选 cwd 与 output_dir 匹配的会话，否则取最新的"""
    home = os.environ.get("DSH_HOME") or os.path.expanduser("~/.dsh")
    pats = os.path.join(home, "sessions", "**", "session-*", "session*.jsonl*")
    files = glob.glob(pats, recursive=True)
    if not files:
        return None
    want = os.path.abspath(output_dir).replace("/", "-").replace("_", "-")
    scored = []
    for f in files:
        parent = os.path.basename(os.path.dirname(os.path.dirname(f)))
        score = 1 if want and (want in parent or parent.strip("-").endswith(os.path.basename(os.path.abspath(output_dir)))) else 0
        scored.append((score, os.path.getmtime(f), f))
    scored.sort()
    return scored[-1][2]

def excerpt(s, n=200):
    s = (s or "").replace("\n", " ").strip()
    return s[:n]

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(2)
    out_dir = sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    session_file = None
    if "--session" in sys.argv:
        session_file = sys.argv[sys.argv.index("--session") + 1]
    else:
        session_file = find_session_for(out_dir)

    lines = []
    ts = lambda ms: round(ms / 1000.0, 3) if ms else round(time.time(), 3)

    if not session_file:
        with open(os.path.join(out_dir, "trace.jsonl"), "w") as f:
            f.write(json.dumps({"tool": "dsh", "event": "no-session", "ts": round(time.time(), 3)}, ensure_ascii=False) + "\n")
        return

    events = load_events(os.path.dirname(session_file))
    names = {}   # callId -> tool name

    for e in events:
        t = e.get("type")
        d = e.get("data") or {}
        tm = ts(e.get("time"))
        if t == "session":
            lines.append({"tool": "dsh", "event": "session", "cwd": e.get("cwd") or d.get("cwd"), "ts": tm})
        elif t == "request/header":
            h = d.get("header") or {}
            cfg = h.get("config") or {}
            lines.append({"tool": "dsh", "event": "request",
                          "provider": cfg.get("provider"), "model": cfg.get("model"),
                          "tools": [x.get("name") for x in (h.get("tools") or [])], "ts": tm})
        elif t == "assistant/message":
            msg = d.get("message") or {}
            parts = msg.get("content") or []
            think = sum(len(p.get("text", "")) for p in parts if p.get("type") == "reasoning")
            text = sum(len(p.get("text", "")) for p in parts if p.get("type") == "text")
            rec = {"tool": "llm", "event": "message", "turn": d.get("turn"), "step": d.get("step"),
                   "reasoning_chars": think, "text_chars": text, "ts": tm}
            # 官方 trace 建议字段：tokens_in / tokens_out（会话里有 usage 就透传）
            usage = d.get("usage") or msg.get("usage") or {}
            if "prompt_tokens" in usage:
                rec["tokens_in"] = usage["prompt_tokens"]
            if "completion_tokens" in usage:
                rec["tokens_out"] = usage["completion_tokens"]
            lines.append(rec)
        elif t == "tool/call":
            cid = d.get("callId"); name = d.get("name"); names[cid] = name
            try:
                args = json.loads(d.get("arguments") or "{}")
            except Exception:
                args = {"raw": d.get("arguments")}
            lines.append({"tool": name, "event": "call", "call_id": cid, "args": args, "ts": tm})
        elif t == "tool/result":
            src = (d.get("message") or {}).get("source") or {}
            cid = src.get("callId") or d.get("toolCallId")
            content = (d.get("message") or {}).get("content") or []
            txt = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            rc = 0
            low = txt.lower()
            if "error" in low or "failed" in low or "traceback" in low:
                rc = 1
            lines.append({"tool": names.get(cid, "tool"), "event": "result", "call_id": cid,
                          "rc": rc, "excerpt": excerpt(txt), "ts": tm})
        elif t == "turn/end":
            reason = (d.get("reason") or {}).get("kind")
            lines.append({"tool": "dsh", "event": "turn_end", "turn": d.get("turn"), "reason": reason, "ts": tm})

    with open(os.path.join(out_dir, "trace.jsonl"), "w", encoding="utf-8") as f:
        for l in lines:
            f.write(json.dumps(l, ensure_ascii=False) + "\n")
    print("trace lines:", len(lines))

if __name__ == "__main__":
    main()

