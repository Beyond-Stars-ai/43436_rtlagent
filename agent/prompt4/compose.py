# -*- coding: utf-8 -*-
"""compose.py：题面 → 关键词自查（模块式）；FSM/序列类且无专属检查时回退固定提示词。"""
import os, re

HERE = os.path.dirname(os.path.abspath(__file__))
BUDGET = 2500

CHECKS = [
 ('sumwidth', r"population count|number of '1'|number of ones|counts the number|sum of"),
 ('kmap',     r'karnaugh|k-?map'),
 ('fsm',      r'state machine|state transition|state diagram|next state|\bfsm\b'),
 ('onehot',   r'one-hot|onehot'),
 ('mealy',    r'mealy'),
 ('count',    r'\bcounter\b|\bcounts?\b|counting'),
 ('edge',     r'any edge|edge[- ]?detect|both edges|detect.*edge|changes? from|one cycle later|next cycle'),
 ('concat',   r'concatenat|\[\d+:\d+\]|slice|sign[- ]?extend'),
 ('reverse',  r'\brevers|bit[- ]?order|\[N-1-i\]'),
 ('adder',    r'adder|carry|overflow'),
 ('ca',       r'rule ?90|rule ?110|cellular'),
 ('seqdet',   r'serial|sequence|stream'),
 ('priority', r'priority|first bit|least significant'),
 ('wave',     r'waveform|simulation'),
 ('truthtable', r'truth[- ]table'),
]

HARD_RE = r'state machine|finite-state|\bFSM\b|state transition|\bMealy\b|\bMoore\b|serial|sequence|stream|shift register|one-hot'
SPECIFIC = {'onehot', 'mealy', 'seqdet', 'kmap', 'kmapdc', 'truthtable', 'ca'}

def _read(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        return f.read().strip() + '\n'

def _hits(prompt):
    return [n for n, pat in CHECKS if re.search(pat, prompt or '', re.I)]

def fit(prompt, budget=BUDGET, max_checks=4):
    hits = _hits(prompt)
    if re.search(HARD_RE, prompt or '', re.I) and not (SPECIFIC & set(hits)):
        try:
            hard = _read('fixed.md')
            if 300 <= len(hard.encode()) <= 2600:
                return hard, ['__fixed__']
        except Exception:
            pass
    picked = hits[:max_checks]
    if 'kmap' in hits and re.search(r"don'?t[- ]?care|\bd is |don't care", prompt or '', re.I):
        hits = ['kmapdc' if h == 'kmap' else h for h in hits]
        picked = hits[:max_checks]
    parts = [_read('base.md'), _read('pitfalls.md')]
    if picked:
        lines = ['## 本题专项自查'] + [_read('checks/%s.md' % n) for n in picked]
        parts.append('\n'.join(lines).strip() + '\n')
    parts.append(_read('check_stable.md'))
    text = '\n'.join(p.strip() + '\n' for p in parts)
    while len(text.encode()) > budget and picked:
        picked.pop()
        lines = ['## 本题专项自查'] + [_read('checks/%s.md' % n) for n in picked] if picked else []
        parts = [_read('base.md'), _read('pitfalls.md')] + (['\n'.join(lines).strip() + '\n'] if picked else []) + [_read('check_stable.md')]
        text = '\n'.join(p.strip() + '\n' for p in parts)
    return text, picked
