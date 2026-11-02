#!/usr/bin/env python3
"""Plot shader/workgroup sweep and independent confirmation receipts."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('input',type=Path)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();d=json.loads(a.input.read_text())
fig,axes=plt.subplots(1,2,figsize=(15,10),gridspec_kw={'width_ratios':[1.3,1]})
for ax,key,title in [(axes[0],'sweep_rates','Shader / workgroup sweep · 7 runs'),(axes[1],'confirmation_rates','Held-out confirmation · 11 runs')]:
 rows=sorted(d[key].items(),key=lambda r:r[1]['wall']['median'],reverse=True)
 values=[r['wall']['median']/1e6 for _,r in rows]
 errors=[[(r['wall']['median']-r['wall']['min'])/1e6 for _,r in rows],[(r['wall']['max']-r['wall']['median'])/1e6 for _,r in rows]]
 names={'pair32':'Paired words','compact32':'In-place permutation','row32':'Fused row','unroll32':'Two-round unroll','final32':'Pruned final round','tree32':'Balanced XOR trees','lean32':'In-place, pruned final','column32':'Column scheduling','tail32':'Rolling final round','early32':'Early target rejection'}
 if not rows:
  ax.axis('off');ax.text(.5,.5,'Preselected candidate\nNo additional selection sweep',ha='center',transform=ax.transAxes)
  continue
 def label(k):
  if k.startswith('previous'):return 'Previous default · group 64'
  shader,group=k.split('/');return names[shader]+' · group '+group
 ax.barh([label(k) for k,_ in rows],values,xerr=errors,capsize=3,color=['#16a085' if k==d['selected_candidate'] else '#3676bb' for k,_ in rows])
 ax.invert_yaxis();ax.set_xlim(0,max(r['wall']['max']/1e6 for _,r in rows)*1.16)
 for y,v in enumerate(values):ax.text(v+8,y-.17,f'{v:.1f}',fontsize=9)
 ax.set_title(title);ax.set_xlabel('Million attempts / second · full wall time')
 ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True)
fig.suptitle('QCash shader scheduling — NVIDIA GeForce RTX 3060',fontsize=16)
fig.text(.5,.065,'Medians and observed min–max. 67,108,864 attempts per round; active background workloads.\nCandidate selected in an earlier sweep; confirmation samples are separate. GPU interval rates retained in JSON.',ha='center',fontsize=10)
fig.subplots_adjust(left=.22,right=.97,top=.89,bottom=.16,wspace=.45)
fig.savefig(a.output,dpi=180)
svg=a.output.with_suffix('.svg');fig.savefig(svg)
svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')

# Compact confirmation chart for the main report; retain the complete sweep too.
rows=sorted(d['confirmation_rates'].items(),key=lambda r:r[1]['wall']['median'],reverse=True)
fig,ax=plt.subplots(figsize=(9,3.6))
values=[r['wall']['median']/1e6 for _,r in rows]
errors=[[(r['wall']['median']-r['wall']['min'])/1e6 for _,r in rows],[(r['wall']['max']-r['wall']['median'])/1e6 for _,r in rows]]
ax.barh([names[k.split('/')[0]] if k==d['selected_candidate'] else 'Previous shader' for k,_ in rows],values,xerr=errors,capsize=4,color=['#16a085' if k==d['selected_candidate'] else '#3676bb' for k,_ in rows])
ax.invert_yaxis();ax.set_xlim(0,max(r['wall']['max']/1e6 for _,r in rows)*1.16)
for y,v in enumerate(values):ax.text(v+12,y-.2,f'{v:.1f}',fontsize=11)
ax.set_xlabel('Million attempts / second · full wall time')
ax.set_title('QCash · RTX 3060 · separate 11-run confirmation')
ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True)
gain=100*(d['confirmation_rates'][d['selected_candidate']]['gpu']['median']/d['confirmation_rates'][d.get('baseline_case','previous pair32/64')]['gpu']['median']-1)
fig.text(.5,.04,f'Medians and observed min–max; busy host. GPU interval gain: {gain:.1f}%.',ha='center',fontsize=10)
fig.subplots_adjust(left=.24,right=.97,top=.83,bottom=.26)
summary=a.output.with_name(a.output.stem+'-summary.png');fig.savefig(summary,dpi=180)
svg=summary.with_suffix('.svg');fig.savefig(svg)
svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
