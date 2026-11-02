#!/usr/bin/env python3
"""Plot measured QCash mining results; no modeled QPU speed bars."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.results.read_text())
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11})
    fig, axes = plt.subplots(1, 2, figsize=(17, 8), gridspec_kw={'width_ratios': [1, 1.3]})
    colors = {'Native C++ miner': '#2563eb', 'Python hashlib loop': '#059669',
              'Python hashlib miner': '#059669', 'Qiskit Aer Grover demo': '#9333ea', 'Vulkan GPU · dispatch + wait': '#ea580c',
              'Vulkan GPU · kernel only': '#fb923c', 'Vulkan GPU miner': '#ea580c'}
    for ax, key, title, descending, scale in [
        (axes[0], 'throughput_hashes_per_second', 'CPU nonce-search throughput\nHigher is faster', True, 1),
        (axes[1], 'verified_proof_latency_seconds', 'Time to a verified proof · log scale\nLower is faster', False, 1000),
    ]:
        rows = sorted(data[key].items(), key=lambda item: item[1]['median'], reverse=descending)
        values = [row['median'] * scale for _, row in rows]
        low = [(row['median'] - row['min']) * scale for _, row in rows]
        high = [(row['max'] - row['median']) * scale for _, row in rows]
        ax.barh(range(len(rows)), values, color=[colors[name] for name, _ in rows], height=.5,
                xerr=[low, high], error_kw={'ecolor': '#334155', 'capsize': 4})
        ax.set_yticks(range(len(rows)), [name for name, _ in rows])
        ax.invert_yaxis()
        ax.set_title(title, fontsize=13, pad=18, weight='bold')
        ax.spines[['top', 'right', 'left']].set_visible(False)
        ax.tick_params(axis='y', length=0)
        ax.grid(axis='x', alpha=.18)
        ax.set_axisbelow(True)
        if descending:
            ax.set_xlabel('SHA3-256 header attempts / second')
            ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x/1000:,.0f}k'))
            if data.get('gpu_receipts'):
                ax.set_xscale('log')
                ax.set_xlim(min(values)/3, max(row['max'] for _, row in rows)*8)
            else:
                ax.set_xlim(0, max(row['max'] for _, row in rows) * 1.45)
        else:
            ax.set_xscale('log')
            ax.set_xlabel('Milliseconds · includes process startup and proof verification')
            ax.set_xlim(min(values) / 3, max(row['max'] for _, row in rows) * scale * 10)
            ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:,.0f}'))
        for index, (_, row) in enumerate(rows):
            value = row['median'] * scale
            text = f'{value:,.0f} H/s' if descending else f'{value:,.2f} ms'
            position = row['max'] * scale
            ax.text(position * 1.08 if not descending or data.get('gpu_receipts') else position + max(values) * .03,
                    index, text, va='center', fontsize=10, weight='bold')
    meta = data['metadata']
    repeats = len(next(iter(data['verified_proof_latency_seconds'].values()))['samples'])
    fig.suptitle('QCash mining implementation comparison', fontsize=20, weight='bold', y=.98)
    hardware = meta['cpu_model'] + (' + ' + meta['gpu']['device'] if meta.get('gpu') else '')
    fig.text(.5, .90, hardware, ha='center', color='#475569')
    fig.text(.5, .865, f'One host logical CPU · {repeats} runs · median with min–max whiskers', ha='center', color='#475569')
    fixture = data['fixture']
    note = (
        f"Left: {data['hash_attempts_per_round']:,} attempts per run on a hard-target header. Python uses native hashlib/OpenSSL; native includes CLI startup.\n"
        f"Right: same classically preselected {fixture['window_size']}-nonce winning window; CPU searches stop at the first proof ({fixture['native_attempts_to_first_proof']} attempts).\n"
        "Simulator timing includes classical SHA3 lookup, Qiskit startup, transpilation, CPU statevector simulation and native verification.\n"
        "Conditional demo timings, not network block times. No physical QPU or quantum speedup measured. Background workloads remained active."
    )
    if data.get('gpu_receipts'):
        note += '\nGPU reuses the fixed first-block midstate. Throughput excludes context setup; proof latency includes it. Both panels use log scales.'
    fig.text(.035, .025, note, fontsize=9.5, color='#475569', linespacing=1.65)
    fig.subplots_adjust(left=.20, right=.94, top=.77, bottom=.30, wspace=1.15)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=180, facecolor='white')
    svg = args.output.with_suffix('.svg')
    fig.savefig(svg, facecolor='white')
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n')
    print(args.output)


if __name__ == '__main__':
    main()
