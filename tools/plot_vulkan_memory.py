#!/usr/bin/env python3
"""Plot measured memory-policy medians and full observed ranges."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('input', type=Path)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
d = json.loads(a.input.read_text())
fig, axes = plt.subplots(1, 2, figsize=(14, 6), gridspec_kw={'width_ratios': [1, 1.25]})
for ax, key, scale, title, xlabel, reverse in [
    (axes[0], 'normal_attempts_per_second', 1e-6, 'Normal mining · higher is faster', 'Million attempts / second (full wall time)', True),
    (axes[1], 'capture_wall_seconds_per_4096', 1e3, 'Debug digest download · lower is faster', 'Milliseconds / 4,096 digests (log scale)', False),
]:
    rows = sorted(d[key].items(), key=lambda r: r[1]['median'], reverse=reverse)
    labels = [k.replace('mapped-local', 'Mapped, prefer local').replace('mapped', 'Mapped VRAM').replace('staged', 'Host-cached staging').replace('aos', 'record-major').replace('soa', 'word-major') for k, _ in rows]
    vals = [r['median'] * scale for _, r in rows]
    errors = [[(r['median'] - r['min']) * scale for _, r in rows], [(r['max'] - r['median']) * scale for _, r in rows]]
    ax.barh(labels, vals, color=['#16a085' if k.startswith('staged') else '#3676bb' for k, _ in rows], xerr=errors, capsize=3, alpha=.9)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=12, pad=16)
    ax.set_xlabel(xlabel)
    ax.grid(axis='x', alpha=.2)
    ax.set_axisbelow(True)
    if not reverse:
        ax.set_xscale('log')
        ax.set_xlim(.15, 35)
    else:
        ax.set_xlim(0, 800)
    for y, v in enumerate(vals):
        ax.text(v * 1.05 if not reverse else v + 15, y - .19, f'{v:.3f}' if not reverse else f'{v:.1f}', fontsize=9)
fig.suptitle('QCash Vulkan memory comparison — NVIDIA GeForce RTX 3060', fontsize=16)
fig.text(.5, .085, 'Medians and observed min–max; 7 repeated sessions. Background workloads active.\nMining ranges overlap; staging improves debug readback ≈35×. Word-major stores show no measurable GPU gain.', ha='center', fontsize=10)
fig.text(.5, .025, 'Mapped buffers already reside in device-local VRAM. Physical DRAM/cache/spill counters unavailable (permission denied).', ha='center', fontsize=9, color='#555555')
fig.subplots_adjust(left=.13, right=.98, top=.83, bottom=.23, wspace=.8)
a.output.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(a.output, dpi=180)
svg = a.output.with_suffix('.svg')
fig.savefig(svg)
svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n')
