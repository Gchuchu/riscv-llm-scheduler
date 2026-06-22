#!/usr/bin/env python3
"""
RISC-V LLM 推理并发调度优化 — 基准测试脚本 (Issue #4)

支持三种模式:
  qemu:   宿主机发请求到 QEMU VM 的端口转发 (默认)
  local:  直接在机器上跑（读取本地 PID /proc）
  remote: 连远程板子的 IP 地址

指标说明:
  TPS        吞吐量 (token/s)        越高越好。 aggregate_tps = 总token数 / 整轮挂钟时间
  Avg TTFT  平均首 token 延迟 (ms)    越低越好。 服务端 prompt 处理耗时, 不含排队
  P95 TTFT  TTFT 的 95 分位值 (ms)   越低越好。 衡量尾延迟
  Avg E2E   客户端端到端平均延迟 (ms)  越低越好。 含网络 + 排队 + 推理全过程
  P95 E2E   端到端延迟的 95 分位值    越低越好。
  V 状态    V 状态切换开销            预留字段, 依赖 eBPF 工具 (Issue #1)

用法:
  # QEMU 模式 -- baseline
  python benchmark_llama.py --mode qemu --tag baseline

  # QEMU 模式 -- 绑核优化后
  python benchmark_llama.py --mode qemu --tag optimized

  # 一键对比（自动跑两组）
  python benchmark_llama.py --mode qemu --compare

  # 本地板子
  python benchmark_llama.py --mode local --concurrency 2,4,8 --trials 3
"""

import argparse
import csv
import time
import requests
import numpy as np
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime
import argparse
import csv
import time
import requests
import numpy as np
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime
import argparse
import csv
import time
import requests
import numpy as np
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime


# ==================== 默认配置 ====================

DEFAULT_URLS = {
    "qemu":   "http://127.0.0.1:18080/completion",
    "local":  "http://127.0.0.1:8080/v1/chat/completions",
    "remote": "http://{host}:8080/v1/chat/completions",
}

MODE_DEFAULTS = {
    "qemu": {
        "trials": 3,
        "max_tokens": 32,
        "timeout": 1800,
        "concurrency": "2,4,8",
        "prompt": "What is RISC-V Vector Extension and its impact on AI inference?",
    },
    "local": {
        "trials": 3,
        "max_tokens": 128,
        "timeout": 600,
        "concurrency": "2,4,8",
        "prompt": "What is RISC-V Vector Extension and its impact on AI inference?",
    },
    "remote": {
        "trials": 3,
        "max_tokens": 128,
        "timeout": 600,
        "concurrency": "2,4,8",
        "prompt": "What is RISC-V Vector Extension and its impact on AI inference?",
    },
}


# ==================== V 状态切换开销接口 ====================
# ⏳ 依赖 Issue #1（eBPF 追踪 riscv_vstate_save）

class VStateOverhead:
    """
    V 状态切换开销采集接口。
    当前为占位实现，返回空数据。
    待 #1 完成后，替换为 eBPF 数据读取逻辑。

    预期接口:
        collect() -> dict {
            "save_count": int,           # V 状态保存次数
            "avg_save_us": float,        # 平均保存耗时 (us)
            "dirty_preempt_count": int,  # dirty V 被抢占次数
        }
    """
    def __init__(self):
        self.enabled = False

    def available(self):
        """检测 eBPF 工具是否就绪"""
        # TODO: 检测 /sys/kernel/debug/tracing/ 或 ebpf 程序是否加载
        return False

    def collect(self, concurrency, trial):
        """采集当前 V 状态切换数据"""
        # TODO: 调用 eBPF 脚本读取统计数据
        return {
            "save_count": None,
            "avg_save_us": None,
            "dirty_preempt_count": None,
        }

    def summary_table(self, all_data):
        """生成量化表格 Markdown"""
        # TODO: 整理为 docs/benchmark/ 下的表格
        return "# V State Overhead Table\n(TODO: depends on Issue #1)\n"


# ==================== CPU Governor 管理 ====================

def set_performance_governor():
    """将 CPU 频率策略固定为 performance（赛题要求）"""
    try:
        subprocess.run(
            ["sudo", "cpupower", "frequency-set", "-g", "performance"],
            capture_output=True, text=True, timeout=10
        )
        print("[INFO] CPU governor set to 'performance'")
        return True
    except FileNotFoundError:
        print("[WARN] cpupower not found, skip setting CPU governor")
        return False
    except Exception as e:
        print(f"[WARN] Failed to set CPU governor: {e}")
        return False


# ==================== 请求辅助 ====================

def make_request_data(prompt, max_tokens, url):
    if "/chat/completions" in url:
        return {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
            "temperature": 0,
        }
    else:
        return {"prompt": prompt, "n_predict": max_tokens, "temperature": 0}


def parse_response(result, url):
    """从 API 响应中提取指标"""
    timings = result.get("timings", {})
    usage = result.get("usage", {})

    if "/chat/completions" in url:
        ttft_ms = timings.get("prompt_ms", 0)
        completion_tokens = usage.get("completion_tokens", timings.get("predicted_n", 0))
        predicted_ms = timings.get("predicted_ms", 0)
        predicted_tps = timings.get("predicted_per_second", 0)
    else:
        ttft_ms = timings.get("prompt_ms", 0)
        completion_tokens = result.get("tokens_predicted", 0)
        predicted_ms = timings.get("predicted_ms", 0)
        predicted_tps = timings.get("predicted_per_second", 0)

    tps = predicted_tps if predicted_tps else (
        completion_tokens / (predicted_ms / 1000) if predicted_ms else 0
    )

    return {
        "ttft_ms": ttft_ms,
        "tokens": completion_tokens,
        "predicted_ms": predicted_ms,
        "tps": tps,
    }


# ==================== PID & 内存（仅 local 模式） ====================

def find_server_pid():
    try:
        result = subprocess.run(
            ["pgrep", "-x", "llama-server"],
            capture_output=True, text=True, timeout=5
        )
        pids = result.stdout.strip().split()
        if pids:
            return int(pids[0])
    except Exception:
        pass
    return None


def get_rss_mib(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except Exception:
        pass
    return None


# ==================== 单请求 ====================

def send_request(url, prompt, max_tokens, timeout):
    payload = make_request_data(prompt, max_tokens, url)
    req_start = time.time()
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        first_byte_time = time.time()  # 近似: 收到完整响应的时间
        resp.raise_for_status()
        result = parse_response(resp.json(), url)
        # 补充客户端视角的端到端延迟
        result["e2e_ms"] = (first_byte_time - req_start) * 1000
        return result
    except Exception as e:
        print(f"[WARN] Request failed: {e}")
        return None


# ==================== 一轮测试 ====================

def _safe_std(vals):
    """样本标准差，单值时返回 0 而非 nan"""
    if len(vals) < 2:
        return 0.0
    return float(np.std(vals, ddof=1))


def run_trial(url, concurrency, prompt, max_tokens, timeout, server_pid=None, vstate=None):
    rss_before = get_rss_mib(server_pid) if server_pid else None

    batch_start = time.time()
    results = []

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(send_request, url, prompt, max_tokens, timeout)
            for _ in range(concurrency)
        ]
        for f in as_completed(futures):
            r = f.result()
            if r is not None:
                results.append(r)

    wall_time = time.time() - batch_start
    rss_after = get_rss_mib(server_pid) if server_pid else None

    # V 状态切换开销采集（预留接口）
    vstate_data = vstate.collect(concurrency, None) if vstate and vstate.enabled else {}

    if not results:
        return None

    total_tokens = sum(x["tokens"] for x in results)
    aggregate_tps = total_tokens / wall_time if wall_time > 0 else 0
    avg_ttft = float(np.mean([x["ttft_ms"] for x in results])) if results else 0
    p95_ttft = float(np.percentile([x["ttft_ms"] for x in results], 95)) if results else 0
    avg_e2e = float(np.mean([x["e2e_ms"] for x in results])) if results else 0
    p95_e2e = float(np.percentile([x["e2e_ms"] for x in results], 95)) if results else 0

    ret = {
        "aggregate_tps": aggregate_tps,
        "avg_ttft_ms": avg_ttft,
        "p95_ttft_ms": p95_ttft,
        "avg_e2e_ms": avg_e2e,
        "p95_e2e_ms": p95_e2e,
        "sample_count": len(results),
        "wall_time_s": round(wall_time, 2),
    }
    # V 状态数据
    ret.update(vstate_data)
    if rss_before is not None:
        ret["rss_before_mib"] = rss_before
    if rss_after is not None:
        ret["rss_after_mib"] = rss_after

    return ret


# ==================== 多档位 ====================

def benchmark(url, concurrencies, trials, prompt, max_tokens, timeout, server_pid, tag, vstate):
    summary = defaultdict(list)
    raw_rows = []

    # 第一轮预热不计入结果（消除冷启动偏差）
    discard_trial = True

    for concurrency in concurrencies:
        print()
        print("=" * 60)
        print(f"  [{tag}] Concurrency = {concurrency}")
        print("=" * 60)

        for trial in range(1, trials + 1):
            if discard_trial:
                # 暗跑一轮预热，数据不计入结果
                _r = run_trial(url, concurrency, prompt, min(max_tokens, 8), timeout, server_pid, vstate)
                print(f"  (discarded warmup trial)")
                discard_trial = False
            print(f"  Trial {trial}/{trials} ... ", end="", flush=True)

            r = run_trial(url, concurrency, prompt, max_tokens, timeout, server_pid, vstate)
            if r is None:
                print("FAILED")
                continue

            log = (f"TPS={r['aggregate_tps']:.2f}  "
                   f"TTFT={r['avg_ttft_ms']:.1f}ms  "
                   f"P95={r['p95_ttft_ms']:.1f}ms  "
                   f"AvgE2E={r['avg_e2e_ms']:.1f}ms")
            if "rss_before_mib" in r:
                log += f"  Mem={r['rss_before_mib']}->{r['rss_after_mib']}MiB"
            print(log)

            summary[concurrency].append(r)
            raw_rows.append({
                "tag": tag,
                "concurrency": concurrency,
                "trial": trial,
                "throughput_tps": round(r["aggregate_tps"], 4),
                "avg_ttft_ms": round(r["avg_ttft_ms"], 2),
                "p95_ttft_ms": round(r["p95_ttft_ms"], 2),
                "avg_e2e_ms": round(r["avg_e2e_ms"], 2),
                "p95_e2e_ms": round(r["p95_e2e_ms"], 2),
                "wall_time_s": r["wall_time_s"],
                "sample_count": r["sample_count"],
                "vstate_save_count": r.get("save_count", ""),
                "vstate_avg_save_us": r.get("avg_save_us", ""),
                "vstate_dirty_preempt": r.get("dirty_preempt_count", ""),
                "rss_idle_mib": r.get("rss_before_mib", ""),
                "rss_after_mib": r.get("rss_after_mib", ""),
            })

    return summary, raw_rows


# ==================== 报告输出 ====================

def print_report(summary, tag):
    print()
    print("#" * 70)
    print(f"  BENCHMARK RESULT [{tag}]")
    print("#" * 70)

    for concurrency in sorted(summary.keys()):
        runs = summary[concurrency]
        tps_vals = [x["aggregate_tps"] for x in runs]
        avg_vals = [x["avg_ttft_ms"] for x in runs]
        p95_vals = [x["p95_ttft_ms"] for x in runs]
        e2e_vals = [x["avg_e2e_ms"] for x in runs]

        print()
        print(f"--- Concurrency = {concurrency} ---")
        print(f"  TPS:          {np.mean(tps_vals):.2f} ± {_safe_std(tps_vals):.2f}")
        print(f"  Avg TTFT(ms): {np.mean(avg_vals):.2f} ± {_safe_std(avg_vals):.2f}  (server-side prompt eval)")
        print(f"  P95 TTFT(ms): {np.mean(p95_vals):.2f} ± {_safe_std(p95_vals):.2f}")
        print(f"  Avg E2E(ms):  {np.mean(e2e_vals):.2f} ± {_safe_std(e2e_vals):.2f}  (client-side end-to-end)")

        if "rss_before_mib" in runs[0]:
            rss_before = runs[0]["rss_before_mib"]
            rss_after_vals = [x["rss_after_mib"] for x in runs]
            print(f"  RSS idle(MiB):  {rss_before}")
            print(f"  RSS after(MiB): {np.mean(rss_after_vals):.0f}")

        # V 状态数据（预留）
        if runs[0].get("save_count") is not None:
            save_vals = [x["save_count"] for x in runs]
            print(f"  V save count:   {np.mean(save_vals):.0f}")


def print_comparison(all_summaries, all_tags):
    """baseline vs optimized 对比报告"""
    print()
    print("#" * 70)
    print("  COMPARISON REPORT")
    print("#" * 70)

    if len(all_summaries) < 2:
        print("  (Need both baseline and optimized data)")
        return

    all_concurrencies = sorted(set().union(*(s.keys() for s in all_summaries)))
    for concurrency in all_concurrencies:
        print()
        print(f"--- Concurrency = {concurrency} ---")
        print(f"  {'Metric':<20} {'Baseline':>12} {'Optimized':>12} {'Change':>12}")

        for metric, label in [("aggregate_tps", "TPS"), ("avg_ttft_ms", "Avg TTFT(ms)"), ("p95_ttft_ms", "P95 TTFT(ms)"), ("avg_e2e_ms", "Avg E2E(ms)")]:
            vals = []
            for summary in all_summaries:
                if concurrency in summary:
                    runs = summary[concurrency]
                    vals.append(np.mean([x[metric] for x in runs]))
                else:
                    vals.append(None)

            if vals[0] is not None and vals[1] is not None:
                change = (vals[1] - vals[0]) / vals[0] * 100
                print(f"  {label:<20} {vals[0]:>12.2f} {vals[1]:>12.2f} {change:>+11.1f}%")
            else:
                print(f"  {label:<20} {'N/A':>12} {'N/A':>12} {'N/A':>12}")

    print()
    print("  Notes:")
    print("    Positive TPS change = improvement")
    print("    Negative TTFT change = improvement")


def save_csv(rows, tag=""):
    if not rows:
        print("[SKIP] No data to save")
        return

    suffix = f"_{tag}" if tag else ""
    filename = f"benchmark{suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    fieldnames = [
        "tag", "concurrency", "trial", "throughput_tps",
        "avg_ttft_ms", "p95_ttft_ms", "avg_e2e_ms", "p95_e2e_ms",
        "wall_time_s", "sample_count",
        "vstate_save_count", "vstate_avg_save_us", "vstate_dirty_preempt",
        "rss_idle_mib", "rss_after_mib",
    ]

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nCSV saved: {filename}")
    return filename


# ==================== 一轮 benchmark（单 tag） ====================

def run_one_benchmark(args, defaults, vstate, tag, url):
    print()
    print("=" * 60)
    print(f"  Running: [{tag}]")
    print("=" * 60)

    concurrencies = [int(x) for x in (args.concurrency or defaults["concurrency"]).split(",")]
    trials = args.trials or defaults["trials"]
    max_tokens = args.max_tokens or defaults["max_tokens"]
    timeout = args.timeout or defaults["timeout"]
    prompt = args.prompt or defaults["prompt"]

    # PID 检测（仅 local 模式）
    server_pid = None
    if args.mode == "local":
        server_pid = args.pid or find_server_pid()
        if server_pid is None:
            print("[ERROR] 找不到 llama-server 进程")
            return None, []

    print(f"  URL:           {url}")
    print(f"  Concurrency:   {concurrencies}")
    print(f"  Trials:        {trials}")
    print(f"  Max tokens:    {max_tokens}")
    print(f"  Timeout:       {timeout}s")

    # --- Warmup: 消除冷启动影响 ---
    print("\n  Warmup request ... ", end="", flush=True)
    # 预热：发一个完整请求（同 prompt + max_tokens），数据丢弃
    # 确保模型加载、prompt cache 初始化完成后才进正式 benchmark
    for attempt in range(5):
        warmup = send_request(url, prompt, max_tokens, timeout)
        if warmup:
            break
        print(f"Warmup failed (attempt {attempt+1}/5), retrying...")
        time.sleep(2)
    if warmup:
        print(f"OK ({warmup['ttft_ms']:.0f}ms / {warmup['tokens']} tok)")
    else:
        print("Warmup FAILED after 5 attempts, continuing...")

    summary, rows = benchmark(url, concurrencies, trials, prompt, max_tokens, timeout, server_pid, tag, vstate)
    print_report(summary, tag)
    return summary, rows


# ==================== 主入口 ====================

def main():
    parser = argparse.ArgumentParser(
        description="LLM推理基准测试（赛题6：RISC-V 并发调度优化）"
    )

    parser.add_argument("--mode",
        choices=["qemu", "local", "remote"],
        default="qemu",
        help="运行模式: qemu(默认), local(本地), remote(远程板子)")
    parser.add_argument("--host", default=None,
        help="远程模式时的板子 IP")
    parser.add_argument("--port", type=int, default=None,
        help="API 端口")
    parser.add_argument("--url", default=None,
        help="完整 API URL (优先级高于 mode/host/port)")
    parser.add_argument("--concurrency", default=None,
        help="并发数，逗号分隔 (default: 2,4,8)")
    parser.add_argument("--trials", type=int, default=None,
        help="每档重复次数 (default: 3)")
    parser.add_argument("--max-tokens", type=int, default=None,
        help="每个请求最大生成 token 数")
    parser.add_argument("--timeout", type=int, default=None,
        help="每个请求超时秒数")
    parser.add_argument("--prompt", default=None,
        help="输入 prompt")
    parser.add_argument("--pid", type=int, default=None,
        help="llama-server PID (仅 local 模式)")

    # ---- Issue #4 新增参数 ----
    parser.add_argument("--tag", default=None,
        help='测试标签，如 baseline / optimized / sched_ext，用于区分优化前后')
    parser.add_argument("--compare", action="store_true",
        help='一键对比模式：自动跑 baseline + optimized 两组并输出对比报告')
    parser.add_argument("--set-performance", action="store_true",
        help='将 CPU governor 设为 performance（赛题要求）')

    args = parser.parse_args()
    mode = args.mode
    defaults = MODE_DEFAULTS[mode]

    # --- 可选：设置 CPU governor ---
    if args.set_performance:
        set_performance_governor()

    # --- V 状态采集（预留接口，当前不可用） ---
    vstate = VStateOverhead()
    if vstate.available():
        vstate.enabled = True
        print("[INFO] V state overhead tracking enabled")

    # --- 确定 URL ---
    if args.url:
        url = args.url
    elif mode == "remote":
        host = args.host or input("Enter board IP address: ").strip()
        url = DEFAULT_URLS["remote"].format(host=host)
    else:
        url = DEFAULT_URLS[mode]
    if args.port:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        netloc = parsed.hostname + (f":{args.port}")
        url = urlunparse(parsed._replace(netloc=netloc))

    # ========================================
    #  一键对比模式
    # ========================================
    if args.compare:
        print()
        print("=" * 60)
        print("  ONE-CLICK COMPARISON MODE")
        print("  Baseline → Optimized")
        print("=" * 60)

        all_summaries = []
        all_tags = []
        r1, r2 = [], []

        # 第一轮: baseline
        s1, r1 = run_one_benchmark(args, defaults, vstate, "baseline", url)
        if s1:
            all_summaries.append(s1)
            all_tags.append("baseline")
            save_csv(r1, "baseline")

        # 第二轮: optimized
        print("\n" + "-" * 60)
        print("  ⏸  Baseline done. Please apply optimized settings now.")
        print("     (e.g. taskset / cgroup / cpu-mask on the server process)")
        print("-" * 60)
        input("  Press Enter to start optimized round ...")
        print()
        s2, r2 = run_one_benchmark(args, defaults, vstate, "optimized", url)
        if s2:
            all_summaries.append(s2)
            all_tags.append("optimized")
            save_csv(r2, "optimized")

        # 对比报告
        if len(all_summaries) >= 2:
            print_comparison(all_summaries, all_tags)
        else:
            print("\n[SKIP] 对比需要 baseline + optimized 两组数据")

        # 合并 CSV
        all_rows = []
        if r1: all_rows.extend(r1)
        if r2: all_rows.extend(r2)
        if all_rows:
            save_csv(all_rows, "comparison")

        return

    # ========================================
    #  单模式
    # ========================================
    tag = args.tag or mode
    s, rows = run_one_benchmark(args, defaults, vstate, tag, url)
    if rows:
        save_csv(rows, tag)


if __name__ == "__main__":
    main()
