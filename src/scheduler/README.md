# riscv-llm-scheduler 调度优化

## 功能

实现 V 状态感知的调度优化策略，减少推理线程被抢占时 V 状态 save/restore 开销。

## 方案规划

### 保底方案（必做）

| 方案 | 实现方式 | 隔离方向 | bench_runner 集成 |
|------|----------|:--------:|:-----------------:|
| CPU affinity | `taskset -c X-Y` 将推理线程绑定到指定物理核心 | 单向 | ✅ 已集成 |
| cgroup cpuset | 创建 cgroup + 设置 `cpuset.cpus` + 移入 PID | 单向 | ✅ 已集成 |
| partition-root | cgroup + `echo root > cpuset.cpus.partition`，独占核心（其他进程无法使用） | **双向** | ✅ 已集成 |
| systemd-scope | `AllowedCPUs` + `systemd-run --scope`，限制系统进程使用目标核 | 单向（等效） | ✅ 已集成 |

上述四种方法已在 [bench_runner.py](../benchmark/bench_runner.py) 中实现，可作为自动化测试脚本直接使用。详细用法参见 [README_bench_runner.md](../benchmark/README_bench_runner.md)。

### 进阶方案（可选）

| 方案 | 说明 | 当前状态 |
|------|------|:--------:|
| sched_ext | BPF 可编程调度器（支持 llm.sched 等调度策略），在用户态编写调度逻辑，动态加载到内核 | 📋 待调研 |
| taskset + 调度策略 | `chrt -b 0`（SCHED_BATCH）/ `chrt -f 90`（SCHED_FIFO）配合 taskset 使用 | ✅ 已集成 |

## 关联工具

bench_runner 已支持以下调度方法组合的自动化测试：

```bash
python ../benchmark/bench_runner.py
# 在 METHODS 列表中配置需要测试的方法
```

生成的 CSV 结果中包含 `v_saves`、`nontarget_saves` 等 V 状态指标，用于量化评估各方案的优化效果。

## 当前状态

📋 进阶方案规划中，bench_runner 已完成绑核/调度方法的自动化框架集成
