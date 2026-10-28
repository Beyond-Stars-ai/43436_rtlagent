# -*- coding: utf-8 -*-
"""fit_prompt.py（零依赖版）—— 按题面拼装 AGENTS.md。

设计约束：
- 只用标准库（评测镜像没有 PyYAML）
- 纯函数：只读题面文本，不读 ref/tb，不联网
- 任何异常 → 调用方回退到固定 AGENTS.md

用法（调试）：
    python3 fit_prompt.py --prompt prompt.txt --print
    python3 fit_prompt.py --prompt prompt.txt --json   # 输出 plan
"""
from __future__ import annotations
import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
_VAR = os.environ.get("RTL_PROMPT_VARIANT", "v3")
GEMS = os.path.join(HERE, "gems_v3.json" if _VAR == "v3" else "gems.json")

BUDGET = 1900

# 大类：优先级顺序（先命中先用），保证每题只有一个主类
MAIN = [
    ('WAVE',  r'^\s*\d+\s*ns\s+\S|simulation waveform|waveform'),
    ('FSM',   r'\bstate\b|\bfsm\b|transition|one-hot'),
    ('KMAP',  r'karnaugh|k-?map'),
    ('MUX',   r'multiplexer|\bmux\b'),
    ('MEM',   r'\bshift register\b|\bshifted\b|\bfifo\b|\bram\b|\brom\b'),
    ('BUG',   r'\bbug\b|unfortunately|fixes'),
    ('VEC',   r'\brevers|concatenat|sign[- ]?extend|\bmsb\b|\blsb\b|slice'),
    ('COUNT', r'\bcounter\b|\bcounts?\b|counting|inclusive'),
    ('ALGO',  r'lfsr|lemming|walk|conway|gshare|rule ?110|cellular'),
    ('SEQ',   r'\bclk\b|\bclock\b|posedge|negedge|\bedge\b|sequential|register'),
    ('COMB',  r'.'),
]

# 关键词小类（可多选）
SUBS = [
    ('onehot',   r'one-?hot|onehot'),
    ('serial',   r'serial'),
    ('ps2',      r'ps2'),
    ('lemmings', r'lemming'),
    ('timer',    r'\btimer\b'),
    ('bcd',      r'\bbcd\b'),
    ('lfsr',     r'lfsr'),
    ('conway',   r'conway|game of life'),
    ('rule110',  r'rule ?110|rule ?90|cellular'),
    ('gshare',   r'gshare'),
    ('shiftreg', r'\bshift register\b|shifted|shifting'),
    ('fifo',     r'\bfifo\b'),
    ('ram',      r'\bram\b|\brom\b|memory'),
    ('adder',    r'adder|subtract'),
    ('mux256',   r'256'),
    ('signext',  r'sign[- ]?extend|sign bit'),
    ('reverse',  r'revers'),
    ('concat',   r'concatenat'),
    ('msb',      r'\bmsb\b|\blsb\b'),
    ('counter',  r'\bcounter\b|counting'),
    ('kmap',     r'karnaugh|k-?map'),
    ('table',    r'waveform|simulation'),
]

CAP = {'S1': 2, 'S2': 1, 'S3': 3, 'S4': 2, 'S5': 3, 'S6': 2}
if _VAR == "v3":
    SECTION = {'BASE': '交付与接口', 'S1': '交付与接口', 'S2': '写法与语法', 'S6': '写法与语法',
               'S3': '语义与时序', 'S4': '语义与时序', 'S5': '写完自查'}
    _ORDER = ('交付与接口', '写法与语法', '语义与时序', '写完自查')
else:
    SECTION = {'BASE': '接口与交付', 'S1': '接口与交付', 'S2': '写法与语法', 'S6': '写法与语法',
               'S3': '语义', 'S4': '时序', 'S5': '写完自查'}
    _ORDER = ('接口与交付', '写法与语法', '语义', '时序', '写完自查')


def _load_gems():
    with open(GEMS, encoding="utf-8") as f:
        d = json.load(f)
    out = []
    for group, items in d.items():
        for g in items or []:
            g = dict(g)
            g.setdefault("tags", [])
            g.setdefault("always", False)
            g.setdefault("priority", 50)
            g["group"] = group
            out.append(g)
    return out


def classify(text):
    """返回 (主类, 小类列表)。"""
    main = 'COMB'
    for name, pat in MAIN:
        if re.search(pat, text, re.I):
            main = name
            break
    subs = [n for n, p in SUBS if re.search(p, text, re.I)]
    return main, subs


def select(gems, main, subs):
    ts = set(subs or [])
    ts.add(main)
    chosen = {}
    for g in gems:
        if g["group"] == "experimental":
            continue
        if g.get("always"):
            chosen.setdefault(g["slot"], []).append(g)
    for g in gems:
        if g["group"] == "experimental" or g.get("always"):
            continue
        if not (("any" in g["tags"]) or (set(g["tags"]) & ts)):
            continue
        slot = g["slot"]
        cond = [x for x in chosen.get(slot, []) if not x.get("always")]
        if len(cond) >= CAP.get(slot, 2):
            continue
        if slot == "S2" and ("style.simple" in g["id"] or "style.complex" in g["id"]):
            want_complex = (main in ("FSM", "ALGO")) or ("fsm" in ts) or ("onehot" in ts)
            if want_complex != ("style.complex" in g["id"]):
                continue
            if any("style." in x["id"] for x in cond):
                continue
        chosen.setdefault(slot, []).append(g)
    return chosen


def render(chosen):
    lines = ["# 本工作区的硬性约定（RTL 赛题生成）", ""]
    for sec in _ORDER:
        items = []
        for slot in ("BASE", "S1", "S2", "S6", "S3", "S4", "S5"):
            if SECTION.get(slot) != sec:
                continue
            items.extend(chosen.get(slot, []))
        if not items:
            continue
        lines.append("## " + sec)
        lines.append("")
        for g in items:
            txt = g["text"].strip()
            lines.append(txt if txt.startswith("- ") else "- " + txt)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def fit(text, budget=BUDGET):
    """题面文本 → (AGENTS.md 文本, plan dict)。"""
    gems = _load_gems()
    main, subs = classify(text or "")
    chosen = select(gems, main, subs)
    out = render(chosen)
    while len(out.encode()) > budget:
        cand = [(g, s) for s, lst in chosen.items() for g in lst if not g.get("always")]
        if not cand:
            break
        g, s = min(cand, key=lambda x: x[0].get("priority", 50))
        chosen[s] = [x for x in chosen[s] if x is not g]
        out = render(chosen)
    plan = dict(main=main, subs=subs, bytes=len(out.encode()),
                gems=[g["id"] for s in chosen.values() for g in s])
    return out, plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    text = open(a.prompt, encoding="utf-8", errors="replace").read()
    out, plan = fit(text)
    if a.json:
        print(json.dumps(plan, ensure_ascii=False))
    if a.print:
        print(out)


if __name__ == "__main__":
    main()
