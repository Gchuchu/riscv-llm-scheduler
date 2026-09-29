#!/usr/bin/env python3
"""
RISC-V LLM 推理并发调度优化 — 基准测试脚本 (Issue #4)

支持三种模式:
  qemu:   宿主机发请求到 QEMU VM 的端口转发 (默认)
  local:  直接在机器上跑（读取本地 PID /proc）
  remote: 连远程板子的 IP 地址

指标说明:
  TPS        吞吐量 (token/s)        越高越好。 aggregate_tps = 总token数 / 整轮挂钟时间
  TTFT       首 token 延迟 (ms)        越低越好。 客户端测量：HTTP POST 前至首个 SSE content 事件到达
  Prompt     提示处理耗时 (ms)          越低越好。 llama-server timings.prompt_ms
  E2E        客户端端到端延迟 (ms)      越低越好。 含网络 + 排队 + 推理全过程
  P95         95 分位值                 越低越好。 跨 trial 池化计算
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

  # 远程板卡自动实验：脚本启动/重启 server，执行模型规模与 parallel 矩阵
  python benchmark_llama.py --mode remote --host <BOARD_IP> --ssh-user root \
      --experiment-matrix full --model-sizes 1,3,8 \
      --server-threads 4 --context-per-slot 2048
"""

import argparse
import csv
import json
import os
import shlex
import shutil
import time
import requests
import numpy as np
import subprocess
import threading
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


# 自动实验使用的板卡模型。仅通过模型规模选择，不需要每次填写文件路径。
REMOTE_EXPERIMENT_MODELS = {
    "1B": "/root/llama-3.2-1b-instruct-q4_k_m.gguf",
    "3B": "/root/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    "8B": "/root/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
}

# ==================== V 状态切换开销接口 ====================
# ⏳ 依赖 Issue #1（eBPF 追踪 riscv_vstate_save）

class VStateOverhead:
    """V 状态切换开销量化接口。

    通过 bpftrace 挂载 sched:sched_switch tracepoint，统计 V 状态 save/restore 次数。

    本地模式 (--mode local):
        vstate = VStateOverhead()
        vstate.start()    # sudo bpftrace ... &
        vstate.collect()  # 读本地 /tmp/vstate.txt
        vstate.stop()     # pkill bpftrace

    远程模式 (--mode remote):
        vstate = VStateOverhead(remote="192.168.0.104")
        vstate.start()    # ssh board "nohup sudo bpftrace ... &"
        vstate.collect()  # ssh board "cat /tmp/vstate.txt"
        vstate.stop()     # ssh board "sudo pkill bpftrace"

    bpftrace 脚本:
        本地: src/tracker/vstate_trace.bt (由 SCRIPT 自动定位)
        远程: 默认 /home/openkylin/vstate_trace.bt (需事先 scp 到板子)
    """

    SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "tracker", "vstate_trace.bt")
    OUTPUT = "/tmp/vstate.txt"
    SCRIPT_REMOTE = "/root/vstate_trace.bt"
    PIDFILE_REMOTE = "/tmp/rvv-benchmark-vstate.pid"
    LOGFILE_REMOTE = "/tmp/rvv-benchmark-vstate.log"

    def __init__(self, remote=None, ssh_user=None, remote_script=None):
        self.enabled = False
        self._proc = None
        self.remote = remote
        self.ssh_user = ssh_user
        self.script = remote_script or self.SCRIPT_REMOTE if remote else self.SCRIPT
        self._ssh_target = None
        self._last_save = None
        self._last_restore = None
        self._last_llama_save = None
        self._last_llama_restore = None
        if remote:
            if ssh_user and "@" not in remote:
                self._ssh_target = f"{ssh_user}@{remote}"
            else:
                self._ssh_target = remote

    def _ssh(self, cmd, timeout=15):
        """SSH 执行命令，返回 CompletedProcess"""
        try:
            return subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
                 self._ssh_target, cmd],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
            )
        except subprocess.TimeoutExpired:
            # 返回一个"失败"对象，让调用方决定后续
            return type("FakeResult", (), {"returncode": -1, "stdout": "", "stderr": "timeout"})
        except Exception as e:
            return type("FakeResult", (), {"returncode": -1, "stdout": "", "stderr": str(e)})

    def available(self):
        """检测 bpftrace 和脚本是否就绪"""
        if self.remote:
            print(f"[VState] Testing SSH connection to {self._ssh_target} ... ", end="", flush=True)
            result = self._ssh("command -v bpftrace")
            if result.returncode != 0:
                print("FAILED (skip V-state collection)")
                return False
            print("OK")
            return True
        else:
            return shutil.which("bpftrace") is not None and os.path.exists(self.SCRIPT)

    def _stop_remote_trace(self):
        """仅停止本工具记录在 PID 文件中的 bpftrace，避免影响其他追踪任务。"""
        command = (
            f"if [ -f {shlex.quote(self.PIDFILE_REMOTE)} ]; then "
            f"pid=$(cat {shlex.quote(self.PIDFILE_REMOTE)}); "
            "kill $pid 2>/dev/null || true; "
            "for i in 1 2 3 4 5; do kill -0 $pid 2>/dev/null || break; sleep 1; done; "
            "kill -9 $pid 2>/dev/null || true; "
            f"rm -f {shlex.quote(self.PIDFILE_REMOTE)}; fi"
        )
        return self._ssh(command, timeout=30)

    def start(self):
        """启动 bpftrace 后台采集"""
        if not self.available():
            return
        try:
            if self.remote:
                self._stop_remote_trace()
                launch = (
                    f"rm -f {shlex.quote(self.OUTPUT)} {shlex.quote(self.LOGFILE_REMOTE)} "
                    f"{shlex.quote(self.PIDFILE_REMOTE)}; "
                    f"nohup stdbuf -oL bpftrace {shlex.quote(self.script)} -o {shlex.quote(self.OUTPUT)} "
                    f"> {shlex.quote(self.LOGFILE_REMOTE)} 2>&1 & "
                    f"echo $! > {shlex.quote(self.PIDFILE_REMOTE)}"
                )
                launched = self._ssh(launch)
                if launched.returncode != 0:
                    raise RuntimeError(launched.stderr.strip() or "远端 bpftrace 启动命令失败")
                time.sleep(1)
                running = self._ssh(
                    f"test -s {shlex.quote(self.PIDFILE_REMOTE)} && "
                    f"kill -0 $(cat {shlex.quote(self.PIDFILE_REMOTE)})"
                )
                if running.returncode != 0:
                    log = self._ssh(f"tail -30 {shlex.quote(self.LOGFILE_REMOTE)} || true").stdout.strip()
                    raise RuntimeError(f"远端 bpftrace 已退出。末尾日志:\n{log}")
            else:
                self._proc = subprocess.Popen(
                    ["sudo", "stdbuf", "-oL", "bpftrace", self.SCRIPT, "-o", self.OUTPUT],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            self.enabled = True
            where = self._ssh_target if self.remote else "localhost"
            print(f"[INFO] V-state trace started ({where})")
            # 自动矩阵只在一轮结束后读取 END 汇总。
            self._last_save = None
            self._last_restore = None
            self._last_llama_save = None
            self._last_llama_restore = None
        except Exception as e:
            print(f"[WARN] Failed to start bpftrace: {e}")
            self.enabled = False

    def delta_from_last(self, snapshot):
        """将累计快照转换为相对上一快照的增量。"""
        fields = (
            ("save_count", "_last_save"),
            ("restore_count", "_last_restore"),
            ("llama_save", "_last_llama_save"),
            ("llama_restore", "_last_llama_restore"),
        )
        delta = dict(snapshot)
        for field, last_field in fields:
            current = snapshot.get(field)
            previous = getattr(self, last_field)
            if current is None:
                delta[field] = None
                continue
            delta[field] = current if previous is None else max(0, current - previous)
            setattr(self, last_field, current)
        return delta

    def collect(self):
        """读取 bpftrace 已写入输出并解析累计计数。

        提取 @saves_total / @restores_total（全系统）和
        @saves[llama-server / @restores[llama-server（推理进程专属）。
        """
        if not self.enabled:
            return {"save_count": None, "restore_count": None,
                    "llama_save": None, "llama_restore": None}
        try:
            text = self._read_output()
            import re
            save_count = None
            restore_count = None
            save_map = {}
            restore_map = {}
            for line in text.strip().split("\n"):
                m = re.search(r"@saves_total:\s*(\d+)", line)
                if m: save_count = int(m.group(1))
                m = re.search(r"@restores_total:\s*(\d+)", line)
                if m: restore_count = int(m.group(1))
                m = re.search(r"@saves\[llama-server([^]]+)\]:\s*(\d+)", line)
                if m: save_map[m.group(1)] = int(m.group(2))
                m = re.search(r"@restores\[llama-server([^]]+)\]:\s*(\d+)", line)
                if m: restore_map[m.group(1)] = int(m.group(2))
            llama_save = sum(save_map.values()) if save_map else None
            llama_restore = sum(restore_map.values()) if restore_map else None
            return {"save_count": save_count, "restore_count": restore_count,
                    "llama_save": llama_save, "llama_restore": llama_restore}
        except Exception:
            return {"save_count": None, "restore_count": None,
                    "llama_save": None, "llama_restore": None}

    def _read_output(self):
        """读取完整 bpftrace 输出；最终 END 区块包含本轮的准确累计值。"""
        if self.remote:
            result = self._ssh(f"cat {shlex.quote(self.OUTPUT)} 2>/dev/null || true", timeout=30)
            return result.stdout
        else:
            with open(self.OUTPUT) as f:
                return f.read()

    def stop(self):
        """停止 bpftrace 并采集最终数据。

        先 kill 触发 END 输出，再读文件，确保拿到完整 SUMMARY。
        返回 {"save_count": N, "restore_count": M}。
        """
        result = {"save_count": None, "restore_count": None}
        try:
            if self.remote:
                self._stop_remote_trace()
                result = self.collect()
            elif self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                result = self.collect()
            where = self._ssh_target if self.remote else "localhost"
            print(f"[INFO] V-state trace stopped ({where}): {result}")
        except Exception as e:
            print(f"[WARN] V-state trace stop error: {e}")
        finally:
            self.enabled = False
        return result

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
            "stream": True,
            "temperature": 0,
        }
    else:
        return {
            "prompt": prompt,
            "n_predict": max_tokens,
            "stream": True,
            "temperature": 0,
        }


# ==================== 单请求 ====================

def send_request(url, prompt, max_tokens, timeout, start_barrier=None):
    """发送单次请求，返回客户端测量指标。

    TTFT 是请求开始到首个 SSE content 事件到达的客户端时延。
    """
    if start_barrier is not None:
        start_barrier.wait()
    request_started_at = time.perf_counter()

    payload = make_request_data(prompt, max_tokens, url)
    # requests.post() returns after response headers, not after the first generated token.
    req_start = time.perf_counter()
    try:
        resp = requests.post(url, json=payload, timeout=timeout, stream=True)
        resp.raise_for_status()

        ttft_ms = None
        prompt_ms = 0
        prompt_n = 0
        tokens = 0
        tps = 0

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]  # strip "data: "
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choices = chunk.get("choices", [])
            content = choices[0].get("delta", {}).get("content") if choices else chunk.get("content")
            if ttft_ms is None and content:
                ttft_ms = (time.perf_counter() - req_start) * 1000

            if "timings" in chunk:
                timings = chunk["timings"]
                prompt_ms = timings["prompt_ms"]
                prompt_n = timings.get("prompt_n", 0)
                tokens = timings["predicted_n"]
                tps = timings["predicted_per_second"]

        if ttft_ms is None:
            print("[WARN] Request completed without a content token; excluded from metrics")
            return None

        return {
            "_request_started_at": request_started_at,
            "ttft_ms": ttft_ms,
            "prompt_ms": prompt_ms,
            "prompt_n": prompt_n,
            "tokens": tokens,
            "tps": tps,
            "e2e_ms": (time.perf_counter() - req_start) * 1000,
        }
    except Exception as e:
        print(f"[WARN] Request failed: {e}")
        return None


# ==================== 一轮测试 ====================

def _safe_std(vals):
    """样本标准差，单值时返回 0 而非 nan"""
    if len(vals) < 2:
        return 0.0
    return float(np.std(vals, ddof=1))


def _print_request_result(result, label=""):
    """输出单个请求的客户端指标。"""
    prefix = f"[{label}] " if label else ""
    request_id = result["request_id"]
    print(
        f"  {prefix}Req-{request_id:02d} "
        f"ClientTTFT={result['ttft_ms']:.1f}ms "
        f"Prompt={result['prompt_ms']:.1f}ms "
        f"E2E={result['e2e_ms']:.1f}ms "
        f"TPS={result['tps']:.2f} Tok={result['tokens']}"
    )


def run_trial(
    url,
    concurrency,
    prompt,
    max_tokens,
    timeout,
    vstate=None,
    print_request_details=False,
    trial_label="",
    requests_total=None,
):
    """执行一个客户端请求 burst，返回本轮聚合指标和原始样本。

    concurrency 是客户端 worker 数，可大于 llama-server 的 --parallel；超出的请求由 server 排队。
    默认发送一波 concurrency 个请求。requests_total 较大时，首波同时启动，后续请求在 worker 空闲后派发。
    返回的 _ttft_samples / _prompt_samples / _e2e_samples 用于跨 trial 池化计算 P95。
    """
    results = []
    request_count = concurrency if requests_total is None else requests_total
    if request_count < 1:
        raise ValueError("requests_total must be at least 1")
    first_wave = min(concurrency, request_count)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        batch_start = []
        start_barrier = threading.Barrier(
            first_wave + 1,
            action=lambda: batch_start.append(time.perf_counter()),
        )
        futures = {}
        for request_index in range(request_count):
            barrier = start_barrier if request_index < first_wave else None
            future = pool.submit(send_request, url, prompt, max_tokens, timeout, barrier)
            futures[future] = request_index + 1
        start_barrier.wait()
        for completion_rank, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if result is not None:
                result["request_id"] = futures[future]
                result["completion_rank"] = completion_rank
                result["start_offset_s"] = round(
                    result.pop("_request_started_at") - batch_start[0], 6
                )
                results.append(result)
                if print_request_details:
                    _print_request_result(result, trial_label)

    wall_time = time.perf_counter() - batch_start[0]
    # V 状态切换开销：普通模式读取当前累计快照；自动矩阵在 tracer 停止后读取 END 汇总。
    vstate_data = vstate.collect() if vstate and vstate.enabled else {}
    if vstate and vstate.enabled and vstate_data.get("save_count") is not None:
        vstate_data = vstate.delta_from_last(vstate_data)

    if not results:
        return None

    total_tokens = sum(x["tokens"] for x in results)
    aggregate_tps = total_tokens / wall_time
    ttft_samples = [x["ttft_ms"] for x in results]
    prompt_samples = [x["prompt_ms"] for x in results]
    e2e_samples = [x["e2e_ms"] for x in results]

    def _safe_pct(samples):
        if len(samples) >= 2:
            return float(np.percentile(samples, 95))
        return samples[0]

    avg_ttft = float(np.mean(ttft_samples))
    p95_ttft = _safe_pct(ttft_samples)
    avg_prompt = float(np.mean(prompt_samples))
    p95_prompt = _safe_pct(prompt_samples)
    avg_e2e = float(np.mean(e2e_samples))
    p95_e2e = _safe_pct(e2e_samples)

    ret = {
        "aggregate_tps": aggregate_tps,
        "avg_ttft_ms": avg_ttft,
        "p95_ttft_ms": p95_ttft,
        "avg_prompt_ms": avg_prompt,
        "p95_prompt_ms": p95_prompt,
        "avg_e2e_ms": avg_e2e,
        "p95_e2e_ms": p95_e2e,
        "sample_count": len(results),
        "wall_time_s": round(wall_time, 2),
        "_ttft_samples": ttft_samples,
        "_prompt_samples": prompt_samples,
        "_e2e_samples": e2e_samples,
        "_request_results": sorted(results, key=lambda item: item["request_id"]),
    }
    # V 状态数据
    ret.update(vstate_data)

    return ret


# ==================== 多档位 ====================

def benchmark(url, concurrencies, trials, prompt, max_tokens, timeout, tag, vstate, mode="remote"):
    summary = defaultdict(list)
    raw_rows = []

    for concurrency in concurrencies:
        print()
        print("=" * 60)
        print(f"  [{tag}] Client burst = {concurrency}")
        print("=" * 60)

        for trial in range(1, trials + 1):
            print(f"  Trial {trial}/{trials} ... ", end="", flush=True)

            r = run_trial(url, concurrency, prompt, max_tokens, timeout, vstate)
            if r is None:
                print("FAILED")
                continue

            log = (f"TPS={r['aggregate_tps']:.2f}  "
                   f"TTFT={r['avg_ttft_ms']:.1f}ms  "
                   f"P95={r['p95_ttft_ms']:.1f}ms  "
                   f"Prompt={r['avg_prompt_ms']:.1f}ms  "
                   f"E2E={r['avg_e2e_ms']:.1f}ms")
            print(log)

            summary[concurrency].append(r)
            raw_rows.append({
                "tag": tag,
                "concurrency": concurrency,
                "trial": trial,
                "mode": mode,
                "max_tokens": max_tokens,
                "prompt": prompt[:60] if prompt else "",
                "throughput_tps": round(r["aggregate_tps"], 4),
                "avg_ttft_ms": round(r["avg_ttft_ms"], 2),
                "p95_ttft_ms": round(r["p95_ttft_ms"], 2),
                "avg_prompt_ms": round(r["avg_prompt_ms"], 2),
                "p95_prompt_ms": round(r["p95_prompt_ms"], 2),
                "avg_e2e_ms": round(r["avg_e2e_ms"], 2),
                "p95_e2e_ms": round(r["p95_e2e_ms"], 2),
                "wall_time_s": r["wall_time_s"],
                "sample_count": r["sample_count"],
                "vstate_save_count": r.get("save_count", ""),
                "vstate_restore_count": r.get("restore_count", ""),
                "vstate_llama_save": r.get("llama_save", ""),
                "vstate_llama_restore": r.get("llama_restore", ""),
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
        ttft_vals = [x["avg_ttft_ms"] for x in runs]
        prompt_vals = [x["avg_prompt_ms"] for x in runs]
        e2e_vals = [x["avg_e2e_ms"] for x in runs]

        # Pool all raw samples across trials for true P95
        def _pool_p95(runs, key):
            all_s = []
            for x in runs:
                all_s.extend(x.get(key, []))
            if len(all_s) >= 2:
                return float(np.percentile(all_s, 95)), len(all_s)
            return (all_s[0] if all_s else 0), len(all_s)

        pooled_p95_ttft, n_ttft = _pool_p95(runs, "_ttft_samples")
        pooled_p95_prompt, n_prompt = _pool_p95(runs, "_prompt_samples")
        pooled_p95_e2e, n_e2e = _pool_p95(runs, "_e2e_samples")

        print()
        print(f"--- Client burst = {concurrency} ---")
        print(f"  TPS:              {np.mean(tps_vals):.2f} ± {_safe_std(tps_vals):.2f}")
        print(f"  Avg TTFT(ms):     {np.mean(ttft_vals):.2f} ± {_safe_std(ttft_vals):.2f}  (client-side)")
        print(f"  P95 TTFT(ms):     {pooled_p95_ttft:.2f}  (pooled N={n_ttft})")
        print(f"  Avg Prompt(ms):   {np.mean(prompt_vals):.2f} ± {_safe_std(prompt_vals):.2f}  (llama-server timings)")
        print(f"  P95 Prompt(ms):   {pooled_p95_prompt:.2f}  (pooled N={n_prompt})")
        print(f"  Avg E2E(ms):      {np.mean(e2e_vals):.2f} ± {_safe_std(e2e_vals):.2f}  (client-side end-to-end)")
        print(f"  P95 E2E(ms):      {pooled_p95_e2e:.2f}  (pooled N={n_e2e})")

        # V state 数据（per-trial delta 的合计）
        save_vals = [x.get("save_count") for x in runs if x.get("save_count") is not None]
        restore_vals = [x.get("restore_count") for x in runs if x.get("restore_count") is not None]
        if save_vals:
            print(f"  V save count:     {int(sum(save_vals))}  (sum of {len(save_vals)} trials)")
            print(f"  V restore count:  {int(sum(restore_vals))}")
        ls_vals = [x.get("llama_save") for x in runs if x.get("llama_save") is not None]
        lr_vals = [x.get("llama_restore") for x in runs if x.get("llama_restore") is not None]
        if ls_vals:
            print(f"  V llama save:    {int(sum(ls_vals))}  (llama-server only)")
            print(f"  V llama restore: {int(sum(lr_vals))}")

def print_comparison(all_summaries):
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
        print(f"--- Client burst = {concurrency} ---")
        print(f"  {'Metric':<20} {'Baseline':>12} {'Optimized':>12} {'Change':>12}")

        # Metrics that use simple mean of per-trial values
        for metric, label in [("aggregate_tps", "TPS"), ("avg_ttft_ms", "Avg TTFT(ms)"),
                               ("avg_prompt_ms", "Avg Prompt(ms)"), ("avg_e2e_ms", "Avg E2E(ms)")]:
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

        # P95: pool raw samples across all trials for each tag
        for sample_key, label in [("_ttft_samples", "P95 TTFT(ms)"),
                                   ("_prompt_samples", "P95 Prompt(ms)"),
                                   ("_e2e_samples", "P95 E2E(ms)")]:
            pooled_vals = []
            for summary in all_summaries:
                if concurrency in summary:
                    all_samples = []
                    for x in summary[concurrency]:
                        all_samples.extend(x.get(sample_key, []))
                    if len(all_samples) >= 2:
                        pooled_vals.append(float(np.percentile(all_samples, 95)))
                    elif all_samples:
                        pooled_vals.append(all_samples[0])
                    else:
                        pooled_vals.append(None)
                else:
                    pooled_vals.append(None)

            if pooled_vals[0] is not None and pooled_vals[1] is not None:
                change = (pooled_vals[1] - pooled_vals[0]) / pooled_vals[0] * 100
                print(f"  {label:<20} {pooled_vals[0]:>12.2f} {pooled_vals[1]:>12.2f} {change:>+11.1f}%")
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
        "tag", "mode", "max_tokens", "prompt",
        "concurrency", "trial", "throughput_tps",
        "avg_ttft_ms", "p95_ttft_ms",
        "avg_prompt_ms", "p95_prompt_ms",
        "avg_e2e_ms", "p95_e2e_ms",
        "wall_time_s", "sample_count",
        "vstate_save_count", "vstate_restore_count", "vstate_llama_save", "vstate_llama_restore",
    ]

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nCSV saved: {filename}")
    return filename


# ==================== 自动服务器实验矩阵 ====================

class RemoteLlamaServer:
    """通过 OpenSSH 管理本工具启动的远程 llama-server 进程。"""

    def __init__(
        self,
        host,
        ssh_user,
        binary,
        lib_dir,
        port,
        threads_batch=None,
        batch_size=None,
        ubatch_size=None,
    ):
        self.host = host
        self.ssh_user = ssh_user
        self.target = f"{ssh_user}@{host}" if ssh_user else host
        self.binary = binary
        self.lib_dir = lib_dir
        self.port = port
        self.threads_batch = threads_batch
        self.batch_size = batch_size
        self.ubatch_size = ubatch_size
        self.pidfile = "/tmp/rvv-benchmark-llama-server.pid"
        self.logfile = "/tmp/rvv-benchmark-llama-server.log"
        self.command = ""

    def _ssh(self, command, timeout=30):
        return subprocess.run(
            [
                "ssh", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no",
                self.target, command,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

    def build_command(self, model_path, parallel, threads, context):
        args = [
            self.binary,
            "-m", model_path,
            "-t", str(threads),
        ]
        if self.threads_batch is not None:
            args.extend(["-tb", str(self.threads_batch)])
        if self.batch_size is not None:
            args.extend(["-b", str(self.batch_size)])
        if self.ubatch_size is not None:
            args.extend(["-ub", str(self.ubatch_size)])
        args.extend([
            "--parallel", str(parallel),
            "-c", str(context),
            "--cont-batching",
            "--no-cache-prompt",
            "--host", "0.0.0.0",
            "--port", str(self.port),
        ])
        if self.lib_dir:
            args = ["env", f"LD_LIBRARY_PATH={self.lib_dir}", *args]
        return " ".join(shlex.quote(arg) for arg in args)

    def _stop_managed_server(self):
        command = (
            f"if [ -f {shlex.quote(self.pidfile)} ]; then "
            f"pid=$(cat {shlex.quote(self.pidfile)}); "
            "if [ -n \"$pid\" ] && [ -r \"/proc/$pid/cmdline\" ] && "
            f"tr '\\0' '\\n' < \"/proc/$pid/cmdline\" | grep -Fxq -- {shlex.quote(self.binary)}; then "
            "kill $pid 2>/dev/null || true; "
            "for i in 1 2 3 4 5; do kill -0 $pid 2>/dev/null || break; sleep 1; done; "
            "kill -9 $pid 2>/dev/null || true; fi; "
            f"rm -f {shlex.quote(self.pidfile)}; fi"
        )
        self._ssh(command)

    def start(self, model_path, model_size, parallel, threads, context):
        self._stop_managed_server()

        pattern = f"[{self.binary[0]}]{self.binary[1:]}"
        active = self._ssh(f"pgrep -f -- {shlex.quote(pattern)} || true")
        if active.stdout.strip():
            raise RuntimeError("远程已有非本工具启动的 llama-server，请先手动停止")

        exists = self._ssh(f"test -f {shlex.quote(model_path)}")
        if exists.returncode != 0:
            raise RuntimeError(f"远程模型文件不存在: {model_path}")

        self.command = self.build_command(model_path, parallel, threads, context)
        started_at = datetime.now().isoformat(timespec="seconds")
        launch = (
            f"rm -f {shlex.quote(self.logfile)} {shlex.quote(self.pidfile)}; "
            f"nohup {self.command} > {shlex.quote(self.logfile)} 2>&1 & "
            f"echo $! > {shlex.quote(self.pidfile)}"
        )
        result = self._ssh(launch)
        if result.returncode != 0:
            raise RuntimeError(f"llama-server 启动命令失败: {result.stderr.strip()}")

        return {
            "model_size": model_size,
            "model_path": model_path,
            "server_parallel": parallel,
            "server_threads": threads,
            "server_threads_batch": self.threads_batch if self.threads_batch is not None else "",
            "server_batch_size": self.batch_size if self.batch_size is not None else "",
            "server_ubatch_size": self.ubatch_size if self.ubatch_size is not None else "",
            "server_context": context,
            "server_context_per_slot": context // parallel,
            "server_port": self.port,
            "server_binary": self.binary,
            "server_lib_dir": self.lib_dir,
            "server_command": self.command,
            "server_started_at": started_at,
            "server_host": self.host,
        }

    def wait_ready(self, health_url, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response = requests.get(health_url, timeout=5)
                if response.status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(2)

        log = self._ssh(f"tail -30 {shlex.quote(self.logfile)} || true").stdout.strip()
        raise RuntimeError(f"llama-server 未在 {timeout}s 内就绪。末尾日志:\n{log}")

    def stop(self):
        self._stop_managed_server()


EXPERIMENT_ROUND_FIELDS = [
    "experiment", "model_size", "model_path",
    "server_parallel", "server_threads", "server_threads_batch", "server_batch_size", "server_ubatch_size",
    "server_context", "server_context_per_slot", "server_port",
    "server_binary", "server_lib_dir", "server_command",
    "server_started_at", "server_host",
    "max_tokens", "prompt", "request_count",
    "trial",
    "throughput_tps", "avg_ttft_ms", "p95_ttft_ms",
    "avg_prompt_ms", "p95_prompt_ms", "avg_e2e_ms", "p95_e2e_ms",
    "wall_time_s", "sample_count",
    "vstate_save_count", "vstate_restore_count",
    "vstate_llama_save", "vstate_llama_restore",
    "vstate_scope",
]

EXPERIMENT_REQUEST_FIELDS = [
    "experiment", "model_size", "model_path",
    "server_parallel", "server_threads", "server_threads_batch", "server_batch_size", "server_ubatch_size",
    "server_context", "server_context_per_slot", "server_port",
    "server_binary", "server_lib_dir", "server_command",
    "server_started_at", "server_host",
    "max_tokens", "prompt", "request_count",
    "trial", "request_id", "completion_rank",
    "ttft_ms", "prompt_ms", "e2e_ms", "tps", "tokens",
]


def _experiment_round_row(metadata, experiment, trial, request_count, result):
    row = {
        **metadata,
        "experiment": experiment,
        "max_tokens": result["max_tokens"],
        "prompt": result["prompt"],
        "request_count": request_count,
        "trial": trial,
        "throughput_tps": round(result["aggregate_tps"], 4),
        "avg_ttft_ms": round(result["avg_ttft_ms"], 2),
        "p95_ttft_ms": round(result["p95_ttft_ms"], 2),
        "avg_prompt_ms": round(result["avg_prompt_ms"], 2),
        "p95_prompt_ms": round(result["p95_prompt_ms"], 2),
        "avg_e2e_ms": round(result["avg_e2e_ms"], 2),
        "p95_e2e_ms": round(result["p95_e2e_ms"], 2),
        "wall_time_s": result["wall_time_s"],
        "sample_count": result["sample_count"],
        "vstate_save_count": result.get("save_count", ""),
        "vstate_restore_count": result.get("restore_count", ""),
        "vstate_llama_save": result.get("llama_save", ""),
        "vstate_llama_restore": result.get("llama_restore", ""),
        "vstate_scope": "all_submitted_requests",
    }
    return row


def _experiment_request_rows(metadata, experiment, trial, request_count, result, max_tokens, prompt):
    rows = []
    for request in result.get("_request_results", []):
        rows.append({
            **metadata,
            "experiment": experiment,
            "max_tokens": max_tokens,
            "prompt": prompt,
            "request_count": request_count,
            "trial": trial,
            "request_id": request["request_id"],
            "completion_rank": request.get("completion_rank", ""),
            "ttft_ms": round(request["ttft_ms"], 2),
            "prompt_ms": round(request["prompt_ms"], 2),
            "e2e_ms": round(request["e2e_ms"], 2),
            "tps": round(request["tps"], 4),
            "tokens": request["tokens"],
        })
    return rows


def _save_experiment_csv(rows, fieldnames, output_dir, suffix, stamp):
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, f"rvv_experiment_{stamp}_{suffix}.csv")
    with open(filename, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV saved: {filename}")
    return filename


def _print_round_vstate(result):
    def value(key):
        raw = result.get(key)
        return "N/A" if raw is None or raw == "" else str(raw)

    print(
        "  [VSTATE] shared round delta (not per-request) "
        f"all-save={value('save_count')} all-restore={value('restore_count')} "
        f"llama-save={value('llama_save')} llama-restore={value('llama_restore')}"
    )


def _parse_model_sizes(value):
    aliases = {
        "1": "1B", "1B": "1B",
        "3": "3B", "3B": "3B",
        "8": "8B", "8B": "8B",
    }
    sizes = []
    for item in value.split(","):
        normalized = item.strip().upper()
        if normalized not in aliases:
            raise ValueError("--model-sizes 仅支持 1、3、8，例如 --model-sizes 1,3,8")
        size = aliases[normalized]
        if size not in sizes:
            sizes.append(size)
    if not sizes:
        raise ValueError("--model-sizes 至少需要选择一个模型规模")
    return sizes


def run_experiment_matrix(args, host, url):
    """运行模型规模与 llama-server parallel 两组固定实验。"""
    if not host:
        raise ValueError("--experiment-matrix 需要 --host")
    if not args.ssh_user:
        raise ValueError("--experiment-matrix 需要 --ssh-user，例如 --ssh-user root")
    if args.server_threads is None or args.server_threads <= 0:
        raise ValueError("--experiment-matrix 需要正整数 --server-threads")
    if args.context_per_slot is None or args.context_per_slot <= 0:
        raise ValueError("--experiment-matrix 需要正整数 --context-per-slot")
    for option, value in (
        ("--server-threads-batch", args.server_threads_batch),
        ("--server-batch-size", args.server_batch_size),
        ("--server-ubatch-size", args.server_ubatch_size),
    ):
        if value is not None and value <= 0:
            raise ValueError(f"{option} 必须为正整数")
    from urllib.parse import urlparse
    server_port = args.port or urlparse(url).port or 8080
    server_binary = args.server_binary
    server_lib_dir = args.server_lib_dir
    manager = RemoteLlamaServer(
        host,
        args.ssh_user,
        server_binary,
        server_lib_dir,
        server_port,
        args.server_threads_batch,
        args.server_batch_size,
        args.server_ubatch_size,
    )

    selected_sizes = _parse_model_sizes(args.model_sizes)
    recorded_requests = 32
    plans = []
    if args.experiment_matrix in ("full", "model-scale"):
        plans.extend(
            ("model_scale", size, REMOTE_EXPERIMENT_MODELS[size], 4, 1)
            for size in selected_sizes
        )
    if args.experiment_matrix in ("full", "parallel-sweep"):
        plans.extend(
            ("parallel_sweep", "3B", REMOTE_EXPERIMENT_MODELS["3B"], parallel, 5)
            for parallel in (2, 4, 8, 16)
        )

    if args.dry_run:
        print("[DRY RUN] 不连接板卡，只展示将执行的实验矩阵。")
        for experiment, model_size, model_path, parallel, trials in plans:
            context = args.context_per_slot * parallel
            print(
                f"  {experiment}: {model_size}, --parallel {parallel}, "
                f"-c {context} ({context // parallel}/slot), "
                f"client burst=32, recorded rounds={trials}"
            )
            print(f"    {manager.build_command(model_path, parallel, args.server_threads, context)}")
        return

    max_tokens = MODE_DEFAULTS["remote"]["max_tokens"]
    timeout = None
    prompt = MODE_DEFAULTS["remote"]["prompt"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    round_rows = []
    request_rows = []
    health_url = f"http://{host}:{server_port}/health"

    print("=" * 72)
    print("  RVV LLM 自动实验矩阵")
    print("  每个配置：启动服务 -> 32 条并发记录请求 -> 停止服务")
    timeout_label = f"{timeout}s" if timeout is not None else "无限制"
    print(f"  单请求超时：{timeout_label}")
    print("=" * 72)

    try:
        for experiment, model_size, model_path, parallel, trials in plans:
            context = args.context_per_slot * parallel
            print()
            print("#" * 72)
            print(
                f"  {experiment} | model={model_size} | server slots={parallel} | "
                f"context={context} ({context // parallel}/slot) | "
                f"client burst=32 simultaneous requests | recorded rounds={trials}"
            )
            print("#" * 72)

            metadata = manager.start(
                model_path, model_size, parallel, args.server_threads, context
            )
            manager.wait_ready(health_url)
            print(f"  Server ready: {metadata['server_command']}")

            for trial in range(1, trials + 1):
                print(f"\n  Recorded round {trial}/{trials}: concurrently sending 32 requests")
                vstate = VStateOverhead(
                    remote=host,
                    ssh_user=args.ssh_user,
                    remote_script=args.vstate_script,
                )
                vstate.start()
                try:
                    result = run_trial(
                        url,
                        recorded_requests,
                        prompt,
                        max_tokens,
                        timeout,
                        print_request_details=True,
                        trial_label=f"{model_size}/p{parallel}/t{trial}",
                    )
                finally:
                    raw_trace_result = vstate.stop() if vstate.enabled else {}
                    trace_result = vstate.delta_from_last(raw_trace_result) if raw_trace_result else {}

                if result is None:
                    print("  [WARN] 本轮没有成功请求，跳过写入。")
                    continue
                result.update(trace_result)
                result["max_tokens"] = max_tokens
                result["prompt"] = prompt
                _print_round_vstate(result)
                round_rows.append(_experiment_round_row(
                    metadata, experiment, trial, recorded_requests, result
                ))
                request_rows.extend(_experiment_request_rows(
                    metadata, experiment, trial, recorded_requests, result, max_tokens, prompt
                ))
            manager.stop()
    finally:
        manager.stop()

    _save_experiment_csv(round_rows, EXPERIMENT_ROUND_FIELDS, args.output_dir, "rounds", stamp)
    _save_experiment_csv(request_rows, EXPERIMENT_REQUEST_FIELDS, args.output_dir, "requests", stamp)


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

    print(f"  URL:           {url}")
    print(f"  Client burst:  {concurrencies}")
    print(f"  Trials:        {trials}")
    print("  Streaming:     True (real client TTFT)")
    print(f"  Max tokens:    {max_tokens}")
    print(f"  Timeout:       {timeout}s")

    summary, rows = benchmark(url, concurrencies, trials, prompt, max_tokens, timeout, tag, vstate, args.mode)
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
        help="每个客户端 burst 同时提交的请求数，逗号分隔 (default: 2,4,8)")
    parser.add_argument("--trials", type=int, default=None,
        help="每档重复次数 (default: 3)")
    parser.add_argument("--max-tokens", type=int, default=None,
        help="每个请求最大生成 token 数")
    parser.add_argument("--timeout", type=int, default=None,
        help="每个请求超时秒数；自动实验矩阵固定为无限制，其他模式按各自默认值")
    parser.add_argument("--prompt", default=None,
        help="输入 prompt")
    parser.add_argument("--ssh-user", default=None,
        help="远程模式 SSH 用户名，用于自动启动 llama-server 和 V-state 追踪")
    parser.add_argument("--experiment-matrix",
        choices=["full", "model-scale", "parallel-sweep"],
        help=("自动远程实验：model-scale=1B/3B/8B 且 --parallel 4，"
              "parallel-sweep=3B 且 --parallel 2/4/8/16，full=两组都跑")
    )
    parser.add_argument("--server-binary", default="/root/llama-server-rvv",
        help="板卡上 llama-server 的绝对路径")
    parser.add_argument("--server-lib-dir", default="",
        help="板卡上 LD_LIBRARY_PATH；默认继承板卡环境，仅在动态库报错时显式设置")
    parser.add_argument("--server-threads", type=int,
        help="自动实验 llama-server 的 -t；运行矩阵时必须指定")
    parser.add_argument("--context-per-slot", type=int,
        help="自动实验每个 slot 的 context；-c 自动计算为该值乘以 --parallel")
    parser.add_argument("--server-threads-batch", type=int,
        help="可选的 -tb；不指定时使用 llama-server 默认值（与 -t 相同）")
    parser.add_argument("--server-batch-size", type=int,
        help="可选的 -b；不指定时使用 llama-server 默认值")
    parser.add_argument("--server-ubatch-size", type=int,
        help="可选的 -ub；不指定时使用 llama-server 默认值")
    parser.add_argument("--model-sizes", default="1,3,8",
        help="model-scale 使用的模型规模，逗号分隔：1,3,8 或 1B,3B,8B (default: 1,3,8)")
    parser.add_argument("--vstate-script", default=VStateOverhead.SCRIPT_REMOTE,
        help="板卡上 bpftrace 脚本的绝对路径 (default: /root/vstate_trace.bt)")
    parser.add_argument("--output-dir", default="results",
        help="自动实验 CSV 输出目录 (default: results)")
    parser.add_argument("--dry-run", action="store_true",
        help="仅展示自动实验矩阵和 server 命令，不连接板卡")

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

    if args.experiment_matrix and args.url:
        parser.error("--experiment-matrix 使用 --host 和 --port，不接受 --url")

    # --- 确定 URL ---
    host = None
    if args.url:
        url = args.url
        if mode == "remote":
            from urllib.parse import urlparse
            host = urlparse(url).hostname
    elif mode == "remote":
        if not args.host:
            parser.error("--mode remote 需要 --host 或 --url")
        host = args.host
        url = DEFAULT_URLS["remote"].format(host=host)
    else:
        url = DEFAULT_URLS[mode]
    if args.port:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        netloc = parsed.hostname + (f":{args.port}")
        url = urlunparse(parsed._replace(netloc=netloc))

    # ========================================
    #  自动远程实验：由脚本重启 llama-server 并运行固定矩阵
    # ========================================
    if args.experiment_matrix:
        if mode != "remote":
            parser.error("--experiment-matrix 仅支持 --mode remote")
        try:
            run_experiment_matrix(args, host, url)
        except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            parser.error(str(exc))
        return

    # --- V 状态采集（bpftrace 远程或本地） ---
    use_vstate = (mode != "remote") or args.ssh_user is not None
    vstate = VStateOverhead(
        remote=host if mode == "remote" else None,
        ssh_user=args.ssh_user,
    ) if use_vstate else VStateOverhead()
    if use_vstate:
        vstate.start()

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
        r1, r2 = [], []

        # 第一轮: baseline
        s1, r1 = run_one_benchmark(args, defaults, vstate, "baseline", url)
        if s1:
            all_summaries.append(s1)
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
            save_csv(r2, "optimized")

        # 对比报告
        if len(all_summaries) >= 2:
            print_comparison(all_summaries)
        else:
            print("\n[SKIP] 对比需要 baseline + optimized 两组数据")

        # 合并 CSV
        all_rows = []
        if r1: all_rows.extend(r1)
        if r2: all_rows.extend(r2)
        if all_rows:
            save_csv(all_rows, "comparison")

        vstate_data = vstate.stop()
        if vstate_data and vstate_data.get("save_count") is not None:
            print(f"\n[TRACE] Total V-state: saves={vstate_data['save_count']}, restores={vstate_data['restore_count']}")
        return

    # ========================================
    #  单模式
    # ========================================
    tag = args.tag or mode
    s, rows = run_one_benchmark(args, defaults, vstate, tag, url)

    vstate_data = vstate.stop()
    if vstate_data and vstate_data.get("save_count") is not None:
        print(f"[TRACE] Total V-state: saves={vstate_data['save_count']}, restores={vstate_data['restore_count']}")

    if rows:
        save_csv(rows, tag)


if __name__ == "__main__":
    main()
