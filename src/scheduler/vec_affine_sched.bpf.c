// SPDX-License-Identifier: GPL-2.0
/*
 * vec_affine_sched - 针对 RISC-V 异构核心单线程 Uprobe 级精准绑定调度器 (Verifier 修复版)
 */

#include <scx/common.bpf.h>
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

char _license[] SEC("license") = "GPL";

#define SHARED_DSQ 0

/* 最优时间片定义 */
#define SLICE_DEFAULT_NS   (4 * 1000 * 1000LL)   /* 普通任务 4ms */
#define SLICE_INFER_NS     (20 * 1000 * 1000LL)  /* 推理计算线程 20ms */

/* 单线程级别的调度上下文 */
struct task_ctx {
    bool is_inference_worker; /* 只要调用过 mul_mat，永久标记为推理线程 */
    bool in_mul_mat;          /* 当前瞬间是否正处于 mul_mat 矩阵计算中 */
    u64  mul_mat_start_ts;    /* 进入 mul_mat 的时间戳 */
};

/* 改用 BPF_MAP_TYPE_HASH，Key 为线程 ID (u32 tid)，完美兼容 uprobe/uretprobe */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, u32);
    __type(value, struct task_ctx);
} task_ctx_map SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 8);
    __type(key, u32);
    __type(value, u64);
} stats SEC(".maps");

enum stat_idx {
    STAT_ENQUEUE_TOTAL,
    STAT_SELECT_WORKER_AI_CORE, /* 纯计算 Worker 线程绑定到 AI 核心 (CPU 0-3) */
    STAT_SELECT_OTHER_CORE,     /* 普通任务隔离到慢核 (CPU 4-7) */
    STAT_INFERENCE_THREADS,     /* 识别到的计算 Worker 线程数 */
    STAT_PROTECT_WINDOW,        /* 保护窗口触发次数 */
};

static __always_inline void stat_inc(enum stat_idx idx)
{
    u32 key = idx;
    u64 *cnt = bpf_map_lookup_elem(&stats, &key);
    if (cnt)
        (*cnt)++;
}

static __always_inline bool is_constrained_or_kthread(struct task_struct *p)
{
    return (p->flags & PF_KTHREAD) || (p->nr_cpus_allowed < 8);
}

/* ============================================================
 * Uprobe 探针：单线程精准识别与保护 (Hash Map 版本)
 * ============================================================ */
SEC("uprobe")
int BPF_UPROBE(vecsched_mul_mat_entry)
{
    u32 tid = (u32)bpf_get_current_pid_tgid();
    struct task_ctx *tctx = bpf_map_lookup_elem(&task_ctx_map, &tid);
    
    if (!tctx) {
        struct task_ctx new_ctx = {
            .is_inference_worker = true,
            .in_mul_mat = true,
            .mul_mat_start_ts = bpf_ktime_get_ns(),
        };
        bpf_map_update_elem(&task_ctx_map, &tid, &new_ctx, BPF_ANY);
        stat_inc(STAT_INFERENCE_THREADS);
    } else {
        if (!tctx->is_inference_worker) {
            tctx->is_inference_worker = true;
            stat_inc(STAT_INFERENCE_THREADS);
        }
        tctx->in_mul_mat = true;
        tctx->mul_mat_start_ts = bpf_ktime_get_ns();
    }
    return 0;
}

SEC("uretprobe")
int BPF_URETPROBE(vecsched_mul_mat_exit)
{
    u32 tid = (u32)bpf_get_current_pid_tgid();
    struct task_ctx *tctx = bpf_map_lookup_elem(&task_ctx_map, &tid);
    if (tctx) {
        tctx->in_mul_mat = false;
    }
    return 0;
}

/* ============================================================
 * select_cpu：Prev-CPU 热 Cache 归巢 + AI 专核绑定
 * ============================================================ */
s32 BPF_STRUCT_OPS(vec_select_cpu, struct task_struct *p, s32 prev_cpu, u64 wake_flags)
{
    bool is_idle;

    if (is_constrained_or_kthread(p)) {
        return scx_bpf_select_cpu_dfl(p, prev_cpu, wake_flags, &is_idle);
    }

    u32 tid = (u32)p->pid;
    struct task_ctx *tctx = bpf_map_lookup_elem(&task_ctx_map, &tid);

    /* A 策略：真正触发过 Uprobe 的 Worker 计算线程，硬锁定在 CPU 0 ~ 3 */
    if (tctx && tctx->is_inference_worker) {
        /* 第一优先：Prev-CPU 归巢，保留 L1/L2 Cache 和 Vector 寄存器热度 */
        if (prev_cpu >= 0 && prev_cpu <= 3) {
            if (scx_bpf_test_and_clear_cpu_idle(prev_cpu)) {
                stat_inc(STAT_SELECT_WORKER_AI_CORE);
                return prev_cpu;
            }
        }

        /* 第二优先：选择 CPU 0-3 中其他空闲的 AI 核心 */
        for (s32 cpu = 0; cpu <= 3; cpu++) {
            if (scx_bpf_test_and_clear_cpu_idle(cpu)) {
                stat_inc(STAT_SELECT_WORKER_AI_CORE);
                return cpu;
            }
        }

        /* 第三优先：回退原核排队 */
        if (prev_cpu >= 0 && prev_cpu <= 3) {
            stat_inc(STAT_SELECT_WORKER_AI_CORE);
            return prev_cpu;
        }

        stat_inc(STAT_SELECT_WORKER_AI_CORE);
        return ((u32)p->pid) & 3;
    }

    /* B 策略：其他普通任务，隔离在 CPU 4 ~ 7 */
    if (prev_cpu >= 4 && prev_cpu <= 7) {
        if (scx_bpf_test_and_clear_cpu_idle(prev_cpu)) {
            stat_inc(STAT_SELECT_OTHER_CORE);
            return prev_cpu;
        }
    }

    for (s32 cpu = 4; cpu <= 7; cpu++) {
        if (scx_bpf_test_and_clear_cpu_idle(cpu)) {
            stat_inc(STAT_SELECT_OTHER_CORE);
            return cpu;
        }
    }

    if (prev_cpu >= 4 && prev_cpu <= 7) {
        stat_inc(STAT_SELECT_OTHER_CORE);
        return prev_cpu;
    }

    stat_inc(STAT_SELECT_OTHER_CORE);
    return 4 + (((u32)p->pid) & 3);
}

/* ============================================================
 * enqueue：唤醒插队 SCX_ENQ_HEAD
 * ============================================================ */
void BPF_STRUCT_OPS(vec_enqueue, struct task_struct *p, u64 enq_flags)
{
    u64 enq_flags_final = enq_flags;

    stat_inc(STAT_ENQUEUE_TOTAL);

    if (is_constrained_or_kthread(p)) {
        scx_bpf_dsq_insert(p, SCX_DSQ_LOCAL, SLICE_DEFAULT_NS, enq_flags);
        return;
    }

    u32 tid = (u32)p->pid;
    struct task_ctx *tctx = bpf_map_lookup_elem(&task_ctx_map, &tid);

    if (tctx && tctx->is_inference_worker) {
        /* 屏障唤醒直插队头，实现微秒级零延迟唤醒 */
        if (enq_flags & SCX_ENQ_WAKEUP)
            enq_flags_final |= SCX_ENQ_HEAD;

        scx_bpf_dsq_insert(p, SCX_DSQ_LOCAL, SLICE_INFER_NS, enq_flags_final);
    } else {
        scx_bpf_dsq_insert(p, SHARED_DSQ, SLICE_DEFAULT_NS, enq_flags_final);
    }
}

/* ============================================================
 * tick：单线程精准计算保护（不影响 Barrier）
 * ============================================================ */
void BPF_STRUCT_OPS(vec_tick, struct task_struct *p)
{
    if (!p)
        return;

    u32 tid = (u32)p->pid;
    struct task_ctx *tctx = bpf_map_lookup_elem(&task_ctx_map, &tid);
    /* 只有当该特定线程此时此刻正处于 mul_mat 算子计算内部时，才跳过抢占 */
    if (tctx && tctx->in_mul_mat) {
        if (bpf_ktime_get_ns() - tctx->mul_mat_start_ts < SLICE_INFER_NS) {
            stat_inc(STAT_PROTECT_WINDOW);
            return; /* 免受抢占打扰 */
        }
    }
}

void BPF_STRUCT_OPS(vec_dispatch, s32 cpu, struct task_struct *prev)
{
    scx_bpf_dsq_move_to_local(SHARED_DSQ);
}

s32 BPF_STRUCT_OPS_SLEEPABLE(vec_init)
{
    scx_bpf_create_dsq(SHARED_DSQ, -1);
    bpf_printk("vec_affine_sched initialized\n");
    return 0;
}

void BPF_STRUCT_OPS_SLEEPABLE(vec_exit, struct scx_exit_info *ei)
{
    bpf_printk("vec_affine_sched exited: %s\n", ei->msg);
}

SEC(".struct_ops")
struct sched_ext_ops vec_affine_ops = {
    .select_cpu = (void *)vec_select_cpu,
    .enqueue    = (void *)vec_enqueue,
    .dispatch   = (void *)vec_dispatch,
    .tick       = (void *)vec_tick,
    .init       = (void *)vec_init,
    .exit       = (void *)vec_exit,
    .name       = "vec_affine_sched",
    .flags      = SCX_OPS_ALLOW_QUEUED_WAKEUP,
};