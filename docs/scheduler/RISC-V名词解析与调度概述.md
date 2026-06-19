# 赛题名词解析

## Hart（Hardware Thread）

Hart 是 RISC-V 架构中的基本执行单元，全称 Hardware Thread。每个 hart 拥有独立的程序计数器（PC）和寄存器组，是 RISC-V 中对应其他架构"核心"或"逻辑处理器"的概念。一个物理处理器芯片可以包含多个 hart，操作系统将每个 hart 视为一个独立的调度资源。

## V 状态（Vector State）

V 状态指一个 hart 上全部向量相关寄存器的完整快照，包括每个 hart 拥有的 32 个向量寄存器（v0~v31）以及 vstart、vtype、vl、vcsr 四个状态寄存器，具体描述如下：

- **vstart (Vector Start Index)**：向量运算恢复位置，记录向量操作因异常中断后下一个要执行的元素索引，用于可重启向量操作；
- **vtype (Vector Type)**：编码当前向量配置：vsew（单个元素宽度）、vlmul（寄存器组合系数 LMUL）、vta（尾部不可知位）、vma（掩码不可知位）；
- **vl (Vector Length)**：当前有效元素个数，由 vsetvl 类指令设定；
- **vcsr (Vector CSR)**：向量控制状态寄存器，包含向量定点舍入模式（vxrm）和向量定点溢出标志（vxsat）。

## CPU 上下文切换过程

Linux 在以下情况触发上下文切换：

- **时间片耗尽**：CFS 调度器的 scheduler_tick() 判断当前任务已用完调度配额，调用 resched_curr() 设置 TIF_NEED_RESCHED 标志
- **阻塞系统调用**：任务主动调用 sleep、wait、I/O 等进入等待队列
- **更高优先级任务唤醒**：实时任务或被唤醒的高优先级任务抢占当前任务

当内核决定切换时，调用 context_switch()，其中 switch_to() 宏负责 hart 状态的切换。RISC-V 的通用寄存器保存发生在两个层次：

### 陷入入口保存（trap entry）

从用户态陷入内核时（无论是中断还是系统调用），内核在线程的内核栈上构造一个 pt_regs 结构，保存所有 32 个通用整数寄存器（x0~x31）以及 sepc（异常返回地址）、sstatus、stval、scause 等 S 模式 CSR。这是完整的用户态执行现场。

### 内核线程上下文保存（switch_to）

在内核中从一个任务切换到另一个任务时，保存的是被调用者保存寄存器（callee-saved registers，即 s0~s11、sp、ra）以及当前的 sstatus，存入 task_struct 中的 thread 字段（thread.ra、thread.sp 等）。

### 具体流程如下

```
用户任务 A 运行
      ↓ 时钟中断触发抢占
trap entry（汇编 _traps）
      ↓ 保存 pt_regs 到内核栈（x0-x31, sepc, sstatus...）
      ↓ 若 VS=dirty （惰性）保存 V 状态
schedule() → context_switch()
      ↓ switch_to(A, B)
      ↓ 保存 A 的 callee-saved 寄存器到 thread_info
      ↓ 切换页表（ASID，satp 寄存器）
      ↓ 恢复 B 的 callee-saved 寄存器
      ↓ （惰性）延迟恢复 B 的 V 状态至 B 首次执行向量指令
trap return（sret）
      ↓ 恢复 B 的 pt_regs
用户任务 B 运行
```

## 向量寄存器的保存与恢复机制

### VS 状态位

VS 状态位为 sstatus CSR 中的 2 bit 字段，位于 sstatus[10:9]。

任何写向量寄存器或向量 CSR 的指令都会由硬件自动将 VS 置为 Dirty。

### 惰性上下文管理策略（lazy save / restore）

Linux 内核对向量状态采用惰性策略，核心逻辑如下：

#### 保存侧（任务被切换出）

当任务切换时，内核检查 VS 位：

```
if (sstatus.VS == Dirty) {
    riscv_vstate_save(task, regs);  // 将 v0-v31 和向量 CSR 写入 task->thread.vstate
    sstatus.VS = Clean;
} else if (sstatus.VS == Clean) {
    // 无需操作——之前已保存且未修改
} else if (sstatus.VS == Off) {
    // 该任务未使用向量扩展，跳过
}
```

#### 恢复侧（任务被切换入）

内核并不立即恢复向量寄存器，而是将 V 状态恢复推迟到该任务第一次执行向量指令时。

```
switch_to(B) 完成后：
    task->thread.vstate 中保存的是 B 上次被切换出时的向量寄存器快照
    但硬件向量寄存器中可能仍是 A 的数据
    VS 保持为 Clean 或 Off

B 首次执行向量指令：
    硬件检测到 VS != Dirty，触发 illegal instruction 异常
    内核在异常处理中：
    if (VS == Clean) {
        riscv_vstate_restore(task, regs);  // 从 task->thread.vstate 恢复 v0-v31 和向量 CSR
        sstatus.VS = Dirty;                // 允许后续向量指令直接执行
    } else if (VS == Off) {
        // 使能向量扩展，初始化向量寄存器为默认值
        sstatus.VS = Initial;
    }
```

## 实现参考
[RISCV V状态手动切换](https://lkml.iu.edu/2603.2/02195.html)
