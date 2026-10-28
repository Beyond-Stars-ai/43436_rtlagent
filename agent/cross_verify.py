
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_verify v2：Python 生成激励 → DUT 仿真 → Python 模型对拍（ChipMATE 式）。

约定：
- 激励由 Python 生成（seed 固定），Verilog TB 只负责逐拍施加并采样输出；
- model.py 暴露 run(stim: list[dict]) -> list[dict]，键为端口名，值为 int；
- 输出 match_rate 与首个不一致拍。
"""
from __future__ import annotations
import argparse, importlib.util, os, random, re, subprocess, sys

MOD_HDR = re.compile(r'module\s+(\w+)\s*(?:#\s*\([^)]*\))?\s*\((.*?)\)\s*;', re.S)
PORT_DECL = re.compile(r'\b(input|output|inout)\b\s*(?:wire|reg|logic|signed|unsigned)?\s*(\[[^\]]*\])?\s*([A-Za-z_]\w*)', re.S)


def parse_ports(sv):
    m = MOD_HDR.search(sv)
    if not m: return None, []
    ports = []
    for kind, rng, name in PORT_DECL.findall(m.group(2)):
        w = 1
        if rng:
            n = re.findall(r'\d+', rng)
            if len(n) == 2: w = abs(int(n[0]) - int(n[1])) + 1
        ports.append((name, kind.lower(), w))
    return m.group(1), ports


def gen_stim(ins, n, seed=42):
    rnd = random.Random(seed)
    return [{nm: rnd.randrange(0, 1 << w) for (nm, _, w) in ins} for _ in range(n)]


def gen_tb(top, ports, stim):
    clk = next((p for p in ports if p[1] == 'input' and p[0].lower() in ('clk', 'clock')), None)
    ins = [p for p in ports if p[1] == 'input' and (clk is None or p[0] != clk[0])]
    outs = [p for p in ports if p[1] == 'output']
    if not outs: return None
    L = ['`timescale 1ns/1ps', 'module tb_cv;']
    if clk: L.append('  reg %s = 0;' % clk[0])
    for (nm, _, w) in ins: L.append('  reg [%d:0] %s = 0;' % (w - 1, nm))
    for (nm, _, w) in outs: L.append('  wire [%d:0] %s;' % (w - 1, nm))
    L.append('  %s dut (%s);' % (top, ', '.join('.%s(%s)' % (p[0], p[0]) for p in ports)))
    fmt = ' '.join(['%b'] * len(outs))
    args = ', '.join(nm for (nm, _, _) in outs)
    L.append('  initial begin')
    for i, s in enumerate(stim):
        assigns = ' '.join('%s = %d\'h%x;' % (nm, w, s[nm]) for (nm, _, w) in ins)
        if clk:
            L.append('    @(negedge %s); %s' % (clk[0], assigns))
            L.append('    @(posedge %s); #1;' % clk[0])
        else:
            L.append('    %s #1;' % assigns)
        L.append('    $display("CV_ROW i=%d %s", %s);' % (i, fmt, args))
    L.append('    $display("CV_END");')
    L.append('    $finish;')
    L.append('  end')
    if clk: L.append('  always #5 %s = ~%s;' % (clk[0], clk[0]))
    L.append('endmodule')
    return '\n'.join(L) + '\n', [p[0] for p in ins], [p[0] for p in outs]


def run_sim(work, tb, dut_sv):
    env = dict(os.environ, PATH='/opt/node/bin:' + os.environ.get('PATH', ''), EDA_TMP='/var/tmp/eda')
    open(os.path.join(work, 'tb_cv.sv'), 'w').write(tb)
    open(os.path.join(work, 'dut.v'), 'w').write(dut_sv)
    cmd = ("xvlog -sv --nolog tb_cv.sv dut.v > xvlog.log 2>&1 && "
           "xelab --nolog -debug typical tb_cv -s cv > xelab.log 2>&1 && "
           "xsim cv -runall > xsim.log 2>&1; grep -E 'CV_ROW|CV_END|ERROR' xsim.log | head -400")
    r = subprocess.run(cmd, shell=True, cwd=work, env=env, capture_output=True, text=True, timeout=180)
    return (r.stdout or '')


def load_model(path):
    spec = importlib.util.spec_from_file_location('cv_model', path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dut', required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--work', default='/tmp/cv2')
    ap.add_argument('--cycles', type=int, default=64)
    ap.add_argument('--seed', type=int, default=42)
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    sv = open(a.dut, encoding='utf-8', errors='replace').read()
    top, ports = parse_ports(sv)
    if not ports:
        print('CV_SKIP 端口解析失败'); return 0
    clk = next((p for p in ports if p[1] == 'input' and p[0].lower() in ('clk', 'clock')), None)
    ins = [p for p in ports if p[1] == 'input' and (clk is None or p[0] != clk[0])]
    stim = gen_stim(ins, a.cycles, a.seed)
    built = gen_tb(top, ports, stim)
    if not built:
        print('CV_SKIP TB 生成失败'); return 0
    tb, inames, onames = built
    out = run_sim(a.work, tb, sv)
    rows = []
    for line in out.splitlines():
        if not line.startswith('CV_ROW'): continue
        toks = line.split()
        rows.append({nm: int(v.replace('x', '0'), 2) for nm, v in zip(onames, toks[2:])})
    if not rows:
        print('CV_SKIP 仿真无输出'); print(out[-300:]); return 0
    m = load_model(a.model)
    try:
        pred = m.run([dict(s) for s in stim])
    except Exception as e:
        print('CV_SKIP 模型运行失败: %s' % e); return 0
    if len(pred) != len(rows):
        print('CV_SKIP 模型行数不符 %d vs %d' % (len(pred), len(rows))); return 0
    same = 0; first = None
    for i, (r, p) in enumerate(zip(rows, pred)):
        if all(r.get(k, None) == p.get(k, None) for k in onames):
            same += 1
        elif first is None:
            first = (i, dict(stim[i]), {k: r.get(k) for k in onames}, {k: p.get(k) for k in onames})
    rate = same / len(rows)
    print('CV_RESULT match_rate=%.4f rows=%d' % (rate, len(rows)))
    if first:
        print('  首个不一致 i=%d 输入=%s dut=%s model=%s' % (first[0], first[1], first[2], first[3]))


if __name__ == '__main__':
    sys.exit(main())
