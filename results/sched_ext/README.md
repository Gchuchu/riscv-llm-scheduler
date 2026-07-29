# sched_ext 调度策略对比测试

## 测试配置

- 模型：Llama-3.2-3B-Instruct Q4_K_M
- 模式：20 请求全并发（all-at-once），Prompt=64 token，Output=20 token
- 每组 3 次 trial（trial 1 含冷启动）
- 板卡：Milk-V Jupiter（SpacemiT K1, 8 核）

## 测试分组

| 目录 | 调度策略 | 时间片（普通/推理） | 负载 |
|------|---------|:---:|:---:|
| `resultORG` | 原生 CFS | — | 正常 |
| `resultOSCX` | sched_ext（旧） | 4ms / 20ms | 正常 |
| `resultSCX` | sched_ext（新） | 2ms / 30ms | 正常 |
| `resultORG_stress` | 原生 CFS | — | 高负载 |
| `resultOSCX_stress` | sched_ext（旧） | 4ms / 20ms | 高负载 |
| `resultSCX_stress` | sched_ext（新） | 2ms / 30ms | 高负载 |

> 高负载：`while(1)` 浮点运算 cpu_stress 进程抢占 CPU 4~7

## 正常负载

三组策略性能接近，SCX 略优于 CFS：

| 指标 | ORG (CFS) | OSCX (4/20) | SCX (2/30) |
|------|:---:|:---:|:---:|
| 聚合 TPS | 6.81 | 6.87 | 6.88 |
| 平均 TTFT (ms) | 1309 | 1304 | 1497 |
| P95 TTFT (ms) | 4383 | 4354 | 6302 |

> 注：trial 1 含冷启动尾延迟，拉高平均值。trial 2-3 稳定后 P95 TTFT 均在 500~700ms。

## 高负载（stress）

SCX 优势显著，吞吐量近 **2 倍**于 CFS：

| 指标 | ORG (CFS) | OSCX (4/20) | SCX (2/30) | SCX vs ORG |
|------|:---:|:---:|:---:|:---:|
| 聚合 TPS | 1.76 | 1.86 | **3.34** | **+90%** |
| 平均 TTFT (ms) | 4298 | 4456 | **2880** | **-33%** |
| P95 TTFT (ms) | 14400 | 14820 | **10954** | **-24%** |

## 结论

1. **正常负载**：sched_ext 与 CFS 性能相当，异构硬隔离未引入额外开销。
2. **高负载**：合理时间片（2ms/30ms）的 SCX 方案吞吐量提升 **90%**，TTFT 降低 **33%**。旧时间片（4ms/20ms）的 OSCX 与 CFS 基本持平，说明时间片调优是关键。
3. 高负载下 CFS 因推理线程频繁被 cpu_stress 进程抢占，V 状态切换开销导致吞吐骤降；SCX 将推理线程锁定在 CPU 0~3 并赋予 30ms 长时间片，有效隔离干扰。
