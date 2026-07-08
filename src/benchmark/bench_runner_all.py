#!/usr/bin/env python3
"""
RISC-V LLM V-state Benchmark Runner — 可复用自动化测试脚本
============================================================
用法: 修改下方 CONFIG 区域，然后 python bench_runner.py 即可。

功能:
  - SSH 连接板卡，自动启动/停止 llama-server + bpftrace
  - 发送并发 HTTP 请求，采集 TPS / TTFT / P95
  - 采集 bpftrace V-state save/restore 计数（per-CPU）
  - 结果保存为本地 CSV

依赖: pip install paramiko requests numpy (宿主机)
      板卡需有: python3 requests numpy curl bpftrace
"""

import paramiko, time, json, statistics, csv, os, sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===================================================================
#  「用户只需修改这里」— 测试组配置
# ===================================================================

# --- 板卡连接 ---
SSH_HOST   = "169.254.20.26"
SSH_USER   = "root"
SSH_PASS   = "123"

# --- 路径（板卡上的绝对路径）---
LLAMA_DIR  = "/root/llama/bin"
MODEL_1B   = "/root/llama-3.2-1b-instruct-q4_k_m.gguf"
MODEL_3B   = "/root/llama-3.2-3b-instruct-q4_k_m.gguf"
BPFTRACE   = "/root/vstate_trace_cpu.bt"
BENCH_PY   = "/tmp/b.py"
VSTATE_LOG = "/tmp/vstate_result.log"
SERVER_LOG = "/tmp/llama-server.log"

TRIALS     = 3
RESULTS_DIR = "D:/比赛/results"

# --- 方法执行顺序 ---
METHODS = [
    "taskset",
    "cpuset",
    "partition-root",
    "systemd-scope",
    "taskset-chrt-b",
    "taskset-chrt-f",
]

# --- 十组测试 ---
GROUPS = [
    # {name, model, t, cores, plen, out, ctx, par}
    {"name": "group1",  "model": "1B", "t": 1, "cores": 1, "plen": 64,  "out": 32,  "ctx": 1024, "par": 2},
    {"name": "group2",  "model": "1B", "t": 2, "cores": 2, "plen": 64,  "out": 32,  "ctx": 1024, "par": 2},
    {"name": "group3",  "model": "3B", "t": 4, "cores": 4, "plen": 128, "out": 64,  "ctx": 2048, "par": 2},
    {"name": "group4",  "model": "3B", "t": 4, "cores": 4, "plen": 128, "out": 32,  "ctx": 2048, "par": 4},
    {"name": "group5",  "model": "3B", "t": 4, "cores": 4, "plen": 64,  "out": 64,  "ctx": 2048, "par": 4},
    {"name": "group6",  "model": "3B", "t": 4, "cores": 4, "plen": 128, "out": 64,  "ctx": 2048, "par": 4},
    {"name": "group7",  "model": "3B", "t": 4, "cores": 4, "plen": 256, "out": 64,  "ctx": 2048, "par": 4},
    {"name": "group8",  "model": "3B", "t": 4, "cores": 4, "plen": 128, "out": 128, "ctx": 2048, "par": 4},
    {"name": "group9",  "model": "3B", "t": 4, "cores": 4, "plen": 128, "out": 64,  "ctx": 2048, "par": 8},
    {"name": "group10", "model": "3B", "t": 4, "cores": 4, "plen": 256, "out": 256, "ctx": 4096, "par": 4},
]

# --- Prompt — 只支持三种固定长度：64, 128, 256 ---
PROMPT_64 = (
    "Describe the RISC-V vector extension and how its scalable register design improves AI inference "
    "performance for large language models compared to older SIMD instruction sets. The key advantage "
    "is the vector length agnostic approach allowing software to adapt to different hardware "
    "capabilities. This provides significant throughput gains for matrix multiplication "
    "in neural networks and transformer attention mechanisms very."
)

PROMPT_128 = (
    "Describe the RISC-V vector extension in detail. Explain the vector register file with its "
    "thirty two configurable registers and variable width design. Cover the vector length agnostic "
    "programming model and how the same code runs on different hardware. Discuss strided and indexed "
    "memory access patterns and reduction operations. Explain how these features benefit deep learning "
    "workloads including convolutions and matrix operations for large language model inference. "
    "The architecture provides masked operations for conditional execution and permutation instructions "
    "for data rearrangement. These features make vector code efficient across many scientific "
    "computing applications. RISC-V vectors offer better forward compatibility than "
    "fixed width SIMD extensions like SSE or AVX and other CPUs now."
)

PROMPT_256 = (
    "Provide a detailed explanation of the RISC-V vector extension and its benefits for machine "
    "learning workloads. First, describe the vector register file including all thirty two registers, "
    "their configurable width, and the vtype configuration register. Second, explain vector length "
    "agnostic programming and how it enables portable code across different processor implementations "
    "without recompilation. Third, cover memory access patterns including unit stride, strided, indexed, "
    "and segmented loads and stores. Fourth, discuss the computation model including integer and "
    "floating point arithmetic, widening and narrowing operations, and reduction instructions for "
    "accumulating results. Fifth, analyze the specific advantages for neural network inference such "
    "as attention mechanism computation, matrix multiplication in transformer models, and large "
    "language model token generation. Compare the performance to traditional SIMD approaches like "
    "AVX and NEON. Finally, explain how the operating system handles vector register context switches "
    "and the costs involved in saving and restoring the large register file during multitasking. "
    "The lazy context save mechanism in the Linux kernel reduces overhead by tracking dirty vector "
    "state through sstatus register bits. Only modified registers need preservation during task "
    "preemption. This hardware and software interaction is critical for maintaining low latency in "
    "production inference systems. Future compiler and kernel improvements may further reduce "
    "context switch costs for vector intensive workloads."
    " Overall this greatly benefits AI deployment."
)

# 根据需要的长度自动选 prompt
def _pick_prompt(length):
    """length: 64, 128, 或 256"""
    prompts = {64: PROMPT_64, 128: PROMPT_128, 256: PROMPT_256}
    if length not in prompts:
        raise ValueError(f"PROMPT_LENGTH must be 64, 128, or 256, got {length}")
    return prompts[length]

# BIND_METHOD 由 METHODS 列表自动遍历
# MODEL/THREADS/BIND_CORES/CONTEXT/PARALLEL/MAX_TOKENS/PROMPT 由 GROUPS 自动遍历

# --- 额外选项 ---
USE_CPU_STRICT = False    # True = server 启动命令加 --cpu-strict 1

# ===================================================================
#  CPU 掩码计算（per-group 调用）
# ===================================================================

def _cpu_mask(n_cores):
    start = 8 - n_cores
    return f"{start}-7"

def _cpu_hex(n_cores):
    val = 0
    for c in range(8 - n_cores, 8):
        val |= (1 << c)
    return format(val, 'x')

def _apply_group(g):
    """将组配置应用到全局变量"""
    global MODEL, THREADS, BIND_CORES, CONTEXT, PARALLEL, MAX_TOKENS
    global GROUP_NAME, PROMPT, CPU_MASK, CPU_HEX, TARGET_CPUS
    MODEL = MODEL_1B if g["model"] == "1B" else MODEL_3B
    THREADS = g["t"]
    BIND_CORES = g["cores"]
    CONTEXT = g["ctx"]
    PARALLEL = g["par"]
    MAX_TOKENS = g["out"]
    GROUP_NAME = g["name"]
    PROMPT = _pick_prompt(g["plen"])
    CPU_MASK = _cpu_mask(g["cores"])
    CPU_HEX = _cpu_hex(g["cores"])
    TARGET_CPUS = set(range(8 - g["cores"], 8))

# ===================================================================
#  SERVER 启动命令（根据 BIND_METHOD 自动生成，一般无需手动改）
# ===================================================================

def get_server_cmd():
    """根据 BIND_METHOD 返回完整的 server 启动命令"""
    binary = f"{LLAMA_DIR}/llama-server "
    args = (
        f"-m {MODEL} "
        f"-t {THREADS} -tb {THREADS} "
        f"--parallel {PARALLEL} -c {CONTEXT} "
        f"--cont-batching --port 8080 "
    )
    env = f"LD_LIBRARY_PATH={LLAMA_DIR} "
    if BIND_METHOD == "taskset":
        prefix = f"taskset -c {CPU_MASK} "
    elif BIND_METHOD == "cpu-range":
        prefix = f"taskset -c {CPU_MASK} "
        args += "--cpu-strict 1 "
    else:
        prefix = ""
    # 绑核 + 调度
    if BIND_METHOD == "taskset-chrt-b":
        prefix = f"taskset -c {CPU_MASK} chrt -b 0 "
    elif BIND_METHOD == "taskset-chrt-f":
        prefix = f"taskset -c {CPU_MASK} chrt -f 90 "

    if USE_CPU_STRICT:
        args += "--cpu-strict 1 "
    return env + "nohup " + prefix + binary + args + f"> {SERVER_LOG} 2>&1 &"

# ===================================================================
#  每个 trial 发送的请求数（建议 ≥ 20，P95 才有统计意义）
# ===================================================================
CONCURRENT = 50            # 固定每次发 50 个并发请求

# ===================================================================
#  SETUP / TEARDOWN HOOKS — 根据 BIND_METHOD 自动选择
# ===================================================================

def pre_server_hook(ssh):
    """启动 server 前执行的额外命令"""
    if BIND_METHOD == "cpuset":
        ssh.exec_command("echo '+cpuset' > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true")
        ssh.exec_command("mkdir -p /sys/fs/cgroup/llm")
        ssh.exec_command(f"echo '{CPU_MASK}' > /sys/fs/cgroup/llm/cpuset.cpus")
        ssh.exec_command("echo 0 > /sys/fs/cgroup/llm/cpuset.mems")
    elif BIND_METHOD == "partition-root":
        ssh.exec_command(f"echo '+cpuset' > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null")
        ssh.exec_command(f"mkdir -p /sys/fs/cgroup/llm")
        ssh.exec_command(f"echo '{CPU_MASK}' > /sys/fs/cgroup/llm/cpuset.cpus")
        ssh.exec_command(f"echo 0 > /sys/fs/cgroup/llm/cpuset.mems")
        ssh.exec_command(f"echo root > /sys/fs/cgroup/llm/cpuset.cpus.partition 2>/dev/null")
    elif BIND_METHOD == "systemd-scope":
        other_mask = f"0-{7 - BIND_CORES}"  # 非目标核给系统用
        ssh.exec_command(f"systemctl set-property --runtime init.scope   AllowedCPUs={other_mask} 2>/dev/null || true")
        ssh.exec_command(f"systemctl set-property --runtime system.slice AllowedCPUs={other_mask} 2>/dev/null || true")
        ssh.exec_command(f"systemctl set-property --runtime user.slice   AllowedCPUs={other_mask} 2>/dev/null || true")
        ssh.exec_command(f"systemctl set-property --runtime llm.slice    AllowedCPUs={CPU_MASK} 2>/dev/null || true")

def post_server_hook(ssh):
    """server 启动后执行的额外命令（如将 PID 移入 cgroup）"""
    if BIND_METHOD in ("cpuset", "partition-root"):
        time.sleep(3)
        ssh.exec_command(f"PID=$(pgrep -f llama-server | head -1); echo $PID > /sys/fs/cgroup/llm/cgroup.procs 2>/dev/null || true")

def get_server_cmd_wrapper(ssh):
    """对于需要包装启动命令的方法（如 systemd-scope），在这里处理"""
    if BIND_METHOD == "systemd-scope":
        return (
            f"nohup systemd-run --scope -p Slice=llm.slice "
            f"-E LD_LIBRARY_PATH={LLAMA_DIR} "
            f"{LLAMA_DIR}/llama-server "
            f"-m {MODEL} -t {THREADS} -tb {THREADS} "
            f"--parallel {PARALLEL} -c {CONTEXT} "
            f"--cont-batching --port 8080 "
            f"> {SERVER_LOG} 2>&1 &"
        )
    return get_server_cmd()

def cleanup_hook(ssh):
    """每次 cleanup 时额外执行的清理"""
    if BIND_METHOD == "systemd-scope":
        ssh.exec_command("systemctl set-property --runtime init.scope   AllowedCPUs= 2>/dev/null || true")
        ssh.exec_command("systemctl set-property --runtime system.slice AllowedCPUs= 2>/dev/null || true")
        ssh.exec_command("systemctl set-property --runtime user.slice   AllowedCPUs= 2>/dev/null || true")
        ssh.exec_command("systemctl set-property --runtime llm.slice    AllowedCPUs= 2>/dev/null || true")
        ssh.exec_command("systemctl stop llm.scope 2>/dev/null || true")
        ssh.exec_command("systemctl reset-failed 2>/dev/null || true")
    elif BIND_METHOD in ("cpuset", "partition-root"):
        ssh.exec_command("echo member > /sys/fs/cgroup/llm/cpuset.cpus.partition 2>/dev/null || true")
        ssh.exec_command("rmdir /sys/fs/cgroup/llm 2>/dev/null || true")
        ssh.exec_command("echo '-cpuset' > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true")

# ===================================================================
#  以下为核心流程，通常不需要修改
# ===================================================================

def ssh_exec(ssh, cmd, timeout=7200):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode(errors="replace")

def deploy_bench_script(ssh):
    """把 HTTP benchmark 脚本部署到板卡上（只执行一次），结果写 /tmp/bench_result.json"""
    code = r"""
import requests,json,time,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
u="http://localhost:8080/v1/chat/completions"
p={"messages":[{"role":"user","content":sys.argv[1]}],"max_tokens":int(sys.argv[2]),"temperature":0}
R=[]
def f(i):
 t=time.time();r=requests.post(u,json=p,timeout=7200);d=r.json()
 d["_e"]=(time.time()-t)*1000;d["_i"]=i;return d
t0=time.time()
with ThreadPoolExecutor(max_workers=int(sys.argv[3])) as P:
 for x in as_completed([P.submit(f,i) for i in range(int(sys.argv[3]))]):R.append(x.result())
out={"wall":time.time()-t0,"rows":[]}
for r in R:
 t=r.get("timings",{});u=r.get("usage",{})
 out["rows"].append({"i":r["_i"],"tt":t.get("prompt_ms",0),"tp":t.get("predicted_per_second",0),"tk":u.get("completion_tokens",t.get("predicted_n",0)),"e2":r["_e"]})
with open("/tmp/bench_result.json","w") as f: json.dump(out,f)
"""
    ssh_exec(ssh, f"cat > {BENCH_PY} << 'EOFBENCH'\n{code}\nEOFBENCH")

def cleanup(ssh):
    ssh_exec(ssh, "pkill -9 llama-server 2>/dev/null || true")
    ssh_exec(ssh, "pkill -9 bpftrace 2>/dev/null || true")
    cleanup_hook(ssh)
    time.sleep(2)

def wait_ready(ssh):
    for i in range(30):
        time.sleep(2)
        out = ssh_exec(ssh, "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/health")
        if out.strip() == "200":
            return True
    return False

def parse_vstate(ssh):
    v = ssh_exec(ssh, f"cat {VSTATE_LOG}")
    saves, restores = 0, 0
    cpu_s = {}
    for line in v.split("\n"):
        line = line.strip()
        if line.startswith("@saves_total:"):
            try: saves = int(line.split(":")[1].strip())
            except: pass
        if line.startswith("@restores_total:"):
            try: restores = int(line.split(":")[1].strip())
            except: pass
    for line in v.split("\n"):
        line = line.strip()
        if line.startswith("@cpu_saves[") and "]:" in line:
            try:
                p = line.split("@cpu_saves[")[1]
                cpu = int(p.split("]")[0])
                cnt = int(p.split(":")[1].strip())
                cpu_s[cpu] = cnt
            except: pass
    return saves, restores, cpu_s

def monitor_server(ssh, stop_flag):
    """实时打印 server log 中的 token 生成进度"""
    while not stop_flag[0]:
        time.sleep(4)
        log = ssh_exec(ssh, f"tail -1 {SERVER_LOG}")
        if any(k in log for k in ["eval", "token", "slot", "prompt", "print_timing"]):
            print(f"    [srv] {log.strip()[-150:]}")

def run_trial(ssh):
    """执行一次 trial，返回数据 dict"""
    # 1. Clean
    cleanup(ssh)

    # 2. Pre-hook
    pre_server_hook(ssh)

    # 3. Start server
    ssh_exec(ssh, get_server_cmd_wrapper(ssh))

    # 4. Post-hook
    post_server_hook(ssh)

    # 5. Wait ready
    if not wait_ready(ssh):
        print("  FAIL: server not ready")
        return None, []
    print("  Server ready")

    # 6. Start bpftrace
    ssh_exec(ssh, f"nohup bpftrace {BPFTRACE} > {VSTATE_LOG} 2>&1 &")
    time.sleep(3)

    # 7. Send requests (background on board, write to file)
    print(f"  Sending {CONCURRENT} requests...")
    ssh_exec(ssh, f"rm -f /tmp/bench_result.json; nohup python3 {BENCH_PY} '{PROMPT}' {MAX_TOKENS} {CONCURRENT} > /dev/null 2>&1 &")
    time.sleep(3)

    # Poll for completion (check server log + bench result file)
    import threading as th
    stop_flag = [False]
    mt = th.Thread(target=monitor_server, args=(ssh, stop_flag), daemon=True)
    mt.start()

    for _ in range(1800):  # max 1800 * 5s = 150 minutes
        time.sleep(5)
        done = ssh_exec(ssh, "cat /tmp/bench_result.json 2>/dev/null | head -1")
        if done.strip():
            break

    stop_flag[0] = True
    time.sleep(1)

    # 8. Stop bpftrace
    ssh_exec(ssh, "pkill bpftrace 2>/dev/null || true; sleep 1")

    # 9. Kill server
    ssh_exec(ssh, "pkill -9 llama-server 2>/dev/null || true; sleep 1")

    # 10. Parse HTTP results from file
    raw = ssh_exec(ssh, "cat /tmp/bench_result.json 2>/dev/null")
    try:
        data = json.loads(raw)
        wall_time = data["wall"]
        results = data["rows"]
    except:
        print("  FAIL: no HTTP results")
        return None, []

    if not results:
        print("  FAIL: no HTTP results")
        return None, []

    detail_rows = []
    for r in sorted(results, key=lambda x: x["i"]):
        detail_rows.append({
            "req": r["i"],
            "ttft_ms": r["tt"],
            "tps": r["tp"],
            "tokens": r["tk"],
            "e2e_ms": r["e2"],
        })

    ttfts = [r["tt"] for r in results]
    tps_vals = [r["tp"] for r in results]
    tokens = [r["tk"] for r in results]
    total_tok = sum(tokens)

    saves, restores, cpu_s = parse_vstate(ssh)
    target = sum(cpu_s.get(c, 0) for c in TARGET_CPUS)
    nontarget = sum(cpu_s.get(c, 0) for c in set(range(8)) - TARGET_CPUS)
    per_cpu = " ".join([f"c{c}:{cpu_s.get(c,0)}" for c in range(8)])

    # Print results
    print(f"\n  --- Results ---")
    for r in sorted(results, key=lambda x: x["i"]):
        print(f"  Req-{r['i']}: TTFT={r['tt']:.0f}ms TPS={r['tp']:.2f} Tok={r['tk']} E2E={r['e2']:.0f}ms")
    print(f"  Wall:{wall_time:.0f}s AggTPS:{total_tok/wall_time:.2f}")
    print(f"  AvgTTFT:{statistics.mean(ttfts):.0f}ms P95:{sorted(ttfts)[min(int(len(ttfts)*.95),len(ttfts)-1)]:.0f}ms")
    print(f"  V-saves:{saves} Target:{target} NonTgt:{nontarget} {'[LEAK]' if nontarget>0 else '[CLEAN]'}")
    print(f"  Per-CPU: {per_cpu}")

    row = {
        "wall_s": round(wall_time, 0),
        "agg_tps": round(total_tok / wall_time, 2) if wall_time > 0 else 0,
        "avg_ttft_ms": round(statistics.mean(ttfts), 0),
        "p95_ttft_ms": round(sorted(ttfts)[min(int(len(ttfts) * 0.95), len(ttfts) - 1)], 0),
        "avg_tps": round(statistics.mean(tps_vals), 2),
        "total_tokens": total_tok,
        "v_saves": saves,
        "v_restores": restores,
        "target_saves": target,
        "nontarget_saves": nontarget,
    }
    for c in range(8):
        row[f"cpu{c}_saves"] = cpu_s.get(c, 0)
    return row, detail_rows

# ===================================================================
#  MAIN
# ===================================================================

def main():
    print(f"{'='*55}")
    print(f"  RISC-V V-state Benchmark Runner")
    print(f"  Host: {SSH_HOST}  Groups: {len(GROUPS)}  Methods: {len(METHODS)}  Trials: {TRIALS}")
    print(f"{'='*55}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SSH_HOST, username=SSH_USER, password=SSH_PASS, timeout=20)

    deploy_bench_script(ssh)

    for g in GROUPS:
        _apply_group(g)
        print(f"\n{'#'*55}")
        print(f"  GROUP: {GROUP_NAME} | Model: {g['model']} | t={THREADS} cores={BIND_CORES}")
        print(f"  Prompt: {g['plen']}  Out: {MAX_TOKENS}  ctx: {CONTEXT}  par: {PARALLEL}")
        print(f"{'#'*55}")

        group_dir = f"{RESULTS_DIR}/{GROUP_NAME}"

        for method in METHODS:
            global BIND_METHOD
            BIND_METHOD = method
            print(f"\n  [{GROUP_NAME}] METHOD: {method}")

            method_rows = []
            all_details = []
            for trial in range(1, TRIALS + 1):
                print(f"    Trial {trial}/{TRIALS}...")
                result = run_trial(ssh)
                if result:
                    row, details = result
                    row["trial"] = trial
                    row["method"] = method
                    method_rows.append(row)
                    for d in details:
                        d["trial"] = trial
                        d["method"] = method
                    all_details.extend(details)

            if method_rows:
                os.makedirs(group_dir, exist_ok=True)
                csv_path = f"{group_dir}/{method}.csv"
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=method_rows[0].keys())
                    w.writeheader()
                    w.writerows(method_rows)
                detail_path = f"{group_dir}/{method}_detail.csv"
                with open(detail_path, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=["method", "trial", "req", "ttft_ms", "tps", "tokens", "e2e_ms"])
                    w.writeheader()
                    w.writerows(all_details)
                print(f"    Saved: {csv_path}")

                for k in ["wall_s", "agg_tps", "avg_ttft_ms", "p95_ttft_ms", "v_saves", "nontarget_saves"]:
                    vals = [r[k] for r in method_rows]
                    if len(vals) >= 2:
                        print(f"    {k}: {statistics.mean(vals):.1f} +/- {statistics.stdev(vals):.1f}")
                    else:
                        print(f"    {k}: {vals[0]:.1f}")

            # Between methods cleanup
            cleanup(ssh)
            ssh.exec_command("systemctl set-property --runtime init.scope   AllowedCPUs= 2>/dev/null || true")
            ssh.exec_command("systemctl set-property --runtime system.slice AllowedCPUs= 2>/dev/null || true")
            ssh.exec_command("systemctl set-property --runtime user.slice   AllowedCPUs= 2>/dev/null || true")
            ssh.exec_command("systemctl set-property --runtime llm.slice    AllowedCPUs= 2>/dev/null || true")
            ssh.exec_command("systemctl stop llm.scope 2>/dev/null || true; systemctl reset-failed 2>/dev/null || true")
            ssh.exec_command("echo member > /sys/fs/cgroup/llm/cpuset.cpus.partition 2>/dev/null || true; rmdir /sys/fs/cgroup/llm 2>/dev/null || true")
            ssh.exec_command("echo '-cpuset' > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true")

    ssh.close()
    print(f"\n{'='*55}")
    print(f"  ALL DONE. Results in {RESULTS_DIR}/group*/")
    print(f"{'='*55}")

if __name__ == "__main__":
    main()
