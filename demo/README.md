# 可验证交付物

本目录是 Milk-V Jupiter 上的实机运行录像。对应数值结果在 `tests/`。决赛 PPT 与总演示视频在 `presentation/`。

## 绑核+策略方法

与 `src/benchmark/bench_runner.py` 的绑核方法对应。

| 文件 | 方法 |
|------|------|
| `Taskset.mp4` | `taskset` |
| `Cgroup.mp4` | `cpuset` |
| `Partition Root.mp4` | `partition-root` |
| `Systemscope.mp4` | `systemd-scope` |
| `Chrtb.mp4` | `taskset` + `chrt -b`（SCHED_BATCH） |
| `Chrtf.mp4` | `taskset` + `chrt -f`（SCHED_FIFO） |
| `Cr.mp4` | llama-server `-Cr` |

## 自定义调度

| 文件 | 内容 |
|------|------|
| `Org.mp4` | 原生 CFS，正常负载 |
| `Org Stress.mp4` | 原生 CFS，高负载 |
| `Scx 2.mp4` | sched_ext，正常负载 |
| `Scx Stress 2.mp4` | sched_ext，高负载 |
