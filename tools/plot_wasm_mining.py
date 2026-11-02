#!/usr/bin/env python3
"""Plot equal-range CPU SIMD worker and GPU measurements, preserving observed ranges."""
import argparse
import json
from pathlib import Path
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter

NAMES={'vulkan':'Native Vulkan GPU','webgpu':'Browser WebGPU','wasm-1':'SIMD WASM · 1 worker','wasm-2':'SIMD WASM · 2 workers','wasm-4':'SIMD WASM · 4 workers','wasm-8':'SIMD WASM · 8 workers'}
COLORS={'vulkan':'#3676bb','webgpu':'#8553b5','wasm-1':'#79b8a4','wasm-2':'#50a58a','wasm-4':'#298b70','wasm-8':'#10735d'}
def rates(data,key):
    return [data['attempts']/(r['samples'][r.get('measured_sample_index',0)]['wall_seconds'] if key=='vulkan' else r['wallSeconds'])/1e6 for r in data['samples'][key]]
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path);p.add_argument('--small',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    datasets=([json.loads(a.small.read_text())] if a.small else [])+[json.loads(a.input.read_text())]
    fig,axes=plt.subplots(1,len(datasets),figsize=(14.5 if a.small else 9,5.5),squeeze=False)
    for ax,data in zip(axes[0],datasets):
        rows=sorted([(key,rates(data,key)) for key in data['samples']],key=lambda row:statistics.median(row[1]),reverse=True)
        med=[statistics.median(v) for _,v in rows];errors=[[m-min(v) for m,(_,v) in zip(med,rows)],[max(v)-m for m,(_,v) in zip(med,rows)]]
        ax.barh([NAMES[k] for k,_ in rows],med,xerr=errors,capsize=3,color=[COLORS[k] for k,_ in rows]);ax.invert_yaxis();ax.set_xscale('log');ax.set_xlim(1,1200)
        ax.xaxis.set_major_formatter(ScalarFormatter());ax.set_xticks([1,10,100,1000]);ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True)
        for y,v in enumerate(med):ax.text(v*1.08,y-.16,f'{v:.1f}',fontsize=10)
        warm='per-sample warm-up' if data.get('warm_each_sample') else 'initial warm-up only'
        ax.set_title(f"{data['attempts']:,} candidates / run\n{warm}",fontsize=12);ax.set_xlabel('Million attempts / second (log scale)')
    fig.suptitle('QCash · SIMD WASM workers vs GPUs\nRyzen 7 5800X + RTX 3060',fontsize=15)
    fig.text(.5,.065,'Full wall throughput: medians and observed min–max, seven rotated rounds; cached prefix; initialization excluded.',ha='center',fontsize=9)
    fig.text(.5,.025,'Busy shared host. Worker scaling is measured, not assumed. CPU and GPU are different hardware execution paths.',ha='center',fontsize=9)
    fig.subplots_adjust(left=.15 if a.small else .29,right=.97,top=.75,bottom=.2,wspace=.65)
    a.output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(a.output,dpi=180)
    svg=a.output.with_suffix('.svg');fig.savefig(svg);svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
if __name__=='__main__':main()
