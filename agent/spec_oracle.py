#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spec-oracle：从题面波形表生成 tb_spec.sv 与 spec_check.sh。

纪律：解析不出完整表 / 端口映射不全 / 行数不足 → 直接跳过（退出码 0，不生成任何文件）。
生成器本身在开发期用 ref.sv 自校验过（5/5 零不匹配）。
"""
from __future__ import annotations
import argparse
import os
import re
import sys

PORT = re.compile(r'^[ \t]*-[ \t]*(input|output)[ \t]+([A-Za-z_]\w*)[ \t]*(?:\((\d+)[ \t]*bits?\))?', re.I | re.M)
HDR  = re.compile(r'^\s*time\s+([A-Za-z_][\w \t]*)$', re.I | re.M)
ROW  = re.compile(r'^\s*(\d+)\s*ns\s+(.+?)\s*$', re.M)

ALIAS = {
    'rst': ['reset', 'rst_n', 'resetn'], 'reset': ['rst'],
    'clk': ['clock', 'clk_i'], 'clock': ['clk'],
    'in': ['din', 'data'], 'out': ['dout'],
}

def ports(prompt):
    d = {}
    for kind, name, w in PORT.findall(prompt):
        d[name] = (kind.lower(), int(w) if w else 1)
    return d

def resolve(col, P):
    if col in P: return col
    for cand in ALIAS.get(col.lower(), []):
        if cand in P: return cand
    lower = {k.lower(): k for k in P}
    return lower.get(col.lower())

def col_radix(tokens, width):
    toks = [t.lower() for t in tokens if t.lower() not in ('x','z')]
    if not toks: return 2
    if all(re.fullmatch(r'[01]+', t) and len(t) == width for t in toks): return 2
    if any(re.search(r'[a-f]', t) for t in toks): return 16
    if all(re.fullmatch(r'[01]+', t) for t in toks): return 2
    return 10

def val(tok, radix):
    t = tok.lower()
    if t in ('x','z'): return None
    try: return int(t, radix)
    except ValueError: return None

def seq_or_comb(rows, ci, oi):
    prev = None
    for (t, v) in rows:
        if prev is not None and v[oi].lower() not in ('x','z'):
            if v[oi].lower() != prev[oi].lower() and v[ci] == prev[ci]:
                return 'comb'
        prev = v
    return 'seq'

def parse(prompt):
    P = ports(prompt)
    m = HDR.search(prompt)
    if not m: return None
    cols = m.group(1).split()
    if 'time' in [c.lower() for c in cols]: return None
    # 列 -> 端口
    mapping = {}
    for c in cols:
        p = resolve(c, P)
        if p: mapping[c] = p
    clkcols = [c for c in cols if mapping.get(c) and P[mapping[c]][0] == 'input'
               and mapping[c].lower() in ('clk', 'clock')]
    clk = clkcols[0] if clkcols else None
    clkport = mapping[clk] if clk else None
    outs = [c for c in cols if mapping.get(c) and P[mapping[c]][0] == 'output']
    ins  = [c for c in cols if mapping.get(c) and P[mapping[c]][0] == 'input' and c != clk]
    if not outs or not ins: return None
    body = prompt[m.end():]
    rows = []
    for t, rest in ROW.findall(body):
        v = rest.split()
        if len(v) != len(cols): break
        rows.append((int(t), v))
    if len(rows) < 4: return None
    radix = {c: col_radix([r[1][i] for r in rows], P[mapping[c]][1] if c in mapping else 1)
             for i, c in enumerate(cols)}
    return dict(P=P, cols=cols, map=mapping, clk=clk, clkport=clkport, ins=ins, outs=outs,
                rows=rows, radix=radix)

def gen(spec):
    P, cols, mp, clk, clkport = spec['P'], spec['cols'], spec['map'], spec['clk'], spec['clkport']
    ins, outs, rows, radix = spec['ins'], spec['outs'], spec['rows'], spec['radix']
    W = {c: P[c][1] for c in P}
    ci = cols.index(clk)
    kind = {o: seq_or_comb(rows, ci, cols.index(o)) for o in outs}
    L = ['`timescale 1ns/1ps', 'module tb_spec;', '  reg %s = 0;' % mp[clk]]
    for c in ins: L.append('  reg [%d:0] %s = 0;' % (W[mp[c]]-1, mp[c]))
    for o in outs: L.append('  wire [%d:0] %s;' % (W[mp[o]]-1, mp[o]))
    L.append('  integer mismatches = 0, checks = 0;')
    L.append('  integer i;')
    for o in outs: L.append('  reg [%d:0] prev_%s;' % (W[mp[o]]-1, mp[o]))
    conns = ['.%s(%s)' % (clkport, mp[clk])]
    conns += ['.%s(%s)' % (mp[c], mp[c]) for c in ins + outs]
    L.append('  TopModule dut (%s);' % ', '.join(conns))
    for o in outs:
        sig = mp[o]
        L.append('  task chk_%s(input [%d:0] exp); begin' % (sig, W[sig]-1))
        L.append('    checks = checks + 1;')
        L.append('    if (%s !== exp) begin mismatches = mismatches + 1;' % sig)
        L.append('      $display("MISMATCH t=%%0t port=%s exp=%%b got=%%b", $time, exp, %s);' % (sig, sig))
        L.append('    end')
        L.append('  end endtask')
    L.append('  initial begin')
    t = 0
    for (rt, v) in rows:
        if rt > t: L.append('    #%d;' % (rt - t)); t = rt
        ck = v[ci]
        L.append("    %s = 1'b%s;" % (mp[clk], ck if ck in '01' else '0'))
        L.append('    #1;')
        for c in ins:
            x = val(v[cols.index(c)], radix[c]) or 0
            L.append("    %s = %d'h%x;" % (mp[c], W[mp[c]], x))
        L.append('    #0;')
        for o in outs:
            if kind[o] == 'comb':
                x = val(v[cols.index(o)], radix[o])
                if x is not None: L.append("    chk_%s(%d'h%x);" % (mp[o], W[mp[o]], x))
        L.append('    #0;')
        for o in outs:
            if kind[o] == 'seq':
                x = val(v[cols.index(o)], radix[o])
                if x is not None: L.append("    chk_%s(%d'h%x);" % (mp[o], W[mp[o]], x))
    # ---- A1-lite：逐输出的时序不变量（只对"变化只发生在某一个 clk 电平"的输出启用）----
    inv_levels = {}
    # 只在"单输入单输出"的简单表上启用不变量（已在 ref 上回归验证）
    simple = (len(ins) == 1 and len(outs) == 1)
    for o in (outs if simple else []):
        levels = set(); cnt = 0; bad = False
        oi = cols.index(o)
        for i in range(1, len(rows)):
            v1 = rows[i-1][1][oi].lower(); v2 = rows[i][1][oi].lower()
            if v1 in ('x','z') or v2 in ('x','z') or v1 == v2:
                continue
            cnt += 1
            if rows[i][1][ci] == rows[i-1][1][ci]:
                bad = True          # 变化不在时钟跳变行 → 不启用不变量
            levels.add(rows[i][1][ci])
        if (not bad) and cnt >= 1 and len(levels) == 1:
            inv_levels[o] = levels.pop()
    if inv_levels:
        L.append('    // 不变量：下列时序输出只允许在采样电平之后变化')
        for o in inv_levels:
            L.append('    prev_%s = %s;' % (mp[o], mp[o]))
        L.append('    for (i = 0; i < 30; i = i + 1) begin')
        # 每个输出分别用自己的采样电平；这里统一按第一个输出的采样电平驱动时钟
        edge_val = list(inv_levels.values())[0]
        nonsample = '0' if edge_val == '1' else '1'
        L.append("      %s = 1'b%s;" % (mp[clk], nonsample))
        L.append('      #5;')
        for c in ins:
            L.append('      %s = $random;' % mp[c])
        L.append('      #1;')
        for o in inv_levels:
            if inv_levels[o] != edge_val:
                continue
            L.append('      checks = checks + 1;')
            L.append('      if (%s !== prev_%s) begin mismatches = mismatches + 1;' % (mp[o], mp[o]))
            L.append('        $display("INVARIANT FAIL t=%%0t port=%s changed while clk=%s", $time);' % (mp[o], nonsample))
            L.append('      end')
        L.append("      %s = 1'b%s;" % (mp[clk], edge_val))
        L.append('      #5;')
        for o in inv_levels:
            if inv_levels[o] != edge_val:
                continue
            L.append('      prev_%s = %s;' % (mp[o], mp[o]))
        L.append('    end')

    L += ['    $display("SPEC_RESULT mismatches=%0d checks=%0d", mismatches, checks);',
          '    $finish;', '  end', 'endmodule']
    return '\n'.join(L) + '\n', kind



CHECK_SH = """#!/bin/bash
# 自动生成：用题面波形表验证 solution.v
cd "$(dirname "$0")/.." || exit 0
rm -rf .spec/work && mkdir -p .spec/work
S=.spec/work
xvlog -sv --nolog .spec/tb_spec.sv solution.v > $S/xvlog.log 2>&1 || true
if grep -q "ERROR:" $S/xvlog.log; then
  echo "SPEC_COMPILE_FAIL"
  grep "ERROR:" $S/xvlog.log | head -3
  exit 3
fi
xelab --nolog -debug typical tb_spec -s spec_s > $S/xelab.log 2>&1 || true
if grep -q "ERROR:" $S/xelab.log; then
  echo "SPEC_ELAB_FAIL"
  grep "ERROR:" $S/xelab.log | head -3
  exit 3
fi
xsim spec_s -runall > $S/xsim.log 2>&1 || true
grep -E "MISMATCH|INVARIANT" $S/xsim.log | head -5
grep -E "SPEC_RESULT" $S/xsim.log | tail -1
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--outdir", required=True)
    a = ap.parse_args()
    if not os.path.exists(a.prompt):
        print("SKIP no prompt"); return 0
    prompt = open(a.prompt, encoding="utf-8", errors="replace").read()
    try:
        spec = parse(prompt)
    except Exception as e:
        print("SKIP parse error: %s" % e); return 0
    if not spec:
        print("SKIP no waveform table"); return 0
    try:
        if not spec.get('clk'):
            print("SKIP combinational table (not supported)"); return 0
        tb, kinds = gen(spec)
    except Exception as e:
        print("SKIP gen error: %s" % e); return 0
    if "TopModule dut" not in tb:
        print("SKIP empty tb"); return 0
    work = os.path.join(a.outdir, ".spec")
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "tb_spec.sv"), "w", encoding="utf-8", newline="\n") as f:
        f.write(tb)
    with open(os.path.join(work, "check.sh"), "w", encoding="utf-8", newline="\n") as f:
        f.write(CHECK_SH)
    os.chmod(os.path.join(work, "check.sh"), 0o755)
    print("GENERATED checks=%d kind=%s" % (len(spec["rows"]), ",".join(sorted(set(kinds.values())))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
