# riscv-llm-scheduler 量化分析工具

## 功能

通过 eBPF / kprobe 挂载内核函数 `riscv_vstate_save`，统计：
- V 状态保存/恢复调用频次
- 每次耗时
- dirty V 线程被抢占事件

## 输出

V 状态切换开销与并发数、模型规模的量化关系。

## 当前状态

🚧 项目开发中
