#!/usr/bin/env python3
"""
RISC-V LLM V-state Benchmark Runner — 场景配置版
=================================================
设计：所有实验场景定义在 SCENARIOS 字典里，RUN 列表决定按什么顺序跑哪些。
     - 切换实验 = 改 RUN 列表，不再改代码/删配置
     - 每个场景结束强制清理现场（llama/bpftrace/stress/sched）+ 逐项验证
     - 每个 trial 结束立即落盘 CSV（中断最多丢当前一轮）

用法:
  python bench_runner_v2.py

依赖: pip install paramiko (Windows 宿主机)
      板卡: python3 requests curl bpftrace（已装好）
"""

import paramiko, time, json, statistics, csv, os, re
from datetime import datetime

# ===================================================================
#  「一、基础配置」— 板卡连接 / 路径 / 固定实验参数
# ===================================================================

SSH_HOST = "192.168.0.100"   # 网线直连；若再断可临时改用热点 10.69.219.18
SSH_USER = "root"
SSH_PASS = "1"

# llama.cpp 构建（板卡绝对路径，bin/lib 分开）
# sched_target：调度器 uprobe 要挂的 libggml-cpu.so——必须和本场景实际使用的构建一致！
LLAMA_BUILDS = {
    "spacemit": {   # 优化版（xsmtvdot IME 内核 + 自带 AI 核偏好）
        "bin": "/home/gh/workspace/llama/debug/install_output/bin",
        "lib": "/home/gh/workspace/llama/debug/install_output/lib",
        "sched_target": "/home/gh/workspace/llama/debug/install_output/lib/libggml-cpu.so",
    },
    "nospacemit": { # 普通版（纯通用 RVV）
        "bin": "/home/gh/workspace/llama/nospacemit/bin",
        "lib": "/home/gh/workspace/llama/nospacemit/lib",
        "sched_target": "/home/gh/workspace/llama/nospacemit/lib/libggml-cpu.so",
    },
}

MODEL_1B   = "/home/gh/workspace/models/llama-3.2-1b-instruct-q4_k_m.gguf"
MODEL_3B   = "/home/gh/workspace/models/llama-3.2-3b-instruct-q4_k_m.gguf"
BPFTRACE   = "/home/gh/workspace/mysched/trace_sh/v_compute_server.bt"
BENCH_PY   = "/tmp/b.py"
VSTATE_LOG = "/tmp/vstate_result.log"
SERVER_LOG = "/tmp/llama-server.log"

# sched_ext 调度器（vec_affine_sched V3）
SCHED_LOADER = "/home/gh/workspace/mysched/vec_affine3/vec_affine_sched"
SCHED_LOG    = "/tmp/vec_sched.log"

# 高负载压测工具（cpu_stress：N 线程各自钉单核，4 线程=压 0-3）
STRESS_BIN     = "/home/gh/workspace/mysched/load/cpu_stress"
STRESS_THREADS = 4
STRESS_LOG     = "/tmp/cpu_stress.log"

# stress-ng 分级负载（--cpu 0 = 每核 1 个 worker；--cpu-load P = 每核占空比 P%）
STRESS_NG_LOG     = "/tmp/stress_ng.log"
STRESS_NG_TIMEOUT = 3600    # 兜底死手开关（秒），正常由 stop_stress_ng 主动停

RESULTS_DIR = "D:/比赛/results/final result"
RESULTS_DIR_NG = "D:/比赛/results/stress_ng"   # stress-ng 实验单独存，不动 final result

# 固定实验参数（所有场景共用）
CONCURRENT = 50        # 每轮 50 个并发请求
THREADS    = 4         # llama-server -t / -tb
CONTEXT    = 2048      # -c
MAX_TOKENS = 64        # 输出 token
PROMPT_LEN = 128       # prompt token（128 模板）

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

# ===================================================================
#  「二、场景配置库」— 所有场景在此定义，跑哪个由 RUN 列表决定
# ===================================================================
# 字段:
#   build  : "spacemit"=优化版 / "nospacemit"=普通版
#   cpus   : "0-3"/"4-7" → llama 自带 -Cr 参数绑核（需要 method="cr"）
#   method : "cr"=-Cr绑核启动 / "none"=不绑定直接启动（不用 taskset）
#   sched  : True=本场景全程开启 vec_affine_sched（场景级常驻，trial 间不杀）
#   stress : True=每个 trial 期间跑 cpu_stress（4线程压0-3，与推理线程同核竞争）
#   stress_ng : N=单档 stress-ng 负载 N%（每个 trial 在 llama-server 启动前就位）；旧式单档用
#   loads  : [N,...]=多档 stress-ng 负载，场景内部逐档循环，每档都跑 par × trials
#   results_dir : 该场景结果目录（缺省用 RESULTS_DIR）
#   par    : 并发列表（llama-server --parallel）
#   trials : 每个并发重复几次
#   model  : "1B"（默认）/ "3B"
SCENARIOS = {
    "noopt_c0_3": {
        "desc": "普通llama 绑0-3 4线程",
        "build": "nospacemit", "cpus": "0-3", "method": "cr",
        "par": [2, 4, 8], "trials": 3,
    },
    "noopt_c4_7": {
        "desc": "普通llama 绑4-7 4线程",
        "build": "nospacemit", "cpus": "4-7", "method": "cr",
        "par": [2, 4, 8], "trials": 3,
    },
    "opt_nobind": {
        "desc": "优化llama 4线程 不绑定",
        "build": "spacemit", "method": "none",
        "par": [2, 4, 8], "trials": 3,
    },
    "sched_opt": {
        "desc": "sched_ext + 优化llama 4线程 不绑定",
        "build": "spacemit", "method": "none", "sched": True,
        "par": [2, 4, 8], "trials": 3,
    },
    "sched_noopt": {
        "desc": "sched_ext + 普通llama 4线程 不绑定",
        "build": "nospacemit", "method": "none", "sched": True,
        "par": [2, 4, 8], "trials": 3,
    },
    "stress_opt": {
        "desc": "压测(4线程压0-3) + 优化llama",
        "build": "spacemit", "method": "none", "stress": True,
        "par": [2, 4, 8], "trials": 3,
    },
    "stress_sched_opt": {
        "desc": "压测 + sched_ext + 优化llama",
        "build": "spacemit", "method": "none", "sched": True, "stress": True,
        "par": [2, 4, 8], "trials": 3,
    },
    "stressng_opt": {
        "desc": "stress-ng 分级负载 + 优化llama（负载先于 server 启动）",
        "build": "spacemit", "method": "none",
        "loads": [20, 40, 60, 80, 100],
        "par": [2, 4, 8], "trials": 3,
        "results_dir": RESULTS_DIR_NG,
    },
    "stressng_sched_opt": {
        "desc": "stress-ng 分级负载 + sched_ext + 优化llama",
        "build": "spacemit", "method": "none", "sched": True, "loads": [20, 40, 60, 80, 100],
        "par": [2, 4, 8], "trials": 3,
        "results_dir": RESULTS_DIR_NG,
    },
}

# 按此顺序执行（不跑的场景注释掉）
RUN = [
    "noopt_c0_3",
    "noopt_c4_7",
    "opt_nobind",
    "sched_opt",
    "sched_noopt",
    "stress_opt",
    "stress_sched_opt",
    "stressng_opt",
    "stressng_sched_opt",
]

# ===================================================================
#  「三、SSH 与基础工具」
# ===================================================================

def ssh_exec(ssh, cmd, timeout=28800):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode(errors="replace")


def connect():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SSH_HOST, username=SSH_USER, password=SSH_PASS, timeout=20)
    return ssh


# ===================================================================
#  「四、现场清理与验证」— 每个场景/trial 收尾必做
# ===================================================================
# 注意：pkill/pgrep 的 -f 模式必须用 [x] 字符类，避免匹配到执行命令的
# shell 自身（其命令行里含同样字符串）。

def cleanup(ssh):
    """杀掉 llama-server / bpftrace / cpu_stress / stress-ng（不动调度器）。"""
    print("  [cleanup] 清理现场...")
    ssh_exec(ssh, 'pkill -9 -f "llama-serve[r]" 2>/dev/null')
    ssh_exec(ssh, 'pkill -9 -f "bpftrac[e]" 2>/dev/null')
    ssh_exec(ssh, 'pkill -INT -f "cpu_stres[s]" 2>/dev/null; sleep 1; pkill -9 -f "cpu_stres[s]" 2>/dev/null')
    ssh_exec(ssh, 'pkill -INT -f "stress-n[g]" 2>/dev/null; sleep 1; pkill -9 -f "stress-n[g]" 2>/dev/null')
    time.sleep(2)
    print("  [cleanup] 完成")


def verify_clean(ssh, sched_on=False):
    """逐项检查现场并打印结果；返回是否全部干净。"""
    print("\n[verify] 现场检查:")
    checks = []
    def chk(label, ok, detail=""):
        checks.append(ok)
        print(f"  [{'✓' if ok else '✗'}] {label} {detail}")

    chk("llama-server 已终止",
        ssh_exec(ssh, 'pgrep -f "llama-serve[r]" | wc -l').strip() == "0")
    chk("bpftrace 已终止",
        ssh_exec(ssh, 'pgrep -f "bpftrac[e]" | wc -l').strip() == "0")
    chk("cpu_stress 已终止",
        ssh_exec(ssh, 'pgrep -f "cpu_stres[s]" | wc -l').strip() == "0")
    chk("stress-ng 已终止",
        ssh_exec(ssh, 'pgrep -f "stress-n[g]" | wc -l').strip() == "0")
    chk("8080 端口已释放",
        ssh_exec(ssh, "ss -tln | grep -c ':8080 ' || true").strip() == "0")

    sched_proc = ssh_exec(ssh, 'pgrep -f "vec_affine_sche[d] -t" | wc -l').strip()
    state = ssh_exec(ssh, "cat /sys/kernel/sched_ext/state 2>/dev/null").strip()
    if sched_on:
        chk("调度器 loader 存活", sched_proc != "0", f"(进程数={sched_proc})")
        chk("sched_ext state=enabled", state == "enabled", f"(state={state})")
    else:
        chk("调度器 loader 已卸载", sched_proc == "0", f"(进程数={sched_proc})")
        chk("sched_ext state=disabled", state != "enabled", f"(state={state})")

    return all(checks)


def wait_state(ssh, target="disabled", max_sec=120):
    """轮询 /sys/kernel/sched_ext/state 直到目标状态（默认 disabled），带进度打印。"""
    for i in range(max_sec // 2):
        time.sleep(2)
        state = ssh_exec(ssh, "cat /sys/kernel/sched_ext/state 2>/dev/null").strip()
        if state == target:
            return True
        if i % 5 == 0:  # 每 10 秒报一次进度
            print(f"  [wait] state={state}，等待变为 {target}...（已等 {i*2}s，最长 {max_sec}s）")
    return False


# ===================================================================
#  「五、调度器管理」（场景级：开启后跨 trial 常驻）
# ===================================================================

def sched_start(ssh, target_so):
    """启动 vec_affine_sched。杀净旧实例 → 等内核释放旧 ops → 启动 → 轮询 enabled。
    target_so: uprobe 挂载目标 libggml-cpu.so——必须与场景 build 一致，
               否则推理线程识别不到（挂错库 = 全员当普通任务 = 4ms 片灾难）。"""
    print("\n[sched] 启动调度器...")
    # 先 SIGTERM 优雅卸载（此内核 SIGKILL 会留下僵尸 ops，永不释放），再 -9 兜底
    ssh_exec(ssh, 'pkill -TERM -f "vec_affine_sche[d] -t" 2>/dev/null; sleep 5; pkill -9 -f "vec_affine_sche[d] -t" 2>/dev/null')
    for i in range(90):  # 内核看门狗实测 ~127s 才释放旧 ops，留足余量
        time.sleep(2)
        state = ssh_exec(ssh, "cat /sys/kernel/sched_ext/state 2>/dev/null").strip()
        if state != "enabled":
            break
        if i % 5 == 0:
            print(f"[sched] 等待内核释放旧调度器...（已等 {i*2}s）")
    if state == "enabled":
        print("[sched] 180s 内内核未释放旧 ops，无法启动新调度器")
        return False
    print(f"[sched] 清理完成，state={state}")
    ssh_exec(ssh, f"nohup {SCHED_LOADER} -t {target_so} > {SCHED_LOG} 2>&1 &")
    for i in range(30):
        time.sleep(2)
        state = ssh_exec(ssh, "cat /sys/kernel/sched_ext/state 2>/dev/null").strip()
        alive = ssh_exec(ssh, 'pgrep -f "vec_affine_sche[d] -t" >/dev/null && echo alive || echo dead').strip()
        if state == "enabled":
            print(f"[sched] loader={alive} state={state}（{i*2}s 内生效）")
            return True
        if alive != "alive" and i >= 3:
            print(f"[sched] loader 已死（{i*2}s），日志:")
            print(ssh_exec(ssh, f"tail -10 {SCHED_LOG}"))
            return False
    print(f"[sched] 等待超时（60s），state 仍为 {state}，日志:")
    print(ssh_exec(ssh, f"tail -10 {SCHED_LOG}"))
    return False


def sched_stop(ssh):
    """卸载调度器并确认 state 回 disabled。"""
    print("\n[sched] 卸载调度器...")
    print("[sched] 实验期间统计:")
    print(ssh_exec(ssh, f"tail -5 {SCHED_LOG}"))
    # 必须 SIGTERM 优雅卸载（loader 走 detach → 内核立即释放）；-9 兜底
    ssh_exec(ssh, 'pkill -TERM -f "vec_affine_sche[d] -t" 2>/dev/null; sleep 5; pkill -9 -f "vec_affine_sche[d] -t" 2>/dev/null')
    ok = wait_state(ssh, target="disabled", max_sec=120)
    state = ssh_exec(ssh, "cat /sys/kernel/sched_ext/state 2>/dev/null").strip()
    print(f"[sched] 卸载后 state={state} {'✓' if ok else '✗ 仍不是 disabled'}")
    return ok


# ===================================================================
#  「六、高负载压测工具管理」（trial 级）
# ===================================================================

def start_stress(ssh):
    print(f"[stress] 启动 cpu_stress ×{STRESS_THREADS}（压 0-3）...")
    ssh_exec(ssh, f"nohup {STRESS_BIN} {STRESS_THREADS} > {STRESS_LOG} 2>&1 &")
    time.sleep(3)
    alive = ssh_exec(ssh, 'pgrep -f "cpu_stres[s]" | wc -l').strip()
    print(f"[stress] 线程数={alive} {'✓' if alive != '0' else '✗ 未启动成功'}")
    return alive != "0"


def stop_stress(ssh):
    ssh_exec(ssh, 'pkill -INT -f "cpu_stres[s]" 2>/dev/null; sleep 2; pkill -9 -f "cpu_stres[s]" 2>/dev/null')
    time.sleep(1)


def start_stress_ng(ssh, load):
    """stress-ng 分级负载：--cpu 0 = 每核 1 个 worker，--cpu-load load = 每核占空比 load%。
    必须在 llama-server 启动前调用（负载先就位，再起推理服务）。"""
    ng = ssh_exec(ssh, "command -v stress-ng || echo NOTFOUND").strip()
    if ng == "NOTFOUND":
        print("[stress-ng] ✗ 板卡上找不到 stress-ng（非交互 SSH 的 PATH 可能与登录 shell 不同）")
        return False
    print(f"[stress-ng] {ng} 启动负载 {load}%（8 worker，每核占空比 {load}%，先于 llama-server）...")
    ssh_exec(ssh, f"nohup {ng} --cpu 0 --cpu-load {load} --timeout {STRESS_NG_TIMEOUT} "
                  f"--metrics-brief > {STRESS_NG_LOG} 2>&1 &")
    time.sleep(3)
    alive = ssh_exec(ssh, 'pgrep -f "stress-n[g]" | wc -l').strip()
    ok = alive != "0"
    print(f"[stress-ng] 进程数={alive}（1 主 + 8 worker）{'✓' if ok else '✗ 未启动成功，日志:'}")
    if not ok:
        print(ssh_exec(ssh, f"tail -5 {STRESS_NG_LOG}"))
    return ok


def stop_stress_ng(ssh):
    ssh_exec(ssh, 'pkill -INT -f "stress-n[g]" 2>/dev/null; sleep 2; pkill -9 -f "stress-n[g]" 2>/dev/null')
    time.sleep(1)
    print("[stress-ng] 已停止，日志尾部（metrics 备查）:")
    print(ssh_exec(ssh, f"tail -4 {STRESS_NG_LOG}"))


# ===================================================================
#  「七、bench 脚本部署 + 单 trial 执行」
# ===================================================================

def deploy_bench_script(ssh):
    """部署 /tmp/b.py：50 并发请求，完成后自行终止 bpftrace（监听窗口与请求窗口对齐）。"""
    code = r"""
import requests,json,time,sys,os
from concurrent.futures import ThreadPoolExecutor,as_completed
u="http://localhost:8080/v1/chat/completions"
p={"messages":[{"role":"user","content":sys.argv[1]}],"max_tokens":int(sys.argv[2]),"temperature":0}
def f(i):
 try:
  t=time.time();r=requests.post(u,json=p,timeout=3600);d=r.json()
  tm=d.get("timings",{});us=d.get("usage",{})
  return {"i":i,"tt":tm.get("prompt_ms",0),"tp":tm.get("predicted_per_second",0),
          "tk":us.get("completion_tokens",tm.get("predicted_n",0)),"e2":(time.time()-t)*1000}
 except Exception:
  return {"i":i,"tt":0,"tp":0,"tk":0,"e2":0}
t0=time.time()
R=[]
try:
 with ThreadPoolExecutor(max_workers=int(sys.argv[3])) as P:
  for x in as_completed([P.submit(f,i) for i in range(int(sys.argv[3]))]):R.append(x.result())
finally:
 # 无论成功失败都写盘，runner 不会干等 3.2 小时
 with open("/tmp/bench_result.json","w") as f: json.dump({"wall":time.time()-t0,"rows":R},f)
os.system("pkill -TERM bpftrace 2>/dev/null")
"""
    ssh_exec(ssh, f"cat > {BENCH_PY} << 'EOFBENCH'\n{code}\nEOFBENCH")


def get_server_cmd(sc, par):
    """按场景生成 llama-server 启动命令。
    绑核方式：method="cr" → 用 llama 自带的 -Cr 参数（只约束计算线程），不用 taskset。"""
    b = LLAMA_BUILDS[sc["build"]]
    env = f"SPACEMIT_MEM_BACKEND=POSIX LD_LIBRARY_PATH={b['lib']} "  # POSIX：自编内核无透明大页
    args = (
        f"-m {MODEL_1B} "
        f"-t {THREADS} -tb {THREADS} "
        f"--parallel {par} -c {CONTEXT} "
        f"--cont-batching --port 8080 "
    )
    if sc.get("method") == "cr":
        args += f"-Cr {sc['cpus']} "
    return f"{env}nohup {b['bin']}/llama-server {args}> {SERVER_LOG} 2>&1 &"


def wait_ready(ssh):
    for i in range(120):
        time.sleep(2)
        out = ssh_exec(ssh, "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/health")
        if out.strip() == "200":
            return True
    return False


def monitor_server(ssh, stop_flag):
    """实时把 server 日志尾部转发到 Windows 控制台（[srv] 前缀）。"""
    while not stop_flag[0]:
        time.sleep(4)
        log = ssh_exec(ssh, f"tail -1 {SERVER_LOG}")
        if any(k in log for k in ["eval", "token", "slot", "prompt", "print_timing"]):
            print(f"    [srv] {log.strip()[-150:]}")


def parse_vstate(ssh):
    """解析 bpftrace 输出的全部数据：
    - 总 save/restore 次数、每核 saves/restores
    - 按进程/线程×核的分布（cpu_saves_by_proc、saves_by_proc、tid_cpu_saves 等六类）
    - 切换耗时统计（count/average/total，V 与非 V）
    分布类数据以 JSON 字符串进 CSV（键值对不定长，不适合拆列）。"""
    v = ssh_exec(ssh, f"cat {VSTATE_LOG}")
    saves, restores = 0, 0
    cpu_s, cpu_r = {}, {}
    dist = {
        "cpu_saves_by_proc": {}, "cpu_restores_by_proc": {},
        "saves_by_proc": {}, "restores_by_proc": {},
        "tid_cpu_saves": {}, "tid_cpu_restores": {},
    }
    v_avg_ns = v_count = v_total_ns = 0
    nonv_avg_ns = nonv_count = nonv_total_ns = 0
    for line in v.split("\n"):
        line = line.strip()
        if "@saves_total:" in line:
            try: saves = int(line.split("@saves_total:")[1].strip().split()[0])
            except: pass
        if "@restores_total:" in line:
            try: restores = int(line.split("@restores_total:")[1].strip().split()[0])
            except: pass
        m = re.search(r"@v_total_stats:\s*count\s+(\d+),\s*average\s+(\d+),\s*total\s+(\d+)", line)
        if m:
            v_count, v_avg_ns, v_total_ns = int(m.group(1)), int(m.group(2)), int(m.group(3))
        m = re.search(r"@non_v_total_stats:\s*count\s+(\d+),\s*average\s+(\d+),\s*total\s+(\d+)", line)
        if m:
            nonv_count, nonv_avg_ns, nonv_total_ns = int(m.group(1)), int(m.group(2)), int(m.group(3))
        for m in re.finditer(r"@cpu_saves\[(\d+)\]:\s*(\d+)", line):
            cpu_s[int(m.group(1))] = int(m.group(2))
        for m in re.finditer(r"@cpu_restores\[(\d+)\]:\s*(\d+)", line):
            cpu_r[int(m.group(1))] = int(m.group(2))
        for name in dist:
            for m in re.finditer(r"@" + re.escape(name) + r"\[([^\]]+)\]:\s*(\d+)", line):
                dist[name][m.group(1)] = int(m.group(2))
    return saves, restores, cpu_s, cpu_r, dist, \
        v_avg_ns, v_count, v_total_ns, nonv_avg_ns, nonv_count, nonv_total_ns


def run_trial(ssh, sc, par, trial_no):
    """执行一轮：清理 → [stress] → 起server → bpftrace → 50请求 → 收数据 → 收尾。"""
    print(f"\n    === Trial {trial_no} | par={par} ===")

    # 1. 清理（不动调度器）
    cleanup(ssh)

    # 2. 场景要求的高负载（stress-ng 必须在 llama-server 之前就位）
    if sc.get("stress_ng"):
        if not start_stress_ng(ssh, sc["stress_ng"]):
            print("  FATAL: stress-ng 启动失败")
            return None
    elif sc.get("stress") and not start_stress(ssh):
        print("  FATAL: cpu_stress 启动失败")
        return None

    # 3. 启动 llama-server
    ssh_exec(ssh, get_server_cmd(sc, par))
    if not wait_ready(ssh):
        print("  FAIL: server 未就绪")
        if sc.get("stress_ng"):
            stop_stress_ng(ssh)
        elif sc.get("stress"):
            stop_stress(ssh)
        return None
    print("  Server ready")

    # 4. 启动 bpftrace（等探针挂载）
    ssh_exec(ssh, f"nohup bpftrace {BPFTRACE} > {VSTATE_LOG} 2>&1 &")
    time.sleep(8)

    # 5. 发 50 并发请求（板卡本地执行，完成后自动杀 bpftrace）
    print(f"  Sending {CONCURRENT} requests...")
    ssh_exec(ssh, f"rm -f /tmp/bench_result.json; nohup python3 {BENCH_PY} '{PROMPT_128}' {MAX_TOKENS} {CONCURRENT} > /dev/null 2>&1 &")
    import threading as th
    stop_flag = [False]
    mt = th.Thread(target=monitor_server, args=(ssh, stop_flag), daemon=True)
    mt.start()
    for _ in range(5760):  # 最多 5760×2s = 3.2h
        time.sleep(2)
        if ssh_exec(ssh, "cat /tmp/bench_result.json 2>/dev/null | head -1").strip():
            break
    stop_flag[0] = True

    # 6. 收尾：先等 b.py 的 TERM 让 bpftrace 跑完 END（保住耗时/分布数据），-9 只兜底
    for _ in range(8):  # 最多等 16s
        if ssh_exec(ssh, 'pgrep -f "bpftrac[e]" | wc -l').strip() == "0":
            break
        time.sleep(2)
    ssh_exec(ssh, 'pkill -9 -f "bpftrac[e]" 2>/dev/null')
    ssh_exec(ssh, 'pkill -9 -f "llama-serve[r]" 2>/dev/null; sleep 1')
    if sc.get("stress_ng"):
        stop_stress_ng(ssh)
    elif sc.get("stress"):
        stop_stress(ssh)

    # 7. 解析 HTTP 结果
    raw = ssh_exec(ssh, "cat /tmp/bench_result.json 2>/dev/null")
    try:
        data = json.loads(raw)
        wall_time = data["wall"]
        results = data["rows"]
    except Exception:
        print("  FAIL: 无 HTTP 结果")
        return None
    if not results:
        print("  FAIL: 无 HTTP 结果")
        return None

    # 8. 解析 V-state（含分布与耗时 total）
    (saves, restores, cpu_s, cpu_r, dist,
     v_avg_ns, v_count, v_total_ns,
     nonv_avg_ns, nonv_count, nonv_total_ns) = parse_vstate(ssh)
    # 注意："0-3" 要展开成 {0,1,2,3}，不能只取两端点
    if sc.get("cpus"):
        lo, hi = sc["cpus"].split("-")
        target_cpus = set(range(int(lo), int(hi) + 1))
    else:
        target_cpus = set(range(8))
    target = sum(cpu_s.get(c, 0) for c in target_cpus)
    nontarget = sum(cpu_s.get(c, 0) for c in set(range(8)) - target_cpus)

    ttfts = [r["tt"] for r in results]
    tps_vals = [r["tp"] for r in results]
    total_tok = sum(r["tk"] for r in results)

    print(f"    Wall:{wall_time:.0f}s AggTPS:{total_tok/wall_time:.2f} "
          f"TTFT avg={statistics.mean(ttfts):.0f}ms p95={sorted(ttfts)[min(int(len(ttfts)*.95),len(ttfts)-1)]:.0f}ms")
    print(f"    V-saves:{saves} V-switch:{v_count}次 avg={v_avg_ns}ns | "
          f"NonV:{nonv_count}次 avg={nonv_avg_ns}ns | target:{target} nontarget:{nontarget}")

    row = {
        "scenario": sc["name"], "par": par, "trial": trial_no,
        "load_pct": sc.get("stress_ng", 0),
        "wall_s": round(wall_time, 0),
        "agg_tps": round(total_tok / wall_time, 2) if wall_time > 0 else 0,
        "avg_ttft_ms": round(statistics.mean(ttfts), 0),
        "p95_ttft_ms": round(sorted(ttfts)[min(int(len(ttfts) * 0.95), len(ttfts) - 1)], 0),
        "avg_tps": round(statistics.mean(tps_vals), 2),
        "total_tokens": total_tok,
        "v_saves": saves, "v_restores": restores,
        "target_saves": target, "nontarget_saves": nontarget,
        "v_switch_count": v_count, "v_switch_avg_ns": v_avg_ns, "v_switch_total_ns": v_total_ns,
        "nonv_switch_count": nonv_count, "nonv_switch_avg_ns": nonv_avg_ns, "nonv_switch_total_ns": nonv_total_ns,
        "cpu_saves_by_proc": json.dumps(dist["cpu_saves_by_proc"], ensure_ascii=False),
        "cpu_restores_by_proc": json.dumps(dist["cpu_restores_by_proc"], ensure_ascii=False),
        "saves_by_proc": json.dumps(dist["saves_by_proc"], ensure_ascii=False),
        "restores_by_proc": json.dumps(dist["restores_by_proc"], ensure_ascii=False),
        "tid_cpu_saves": json.dumps(dist["tid_cpu_saves"], ensure_ascii=False),
        "tid_cpu_restores": json.dumps(dist["tid_cpu_restores"], ensure_ascii=False),
    }
    for c in range(8):
        row[f"cpu{c}_saves"] = cpu_s.get(c, 0)
        row[f"cpu{c}_restores"] = cpu_r.get(c, 0)

    details = [{
        "scenario": sc["name"], "par": par, "trial": trial_no,
        "req": r["i"], "ttft_ms": r["tt"], "tps": r["tp"], "tokens": r["tk"], "e2e_ms": r["e2"],
    } for r in sorted(results, key=lambda x: x["i"])]
    return row, details


def write_csv_rows(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)


# ===================================================================
#  「八、场景执行器」
# ===================================================================

def run_scenario(ssh, name, sc):
    sc = dict(sc); sc["name"] = name
    rounds = len(sc.get("loads") or [0]) * len(sc["par"]) * sc["trials"]
    print(f"\n{'#'*60}")
    print(f"  场景: {name} — {sc['desc']}")
    if sc.get("loads"):
        stress_info = "stress-ng " + "/".join(str(x) for x in sc["loads"]) + "%"
    elif sc.get("stress_ng"):
        stress_info = f"stress-ng {sc['stress_ng']}%"
    elif sc.get("stress"):
        stress_info = "开(cpu_stress)"
    else:
        stress_info = "关"
    print(f"  构建={sc['build']} 绑核={sc.get('cpus','不绑定')} 并发={sc['par']} "
          f"重复={sc['trials']}× 调度器={'开' if sc.get('sched') else '关'} "
          f"压测={stress_info}")
    print(f"{'#'*60}")

    # 1. 场景开始：清场 + 按需起调度器
    cleanup(ssh)
    if sc.get("sched"):
        if not sched_start(ssh, LLAMA_BUILDS[sc["build"]]["sched_target"]):
            print("  FATAL: 调度器启动失败，跳过本场景")
            return
    else:
        # 非 sched 场景：确保没有残留调度器
        ssh_exec(ssh, 'pkill -TERM -f "vec_affine_sche[d] -t" 2>/dev/null; sleep 5; pkill -9 -f "vec_affine_sche[d] -t" 2>/dev/null')
        print("  [场景准备] 已杀调度器进程，等待内核释放旧 ops（看门狗实测 ~127s）...")
        if not wait_state(ssh, target="disabled", max_sec=180):
            print("  FATAL: 内核 180s 内未释放旧调度器，跳过本场景")
            return
    verify_clean(ssh, sched_on=bool(sc.get("sched")))

    # 2. 逐负载 × 并发 × 重复跑，每 trial 结束立即落盘
    #    负载档位在方法内部循环：新场景用 loads 列表；旧场景无负载走单档（None）
    loads = sc.get("loads") or [sc.get("stress_ng")]
    sc_dir = f"{sc.get('results_dir', RESULTS_DIR)}/{name}"
    os.makedirs(sc_dir, exist_ok=True)
    for load in loads:
        sc_run = dict(sc)
        if load is not None:
            sc_run["stress_ng"] = load
        ltag = f"_load{load}" if load is not None else ""
        for par in sc["par"]:
            rows, details = [], []
            for trial in range(1, sc["trials"] + 1):
                r = run_trial(ssh, sc_run, par, trial)
                if r is None:
                    print(f"  FATAL: {name} load={load} par={par} trial={trial} 失败，跳过该组")
                    break
                row, det = r
                rows.append(row); details.extend(det)
                # 立即落盘（中断最多丢当前轮）
                method = sc.get("method", "none")
                write_csv_rows(f"{sc_dir}/{name}{ltag}_par{par}_{method}.csv", rows)
                write_csv_rows(f"{sc_dir}/{name}{ltag}_par{par}_{method}_detail.csv", details)
            if rows:
                print(f"  Saved: {sc_dir}/{name}{ltag}_par{par}_{method}.csv（{len(rows)} 轮）")

    # 3. 场景收尾：卸载调度器 + 全清 + 验证
    if sc.get("sched"):
        sched_stop(ssh)
    cleanup(ssh)
    ok = verify_clean(ssh, sched_on=False)
    print(f"  [场景收尾] {'✓ 现场已清理干净' if ok else '✗ 有残留，请人工检查'}")


def main():
    print("=" * 60)
    print("  RISC-V V-state Benchmark Runner（场景配置版）")
    print(f"  Host: {SSH_HOST}")
    print("  将按序执行场景: " + " → ".join(RUN))
    print("=" * 60)

    ssh = connect()
    deploy_bench_script(ssh)

    for name in RUN:
        if name not in SCENARIOS:
            print(f"  ✗ 场景 '{name}' 不存在，跳过")
            continue
        run_scenario(ssh, name, SCENARIOS[name])

    # 全局收尾验证
    final_ok = verify_clean(ssh, sched_on=False)
    ssh.close()
    print("\n" + "=" * 60)
    print(f"  全部场景结束。现场状态: {'✓ 干净' if final_ok else '✗ 有残留'}")
    print(f"  结果在 {RESULTS_DIR}/<场景名>/")
    print("=" * 60)


if __name__ == "__main__":
    main()
