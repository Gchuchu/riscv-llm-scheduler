# riscv-llm-scheduler 调度优化

## 功能

实现 V 状态感知的调度优化策略：

### 保底方案（必做）
- [ ] CPU affinity：推理线程绑定指定核心
- [ ] cgroup cpuset：限制推理服务使用指定核心

### 进阶方案（可选）
- [ ] sched_ext 调度器实现
- [ ] sched_setattr 扩展调度提示

## 当前状态

🚧 项目开发中
