#!/usr/bin/env python3
"""从 CB 对照实验 JSON 生成 TPS / P95 TTFT 对比图（PNG 与 SVG）。"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / 'tests' / 'benchmark' / 'cb-ab'
BLUE, PURPLE = '#2859A8', '#6744BE'
GRID, TEXT, MUTED = '#EAF0F8', '#26364A', '#607187'
FILES = [
    ('cb8_wave1_trials_20260917_193610.json', '单波\n8 请求'),
    ('cb8_trials_20260917_191818.json', '三波\n24 请求'),
]


def load_values(data_dir):
    groups = []
    for filename, label in FILES:
        data = json.loads((data_dir / filename).read_text(encoding='utf-8'))
        groups.append((label, data['stats']))
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_DATA / 'charts')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    groups = load_values(args.data_dir)

    plt.rcParams.update({
        'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'DejaVu Sans'],
        'font.family': 'sans-serif',
        'axes.unicode_minus': False,
        'svg.fonttype': 'path',
    })
    fig, axes = plt.subplots(1, 2, figsize=(10.2, 4.8), dpi=180)
    fig.patch.set_facecolor('white')
    colors = {'cb': BLUE, 'nocb': PURPLE}
    labels = {'cb': 'CB 开启', 'nocb': 'CB 关闭'}
    x_positions = [0, 1]
    width = 0.3
    panels = [
        ('聚合 TPS', 'tokens/s', lambda stats, arm: stats[arm]['tps_mean'], 8.0, [0, 2, 4, 6, 8], '{:.2f}'),
        ('P95 TTFT (s)', '', lambda stats, arm: stats[arm]['p95_pooled'] / 1000, 38.0, [0, 10, 20, 30], '{:.1f}'),
    ]
    for ax, (title, unit, value, ymax, yticks, fmt) in zip(axes, panels):
        ax.set_facecolor('white')
        ax.set_ylim(0, ymax)
        ax.set_yticks(yticks)
        ax.yaxis.grid(True, color=GRID, linewidth=0.9)
        ax.set_axisbelow(True)
        if unit:
            ax.set_ylabel(unit, color=MUTED, fontsize=9)
        ax.set_title(title, loc='left', color=BLUE if title == '聚合 TPS' else PURPLE,
                     fontsize=14, fontweight='bold', pad=14)
        for i, (label, stats) in enumerate(groups):
            for j, arm in enumerate(('cb', 'nocb')):
                x = x_positions[i] + (j - 0.5) * width
                v = value(stats, arm)
                ax.bar(x, v, width=width * 0.88, color=colors[arm], edgecolor='none', zorder=3)
                ax.text(x, v + ymax * 0.018, fmt.format(v), ha='center', va='bottom',
                        fontsize=9.5, color=colors[arm], fontweight='bold')
            if title == '聚合 TPS':
                delta = (stats['cb']['tps_mean'] / stats['nocb']['tps_mean'] - 1) * 100
                note = f'开启 CB：TPS +{delta:.1f}%'
            else:
                delta = (1 - stats['cb']['p95_pooled'] / stats['nocb']['p95_pooled']) * 100
                note = f'开启 CB：P95 TTFT −{delta:.1f}%'
            ax.text(i, -0.19, note, transform=ax.get_xaxis_transform(), ha='center', va='top',
                    fontsize=9, color=TEXT, fontweight='bold', clip_on=False)
        ax.set_xticks(x_positions, [label for label, _ in groups], fontsize=9.5, color=TEXT)
        ax.tick_params(axis='y', labelsize=8.5, colors=MUTED, length=0)
        ax.tick_params(axis='x', length=0, pad=8)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_xlim(-0.55, 1.55)

    handles = [plt.Rectangle((0, 0), 1, 1, color=colors[a]) for a in ('cb', 'nocb')]
    fig.legend(handles, [labels[a] for a in ('cb', 'nocb')], loc='upper center',
               bbox_to_anchor=(0.5, 0.99), ncol=2, frameon=False, fontsize=9.5)
    fig.suptitle('Continuous Batching 开关对照', x=0.05, y=0.99, ha='left',
                 fontsize=15, color=TEXT, fontweight='bold')
    fig.text(0.05, 0.025,
             'Milk-V Jupiter · Llama-3.2-1B-Instruct Q4_K_M · 并发 8 · 每臂 3 轮；TTFT P95 汇总请求级样本',
             fontsize=8.2, color=MUTED)
    fig.subplots_adjust(left=0.07, right=0.99, top=0.80, bottom=0.24, wspace=0.22)
    for ext in ('png', 'svg'):
        fig.savefig(args.output_dir / f'cb_compare.{ext}', facecolor='white', bbox_inches='tight', pad_inches=0.08)
    svg_path = args.output_dir / 'cb_compare.svg'
    svg_path.write_text('\n'.join(line.rstrip() for line in svg_path.read_text(encoding='utf-8').splitlines()) + '\n', encoding='utf-8')
    print(f'Wrote {args.output_dir / "cb_compare.png"} and .svg')


if __name__ == '__main__':
    main()
