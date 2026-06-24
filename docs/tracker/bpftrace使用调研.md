# bpftrace使用调研

## 目标函数

V‑state 保存操作由 `riscv_v_vstate_save` 完成，定义在 `arch/riscv/include/asm/vector.h`（openEuler）：

```C
static __always_inline void riscv_v_vstate_save( //函数符号不导出到 kallsyms
        struct riscv_v_ext_state *vstate, struct pt_regs *regs)
{
    if ((regs->status & SR_VS) == SR_VS_DIRTY) {
        __riscv_v_vstate_save(vstate, regs);   // 实际保存 v0–v31
        __vstate_clean(regs);                  // 将 VS 置为 CLEAN
    }
}
```

### 目标函数调用路径

```Bash
__schedule() → context_switch() → switch_to()
              → __switch_to_vector() → riscv_v_vstate_save()
```

查找符号进行挂载：

```Bash
grep -E "riscv_v_vstate_save|__switch_to_vector|__vstate_clean" /proc/kallsyms
#输出为空，无独立符号

grep -E "riscv_v_vstate_save|__switch_to|__vstate_clean" /proc/kallsyms
ffffffff81125864 T __switch_to #有符号

bpftrace -e 'kprobe:__switch_to { printf("hit\n"); exit(); }' #尝试挂载
#报错 notrace
stdin:1:1-19: WARNING: __switch_to is not traceable (either non-existing, inlined, or marked as "notrace"); attaching to it will likely fail
kprobe:__switch_to { printf("hit\n"); exit(); }
~~~~~~~~~~~~~~~~~~
Attaching 1 probe...
[85602.503697][ T9841] trace_kprobe: Could not probe notrace function __switch_to
[85602.514970][ T9841] trace_kprobe: Could not probe notrace function __switch_to
cannot attach kprobe, Invalid argument
ERROR: Error attaching probe: 'kprobe:__switch_to'
```

目标函数还有其他几条调用路径，比如信号处理调用：

```C
arch_do_signal_or_restart()
  └── handle_signal()
        └── setup_rt_frame()
              └── setup_sigcontext()
                    └── save_v_state()
                          └── riscv_v_vstate_save()
```

但应该都不影响下属方案。

## 目前方案原理说明

在 `tracepoint:sched:sched_switch` 这个位置插桩。

`trace_sched_switch `的调用点在` __schedule() `函数`kernel/sched/core.c`中：

```C
static void __sched notrace __schedule(unsigned int sched_mode)
{
    // ...
    next = pick_next_task(rq, prev, &rf);
    clear_tsk_need_resched(prev);
    clear_preempt_need_resched();

    if (likely(prev != next)) {
        rq->nr_switches++;
        rq->curr = next;              // ← rq->curr 在这里已经变成 next
        ++*switch_count;

        trace_sched_switch(preempt, prev, next);   // ← tracepoint 在这里，且只在真实切换时触发
        rq = context_switch(rq, prev, next, &rf);  // ← switch_to() 宏（含 __switch_to_vector）在这里
    } else {
        // prev == next，不会触发 trace_sched_switch，也不会进入 context_switch
        rq_unpin_lock(rq, &rf);
        raw_spin_unlock_irq(&rq->lock);
    }
}
```

此时`riscv_v_vstate_save()` 还没跑，所以此刻 `task_pt_regs(prev)->status` 里的 VS 位，反映的是硬件在上一次异常/陷入内核时存入的真实值，还没被 `__vstate_clean()` 改写。且`trace_sched_switch()` 被包在 `if (likely(prev != next))` 里，`prev == next` 的自切换根本不会触发这个 tracepoint。

虽然此时 `rq->curr` 这个调度器内部字段在 tracepoint 触发前已经被赋值成 `next`，但架构层面真正的"当前任务"（bpftrace 里的 `curtask`，RISC\-V 上对应寄存器/栈推出来的 `task_struct`）要等到 `context_switch()` 内部 `switch_to()` 宏真正做寄存器/栈切换才会改变。所以此刻 `curtask` 读到的仍然是 `prev`。

只要在这个时间点判断 `(status & SR_VS) == SR_VS_DIRTY`，就和内核里 `riscv_v_vstate_save()` 内部"是否需要保存"的判断条件完全等价：这一刻读到 DIRTY，就意味着接下来 `context_switch()` 执行时必然会触发一次真实的向量寄存器保存。



## 脚本说明

### 概述

利用 bpftrace 挂载 `tracepoint:sched:sched_switch`，在每次任务切换前读取即将被切走的线程（prev）的 RISC\-V 向量状态。若 `sstatus.VS == DIRTY`，计数一次——这与内核 `riscv_v_vstate_save()`（定义于 `arch/riscv/include/asm/vector.h`）内部的判断条件完全相同，因此每条计数都对应一次真实发生的 V\-state 保存。

### 过滤条件

### 硬编码常量

脚本里 `regs = (struct pt_regs *)(t->stack + 16352 - 256)` 这一串数字，对应的是当前内核（`THREAD_SIZE=16384`、`sizeof(pt_regs)=288`、`status` 偏移 256）算出来的 `task_pt_regs(task)` 起始地址；得到这个地址后用 `$regs->status` 走正常的结构体字段访问。

换内核版本/配置时，`task->stack` 偏移、`THREAD_SIZE`、`sizeof(pt_regs)`、`status` 偏移这四个数字都可能变，建议重新核对后改这一行的计算公式。

```Bash
bpftrace -e 'BEGIN {
      printf("stack=%d ptregs=%d status=%d\n",
          offsetof(struct task_struct, stack),
          sizeof(struct pt_regs),
          offsetof(struct pt_regs, status));
      exit();
  }'
```

如果输出为：stack=80, pt\_regs=288, status=256，则脚本可用。

### 使用方法

```Bash
sudo apt install bpftrace
```

以llama用户端为例，执行以下指令启动llama\.cpp，这里为加快测试，限制仅输出20token，设置使用2核。

```Bash
cd ~/bin && \
  LD_LIBRARY_PATH=/root/bin:/opt/openEuler/gcc-toolset-14/root/usr/lib64 \
  ./llama-cli \
    --model /root/llama-3.2-1b-instruct-q4_k_m.gguf \
    -n 20 \
    -t 2
```

运行脚本

```Bash
bpftrace /root/vstate_trace.bt
```

待模型一轮输出结束后，结束脚本，可以观察到下图类似的输出结果。

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=Njk3Mzk2MDZlZGYxYzNiMWNlYmVkMTliNWU1MjJiNzNfOTNjNGQ3N2NlZGMxMzQ5NzUyZjU5YjY5YzllMzM2MTJfSUQ6NzY1NDg0NzQ0NzEzNzI5MTQ3OF8xNzgyMjg3MjA0OjE3ODIzNzM2MDRfVjM)

并发测试可开启llama server端，以2并发为例

```Bash
 LD_LIBRARY_PATH=/root/bin:/opt/openEuler/gcc-toolset-14/root/usr/lib64 \
  ./llama-server \
    -m /root/llama-3.2-1b-instruct-q4_k_m.gguf \
    -t 2 \
    --port 8080
```

待加载完毕后发送请求命令，这里同样的限制20token输出

```Bash
 curl -s http://127.0.0.1:8080/v1/chat/completions -H "Content-Type: application/json" -d '{"messages":[{"role":"user","content":"Explain vector extension"}],"max_tokens":20}' &
```

## 问题

1. 目前只做了save的插桩，restore如何做呢？

    可以的，类似于save，同一地点插桩，原理也类似save的做法，用bpftrace去做内核相同的判断，来"预测"是否有restore发生，restore次数理论上应该会≥save次数

2. Dirty V线程被抢占事件和riscv\_v\_vstate\_save是否是一回事【概念补充】

    Dirty V 线程被抢占就会触发一次 V 状态保存，riscv v state save函数就是 V 状态保存的执行函数。

3. 能够统计频次，但无法统计精准的保存和恢复开销\(耗时\) 

    1. 不精准的耗时如何统计

        - 不精准的方法依旧是挂载我目前挂载的点，我觉得统计频次可以一定程度上反映开销大小，此外在qemu上测试时间应该没有太大意义

    2. 精准的耗时如何统计

        - 精准耗时我目前没有找到可行的方案，除非修改内核——可以尝试之前的方案：将riscv v state save这个函数修改为不内联的？



