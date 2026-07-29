# bench_runner.py 使用说明

RISC-V LLM 推理 V-state 开销量化自动化测试脚本。

## 依赖

**宿主机：**
```bash
pip install paramiko requests numpy
```

**板卡：**
```bash
apt install python3-requests python3-numpy curl bpftrace
```

---

## 快速开始

打开 `bench_runner.py`，修改顶部 CONFIG 区，然后运行：

```bash
python3 bench_runner.py
```

---

## 只需要改这些

```python
# ===== 板卡连接 =====
SSH_HOST   = "169.254.20.26"    # 板卡 IP
SSH_USER   = "root"
SSH_PASS   = "123"

# ===== 路径 =====
LLAMA_DIR  = "/root/llama/bin"                           # llama.cpp 安装目录
MODEL      = "/root/llama-3.2-3b-instruct-q4_k_m.gguf"  # 模型文件路径
BPFTRACE   = "/root/vstate_trace_cpu.bt"                 # bpftrace 脚本

# ===== 测试参数 =====
THREADS    = 4             # -t / -tb
BIND_CORES = 4             # 绑定核心数（从核7往回数：1→7  2→6-7  4→4-7  6→2-7）
CONTEXT    = 2048          # -c
PARALLEL   = 4             # --parallel / slot 数
MAX_TOKENS = 32            # 输出 token 数
TRIALS     = 3             # 每组跑几轮
GROUP_NAME = "group6"      # CSV 命名前缀

# ===== Prompt 长度（三选一：64 / 128 / 256）=====
PROMPT_LENGTH = 128

# ===== 方法执行顺序（默认全部，不需要的方法注释掉）=====
METHODS = [
    "taskset",
    "cpuset",
    "partition-root",
    "systemd-scope",
    "taskset-chrt-b",
    "taskset-chrt-f",
]
```

---

## 速查表

| 场景 | 需要改 |
|------|--------|
| 换板卡 | `SSH_HOST` `SSH_USER` `SSH_PASS` |
| 换路径 | `LLAMA_DIR` `MODEL` |
| 换组 | `MODEL` `THREADS` `BIND_CORES` `CONTEXT` `PARALLEL` `MAX_TOKENS` `PROMPT_LENGTH` `GROUP_NAME` |
| 只测某几个方法 | 编辑 `METHODS` 列表 |
| 自定义输出目录 | CSV 固定输出到 `D:/比赛/results/` |

---

## 七种方法

| 方法 | 命令 | 隔离方向 | 清理 |
|------|------|:---:|:---:|
| `taskset` | `taskset -c X-Y ./llama-server ...` | 单向 | 无 |
| `cpuset` | 建 cgroup + echo cpuset.cpus + PID 移入 | 单向 | 删 cgroup |
| `cpu-range` | `taskset` + `--cpu-strict 1` | 单向 | 无 |
| `partition-root` | cgroup + `echo root > cpuset.cpus.partition` | **双向** | 删 cgroup |
| `systemd-scope` | `AllowedCPUs` + `systemd-run --scope` | 单向(等效) | 清 AllowedCPUs |
| `taskset-chrt-b` | `taskset -c X-Y chrt -b 0` | 单向+SCHED_BATCH | 无 |
| `taskset-chrt-f` | `taskset -c X-Y chrt -f 90` | 单向+SCHED_FIFO | 无 |

## 输出文件

每个方法跑完生成两个 CSV：

| 文件 | 内容 |
|------|------|
| `results/{group}_{method}.csv` | 汇总，每 trial 一行（TPS/TTFT/V-saves/Per-CPU） |
| `results/{group}_{method}_detail.csv` | 明细，每请求一行（TTFT/TPS/E2E） |

## 工作流程

```
对每个 METHODS 中的方法:
  ├─ 3 次 trial:
  │   ├─ 清理 + 启动 server（按方法配置）
  │   ├─ 启动 bpftrace
  │   ├─ 板子后台发 50 个并发 HTTP 请求 → 写 JSON 文件
  │   ├─ 每 5s 轮询 JSON 直到完成
  │   ├─ 停 bpftrace + 杀 server
  │   └─ 解析结果
  ├─ 保存汇总 + 明细 CSV
  └─ 清理现场（cgroup/systemd 痕迹）
```

## 超时设置

| 层 | 超时 | 位置 |
|----|------|------|
| 单个 HTTP 请求 | 3600s | `/tmp/b.py` 内 `requests.post(timeout=3600)` |
| SSH 通道 | 3600s | `ssh_exec` 函数 |
| 轮询等待 | 720 × 5s = 60min | `run_trial` 中 `range(720)` |
| 方法间清理 | 每次方法结束 | `cleanup()` + 额外 systemd/cgroup 清理 |

## 注意事项

1. `systemd-scope` 测试后会自动清 `AllowedCPUs`
2. `partition-root` / `cpuset` 测试后会自动删 cgroup
3. 每次 trial 都 pkill 重开 server
4. Prompt 只有 64 / 128 / 256 token 三种
5. 固定 50 个并发请求，`--parallel` 控制 server slot 数
