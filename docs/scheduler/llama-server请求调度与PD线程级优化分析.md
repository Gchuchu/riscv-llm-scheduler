# llama-server 请求调度

> 关联 Issue：`feat: how do llama server dispatch`

## 1. 摘要

1. `llama-server` 使用 slot 保存请求的逻辑推理状态，但 slot 不会固定绑定某个计算线程。
2. server 先把多个 slot 的 token 合并为 `server_batch`，再由 llama.cpp 按 `n_ubatch` 切为一个或多个 `llama_ubatch`。ggml 对每个 ubatch 构图，并把图中的算子工作分给 CPU worker。
3. 连续批处理（--cont-batching）开启时，一个 batch/ubatch 可以同时包含已有请求的 Decode token 和新请求的 Prompt token。因此，运行时不一定存在“纯 P 阶段”和“纯 D 阶段”。
4. `-t` 与 `-tb` 不是严格的 D/P 线程配置。源码按照 `ubatch.n_tokens == 1(-t)` 或 `> 1(-tb)` 选择；并发 Decode 形成多 token ubatch 时也会使用 `-tb`。

## 2. 多请求如何进入 llama-server

### 2.1 请求与 slot

一个 slot 是一个推理任务的逻辑上下文，主要保存：

- 当前任务和请求参数；
- sequence id；
- Prompt token 与已生成 token；
- KV cache 对应的 sequence；
- Prompt 处理进度和 batch 索引；
- sampler、停止条件和计时数据。

普通请求被分配给空闲 slot 后，状态依次经过：

```text
IDLE
  -> STARTED
  -> PROCESSING_PROMPT
  -> DONE_PROMPT
  -> GENERATING
  -> IDLE
```

完整状态机参考：[llama-server 单个 slot 状态机](./figures/llama_server_slot_state_machine.drawio)。

有些路径代码里写了但是其实不太用得到，不必理会，只看主要路径即可。

### 2.2 slot 数量与并发

`--parallel N` 决定 server 可以同时维护的 slot 数量。它不表示创建 N 套独立计算线程。

当请求数大于空闲 slot 数时，多余请求留在任务队列中，直到某个 slot 释放。

一个请求进入 slot 后，正常情况下会一直保留该 slot，直至生成完成、取消或发生错误。

### 2.3 空闲 slot 的选择

当存在多个空闲 slot 时，server 按以下顺序选择：

1. 请求指定了 `id_slot` 时，优先使用指定 slot；
2. 启用 `slot_prompt_similarity` 时，选择 Prompt 最长公共前缀相似度超过阈值且相似度最高的空闲 slot，以复用 KV cache；(sps是server启动参数 默认是开启的)
3. 未命中上述条件时，选择最久未使用的空闲 slot（LRU）；
4. 没有可用 slot 时，将任务重新放回队列等待。

## 3. 多个 slot 如何组成 batch

### 3.1 server_batch 的组装顺序

`server_context` 每轮先处理处于 `GENERATING` 的 slot，把每个 slot 上一轮采样得到的 token 加入 batch。之后，在 `--cont-batching` 开启或 batch 为空时，再加入等待处理的 Prompt token。

因此 batch 的典型顺序为：

```text
[slot 0 decode token]
[slot 1 decode token]
...
[slot N decode token]
[new slot A prompt tokens ...]
[new slot B prompt tokens ...]
```

这意味着一个 batch 可以是：

- Decode-only；
- Prompt-only；
- 同时包含 Prompt token 和 Decode token。

### 3.2 batch 与 ubatch

`-b/--batch-size` 控制逻辑 batch 的 token 上限，`-ub/--ubatch-size` 控制一次物理计算 ubatch 的 token 上限。

普通 KV cache 路径使用 `split_simple(n_ubatch)` 或与多 stream 相关的 `split_equal()`。`split_simple()` 按 batch 中原始 token 顺序取最多 `n_ubatch` 个 token，不识别 P/D 语义。

例如：

```text
server_batch = 8 decode tokens + 600 prompt tokens
n_ubatch = 512

ubatch 0 = 8 decode tokens + 504 prompt tokens   # mixed
ubatch 1 = 96 prompt tokens                      # Prompt-only
```

所以不能只通过“当前有 Prompt slot”判断正在执行的 ubatch 内容。

## 4. slot 如何分发给 thread

### 4.1 不存在 slot -> thread 绑定

slot 只提供 token、position、sequence id 和输出位置。server 将多个 slot 的 token 合并后调用 `llama_decode()`，llama.cpp 为当前 ubatch 创建计算图，ggml 再按算子和数据分块把工作分配给 worker。

因此关系是：

```text
多个 slot
  -> 一个 server_batch
  -> 一个或多个 ubatch
  -> 一个或多个 ggml graph
  -> worker team 并行执行 graph 中的算子
```

一个 slot 的计算可以由多个 worker 协作完成；一个 worker 在不同 graph 中也会处理来自不同 slot 的数据。不能把某个 worker 简单解释为“slot 3 的线程”。

### 4.2 `-t` 与 `-tb` 的真实选择条件

源码调用：

```cpp
graph_compute(graph, ubatch.n_tokens > 1);
```

随后选择：

```text
ubatch.n_tokens == 1 -> n_threads / -t
ubatch.n_tokens > 1  -> n_threads_batch / -tb
```

所以：

- 单 slot、单 token Decode 常使用 `-t`；
- 8 个活动 slot 的 Decode ubatch 可能有 8 个 token，此时使用 `-tb`；
- mixed ubatch 也使用 `-tb`；
- `-t/-tb` 是单 token graph 与多 token graph 的配置，不是严格的 D/P 配置。

P/D 阶段不会决定 worker 的身份。同一组 ggml/OpenMP worker 会随不同 graph 依次执行 Prompt-only、Decode-only 或 mixed 工作；变化的是当前 graph 的 token 形态和使用的线程参数，不是 slot 被重新绑定到另一组专属线程。

## 5. P/D 阶段能否在线程级准确分离

### 5.1 实现准确 P/D 标记需要什么

比较可行的方案是把标记粒度从 slot 改为 graph/ubatch：

1. 为 `server_batch` 中每个 token 保存 `PROMPT` 或 `DECODE` 来源标记；
2. batch view 和 llama.cpp 的 ubatch 切分过程保留该来源元数据；
3. 根据每个实际 ubatch 的 token 组成，将其分类为 `PROMPT_ONLY`、`DECODE_ONLY`、`MIXED` 或 `IDLE`；
4. graph kickoff 前将分类写入 threadpool 的共享原子状态；
5. 每个 worker 开始执行新 graph 时，根据共享状态为自己的 TID 更新标签；
6. 仅在标签变化时执行 ioctl，避免每个算子都产生系统调用。

这可以准确说明“worker 当前在执行什么 graph”，但 mixed graph 仍不能把一个 worker 精确拆成 P 或 D，因为 worker 按算子数据块协作执行同一张图。

如果不修改 `llama_batch` 或相关的 ubatch 切分数据结构，只记录 `n_prompt_tokens` 和 `n_decode_tokens`，则最多只能实现一次 `llama_decode()` 调用级的分类，不能称为物理 ubatch 级分类。

### 5.2 强制 P/D 线程分离的代价

若要求互斥的 P 线程和 D 线程，必须在应用层拆分 mixed batch，分别执行 Prompt graph 和 Decode graph，或维护两个独立计算队列/线程池。

代价包括：

- continuous batching 合并效率下降；
- graph 提交次数增加；
- barrier、唤醒和线程池切换增加；
- 总 TPS 可能下降；
- KV cache 与输出索引管理更复杂。

综上，现有 llama-server 不能把 worker 准确划分为固定的 P 线程和 D 线程。实现 graph/ubatch 级的 P/D/MIXED 标记是可行的；实现互斥的 P/D 线程集合则需要拆分 mixed batch 或维护独立执行队列，会牺牲 continuous batching 的部分收益。

## 6. 源码位置

- `llama.cpp/tools/server/server-context.cpp:1603`：空闲 slot 的指定、Prompt 相似度和 LRU 选择逻辑；
- `llama.cpp/tools/server/server-context.cpp:2410`：任务获取 slot，无可用 slot 时重新入队；
- `llama.cpp/tools/server/server-context.cpp:3089`：Decode token 先加入 `server_batch`；
- `llama.cpp/tools/server/server-context.cpp:3099`：随后加入等待处理的 Prompt token；
- `llama.cpp/src/llama-batch.cpp:474`：`split_simple(n_ubatch)` 的顺序切分逻辑；
- `llama.cpp/src/llama-context.cpp:1364`：按 `ubatch.n_tokens > 1` 选择 batched 模式；
- `llama.cpp/src/llama-context.cpp:2428`：选择 `n_threads/-t` 或 `n_threads_batch/-tb`。
