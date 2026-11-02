#!/usr/bin/env python3
"""Export saved before/after GPU measurements as PNG and SVG."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('results',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=json.loads(a.results.read_text())
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11})
    fig,axes=plt.subplots(1,2,figsize=(17,8))
    selected=r['selected_shader']
    for ax,key,title,scale,reverse in [
        (axes[0],'throughput_attempts_per_second','Nonce-search throughput\nHigher is faster',1e-6,True),
        (axes[1],'proof_latency_seconds','Time to a CPU-verified proof · log scale\nLower is faster',1000,False)]:
        rows=sorted(r[key].items(),key=lambda row:row[1]['median'],reverse=reverse)
        values=[v['median']*scale for _,v in rows]
        colors=[]
        for name,_ in rows:
            if name=='Queued · '+selected or name=='Warm worker · GPU prefix':colors.append('#ea580c')
            elif name.startswith('Previous'):colors.append('#64748b')
            elif name.startswith('Native'):colors.append('#2563eb')
            else:colors.append('#93c5fd')
        ax.barh(range(len(rows)),values,color=colors,height=.6,
                xerr=[[(v['median']-v['min'])*scale for _,v in rows],[(v['max']-v['median'])*scale for _,v in rows]],
                error_kw={'ecolor':'#334155','capsize':3})
        ax.set_yticks(range(len(rows)),[name for name,_ in rows]);ax.invert_yaxis()
        ax.set_title(title,weight='bold',fontsize=14,pad=20)
        ax.spines[['top','right','left']].set_visible(False);ax.tick_params(axis='y',length=0)
        ax.grid(axis='x',alpha=.18);ax.set_axisbelow(True)
        if reverse:
            ax.set_xlabel('Million SHA3-256 header attempts / second')
            ax.set_xlim(0,max(v['max']*scale for _,v in rows)*1.30)
        else:
            ax.set_xscale('log');ax.set_xlabel('Milliseconds · includes IPC, search, native verification and file creation')
            ax.set_xlim(min(values)/3,max(v['max']*scale for _,v in rows)*8)
            ax.xaxis.set_major_formatter(FuncFormatter(lambda x,_:f'{x:g}'))
        for i,(_,v) in enumerate(rows):
            x=v['max']*scale
            ax.text(x+max(values)*.025 if reverse else x*1.10,i,
                    f'{v["median"]*scale:,.1f} MH/s' if reverse else f'{v["median"]*scale:,.3f} ms',
                    va='center',fontsize=10,weight='bold')
    fig.suptitle('QCash GPU optimization: previous implementation vs measured candidates',fontsize=19,weight='bold',y=.98)
    fig.text(.5,.90,f'{r["metadata"]["device"]} · {r["metadata"]["cpu_model"]} · one host logical CPU · {r["repeats"]} runs',ha='center',color='#475569')
    fig.text(.5,.865,'Medians with min–max whiskers · selected settings in orange · active background workloads',ha='center',color='#475569')
    note=(f'Left: {r["attempts_per_round"]:,} complete attempts per run on identical hard-target work. Startup excluded; previous executable built from c3c8ac4.\n'
          'Four dispatches are queued per wait; command buffers and the GPU context are reused. The 32-bit-pair shader remains exact SHA3-256.\n'
          'Right: identical classically preselected 256-nonce winning window. Warm-worker timings exclude its one-time startup and one warm-up job.\n'
          'Every candidate is independently rehashed on the CPU. These are conditional proof latencies, not network block times.\n'
          'CPU remains responsible for job control, network traffic, bounded synchronization and final verification; no CPU hashing per unsuccessful nonce.')
    fig.text(.025,.025,note,fontsize=9.5,color='#475569',linespacing=1.65)
    fig.subplots_adjust(left=.18,right=.94,top=.76,bottom=.28,wspace=1.03)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(a.output,dpi=180,facecolor='white')
    svg=a.output.with_suffix('.svg');fig.savefig(svg,facecolor='white')
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    print(a.output)


if __name__=='__main__':main()
