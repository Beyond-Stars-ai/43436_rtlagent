---
name: verilog-2001-syntax
description: 避免 Verilog-2001 与 SystemVerilog 混用导致的 xvlog 编译失败（块内声明、output reg 与 assign 冲突、ANSI 端口重复声明、门级原语具名端口）。
---

# Verilog-2001 语法红线

本赛道判定工具以 `xvlog -sv` 编译 `solution.v`（SystemVerilog 模式），但下列四类写法
无论 2001 还是 SV 模式都会编译失败，会导致该题直接 0 分。

## 1. always 块内不要声明 wire/reg

```verilog
// ✗ 块内声明
always @(posedge clk) begin
    wire [7:0] next = {q[6:0], q[7]^q[5]};
    q <= next;
end

// ✓ 模块级声明
wire [7:0] next;
assign next = {q[6:0], q[7]^q[5]};
always @(posedge clk) q <= next;
```

## 2. 被 always 赋值的输出必须 `output reg`；但不要再对 reg 用 assign

```verilog
// ✗ 冲突
output reg f;
assign f = a & b;      // VRFC 10-3236 concurrent assignment to a non-net

// ✓ 风格 A（纯组合）
output f;
assign f = a & b;

// ✓ 风格 B（时序）
output reg f;
always @(posedge clk) f <= a & b;
```

**模块里没有 `always` 块时，一律用风格 A。**

## 3. 不要在模块体内重复声明 ANSI 端口

```verilog
// ✗
module TopModule (input clk, output q);
    reg q;              // VRFC 10-9336 redeclaration of ANSI port
endmodule

// ✓
module TopModule (input clk, output reg q);
```

## 4. 门级原语禁止具名端口

```verilog
and g1 (.a(x), .b(y), .z(w));   // ✗ VRFC 10-9195
and g1 (w, x, y);               // ✓ 按位置
```
