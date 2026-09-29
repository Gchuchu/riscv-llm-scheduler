#!/usr/bin/env python3
"""CB vs noCB 控制变量实验。

唯一自变量：--cont-batching / --no-cont-batching
固定：模型、构建、并发、--parallel、线程、上下文、batch、prompt/输出长度、采样参数

指标口径直接复用 benchmark_llama.run_trial（客户端流式 TTFT + 总生成 token / 墙钟），
不另写一套计算，避免口径漂移。
"""
import argparse, csv, json, os, shlex, statistics, time, datetime
from pathlib import Path


from benchmark_llama import run_trial  # 复用统一的 TTFT/TPS 指标口径

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST = None
USER = 'root'
PASS = None
PORT = 8080
BIN = '<SERVER_BINARY>'
MODEL = '<MODEL_PATH>'
LOG = '/tmp/cb_ab_server.log'
OUT = REPO_ROOT / 'tests' / 'benchmark' / 'cb-ab'

ARMS = [('cb', '--cont-batching'), ('nocb', '--no-cont-batching')]

BASE_SENTENCE = 'Explain how an operating system scheduler assigns CPU time to compute threads, and why vector context switching can slow down matrix workloads. '


def make_prompt(repeat):
    return (BASE_SENTENCE * repeat).strip()


def connect():
    import paramiko

    c = paramiko.SSHClient()
    c.load_system_host_keys()
    c.set_missing_host_key_policy(paramiko.RejectPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=15, look_for_keys=False, allow_agent=False)
    return c


def sh(client, cmd, timeout=180):
    _, out, err = client.exec_command(cmd, timeout=timeout)
    o = out.read().decode(errors='replace')
    e = err.read().decode(errors='replace')
    return out.channel.recv_exit_status(), o, e


def build_cmd(cb_flag, args):
    return (
        f'{shlex.quote(BIN)} -m {shlex.quote(MODEL)} '
        f'-t {args.threads} -tb {args.threads} '
        f'-b {args.batch_size} -ub {args.ubatch_size} '
        f'-c {args.context} --parallel {args.parallel} '
        f'--host 0.0.0.0 --port {PORT} '
        f'--no-cache-prompt {cb_flag}'
    )


def stop_server(client):
    # 方括号技巧避免 pkill 匹配到执行该命令的 shell 自身
    sh(client, 'pkill -x llama-server 2>/dev/null; pkill -f "[l]lama-server -m" 2>/dev/null; '
               f'if [ -f {LOG}.pid ]; then kill $(cat {LOG}.pid) 2>/dev/null; fi; sleep 2; true')


def wait_health(timeout_s=240):
    """从本机探测 /health，走的就是压测使用的同一条网络路径。"""
    import requests
    url = f'http://{HOST}:{PORT}/health'
    t0 = time.time()
    last = ''
    while time.time() - t0 < timeout_s:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                return True, round(time.time() - t0, 1), ''
            last = f'HTTP {resp.status_code}'
        except Exception as exc:  # 连接被拒 / 超时 / 尚未监听
            last = type(exc).__name__
        time.sleep(2)
    return False, round(time.time() - t0, 1), last


def start_server(client, cmd, wait_s=240):
    stop_server(client)
    sh(client, f'rm -f {LOG} {LOG}.pid; nohup {cmd} > {LOG} 2>&1 & echo $! > {LOG}.pid')
    ok, waited, last = wait_health(wait_s)
    if ok:
        return True, waited
    rc, tail, _ = sh(client, f'tail -12 {LOG}')
    print(f'[ERR] 服务未就绪（等待 {waited}s，最后状态 {last}），日志尾部：\n{tail}')
    return False, waited


def read_temps(client):
    rc, out, _ = sh(client, 'for z in /sys/class/thermal/thermal_zone*/temp; do printf "%s " "$(cat $z 2>/dev/null)"; done')
    vals = [int(v) / 1000 for v in out.split() if v.isdigit()]
    return (max(vals) if vals else None)


def summarize_trial(r):
    reqs = r.get('_request_results', [])
    tokens = sum(x['tokens'] for x in reqs)
    return {
        'aggregate_tps': round(r['aggregate_tps'], 4),
        'avg_ttft_ms': round(r['avg_ttft_ms'], 2),
        'p95_ttft_ms': round(r['p95_ttft_ms'], 2),
        'avg_prompt_ms': round(r['avg_prompt_ms'], 2),
        'avg_prompt_n': round(statistics.mean([x.get('prompt_n', 0) for x in reqs]), 1) if reqs else 0,
        'avg_e2e_ms': round(r['avg_e2e_ms'], 2),
        'wall_time_s': r['wall_time_s'],
        'samples': r['sample_count'],
        'total_tokens': tokens,
        'p95_e2e_ms': round(r['p95_e2e_ms'], 2),
        'reqs': [
            {k: x[k] for k in ('request_id', 'start_offset_s', 'ttft_ms', 'prompt_ms', 'prompt_n',
                               'e2e_ms', 'tokens', 'tps') if k in x}
            for x in sorted(reqs, key=lambda z: z['request_id'])
        ],
    }


def run_one(client, arm, cb_flag, args, prompt, tag):
    cmd = build_cmd(cb_flag, args)
    print(f'\n----- [{tag}] {arm} : {cb_flag} -----')
    print(f'  cmd: {cmd}')
    ok, wait = start_server(client, cmd)
    if not ok:
        return None
    temp0 = read_temps(client)
    t_start = datetime.datetime.now().isoformat(timespec='seconds')
    r = run_trial(
        url=f'http://{HOST}:{PORT}/v1/chat/completions',
        concurrency=args.concurrency,
        prompt=prompt,
        max_tokens=args.max_tokens,
        timeout=args.request_timeout,
        requests_total=args.requests,
    )
    temp1 = read_temps(client)
    stop_server(client)
    if r is None:
        print('  [ERR] 本轮无有效样本')
        return None
    s = summarize_trial(r)
    s.update({'arm': arm, 'cb_flag': cb_flag, 'tag': tag, 'requests': args.requests,
              'concurrency': args.concurrency, 'started_at': t_start,
              'server_wait_s': wait, 'temp_before_c': temp0, 'temp_after_c': temp1,
              'dropped': args.requests - s['samples'], 'cmd': cmd})
    print(f"  TPS={s['aggregate_tps']:.3f}  AvgTTFT={s['avg_ttft_ms']:.0f}ms  "
          f"P95TTFT={s['p95_ttft_ms']:.0f}ms  wall={s['wall_time_s']:.1f}s  "
          f"tokens={s['total_tokens']}  prompt_n={s['avg_prompt_n']}  dropped={s['dropped']}")
    return s


def pool_p95(trials):
    s = [x['ttft_ms'] for t in trials for x in t['reqs']]
    s.sort()
    if len(s) < 2:
        return s[0] if s else 0
    idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
    return round(s[idx], 2)


def report(trials, args):
    print('\n' + '#' * 74)
    print('  CB vs noCB 对照结果')
    print('#' * 74)
    print(f"  条件：{args.model_label} | 并发 {args.concurrency} | 总请求 {args.requests} | "
          f"prompt≈{args.prompt_tokens}tok | 输出 {args.max_tokens}tok | -t/-tb {args.threads} | "
          f"--parallel {args.parallel} | -c {args.context}")
    stats = {}
    for arm, _ in ARMS:
        ts = [t for t in trials if t['arm'] == arm]
        if not ts:
            continue
        tps = [t['aggregate_tps'] for t in ts]
        ttft = [t['avg_ttft_ms'] for t in ts]
        tokens = [t['total_tokens'] for t in ts]
        stats[arm] = {
            'n': len(ts),
            'tps_mean': statistics.mean(tps),
            'tps_std': statistics.pstdev(tps) if len(tps) > 1 else 0.0,
            'ttft_mean': statistics.mean(ttft),
            'ttft_std': statistics.pstdev(ttft) if len(ttft) > 1 else 0.0,
            'p95_pooled': pool_p95(ts),
            'tokens_mean': statistics.mean(tokens),
            'wall_mean': statistics.mean([t['wall_time_s'] for t in ts]),
        }
    for arm in stats:
        s = stats[arm]
        print(f"\n  [{arm}]  n={s['n']}")
        print(f"    聚合 TPS      {s['tps_mean']:.3f} ± {s['tps_std']:.3f}")
        print(f"    平均 TTFT     {s['ttft_mean']:.0f} ± {s['ttft_std']:.0f} ms")
        print(f"    P95 TTFT(池化) {s['p95_pooled']:.0f} ms")
        print(f"    总生成 token  {s['tokens_mean']:.0f}    墙钟 {s['wall_mean']:.1f} s")
    if 'cb' in stats and 'nocb' in stats:
        a, b = stats['cb'], stats['nocb']
        print('\n  差异 (noCB 相对 CB):')
        print(f"    聚合 TPS   {(b['tps_mean'] / a['tps_mean'] - 1) * 100:+.1f}%")
        print(f"    平均 TTFT  {(b['ttft_mean'] / a['ttft_mean'] - 1) * 100:+.1f}%")
        print(f"    P95 TTFT   {(b['p95_pooled'] / a['p95_pooled'] - 1) * 100:+.1f}%")
        d = abs(b['tokens_mean'] - a['tokens_mean']) / max(a['tokens_mean'], 1)
        print(f"    输出 token 数差异 {d * 100:.2f}% " +
              ('（>5%，按判读规则该对比作废）' if d > 0.05 else '（在 5% 以内，可比）'))
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--concurrency', type=int, default=8)
    ap.add_argument('--requests', type=int, default=24, help='每轮请求总数，可等于或大于并发')
    ap.add_argument('--trials', type=int, default=3)
    ap.add_argument('--warmup', type=int, default=1)
    ap.add_argument('--max-tokens', type=int, default=32)
    ap.add_argument('--prompt-repeat', type=int, default=2)
    ap.add_argument('--prompt-tokens', type=int, default=61, help='仅用于记录的目标值')
    ap.add_argument('--threads', type=int, default=8)
    ap.add_argument('--parallel', type=int, default=8)
    ap.add_argument('--context', type=int, default=8192)
    ap.add_argument('--batch-size', type=int, default=2048)
    ap.add_argument('--ubatch-size', type=int, default=512)
    ap.add_argument('--request-timeout', type=int, default=1800)
    ap.add_argument('--model-label', default='Llama-3.2-1B-Instruct Q4_K_M')
    ap.add_argument('--board-host', default=os.environ.get('BOARD_HOST'))
    ap.add_argument('--board-user', default=os.environ.get('BOARD_USER', 'root'))
    ap.add_argument('--port', type=int, default=int(os.environ.get('BOARD_PORT', '8080')))
    ap.add_argument('--server-binary', default=os.environ.get('LLAMA_SERVER_BIN'))
    ap.add_argument('--model-path', default=os.environ.get('LLAMA_MODEL_PATH'))
    ap.add_argument('--output-dir', type=Path, default=REPO_ROOT / 'tests' / 'benchmark' / 'cb-ab')
    ap.add_argument('--pilot', action='store_true', help='校准档：单波、短输出、每臂 1 轮')
    ap.add_argument('--dry-run', action='store_true', help='只打印两臂命令行')
    ap.add_argument('--tag', default='cbab')
    args = ap.parse_args()

    global HOST, USER, PASS, PORT, BIN, MODEL, OUT
    HOST, USER, PORT = args.board_host, args.board_user, args.port
    PASS = os.environ.get('BOARD_PASS')
    BIN = args.server_binary or '<SERVER_BINARY>'
    MODEL = args.model_path or '<MODEL_PATH>'
    OUT = args.output_dir

    if args.pilot:
        args.requests = args.concurrency
        args.max_tokens = 32
        args.trials, args.warmup = 1, 0

    prompt = make_prompt(args.prompt_repeat)
    print(f'prompt 字符数={len(prompt)}（目标 ≈{args.prompt_tokens} token，实际值以 prompt_n 为准）')

    if args.dry_run:
        for arm, flag in ARMS:
            print(f'[{arm}] {build_cmd(flag, args)}')
        return

    missing = []
    if not HOST:
        missing.append('BOARD_HOST / --board-host')
    if not PASS:
        missing.append('BOARD_PASS')
    if not args.server_binary:
        missing.append('LLAMA_SERVER_BIN / --server-binary')
    if not args.model_path:
        missing.append('LLAMA_MODEL_PATH / --model-path')
    if missing:
        ap.error('运行实验前请配置：' + ', '.join(missing))

    OUT.mkdir(parents=True, exist_ok=True)
    client = connect()
    trials = []
    try:
        rc, out, _ = sh(client, 'pgrep -a -x llama-server || true')
        if out.strip():
            print('[INFO] 板上有残留 llama-server，先清理')
            stop_server(client)
        print(f"\n[INFO] 实验开始 {datetime.datetime.now().isoformat(timespec='seconds')}  "
              f"预热 {args.warmup} 轮（丢弃）+ 计入 {args.trials} 轮，两臂固定按 CB 开启→关闭运行")
        for w in range(args.warmup):
            for arm, flag in ARMS:
                run_one(client, arm, flag, args, prompt, tag='warmup')
        for t in range(1, args.trials + 1):
            for arm, flag in ARMS:
                s = run_one(client, arm, flag, args, prompt, tag=f'trial{t}')
                if s:
                    trials.append(s)
    finally:
        stop_server(client)
        client.close()

    if not trials:
        print('[ERR] 没有有效结果')
        return
    stats = report(trials, args)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_args = vars(args).copy()
    for key in ('board_host', 'board_user', 'server_binary', 'model_path'):
        if safe_args.get(key):
            safe_args[key] = '<redacted>'
    safe_trials = []
    for trial in trials:
        safe_trial = trial.copy()
        safe_trial['cmd'] = (safe_trial.get('cmd', '')
                             .replace(args.server_binary, '<SERVER_BINARY>')
                             .replace(args.model_path, '<MODEL_PATH>'))
        safe_trials.append(safe_trial)
    with open(OUT / f'{args.tag}_trials_{stamp}.json', 'w', encoding='utf-8') as f:
        json.dump({'args': safe_args, 'prompt_repeat': args.prompt_repeat,
                   'trials': safe_trials, 'stats': stats}, f, ensure_ascii=False, indent=2)
    with open(OUT / f'{args.tag}_trials_{stamp}.csv', 'w', newline='', encoding='utf-8') as f:
        cols = ['tag', 'arm', 'aggregate_tps', 'avg_ttft_ms', 'p95_ttft_ms', 'avg_prompt_ms',
                'avg_prompt_n', 'avg_e2e_ms', 'p95_e2e_ms', 'wall_time_s', 'total_tokens',
                'samples', 'dropped', 'temp_before_c', 'temp_after_c', 'started_at']
        wcsv = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        wcsv.writeheader()
        for t in trials:
            wcsv.writerow(t)
    print(f'\n结果已写入 {OUT}/')


if __name__ == '__main__':
    main()
