# 本工作区的硬性约定（RTL 赛题生成）

## 0. 交付物只有一个文件

**只有写进 `<output_dir>/solution.v` 才算完成。** 在回复里给代码、在分析里写表达式，都不算交付。
用 `write` 整体覆盖，每次都提交一份完整、可编译的实现。

## 1. 顶层模块名

题面若未显式给出模块名，**必须命名为 `TopModule`**。

## 2. 接口即契约

端口名、方向、位宽、顺序必须与题面**逐字一致**；不加端口、不改名。
题面若把某个输出写成 `input`，而描述里它被计算（"the output bit should be set…"），
就按 `output reg` 写，端口名不改。

## 3. 语言与语法

判分器以 **`xvlog -sv`** 编译，**SystemVerilog 允许**。

**先判断题的类型，再选写法：**

- **简单/组合题**（门电路、多路器、位向量、真值表、K-map、计数器）：
  用 Verilog-2001 风格——`assign` 配 `wire`，或 `always @(*)` 配 `output reg`。
- **复杂题**（状态机、多状态 + 数据通路，题面出现 `fsm`/`state`/`serial`/`lemmings`/`circuit`）：
  用 SystemVerilog——`typedef enum` 声明状态、`always_ff` 描述时序、`case` 全覆盖。

**必然编译失败的四类写法（禁止）：**

- `always` 块内声明 `wire`/`reg`（`VRFC 10-2989`）；中间信号提到模块级。
- 同一信号既用 `always` 赋值又用 `assign` 驱动（`VRFC 10-3236`）。
- 模块体内重复声明 ANSI 端口（`VRFC 10-9336`）。
- 门级原语具名连接（`VRFC 10-9195`）。

## 4. 写完自查一遍

1. **编译**：`xvlog -sv solution.v` 0 错、`xelab -debug typical TopModule -s s1` 打印 `Built`，
   最多 3 轮。
2. **功能**：按题面的真值表 / 波形 / 文字描述逐条对一遍输出，发现不符就改 `solution.v`。
3. **细节**：端口方向与位宽、位序（`out[0]` 来自哪一位）、题面是否要求"下一周期"。

不要为了"更优雅"重写已通过的实现。

## 5. 只输出代码

不写设计文档。最终代码只放在 `solution.v`。
