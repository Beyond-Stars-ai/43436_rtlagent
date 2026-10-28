import { randomUUID, createHash } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";

export const name = "rtl-guard";

const MAX_BLOCKS = 3;            // 总拦截预算
const CAP_A = 2;                 // 空文件/端口/编译 的额度
const CAP_B = 1;                 // 波形核验 的额度（保证至少跑一次）
const TIME_GATE_MS = 600000;     // 超过 600 秒不再拦截
const CMD_TIMEOUT_MS = 180000;
const SPEC_OFF = process.env.RTL_SPEC_OFF === "1";
const XCHK_ON = process.env.RTL_SPEC_XCHK === "1";   // 实验性：Python 交叉验证
const CAP_C = 1;

const tasks = new Map();          // key: cwd

function st() {
  const k = process.cwd();
  let s = tasks.get(k);
  if (!s) { s = { blocks: 0, aBlocks: 0, bBlocks: 0, cBlocks: 0, startedAt: Date.now(), lastDigest: null, specReady: false, xchkAsked: false }; tasks.set(k, s); }
  return s;
}
function log(...a) { console.error("[rtl-guard]", ...a); }
function p(cwd, f) { return path.join(cwd, f); }
function exists(cwd, f) { try { return existsSync(p(cwd, f)); } catch { return false; } }
function sizeOf(cwd, f) { try { return statSync(p(cwd, f)).size; } catch { return -1; } }
function digest(cwd, f) { try { return createHash("sha1").update(readFileSync(p(cwd, f))).digest("hex").slice(0, 12); } catch { return "none"; } }
const WIN = process.platform === "win32";
const VIV = process.env.RTL_VIVADO_BIN || "";
const PY = process.env.RTL_PY || (WIN ? "python" : "python3");
function tool(n) { return WIN ? (VIV ? path.join(VIV, n + ".bat") : n + ".bat") : n; }
function run(cmd, args, cwd) {
  const opts = { cwd, timeout: CMD_TIMEOUT_MS, encoding: "utf8" };
  if (WIN) opts.shell = true;
  const r = spawnSync(cmd, args, opts);
  return { out: String(r.stdout ?? "") + String(r.stderr ?? ""), err: r.error, sig: r.signal };
}
function notice(text, summary) {
  return { id: randomUUID(), role: "user", content: [{ type: "text", text }],
           source: { kind: "rtl-guard", form: "notice", summary } };
}
function push(agent, text, summary) { log("msgchars=" + text.length + " kind=" + (summary || ""));
  agent.inbox.splice("next-step", agent.inbox.nextStep.length, 0, [notice(text, summary)]);
}

const FIXES = {
  "10-2951": "位选范围必须是常量：in[sel*4+3 : sel*4] → in[sel*4 +: 4]",
  "10-1775": "同上：范围边界必须是常量表达式",
  "10-9336": "ANSI 端口重复声明：模块体内不要再写一次",
  "10-2649": "enum 只能赋同类型值：改用 logic 或显式类型转换",
  "10-3818": "同一变量被多个过程块驱动：合并到一个 always",
  "10-2989": "always 内不能声明 wire/reg：提到模块级",
  "10-3236": "同一信号不能既被 always 赋值又被 assign 驱动",
  "10-3180": "测试台会连 clk：题面没写也要加 input clk",
  "10-395":  "端口位宽写在名字前：output out [24:0] → output [24:0] out",
  "10-3225": "elaborate 找不到 TopModule：检查模块名",
  "10-1280": "有未声明的标识符：检查拼写或把中间信号提到模块级",
  "10-4982": "语法错误：常见于位选写错，向量用 +: / -:",
};
const PORT_ORDER = /\b(?:input|output|inout)\b\s+(?:(?:reg|logic|wire|signed|unsigned|var)\s+)*(?!(?:reg|logic|wire|signed|unsigned|var)\b)([A-Za-z_]\w*)\s*\[\s*[^\]]*\]/g;

function lintPorts(cwd) {
  let src = ""; try { src = readFileSync(p(cwd, "solution.v"), "utf8"); } catch { return { ok: true }; }
  const head = src.split("\n").slice(0, 40).join("\n");
  const hits = [...head.matchAll(PORT_ORDER)].map((m) => m[0].replace(/\s+/g, " ").trim());
  if (!hits.length) return { ok: true };
  return { ok: false, msg: "端口位宽写在名字后面（会被当成非压缩数组）：\n```\n" + hits.slice(0, 3).join("\n") +
    "\n```\n正确写法：`output [24:0] out`（位宽在名字前）。" };
}

function compileCheck(cwd) {
  const v = run(tool("xvlog"), ["-sv", "--nolog", p(cwd, "solution.v")], cwd);
  const verr = v.out.split("\n").filter((l) => /ERROR:/.test(l)).slice(0, 4);
  if (v.err) return { ok: false, stage: "xvlog", errors: ["xvlog 启动失败: " + v.err.message] };
  if (verr.length) return { ok: false, stage: "xvlog", errors: verr };
  const e = run(tool("xelab"), ["--nolog", "-debug", "typical", "TopModule", "-s", "rtl_guard"], cwd);
  const eerr = e.out.split("\n").filter((l) => /ERROR:/.test(l)).slice(0, 4);
  if (e.err) return { ok: false, stage: "xelab", errors: ["xelab 启动失败: " + e.err.message] };
  if (eerr.length) return { ok: false, stage: "xelab", errors: eerr };
  return { ok: true };
}
function compactErrors(errors) {
  const seen = new Set(); const out = [];
  for (const raw of errors || []) {
    const m = raw.match(/\[([A-Z]+ \d+-\d+)\]/) || raw.match(/(\d+-\d+)/);
    const code = m ? m[1].replace(/^[A-Z]+ /, "") : "";
    const msg = raw
      .replace(/\[[A-Z]+ \d+-\d+\]\s*/, "")
      .replace(/\s*\[[^\]]*:\d+\]\s*$/, "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 60);
    const key = code + "|" + msg;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push("- " + (code ? "[" + code + "] " : "") + msg);
    if (out.length >= 3) break;
  }
  return out.length ? out.join("\n") : "- （无法提取错误摘要，请检查 solution.v 语法）";
}

function explain(errors) {
  const tips = new Set();
  for (const e of errors) for (const [code, tip] of Object.entries(FIXES)) if (e.includes(code)) tips.add("- `" + code + "` → " + tip);
  return [...tips].slice(0, 4).join("\n");
}

// ── 端口名闸门：题面声明的端口 vs solution.v 实际端口 ──
function promptPorts(cwd) {
  const f = p(cwd, ".spec_prompt.txt");
  if (!existsSync(f)) return null;
  let t = "";
  try { t = readFileSync(f, "utf8"); } catch { return null; }
  const exp = { input: [], output: [], inout: [] };
  const re = /^\s*[-*]?\s*(input|output|inout)\s+([A-Za-z_]\w*)/gim;
  let m;
  while ((m = re.exec(t))) exp[m[1].toLowerCase()].push(m[2]);
  if (!exp.input.length && !exp.output.length && !exp.inout.length) return null;
  return exp;
}
function dutPorts(src) {
  const m = src.match(/module\s+\w+\s*\(([\s\S]*?)\)\s*;/);
  if (!m) return null;
  const out = { input: [], output: [], inout: [] };
  let dir = null;
  for (let seg of m[1].split(",")) {
    seg = seg.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "").trim();
    if (!seg) continue;
    const d = seg.match(/\b(input|output|inout)\b/);
    if (d) dir = d[1].toLowerCase();
    if (!dir) continue;
    let body = seg.replace(/\b(input|output|inout)\b/, "").replace(/\b(wire|reg|logic|signed|unsigned|var)\b/g, "");
    body = body.replace(/\[[^\]]*\]/g, " ").replace(/=.*$/, " ");
    const ids = body.match(/[A-Za-z_]\w*/g) || [];
    if (ids.length) out[dir].push(ids[ids.length - 1]);
  }
  if (!out.input.length && !out.output.length && !out.inout.length) return null;
  return out;
}
function checkPortNames(cwd) {
  let src = "";
  try { src = readFileSync(p(cwd, "solution.v"), "utf8"); } catch { return { ok: true }; }
  const exp = promptPorts(cwd);
  const act = dutPorts(src);
  if (!exp || !act) return { ok: true };
  const bad = [];
  for (const dir of ["input", "output", "inout"]) {
    const a = new Set(act[dir]), e = new Set(exp[dir]);
    if (!e.size) continue;
    const missing = [...e].filter((n) => !a.has(n));
    const extra = [...a].filter((n) => !e.has(n));
    if (missing.length || extra.length) {
      bad.push(dir + " 缺[" + (missing.join(",") || "-") + "] 多[" + (extra.join(",") || "-") + "]");
    }
  }
  if (!bad.length) return { ok: true };
  return { ok: false, msg: "端口名与题面不一致：" + bad.join("；") +
    "。端口名必须**逐字一致**，不要加下划线/后缀/缩写。" };
}

function generatorPath() {
  const base = process.env.RTL_AGENT_DIR || "";
  if (!base) return "";
  for (const c of [path.join(base, "spec_oracle.py"), path.join(base, "agent", "spec_oracle.py"),
                   path.join(base, "..", "agent", "spec_oracle.py")]) {
    try { if (existsSync(c)) return c; } catch {}
  }
  return "";
}

export function apply(ctx) {
  ctx.on("agent/pre-step", async ({ agent }, next) => {
    try {
      const cwd = process.cwd();
      const s = st();
      if (!s.specReady && !SPEC_OFF) {
        s.specReady = true;
        if (exists(cwd, ".spec_prompt.txt") && !exists(cwd, ".spec/tb_spec.sv")) {
          const gen = generatorPath();
          if (gen) {
            const r = run(PY, [gen, "--prompt", ".spec_prompt.txt", "--outdir", "."], cwd);
            const last = (r.out || "").trim().split("\n").pop() || "";
            if (!last.startsWith("GENERATED") && XCHK_ON && !s.xchkAsked) {
              s.xchkAsked = true;
              push(agent, "另外请写一个 `model.py`：`def run(stim)` 接收每拍输入字典列表，返回同长度的输出字典列表（同样的位宽语义）。我会用它和你的 Verilog 对拍。", "xchk ask model.py");
              log("xchk: asked for model.py");
            }
            if (last.startsWith("GENERATED")) {
              log("spec: generated (" + last + ")");
              push(agent, "本题波形表已转为自动核对。写完 `solution.v` 即可结束，我会核对并告知差异。", "spec prepared");
            } else {
              log("spec: skipped (" + last + ")");
            }
          }
        }
      }
    } catch (e) { log("pre-step error:", e && e.message); }
    return next();
  });

  ctx.on("agent/turn-stopping", async ({ agent }) => {
    try {
      const cwd = process.cwd();
      const s = st();
      const elapsed = Date.now() - s.startedAt;
      // 时间闸：但保证至少做一次核验（blocks==0 时不受闸门限制）
      if (elapsed > TIME_GATE_MS && s.blocks >= 1) {
        log("time gate hit (" + Math.round(elapsed / 1000) + "s, blocks=" + s.blocks + "), allow output");
        return;
      }
      if (s.blocks >= MAX_BLOCKS) return;

      const aFull = s.aBlocks >= CAP_A;
      const sz = sizeOf(cwd, "solution.v");

      // ① 空文件：递进通牒（不受 A 额度限制，仍受总预算约束）
      if (sz <= 0) {
        s.blocks += 1; s.aBlocks += 1;
        const n = s.blocks;
        const msg = n === 1
          ? "`solution.v` 是空的。交付物只有这一个文件 —— 请用 `write` 写入完整实现后再结束。"
          : n === 2
          ? "`solution.v` 还是空的。**先写一个最简可编译版本**（哪怕只是猜测），再在它上面改。"
          : "**最后一次机会**：立刻用 `write` 写一个能编译的骨架（模块头 + 端口 + 一句 assign 即可）。0 字节一定是 0 分。";
        push(agent, msg, "empty solution.v");
        log("blocked: empty (#" + n + ")");
        return;
      }

      // ② 端口 lint
      const lint = aFull ? { ok: true } : lintPorts(cwd);
      if (!lint.ok) {
        s.blocks += 1; s.aBlocks += 1;
        push(agent, lint.msg + "\n改好后结束这一回合。", "port width order");
        log("blocked: port lint (#" + s.blocks + ")");
        return;
      }

      // ③ 编译

      const compiled = aFull ? { ok: false, skipped: true } : compileCheck(cwd);
      const c = compiled;
      if (!c.ok && !c.skipped) {
        s.blocks += 1; s.aBlocks += 1;
        const ce = compactErrors(c.errors);
        const tips = ce.unmapped ? explain(c.errors) : "";
        push(agent, "`solution.v` 没过 `" + c.stage + "`：\n" + ce.text +
          (tips ? "\n其他修法：\n" + tips : "") + "\n改到编译通过再结束。", "compile failed");
        log("blocked: " + c.stage + " (#" + s.blocks + ")");
        return;
      }

      // ④ spec 仿真（仅在编译通过后）
      if (!SPEC_OFF && !c.skipped && c.ok && s.bBlocks < CAP_B && exists(cwd, ".spec/check.sh")) {
        let r;
        if (WIN) {
          let o = run(tool("xvlog"), ["-sv", "--nolog", ".spec/tb_spec.sv", "solution.v"], cwd).out;
          if (/ERROR:/.test(o)) r = { out: "SPEC_COMPILE_FAIL" };
          else { o = run(tool("xelab"), ["--nolog","-debug","typical","tb_spec","-s","spec_s"], cwd).out;
                 r = /ERROR:/.test(o) ? { out: "SPEC_ELAB_FAIL" } : run(tool("xsim"), ["spec_s","-runall"], cwd); }
        } else { r = run("bash", [".spec/check.sh"], cwd); }
        const out = r.out || "";
        const m = out.match(/mismatches=(\d+)\s+checks=(\d+)/);
        if (!m) {
          if (/SPEC_(COMPILE|ELAB)_FAIL/.test(out)) {
            s.blocks += 1; s.bBlocks += 1;
            const head = out.split("\n").filter((l) => /ERROR/.test(l)).slice(0, 3).join("\n");
            push(agent, "自动核对台无法和 `solution.v` 一起编译，报错：\n```\n" + head +
              "\n```\n通常是端口名/方向/位宽与题面不一致（如 `rst` vs `reset`）。改好后结束。", "spec compile fail");
            log("blocked: spec compile (#" + s.blocks + ")");
          }
          return;
        }
        const mm = parseInt(m[1], 10), cc = parseInt(m[2], 10);
        if (mm === 0) { log("spec ok (" + cc + " checks)"); return; }
        // 差得太远（>50%）说明模型没抓住规律，拦了也改不动 → 直接放行，不烧额度
        if (mm / cc > 0.5) {
          log("spec: " + mm + "/" + cc + " (>50%) — hopeless, allow output without blocking");
          return;
        }
        s.blocks += 1; s.bBlocks += 1;
        const first = out.split("\n").find((l) => /MISMATCH|INVARIANT/.test(l)) || "";
        push(agent, "波形核对：" + mm + "/" + cc + " 不一致。" +
          (first ? "首处 " + first.replace(/^\s*(MISMATCH|INVARIANT)\s*/, "").slice(0, 60) + "。" : "") +
          "（exp = 题面要求，got = 你的输出）改 `solution.v` 后结束。", "spec mismatch");
        log("blocked: spec mismatch " + mm + "/" + cc + " (#" + s.blocks + ")");
        return;
      }

      // ⑤ 实验通道：Python 交叉验证（仅 RTL_SPEC_XCHK=1）
      if (XCHK_ON && !SPEC_OFF && s.cBlocks < CAP_C && elapsed < 300000 &&
          !exists(cwd, ".spec/check.sh") && exists(cwd, "model.py") && generatorPath()) {
        const genAbs = generatorPath();
        const cvPath = path.join(path.dirname(genAbs), "cross_verify.py");
        if (existsSync(cvPath)) {
          const r2 = run(PY, [cvPath, "--dut", "solution.v", "--model", "model.py", "--work", ".xchk"], cwd);
          const o2 = r2.out || "";
          const m2 = o2.match(/match_rate=([0-9.]+)/);
          if (m2) {
            const rate = parseFloat(m2[1]);
            if (rate < 0.95) {
              s.blocks += 1; s.cBlocks += 1;
              const first = (o2.split("\n").find((l) => l.includes("首个不一致")) || "").trim().slice(0, 160);
              push(agent, "Python 交叉验证不一致：match_rate=" + rate.toFixed(2) + "。\n" + first +
                "\n（dut = 你的 Verilog，model = 你的 model.py）请确认哪一侧符合题面，改好后结束。", "xchk mismatch");
              log("blocked: xchk rate=" + rate + " (#" + s.blocks + ")");
              return;
            }
            log("xchk ok (rate=" + rate + ")");
          } else {
            log("xchk skipped: " + (o2.trim().split("\n").pop() || "no result").slice(0, 80));
          }
        }
      }

      s.lastDigest = digest(cwd, "solution.v");
      log("ok: clean (size=" + sz + ", blocks=" + s.blocks + ")");
    } catch (e) { log("turn-stopping error:", e && e.message); }
  });
}
