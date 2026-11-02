#!/usr/bin/env python3
"""Plot held-out browser/native comparisons from recorded Chrome results."""
import argparse
import json
from pathlib import Path
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
d=json.loads(a.input.read_text());b=d['benchmark'];attempts=16777216
rows=[]
for key,receipts in b['confirmation'].items():
 wall=[attempts/(r['wallSeconds'] if key=='webgpu' else r['samples'][0]['wall_seconds'])/1e6 for r in receipts]
 gpu_seconds=[r['gpuSeconds'] if key=='webgpu' else r['samples'][0]['gpu_seconds'] for r in receipts]
 gpu=[attempts/t/1e6 for t in gpu_seconds if t is not None and t>0]
 rows.append((key,wall,gpu))
rows.sort(key=lambda r:statistics.median(r[1]),reverse=True)
fig,ax=plt.subplots(figsize=(10,4))
med=[statistics.median(r[1]) for r in rows];err=[[m-min(r[1]) for m,r in zip(med,rows)],[max(r[1])-m for m,r in zip(med,rows)]]
labels=['Browser WebGPU · '+b['selected'] if k=='webgpu' else 'Native Vulkan · final32/64' for k,_,_ in rows]
ax.barh(labels,med,xerr=err,capsize=4,color=['#8553b5' if k=='webgpu' else '#3676bb' for k,_,_ in rows]);ax.invert_yaxis()
ax.set_xlim(0,max(max(r[1]) for r in rows)*1.15)
for y,v in enumerate(med):ax.text(v+10,y-.18,f'{v:.1f}',fontsize=12)
ax.set_xlabel('Million attempts / second · full wall time');ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True)
device=b['confirmation']['native'][0]['device']
ax.set_title('QCash mining · '+device)
summary=' · '.join(('WebGPU' if k=='webgpu' else 'Vulkan')+(f' GPU interval: {statistics.median(gpu):.1f} MH/s' if gpu else ' GPU interval: unavailable') for k,_,gpu in rows)
fig.text(.5,.075,'11 alternating confirmation pairs; medians and observed min–max. Setup excluded.',ha='center',fontsize=10)
fig.text(.5,.025,summary,ha='center',fontsize=9)
fig.subplots_adjust(left=.32,right=.97,top=.86,bottom=.29)
a.output.parent.mkdir(parents=True,exist_ok=True)
fig.savefig(a.output,dpi=180)
svg=a.output.with_suffix('.svg');fig.savefig(svg);svg.write_text('\n'.join(x.rstrip() for x in svg.read_text().splitlines())+'\n')
