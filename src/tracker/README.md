# riscv-llm-scheduler 量化分析工具

## 功能

通过 bpftrace 挂载 `sched:sched_switch` tracepoint，在内核上下文切换时从内核栈偏移读取 `pt_regs.status` 的 VS 域（`0x0600`），判断被切换进程的 V 状态是否 dirty，从而量化统计 V 状态 save/restore 次数。

不依赖 `riscv_vstate_save` 的具体内核符号，而是通过 tracepoint 通用接口采集，兼容性更好。

## bpftrace 脚本

| 文件 | 说明 |
|------|------|
| [vstate_trace.bt](vstate_trace.bt) | **基础版** — 全局统计 V 状态 save/restore 总次数，按进程名和 PID 分组。通过缓存 `task_struct` 指针 + `start_time` 校验解决 PID 复用问题，并区分 save 侧（当前被抢占进程）和 restore 侧（被调度回来的进程）。每 10s 输出一次中间结果，END 时输出完整 SUMMARY。 |
| [vstate_trace_cpu.bt](vstate_trace_cpu.bt) | **per-CPU 增强版** — 在基础版之上增加了 per-CPU 统计维度：`@cpu_saves`、`@cpu_restores`、`@cpu_saves_by_proc`、`@tid_cpu_saves`、`@tid_cpu_restores`，可精确到每个 CPU 核上哪个线程发生了 V 状态切换，用于识别绑核后 V 状态泄漏（非目标核上仍出现 V 状态 save 即为泄漏）。 |
| [vstate_trace_llama_server.bt](vstate_trace_llama_server.bt) | **llama-server 专用版（在线压测用）** — 只统计涉及 `llama-server` 的切换，并用 `kprobe:finish_task_switch` 采集切换耗时，区分 V 状态/非 V 状态两类，每 10s 输出区间 stats + histogram，END 输出全量 SUMMARY。用于量化抢占对推理进程的实际开销。 |

### 核心原理

```c
// 从当前进程的内核栈中读取 pt_regs，获取 status 寄存器
$regs = (struct pt_regs *)((uint64)$t->stack + 16384 - sizeof(struct pt_regs));

// VS 域（status[12:9]）判断 V 状态：
//   0x0000 = Off（无向量状态）
//   0x0200 = Initial（已初始化，clean）
//   0x0400 = Clean（已加载，clean）
//   0x0600 = Dirty（已修改，需要 save）
$vs = $regs->status & 0x0600;
```

## 输出

### 基础版输出
- `@saves_total` / `@restores_total` — 全系统 V 状态 save/restore 总次数
- `@saves[comm, pid]` / `@restores[comm, pid]` — 按进程名 + PID 分组统计

### per-CPU 增强版输出
- `@cpu_saves[CPU]` / `@cpu_restores[CPU]` — 每个 CPU 核上的 save/restore 次数
- `@cpu_saves_by_proc[CPU, comm]` — 每个 CPU 核上发生 save 的进程分布
- `@tid_cpu_saves[pid, CPU]` / `@tid_cpu_restores[pid, CPU]` — 每个线程在哪些核上发生过 save/restore

## 使用方法

```bash
# 基础版
sudo bpftrace src/tracker/vstate_trace.bt

# per-CPU 增强版（推荐）
sudo bpftrace src/tracker/vstate_trace_cpu.bt

# 输出重定向到文件
sudo bpftrace src/tracker/vstate_trace_cpu.bt > /tmp/vstate_result.log
```

## 依赖

- Linux 内核开启 `CONFIG_BPF`、`CONFIG_BPF_EVENTS`
- 安装 `bpftrace`

## 当前状态

✅ 已完成
