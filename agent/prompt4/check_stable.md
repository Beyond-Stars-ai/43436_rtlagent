## 写完自查（必做）
1. **编译**：`xvlog -sv solution.v` 0 错、`xelab -debug typical TopModule -s s1` 打印 `Built`，≤3 轮。
2. **功能**：按题面真值表/波形/文字逐条对输出，不符就改。
3. **细节**：端口方向位宽、位序（`out[0]` 来自哪一位）、是否要求“下一周期”。
