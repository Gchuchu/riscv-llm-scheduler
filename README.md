# riscv-llm-scheduler

面向 RISC-V Vector 扩展的大语言模型推理并发调度优化（OS 高校赛题）

## 项目概述

LLM 推理是典型的高强度持续向量计算场景。在 RISC-V 架构中，每个 hart 拥有 32 个向量寄存器（v0–v31），单次完整 V 状态保存需要 512 字节以上的内存写入。Linux 默认调度策略（CFS）对向量密集型推理线程与普通线程一视同仁，导致推理线程在计算关键路径上被频繁抢占，积累的 V 状态切换开销造成整体吞吐下降与尾延迟恶化。

本项目的目标是：量化 V 状态上下文切换开销，并实现至少一种 V 状态感知的调度优化策略。

## 目录结构

```
riscv-llm-scheduler/
├── docs/
│   ├── competition/        # 赛题说明与要求
│   ├── scheduler/          # 调度器调研（CFS、其他策略、性能损失分析）
│   ├── tracker/            # 跟踪工具与性能指标（perf、kprobe、affinity）
│   ├── setup/              # 环境搭建指南（QEMU、板卡选型、llama.cpp）
│   └── benchmark/          # 性能评测方法与指标定义
├── src/
│   ├── tracker/            # 量化分析工具（eBPF/kprobe 插桩）
│   ├── scheduler/          # 调度优化实现（affinity/sched_ext）
│   └── benchmark/          # 性能评测脚本
├── scripts/                # 构建与运行脚本
├── results/                # 测试结果与数据
├── hardware/               # 硬件配置与说明
└── .gitignore
```

## 快速开始

### 环境要求

- 主机：Linux（推荐 openEuler 24.03 RISC-V 或 Ubuntu）
- QEMU 9.x（用于功能验证）
- RISC-V RVV 1.0 硬件（用于性能评测）

### 环境搭建

参见 [docs/setup/](docs/setup/) 下的环境搭建指南。

## 赛题要求

| 模块 | 分值 | 说明 |
|------|------|------|
| 量化分析工具 | 10 分 | eBPF/kprobe 插桩统计 V 状态切换开销 |
| 调度优化实现 | 15 分 | CPU affinity / cgroup cpuset / sched_ext |
| 性能评测 | 10 分 | TPS、平均 TTFT、P95 TTFT |
| 技术报告 | 40 分 | 设计方案 + 实现方案 + 实验结果 + 特色创新 |
| 开发过程 | 20 分 | Git 规范 + README + 演示视频 |

## License

本项目仅供学习研究使用。
