import { randomUUID } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import path from "node:path";

export const name = "spec-oracle";

const MAX_BLOCKS = 2;          // 拦截上限
const TIME_GATE_MS = 420000;    // 超过 420 秒不再拦截，允许直接交卷
const TIMEOUT_MS = 180000;
const state = new WeakMap();

function log(...a) { console.error("[spec-oracle]", ...a); }
function here() { return process.env.RTL_AGENT_DIR || ""; }
function generatorPath() {
  const base = here();
  if (!base) return "";
  const candidates = [
    path.join(base, "spec_oracle.py"),
    path.join(base, "agent", "spec_oracle.py"),
    path.join(base, "..", "agent", "spec_oracle.py"),
  ];
  for (const c of candidates) { try { if (existsSync(c)) return c; } catch {} }
  return "";
}
function p(cwd, f) { return path.join(cwd, f); }
function digest(cwd, f) {
  try { return createHash("sha1").update(readFileSync(p(cwd, f))).digest("hex").slice(0, 12); }
  catch { return "none"; }
}
function exists(cwd, f) { try { return existsSync(p(cwd, f)); } catch { return false; } }

function notice(text, summary) {
  return { id: randomUUID(), role: "user", content: [{ type: "text", text }],
           source: { kind: "spec-oracle", form: "notice", summary } };
}
function push(agent, text, summary) {
  agent.inbox.splice("next-step", agent.inbox.nextStep.length, 0, [notice(text, summary)]);
}
function run(cmd, args, cwd) {
  const r = spawnSync(cmd, args, { cwd, timeout: TIMEOUT_MS, encoding: "utf8" });
  return { out: String(r.stdout ?? "") + String(r.stderr ?? ""), err: r.error, sig: r.signal };
}

export function apply(ctx) {
  // ① 题面命中波形表 → 生成 tb_spec.sv + spec_check.sh，并告知模型必须自测
  ctx.on("agent/pre-step", async ({ agent }, next) => {
    try {
      const cwd = process.cwd();
      const st = state.get(agent) ?? { prepared: false, blocks: 0, startedAt: Date.now() };
      state.set(agent, st);
      if (!st.prepared) {
        st.prepared = true;
        if (exists(cwd, ".spec_prompt.txt") && !exists(cwd, ".spec/tb_spec.sv") && here()) {
          const gen = generatorPath();
          const r = gen ? run("python3", [gen, "--prompt", ".spec_prompt.txt", "--outdir", "."], cwd)
                        : { out: "SKIP generator not found", err: null, sig: null };
          const line = (r.out || "").trim().split("\n").pop() || "";
          if (line.startsWith("GENERATED")) {
            log("generated tb_spec.sv (" + line + ")");
            push(agent,
              "本题波形表已转为自动核对（无需写测试台）。写完 `solution.v` 即可结束，我会核对并告知差异。",
              "spec oracle prepared");
          } else {
            log("skipped: " + line);
          }
        }
      }
    } catch (e) { log("pre-step error:", e && e.message); }
    return next();
  });

  // ② 回合结束前跑 spec 仿真；不匹配就拦住并回注具体时刻
  ctx.on("agent/turn-stopping", async ({ agent }) => {
    try {
      const cwd = process.cwd();
      const st = state.get(agent) ?? { prepared: true, blocks: 0 };
      state.set(agent, st);
      if (!exists(cwd, ".spec/check.sh") || !exists(cwd, "solution.v")) return;
      const elapsed = Date.now() - (st.startedAt ?? Date.now());
      if (elapsed > TIME_GATE_MS) {
        log("time gate hit (" + Math.round(elapsed/1000) + "s), allow output without spec check");
        return;
      }
      try { if (statSync(p(cwd, "solution.v")).size <= 0) return; } catch { return; }
      if (st.blocks >= MAX_BLOCKS) return;

      const r = run("bash", [".spec/check.sh"], cwd);
      const out = r.out || "";
      const m = out.match(/mismatches=(\d+)\s+checks=(\d+)/);
      if (!m) {
        if (/SPEC_(COMPILE|ELAB)_FAIL/.test(out)) {
          st.blocks += 1;
          const head = out.split("\n").filter((l) => /ERROR/.test(l)).slice(0, 3).join("\n");
          push(agent,
            "自动核对台无法和你的 `solution.v` 一起编译/例化，报错：\n\n```\n" + head +
            "\n```\n\n这通常说明端口名/方向/位宽与题面不一致（例如 `rst` 与 `reset`、`clk` 与 `clock`、位宽不同）。" +
            "请对照题面的接口逐项修正端口名/方向/位宽后结束回合，我会再核对一次。", "spec compile fail");
          log("blocked: spec compile/elab fail (#" + st.blocks + ")");
        }
        return;
      }
      const mm = parseInt(m[1], 10), cc = parseInt(m[2], 10);
      if (mm === 0) { log("spec ok (" + cc + " checks)"); return; }

      st.blocks += 1;
      const previous = st.lastDigest;               // 上一次拦截时的指纹（只保留最近一次）
      const current = digest(cwd, "solution.v");
      const lines = out.split("\n").filter((l) => /MISMATCH|SPEC_RESULT/.test(l)).slice(0, 6).join("\n");
      const first = out.split("\n").find((l) => /MISMATCH/.test(l)) || "";
      const brief = first.replace(/^\s*MISMATCH\s*/, "").slice(0, 90);
      push(agent,
        "波形核对：" + mm + "/" + cc + " 不一致。" + (brief ? "首处 " + brief + "。" : "") +
        "改 `solution.v`（用 write）后结束，这是最后一次核对。",
        "spec mismatch");
      st.lastDigest = current;                      // 覆盖式记忆，只留最近一次
      const changed = previous !== undefined && previous !== current;
      log("blocked: spec mismatch " + mm + "/" + cc + " (#" + st.blocks +
          (changed ? ", file changed since last block" : ", no change since last block") + ")");
    } catch (e) { log("turn-stopping error:", e && e.message); }
  });
}
