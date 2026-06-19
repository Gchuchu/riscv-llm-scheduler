Author: Chenzixuan
# CFS Completely Fair Scheduler

## 概述

CFS（完全公平调度器）是 Linux 默认的 CPU 调度策略。其核心思想是：**每个任务都应该获得公平的 CPU 时间**。CFS 在真实硬件上建模了一个"理想的、精确的多任务 CPU"。

## 核心概念

### vruntime（虚拟运行时间）

CFS 通过 vruntime 来量化每个进程已经获得的 CPU 时间。调度器维护一棵**红黑树**（以 vruntime 为 key）存放所有可运行进程，每次选择 **vruntime 值最小的进程**运行——即"至今获得 CPU 时间最少的进程优先执行"。该进程运行时 vruntime 递增，直到它不再是最小值，调度器转而选择下一个 vruntime 最小的进程。

### min_vruntime 与 max_vruntime

- **min_vruntime**：新进程或重新回到 ready 状态的进程，用 `vruntime = min_vruntime` 来初始化，放到红黑树最左边；防止进程饥饿。
- **max_vruntime**：用于限制 vruntime 的过度增长。

### 权重与 nice 值

```
vruntime += 实际运行时间 * 1024 (nice_0_load) / 进程权重
```

- 进程权重与 nice 值相关，nice 值从 -20 到 19，越小优先级越高。
- nice 值越小 → 进程权重越大 → vruntime 增加得越慢 → 占用更多 CPU。
- 所以 CFS 的 "Fair" 本质是**加权的 Fair**。
- 默认任务的 nice = 0，对应权重 1024（即 `nice_0_load`）。
- nice 值可在启动时或运行时调整。

## 调度过程

### 时间片计算（旧版 sched_fair.c）

```c
// sched_fair.c:705-706
ideal_runtime = max(sysctl_sched_latency / cfs_rq->nr_running,
                    (unsigned long)sysctl_sched_min_granularity);
// 假如有4个进程 → ideal_runtime = max(20ms/4, 2ms) = 5ms
// 假如有10个进程 → ideal_runtime = max(20ms/10, 2ms) = 2ms
```

每次时钟 tick 触发 `entity_tick()`：
1. 将当前进程从红黑树中移除
2. 更新 fair_key（vruntime）
3. 重新插入树中
4. 取最左节点
5. 如果最左节点变了，说明有跑得更少的进程，调用 `resched_task()` 触发切换

单次运行时间 = `max(sched_latency/nr_running, min_granularity)`，默认 20ms/进程数（最少 2ms）。

### 新版演进

目前新版 Linux 的 `fair.c` 已将 CFS 升级为 **EEVDF**（Earliest Eligible Virtual Deadline First），大部分沿用了 CFS 的基础架构但有很多改进。

## 总结

CFS 三个核心点：
1. **选择最小 vruntime**
2. **vruntime 加权增加**
3. **时间片计算（ideal_runtime）**

---

# CFS 在 RVV 场景下的性能损失

## 问题本质

CFS 只关注"运行时间的加权公平"，但它**并不关注任务切换带来的成本**。CFS 的设计假设不同任务的上下文切换成本近似相同，因此调度决策主要基于 CPU 时间公平性。

## RVV 场景的特殊性

在 RISC-V Vector 扩展场景下，持有 **Dirty Vector State** 的推理线程具有显著更高的上下文切换成本：

1. **V 状态保存开销**：每个 hart 拥有 32 个向量寄存器（v0–v31），单次完整保存需要 512–1024 字节内存写入
2. **Dirty 状态的触发频率**：LLM 推理是持续向量密集型计算，线程几乎始终持有 Dirty V 状态
3. **频繁被抢占**：CFS 在时间片用完后触发切换，推理线程在计算关键路径上被频繁打断

## 影响

默认的调度策略无法感知 RVV 上下文的差异，导致推理线程被频繁抢占时产生额外的 V 状态保存与恢复开销，从而影响 LLM 推理性能：

- 整体吞吐（TPS）下降
- 尾延迟（P95 TTFT）恶化

## 参考

- [Linux 调度器文档](https://docs.kernel.org/scheduler/)
- [Red-black Trees (rbtree) in Linux — The Linux Kernel documentation](https://docs.kernel.org/core-api/rbtree.html)
- [完全公平调度器 — The Linux Kernel documentation](https://docs.kernel.org/scheduler/sched-design-CFS.html)
- <https://github.com/torvalds/linux/blob/master/kernel/sched/fair.c>
- [linux/kernel/sched_fair.c at v2.6.23](https://github.com/torvalds/linux/blob/v2.6.23/kernel/sched_fair.c)
