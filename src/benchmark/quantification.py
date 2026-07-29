#!/usr/bin/env python3

import argparse
import csv
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

DEFAULT_PORT = 8080
REQUEST_COUNT = 20
CLIENT_WORKERS = 20
# PROMPT_TOKEN_OPTIONS = [64, 256, 512, 1024]
PROMPT_TOKEN_OPTIONS = [64]
DEFAULT_OUTPUT_TOKENS = 8
PROMPT_BASE = (
    "The capital of France is Paris. The capital of Germany is Berlin. "
    "The capital of Italy is Rome. The capital of Spain is Madrid. "
)

def build_prompt(target_tokens: int) -> str:
    """按照文档示例构造长度大致为 target_tokens 的 Prompt。"""
    words_per_token = 1.3
    num_words = int(target_tokens * words_per_token) + 10
    words = PROMPT_BASE.split()
    repeated = (words * ((num_words // len(words)) + 1))[:num_words]
    return " ".join(repeated)

def p95_20(values):
    """20条请求排序后直接取第19条"""
    if len(values) != 20:
        return None
    return sorted(values)[18]

def send_request(url: str, prompt_target_tokens: int, n_predict: int, timeout=None) -> dict:
    """发送单个请求并等待完整响应；timeout=None 表示永不超时。"""
    prompt = build_prompt(prompt_target_tokens)
    payload = {
        "prompt": prompt,
        "n_predict": n_predict,
        "temperature": 0.7,
        # 文档示例是非流式；为取得真实 TTFT，仅此处改为流式。
        "stream": True,
    }
    started = time.perf_counter()

    try:
        with requests.Session() as session:
            session.trust_env = False
            # 每条请求独立计时，起点紧邻 HTTP 发送调用。
            started = time.perf_counter()
            with session.post(url, json=payload, timeout=timeout, stream=True) as response:
                response.raise_for_status()
                ttft_ms = None
                prompt_ms = None
                tokens = None
                predicted_ms = None
                request_tps = None

                for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                    if not line or not line.startswith("data: "):
                        continue
                    data_text = line[6:]
                    if data_text == "[DONE]":
                        break
                    try:
                        event = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue

                    token_ids = event.get("tokens")
                    has_token = isinstance(token_ids, list) and len(token_ids) > 0
                    if ttft_ms is None and (
                        has_token or event.get("content") not in (None, "")
                    ):
                        ttft_ms = (time.perf_counter() - started) * 1000

                    timings = event.get("timings", {})
                    if timings.get("prompt_ms") is not None:
                        prompt_ms = float(timings["prompt_ms"])
                    if timings.get("predicted_n") is not None:
                        tokens = int(timings["predicted_n"])
                    if timings.get("predicted_ms") is not None:
                        predicted_ms = float(timings["predicted_ms"])
                    if timings.get("predicted_per_second") is not None:
                        request_tps = float(timings["predicted_per_second"])

        e2e_ms = (time.perf_counter() - started) * 1000
        if None in (ttft_ms, prompt_ms, tokens, predicted_ms, request_tps):
            raise RuntimeError("响应中缺少 TTFT、Prompt 或 llama-server timings")

        return {
            "status": "ok",
            "error": "",
            "prompt_target_tokens": prompt_target_tokens,
            "n_predict": n_predict,
            "ttft_ms": ttft_ms,
            "prompt_ms": prompt_ms,
            "e2e_ms": e2e_ms,
            "predicted_ms": predicted_ms,
            "tps": request_tps,
            "tokens": tokens,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error": str(exc),
            "prompt_target_tokens": prompt_target_tokens,
            "n_predict": n_predict,
            "ttft_ms": "",
            "prompt_ms": "",
            "e2e_ms": (time.perf_counter() - started) * 1000,
            "predicted_ms": "",
            "tps": "",
            "tokens": 0,
        }

def make_groups(count):
    """按照文档示例将请求随机拆成每组1至8条。"""
    groups = []
    remaining = count
    while remaining > 0:
        size = random.randint(1, min(8, remaining))
        groups.append(size)
        remaining -= size
    return groups

def choose_prompt_tokens(fixed_prompt_tokens):
    if fixed_prompt_tokens is not None:
        return fixed_prompt_tokens
    return random.choice(PROMPT_TOKEN_OPTIONS)

def mode_grouped(
    url: str,
    fixed_prompt_tokens,
    output_tokens: int,
):
    """组内间隔0.2秒、组间间隔0.5秒，最后等待全部请求完成。"""
    groups = make_groups(REQUEST_COUNT)
    print(
        f"分组发送: {groups}，总请求数={REQUEST_COUNT}，"
        f"线程池={CLIENT_WORKERS}"
    )
    started = time.perf_counter()
    futures = {}
    request_id = 1

    with ThreadPoolExecutor(max_workers=CLIENT_WORKERS) as executor:
        for group_id, group_size in enumerate(groups, start=1):
            for request_in_group in range(1, group_size + 1):
                prompt_target_tokens = choose_prompt_tokens(fixed_prompt_tokens)
                future = executor.submit(
                    send_request,
                    url,
                    prompt_target_tokens,
                    output_tokens,
                )
                futures[future] = (request_id, group_id)
                request_id += 1
                if request_in_group < group_size:
                    time.sleep(0.2)
            if group_id < len(groups):
                time.sleep(0.5)

        print(f"已提交 {len(futures)} 条请求，等待全部完成...")
        results = collect_results(futures)

    return results, time.perf_counter() - started, groups

def mode_all_at_once(
    url: str,
    fixed_prompt_tokens,
    output_tokens: int,
):
    """按照文档示例一次性提交全部请求，最后等待全部完成。"""
    print(f"连续发送: 总请求数={REQUEST_COUNT}，线程池={CLIENT_WORKERS}")
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=CLIENT_WORKERS) as executor:
        futures = {
            executor.submit(
                send_request,
                url,
                choose_prompt_tokens(fixed_prompt_tokens),
                output_tokens,
            ): (request_id, 1)
            for request_id in range(1, REQUEST_COUNT + 1)
        }
        results = collect_results(futures)
    return results, time.perf_counter() - started, [REQUEST_COUNT]

def collect_results(futures):
    results = []
    for future in as_completed(futures):
        result = future.result()
        result["request_id"], result["group_id"] = futures[future]
        results.append(result)
        if result["status"] == "ok":
            print(
                f"  Req-{result['request_id']:02d} Group={result['group_id']} "
                f"PromptN={result['prompt_target_tokens']} "
                f"OutputN={result['n_predict']} "
                f"TTFT={result['ttft_ms']:.1f}ms "
                f"Prompt={result['prompt_ms']:.1f}ms "
                f"E2E={result['e2e_ms']:.1f}ms "
                f"TPS={result['tps']:.2f} Tokens={result['tokens']}"
            )
        else:
            print(f"  Req-{result['request_id']:02d} FAILED: {result['error']}")
    return sorted(results, key=lambda row: row["request_id"])

def summarize(trial, results, wall_time, args, groups):
    successful = [result for result in results if result["status"] == "ok"]
    ttfts = [result["ttft_ms"] for result in successful]
    prompts = [result["prompt_ms"] for result in successful]
    e2es = [result["e2e_ms"] for result in successful]
    tps_values = [result["tps"] for result in successful]
    total_tokens = sum(result["tokens"] for result in successful)
    p95_ttft = p95_20(ttfts)
    p95_prompt = p95_20(prompts)
    p95_e2e = p95_20(e2es)

    return {
        "platform": args.platform,
        "url": args.url,
        "mode": args.mode,
        "trial": trial,
        "groups": ",".join(str(size) for size in groups),
        "requests": len(results),
        "successful": len(successful),
        "failed": len(results) - len(successful),
        "prompt_token_setting": (
            str(args.prompt_tokens)
            if args.prompt_tokens is not None
            else ",".join(str(value) for value in PROMPT_TOKEN_OPTIONS)
        ),
        "output_tokens": args.output_tokens,
        "wall_time_s": round(wall_time, 4),
        "aggregate_throughput_tps": round(total_tokens / wall_time, 4),
        "avg_tps": round(sum(tps_values) / len(tps_values), 4) if tps_values else "",
        "avg_ttft_ms": round(sum(ttfts) / len(ttfts), 2) if ttfts else "",
        "p95_ttft_ms": round(p95_ttft, 2) if p95_ttft is not None else "",
        "avg_prompt_ms": round(sum(prompts) / len(prompts), 2) if prompts else "",
        "p95_prompt_ms": round(p95_prompt, 2) if p95_prompt is not None else "",
        "avg_e2e_ms": round(sum(e2es) / len(e2es), 2) if e2es else "",
        "p95_e2e_ms": round(p95_e2e, 2) if p95_e2e is not None else "",
    }

def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", required=True, help="llama-server 所在主机的 IP 地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--platform", choices=["x86", "riscv"], required=True)
    parser.add_argument(
        "--mode",
        choices=["grouped", "all-at-once"],
        default="grouped",
        help="grouped 是随机分组间隔发送，all-at-once 是 20 条请求一次性提交",
    )
    parser.add_argument(
        "--prompt-tokens",
        type=int,
        help="固定 Prompt 构造目标长度；不指定则从 64,256,512,1024 随机选择",
    )
    parser.add_argument(
        "--output-tokens",
        type=int,
        default=DEFAULT_OUTPUT_TOKENS,
        help="每条请求的最大输出 token 数，默认 8",
    )
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 到 65535 之间")
    if args.trials < 1:
        parser.error("--trials 必须大于 0")
    if args.prompt_tokens is not None and args.prompt_tokens < 1:
        parser.error("--prompt-tokens 必须大于 0")
    if args.output_tokens < 1:
        parser.error("--output-tokens 必须大于 0")

    all_requests = []
    summaries = []
    runner = mode_grouped if args.mode == "grouped" else mode_all_at_once
    args.url = f"http://{args.ip}:{args.port}/completion"

    for trial in range(1, args.trials + 1):
        print(f"\nTrial {trial}/{args.trials}")
        results, wall_time, groups = runner(
            args.url,
            args.prompt_tokens,
            args.output_tokens,
        )
        for result in results:
            all_requests.append(
                {
                    "platform": args.platform,
                    "mode": args.mode,
                    "trial": trial,
                    **result,
                }
            )
        summaries.append(summarize(trial, results, wall_time, args, groups))

    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"quant_{args.platform}_{args.mode}_{timestamp}"
    request_path = os.path.join(args.output_dir, f"{prefix}_requests.csv")
    summary_path = os.path.join(args.output_dir, f"{prefix}_summary.csv")
    write_csv(
        request_path,
        all_requests,
        [
            "platform", "mode", "trial", "request_id", "group_id", "status", "error",
            "prompt_target_tokens", "n_predict", "ttft_ms", "prompt_ms", "e2e_ms",
            "predicted_ms", "tps", "tokens",
        ],
    )
    write_csv(summary_path, summaries, list(summaries[0].keys()))
    print(f"\n逐请求记录: {os.path.abspath(request_path)}")
    print(f"汇总记录:   {os.path.abspath(summary_path)}")

if __name__ == "__main__":
    main()
