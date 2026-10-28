# `rtl-module-interface-contract`（官方技能包，未启用）

本目录保存的是 **AMD 官方随基础镜像提供的技能包原文**，保留在此仅供查阅与合规核对。

**当前状态：未启用。**

原文件已改名为 `SKILL.md.disabled`，DSH 只扫描 `SKILL.md`，因此该技能包不会出现在
可加载列表中，**每轮上下文开销为 0**。

## 为什么未启用

该技能包的触发条件是"**能看到官方测试台的 elaborate 报错**"（`VRFC 10-3180`、
`cannot find port`、`not found while processing module instance`）。

本赛道的参考测试台与参考实现**在评测前不公开**（见 `tasks/README.md`），本地只编译 DUT
本身，因此这些报错在本地不会出现，技能包不会被触发；实测 156 题中仅被加载 8 次。

另外它的输出格式是结构化诊断报告，与本方案"只输出代码"的交付要求不一致。

## 如需启用

```bash
mv SKILL.md.disabled SKILL.md
```

并确认 `agent/AGENTS.md` 未禁止诊断报告类输出。