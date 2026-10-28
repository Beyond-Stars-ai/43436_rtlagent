**位反转**：`out[i] = in[N-1-i]`（`out[0]` 来自 `in[N-1]`）；宽向量用 `generate`/`for` 循环或逐位拼接，写完先验两端：`out[0]==in[N-1]`、`out[N-1]==in[0]`。
