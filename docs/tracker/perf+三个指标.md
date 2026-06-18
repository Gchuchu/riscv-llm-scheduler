Author: Wangkun
# perf + 三个性能指标

## perf 的原理

perf 是 Linux 的性能分析框架，通过 PMU（Performance Monitoring Unit）、软件计数器和 Tracepoint 等机制收集运行时事件：

- **采样模式**：周期性或在硬件事件达到阈值时触发中断，记录当前执行位置（Program Counter）及调用栈信息，通过统计大量样本估计各函数的 CPU 时间占比。
- **计数模式**：直接统计上下文切换、CPU 迁移、缓存未命中、周期数等事件总数。

## 基本用法

### perf stat — 统计总数

```bash
# 基本用法：跑完一条命令，输出统计
perf stat ./llama-server -m model.gguf -t 4 --port 8080
```

输出示例：
```
 Performance counter stats for './llama-server -m model.gguf -t 4':

       3,847,291,431      cycles                    #    3.847 GHz
       1,234,567,890      instructions              #    0.32  insn per cycle
          45,678          context-switches          #  ← 上下文切换
             123          cpu-migrations            #  ← 线程在核心间迁移
         123,456          page-faults               #  ← 缺页异常

       5.234234123 seconds time elapsed
```

指定想看的事件：
```bash
perf stat -e context-switches,cpu-migrations,cycles,instructions \
  ./llama-server ...
```

针对已运行的进程：
```bash
# 盯 10 秒
perf stat -p <PID> -e context-switches -- sleep 10
```

### perf record + perf report — 采样找热点

```bash
# 采集数据
perf record ./llama-server -m model.gguf -t 4

# 或：只盯一个进程 30 秒
perf record -p <PID> -- sleep 30

# 生成报告
perf report
```

输出示例：
```
Samples: 1M of event 'cycles', 4000 Hz
Overhead  Command         Shared Object     Symbol
  45.2%  llama-server    llama-server      ggml_mat_mul_q4_K
  12.1%  llama-server    llama-server      ggml_vec_dot_q4_K
   8.5%  llama-server    [kernel]          riscv_vstate_save
   7.2%  llama-server    [kernel]          __schedule
   5.1%  llama-server    [kernel]          __switch_to
   ...
```

加上调用链，看谁调了 `riscv_vstate_save`：
```bash
perf record -g ./llama-server ...
perf report -g graph
```

输出：
```
 __schedule
  → context_switch
    → __switch_to
      → (若 VS=Dirty)
           riscv_vstate_save
```

### perf top — 实时查看热点

```bash
# 像 top 一样实时刷新最耗 CPU 的函数
perf top

# 只看特定进程
perf top -p <PID>

# 只看内核函数
perf top -k
```

### perf list — 查看支持的事件

```bash
perf list | grep -i cache
perf list | grep -i context
```

## 三个核心性能指标

### 吞吐量（TPS）

每秒生成的 token 数。衡量推理服务的整体处理能力。

### 平均 TTFT（Average Time To First Token）

平均首 token 生成时间（毫秒）。衡量用户感知的响应速度。

### P95 TTFT

95 分位首 token 生成时间（毫秒）。衡量尾延迟，反映最差情况下的用户体验。

> **注意**：这三个指标 perf 无法直接记录，需要写脚本自行采集。参见 `src/benchmark/` 下的测试脚本。

## 参考

- [Linux perf 全解析](https://zhuanlan.zhihu.com/p/8497782204)
- [perf(1) — Linux manual page](https://man7.org/linux/man-pages/man1/perf.1.html)
