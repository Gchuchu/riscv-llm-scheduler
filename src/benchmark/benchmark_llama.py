#!/usr/bin/env python3
"""
RISC-V LLM 推理并发调度优化 — 基准测试脚本 (Issue #4)

支持三种模式:
  qemu:   宿主机发请求到 QEMU VM 的端口转发 (默认)
  local:  直接在机器上跑（读取本地 PID /proc）
  remote: 连远程板子的 IP 地址

指标说明:
  TPS        吞吐量 (token/s)        越高越好。 aggregate_tps = 总token数 / 整轮挂钟时间
  TTFT       首 token 延迟 (ms)        越低越好。 客户端测量：非流式=E2E，流式=首个SSE事件到达
  Prompt Ms  服务端 prompt 处理 (ms)    越低越好。 server-side prompt eval，不含排队
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
"""

import argparse
import csv
import json
import os
import shutil
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
        "rounds": 3,
        "streaming": False,
        "max_tokens": 32,
        "timeout": 1800,
        "concurrency": "2,4,8",
        "prompt": "What is RISC-V Vector Extension and its impact on AI inference?",
    },
    "local": {
        "trials": 3,
        "rounds": 3,
        "streaming": False,
        "max_tokens": 128,
        "timeout": 600,
        "concurrency": "2,4,8",
        "prompt": "What is RISC-V Vector Extension and its impact on AI inference?",
    },
    "remote": {
        "trials": 3,
        "rounds": 3,
        "streaming": False,
        "max_tokens": 128,
        "timeout": 600,
        "concurrency": "2,4,8",
        "prompt": "What is RISC-V Vector Extension and its impact on AI inference?",
    },
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
        return subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
             self._ssh_target, cmd],
            capture_output=True, text=True, timeout=timeout
        )

    def available(self):
        """检测 bpftrace 和脚本是否就绪"""
        if self.remote:
            result = self._ssh("echo ok && which bpftrace")
            return result.returncode == 0 and "bpftrace" in result.stdout
        else:
            return shutil.which("bpftrace") is not None and os.path.exists(self.SCRIPT)

    def start(self):
        """启动 bpftrace 后台采集"""
        if not self.available():
            return
        try:
            if self.remote:
                self._ssh(
                    f"nohup stdbuf -oL bpftrace {self.script} -o {self.OUTPUT} "
                    f"> /dev/null 2>&1 &"
                )
            else:
                self._proc = subprocess.Popen(
                    ["sudo", "stdbuf", "-oL", "bpftrace", self.SCRIPT, "-o", self.OUTPUT],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            self.enabled = True
            where = self._ssh_target if self.remote else "localhost"
            print(f"[INFO] V-state trace started ({where})")
            # 等 bpftrace 第一次 interval 输出（最多 3s）
            for _ in range(3):
                time.sleep(1)
                baseline = self.collect()
                if baseline.get("save_count") is not None:
                    break
            self._last_save = baseline.get("save_count")
            self._last_restore = baseline.get("restore_count")
            self._last_llama_save = baseline.get("llama_save")
            self._last_llama_restore = baseline.get("llama_restore")
        except Exception as e:
            print(f"[WARN] Failed to start bpftrace: {e}")
            self.enabled = False

    def collect(self, concurrency=None, trial=None):
        """读取 bpftrace 最新 interval 输出。

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
            llama_save = None
            llama_restore = None
            for line in text.strip().split("\n"):
                m = re.search(r"@saves_total:\s*(\d+)", line)
                if m: save_count = int(m.group(1))
                m = re.search(r"@restores_total:\s*(\d+)", line)
                if m: restore_count = int(m.group(1))
                m = re.search(r"@saves\[llama-server[^]]+\]:\s*(\d+)", line)
                if m: llama_save = (llama_save or 0) + int(m.group(1))
                m = re.search(r"@restores\[llama-server[^]]+\]:\s*(\d+)", line)
                if m: llama_restore = (llama_restore or 0) + int(m.group(1))
            return {"save_count": save_count, "restore_count": restore_count,
                    "llama_save": llama_save, "llama_restore": llama_restore}
        except Exception:
            return {"save_count": None, "restore_count": None,
                    "llama_save": None, "llama_restore": None}

    def _read_output(self):
        """读取 bpftrace 输出中所有 vstate 相关行"""
        if self.remote:
            result = self._ssh(
                "grep -a '@saves_total\\|@restores_total\\|@saves\\[llama\\|@restores\\[llama' "
                f"{self.OUTPUT} | tail -50"
            )
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
                self._ssh("pkill -f 'bpftrace vstate_trace'", timeout=30)
                time.sleep(1)
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

    def summary_table(self, all_data):
        """生成量化表格 Markdown（TODO: Issue #2）"""
        return "# V State Overhead Table\n(TODO: depends on Issue #2)\n"


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

def make_request_data(prompt, max_tokens, url, stream=False):
    if "/chat/completions" in url:
        return {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": stream,
            "temperature": 0,
        }
    else:
        return {"prompt": prompt, "n_predict": max_tokens, "stream": stream, "temperature": 0}


def parse_response(result, url):
    """从 API 响应中提取服务端指标。

    返回 prompt_ms（服务端 prompt 处理时间），而非客户端 TTFT。
    客户端 TTFT 由 send_request 根据请求模式（流式/非流式）独立测量。
    """
    timings = result.get("timings", {})
    usage = result.get("usage", {})

    if "/chat/completions" in url:
        prompt_ms = timings.get("prompt_ms", 0)
        completion_tokens = usage.get("completion_tokens", timings.get("predicted_n", 0))
        predicted_ms = timings.get("predicted_ms", 0)
        predicted_tps = timings.get("predicted_per_second", 0)
    else:
        prompt_ms = timings.get("prompt_ms", 0)
        completion_tokens = result.get("tokens_predicted", 0)
        predicted_ms = timings.get("predicted_ms", 0)
        predicted_tps = timings.get("predicted_per_second", 0)

    tps = predicted_tps if predicted_tps else (
        completion_tokens / (predicted_ms / 1000) if predicted_ms else 0
    )

    return {
        "prompt_ms": prompt_ms,
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

def send_request(url, prompt, max_tokens, timeout, stream=False):
    """发送单次请求，返回客户端测量指标。

    - 非流式 (stream=False): TTFT = E2E（所有 token 一次性返回，无独立首 token 事件）
    - 流式   (stream=True):  TTFT = 首个 SSE content 事件到达时间，E2E = 流结束时间
    - prompt_ms 始终来自服务端 timings（流式下可能为 0，取决于服务端是否在 SSE 中返回）
    """
    payload = make_request_data(prompt, max_tokens, url, stream)
    req_start = time.time()
    try:
        if stream:
            return _send_request_streaming(url, payload, timeout, req_start)
        else:
            return _send_request_non_streaming(url, payload, timeout, req_start)
    except Exception as e:
        print(f"[WARN] Request failed: {e}")
        return None


def _send_request_non_streaming(url, payload, timeout, req_start):
    """非流式请求：TTFT = E2E（全部 token 一次性到达）"""
    resp = requests.post(url, json=payload, timeout=timeout)
    resp_end = time.time()
    resp.raise_for_status()
    result = parse_response(resp.json(), url)
    e2e_ms = (resp_end - req_start) * 1000
    result["e2e_ms"] = e2e_ms
    result["ttft_ms"] = e2e_ms          # 非流式：首 token = 全部到达
    return result


def _send_request_streaming(url, payload, timeout, req_start):
    """流式请求：TTFT = 首个 SSE content 事件到达时间"""
    resp = requests.post(url, json=payload, timeout=timeout, stream=True)
    resp.raise_for_status()

    ttft_ms = None
    prompt_ms = 0
    tokens = 0
    predicted_ms = 0
    predicted_tps = 0
    sse_content_events = 0  # fallback: 计数 SSE content 事件作为 token 数

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

        # 首个包含 content 的 chunk → 记录客户端真实 TTFT
        if ttft_ms is None:
            choices = chunk.get("choices", [])
            if choices and choices[0].get("delta", {}).get("content"):
                ttft_ms = (time.time() - req_start) * 1000

        # 计数 SSE content 事件（每个 ≈ 1 个生成的 token）
        choices = chunk.get("choices", [])
        if choices and choices[0].get("delta", {}).get("content"):
            sse_content_events += 1

        # 从 chunk 中提取服务端 timings（如果服务端在 SSE 中返回）
        if "timings" in chunk:
            t = chunk["timings"]
            prompt_ms = t.get("prompt_ms", 0)
            predicted_ms = t.get("predicted_ms", 0)
            predicted_tps = t.get("predicted_per_second", 0)

        # usage 可能随最后一个 chunk 返回
        if "usage" in chunk:
            tokens = chunk["usage"].get("completion_tokens", 0)

        # 也尝试从 x-timings 获取
        if "x-timings" in chunk:
            t = chunk["x-timings"]
            if prompt_ms == 0:
                prompt_ms = t.get("prompt_ms", 0)
            predicted_ms = t.get("predicted_ms", 0) or predicted_ms
            predicted_tps = t.get("predicted_per_second", 0) or predicted_tps

    e2e_ms = (time.time() - req_start) * 1000
    if ttft_ms is None:
        ttft_ms = e2e_ms  # fallback: 没有 content chunk（极端情况）

    # 如果 SSE 中没有返回 usage，用 content 事件数作为 token 数
    if tokens == 0 and sse_content_events > 0:
        tokens = sse_content_events
        predicted_ms = e2e_ms - (ttft_ms or 0)  # 近似生成时间

    tps = predicted_tps if predicted_tps else (
        tokens / (predicted_ms / 1000) if predicted_ms else 0
    )

    return {
        "ttft_ms": ttft_ms,
        "prompt_ms": prompt_ms,
        "tokens": tokens,
        "predicted_ms": predicted_ms,
        "tps": tps,
        "e2e_ms": e2e_ms,
    }


# ==================== 一轮测试 ====================

def _safe_std(vals):
    """样本标准差，单值时返回 0 而非 nan"""
    if len(vals) < 2:
        return 0.0
    return float(np.std(vals, ddof=1))


def run_trial(url, concurrency, prompt, max_tokens, timeout, rounds=1, stream=False, server_pid=None, vstate=None):
    """一轮测试：发送 concurrency × rounds 个并发请求，返回聚合指标和原始样本。

    每 round 提交 concurrency 个请求，等待全部完成后开始下一 round。
    返回的 _ttft_samples / _prompt_samples / _e2e_samples 用于跨 trial 池化计算 P95。
    """
    rss_before = get_rss_mib(server_pid) if server_pid else None

    batch_start = time.time()
    results = []

    for _ in range(rounds):
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(send_request, url, prompt, max_tokens, timeout, stream)
                for _ in range(concurrency)
            ]
            for f in as_completed(futures):
                r = f.result()
                if r is not None:
                    results.append(r)

    wall_time = time.time() - batch_start
    rss_after = get_rss_mib(server_pid) if server_pid else None

    # V 状态切换开销——bpftrace 每 10s 刷新文件，读最新累积值
    vstate_data = vstate.collect(concurrency, None) if vstate and vstate.enabled else {}
    if vstate and vstate.enabled and vstate_data.get("save_count") is not None:
        raw_save = vstate_data["save_count"]
        raw_restore = vstate_data["restore_count"]
        raw_ls = vstate_data.get("llama_save")
        raw_lr = vstate_data.get("llama_restore")
        if vstate._last_save is None:
            vstate._last_save = raw_save
            vstate._last_restore = raw_restore
            vstate._last_llama_save = raw_ls
            vstate._last_llama_restore = raw_lr
            vstate_data["save_count"] = 0
            vstate_data["restore_count"] = 0
            vstate_data["llama_save"] = 0
            vstate_data["llama_restore"] = 0
        else:
            vstate_data["save_count"] = max(0, raw_save - vstate._last_save)
            vstate_data["restore_count"] = max(0, raw_restore - vstate._last_restore)
            if raw_ls is not None and vstate._last_llama_save is not None:
                vstate_data["llama_save"] = max(0, raw_ls - vstate._last_llama_save)
            if raw_lr is not None and vstate._last_llama_restore is not None:
                vstate_data["llama_restore"] = max(0, raw_lr - vstate._last_llama_restore)
            vstate._last_save = raw_save
            vstate._last_restore = raw_restore
            vstate._last_llama_save = raw_ls
            vstate._last_llama_restore = raw_lr

    if not results:
        return None

    total_tokens = sum(x["tokens"] for x in results)
    aggregate_tps = total_tokens / wall_time if wall_time > 0 else 0
    ttft_samples = [x["ttft_ms"] for x in results]
    prompt_samples = [x["prompt_ms"] for x in results]
    e2e_samples = [x["e2e_ms"] for x in results]

    def _safe_pct(samples):
        if len(samples) >= 2:
            return float(np.percentile(samples, 95))
        return samples[0] if samples else 0

    avg_ttft = float(np.mean(ttft_samples)) if ttft_samples else 0
    p95_ttft = _safe_pct(ttft_samples)
    avg_prompt = float(np.mean(prompt_samples)) if prompt_samples else 0
    p95_prompt = _safe_pct(prompt_samples)
    avg_e2e = float(np.mean(e2e_samples)) if e2e_samples else 0
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
    }
    # V 状态数据
    ret.update(vstate_data)
    if rss_before is not None:
        ret["rss_before_mib"] = rss_before
    if rss_after is not None:
        ret["rss_after_mib"] = rss_after

    return ret


# ==================== 多档位 ====================

def benchmark(url, concurrencies, trials, prompt, max_tokens, timeout, rounds, stream, server_pid, tag, vstate):
    summary = defaultdict(list)
    raw_rows = []

    for concurrency in concurrencies:
        print()
        print("=" * 60)
        print(f"  [{tag}] Concurrency = {concurrency}")
        print("=" * 60)

        # 每个并发档位先跑一轮预热（消除冷启动偏差），数据保留并标注 is_warmup
        print(f"  Warmup ... ", end="", flush=True)
        warmup_r = run_trial(url, concurrency, prompt, max_tokens, timeout, 1, stream, server_pid, vstate)
        if warmup_r:
            log = (f"TPS={warmup_r['aggregate_tps']:.2f}  "
                   f"TTFT={warmup_r['avg_ttft_ms']:.1f}ms  "
                   f"P95={warmup_r['p95_ttft_ms']:.1f}ms  "
                   f"Prompt={warmup_r['avg_prompt_ms']:.1f}ms  "
                   f"E2E={warmup_r['avg_e2e_ms']:.1f}ms")
            if "rss_before_mib" in warmup_r:
                log += f"  Mem={warmup_r['rss_before_mib']}->{warmup_r['rss_after_mib']}MiB"
            print(log)
            raw_rows.append({
                "tag": tag,
                "concurrency": concurrency,
                "trial": 0,
                "throughput_tps": round(warmup_r["aggregate_tps"], 4),
                "avg_ttft_ms": round(warmup_r["avg_ttft_ms"], 2),
                "p95_ttft_ms": round(warmup_r["p95_ttft_ms"], 2),
                "avg_prompt_ms": round(warmup_r["avg_prompt_ms"], 2),
                "p95_prompt_ms": round(warmup_r["p95_prompt_ms"], 2),
                "avg_e2e_ms": round(warmup_r["avg_e2e_ms"], 2),
                "p95_e2e_ms": round(warmup_r["p95_e2e_ms"], 2),
                "wall_time_s": warmup_r["wall_time_s"],
                "sample_count": warmup_r["sample_count"],
                "vstate_save_count": warmup_r.get("save_count", ""),
                "vstate_restore_count": warmup_r.get("restore_count", ""),
                "vstate_llama_save": warmup_r.get("llama_save", ""),
                "vstate_llama_restore": warmup_r.get("llama_restore", ""),
                "rss_idle_mib": warmup_r.get("rss_before_mib", ""),
                "rss_after_mib": warmup_r.get("rss_after_mib", ""),
                "is_warmup": True,
            })
        else:
            print("FAILED (continuing anyway)")

        for trial in range(1, trials + 1):
            print(f"  Trial {trial}/{trials} ... ", end="", flush=True)

            r = run_trial(url, concurrency, prompt, max_tokens, timeout, rounds, stream, server_pid, vstate)
            if r is None:
                print("FAILED")
                continue

            log = (f"TPS={r['aggregate_tps']:.2f}  "
                   f"TTFT={r['avg_ttft_ms']:.1f}ms  "
                   f"P95={r['p95_ttft_ms']:.1f}ms  "
                   f"Prompt={r['avg_prompt_ms']:.1f}ms  "
                   f"E2E={r['avg_e2e_ms']:.1f}ms")
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
                "rss_idle_mib": r.get("rss_before_mib", ""),
                "rss_after_mib": r.get("rss_after_mib", ""),
                "is_warmup": False,
            })

    return summary, raw_rows


# ==================== 报告输出 ====================

def print_report(summary, tag, streaming=False):
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
        print(f"--- Concurrency = {concurrency} ---")
        print(f"  TPS:              {np.mean(tps_vals):.2f} ± {_safe_std(tps_vals):.2f}")
        print(f"  Avg TTFT(ms):     {np.mean(ttft_vals):.2f} ± {_safe_std(ttft_vals):.2f}  (client-side, {'streaming' if streaming else 'non-streaming=E2E'})")
        print(f"  P95 TTFT(ms):     {pooled_p95_ttft:.2f}  (pooled N={n_ttft})")
        print(f"  Avg Prompt(ms):   {np.mean(prompt_vals):.2f} ± {_safe_std(prompt_vals):.2f}  (server-side prompt eval)")
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

        if "rss_before_mib" in runs[0]:
            rss_before = runs[0]["rss_before_mib"]
            rss_after_vals = [x["rss_after_mib"] for x in runs]
            print(f"  RSS idle(MiB):    {rss_before}")
            print(f"  RSS after(MiB):   {np.mean(rss_after_vals):.0f}")



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
        "tag", "concurrency", "trial", "is_warmup", "throughput_tps",
        "avg_ttft_ms", "p95_ttft_ms",
        "avg_prompt_ms", "p95_prompt_ms",
        "avg_e2e_ms", "p95_e2e_ms",
        "wall_time_s", "sample_count",
        "vstate_save_count", "vstate_restore_count", "vstate_llama_save", "vstate_llama_restore",
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
    rounds = getattr(args, 'rounds', None) or defaults.get("rounds", 3)
    stream = getattr(args, 'streaming', None)
    if stream is None:
        stream = defaults.get("streaming", False)
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
    print(f"  Rounds:        {rounds}  (samples per trial = concurrency × {rounds})")
    print(f"  Streaming:     {stream}  ({'real client TTFT' if stream else 'TTFT = E2E'})")
    print(f"  Max tokens:    {max_tokens}")
    print(f"  Timeout:       {timeout}s")

    # --- Warmup: 消除冷启动影响 ---
    print("\n  Warmup request ... ", end="", flush=True)
    # 预热：发一个完整请求（同 prompt + max_tokens），数据丢弃
    # 确保模型加载、prompt cache 初始化完成后才进正式 benchmark
    for attempt in range(5):
        warmup = send_request(url, prompt, max_tokens, timeout, stream)
        if warmup:
            break
        print(f"Warmup failed (attempt {attempt+1}/5), retrying...")
        time.sleep(2)
    if warmup:
        print(f"OK ({warmup['ttft_ms']:.0f}ms / {warmup['tokens']} tok)")
    else:
        print("Warmup FAILED after 5 attempts, continuing...")

    summary, rows = benchmark(url, concurrencies, trials, prompt, max_tokens, timeout, rounds, stream, server_pid, tag, vstate)
    print_report(summary, tag, stream)
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
    parser.add_argument("--rounds", type=int, default=None,
        help="每 trial 内重复批次数 (default: 3)。样本数 = concurrency × rounds，增大可提高 P95 精度")
    parser.add_argument("--streaming", action="store_true", default=None,
        help="使用流式请求 (stream=True)，测量客户端真实 TTFT（首个 SSE 事件到达时间）。非流式下 TTFT = E2E")
    parser.add_argument("--max-tokens", type=int, default=None,
        help="每个请求最大生成 token 数")
    parser.add_argument("--timeout", type=int, default=None,
        help="每个请求超时秒数")
    parser.add_argument("--prompt", default=None,
        help="输入 prompt")
    parser.add_argument("--pid", type=int, default=None,
        help="llama-server PID (仅 local 模式)")
    parser.add_argument("--ssh-user", default=None,
        help="远程模式 SSH 用户名 (default: 当前用户)，用于启停 bpftrace")

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

    # --- 确定 URL ---
    host = None
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

    # --- V 状态采集（bpftrace 远程或本地） ---
    vstate = VStateOverhead(
        remote=host if mode == "remote" else None,
        ssh_user=args.ssh_user,
    )
    if vstate.available():
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
