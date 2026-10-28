**多位计数/求和**：直接用 `always @(*) begin out = 0; for (i=0;i<N;i=i+1) out = out + in[i]; end`（配 `integer i` 或 `int i`），**不要手写压缩树/进位链/Wallace 树**——结构越简单越不容易算错；目标变量位宽要装得下最大和。
