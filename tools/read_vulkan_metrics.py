"""Read only recorded Nsight GPU metrics; preserve native units and scope.

The generic SQLite schema handling is reused from the workspace ML-KEM counter
reader, without its operation labels or a dependency on that repository.
"""
import bisect
import json
import math
from pathlib import Path
import sqlite3
import statistics

def read_metrics(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        errors = []
        if 'StringIds' in tables:
            errors = [r[0] for r in db.execute("SELECT value FROM StringIds WHERE value LIKE 'GPU Metrics [%'")]
        windows=[]
        if {'VULKAN_API','StringIds'} <= tables:
            pending={}
            for event in db.execute("SELECT a.start,a.end,a.globalTid,s.value FROM VULKAN_API a JOIN StringIds s ON s.id=a.nameId WHERE s.value IN ('vkQueueSubmit','vkWaitForFences') ORDER BY a.start"):
                if event['value']=='vkQueueSubmit':
                    pending.setdefault(event['globalTid'],event['start'])
                elif event['globalTid'] in pending:
                    windows.append((pending.pop(event['globalTid']),event['end']))
        windows.sort()
        scoped_windows=[(start,end,'Target Vulkan submit-to-fence windows') for start,end in windows]
        starts=[w[0] for w in scoped_windows]
        def scope(timestamp):
            if timestamp is None:return None
            index=bisect.bisect_right(starts,timestamp)-1
            return scoped_windows[index][2] if index>=0 and timestamp<=scoped_windows[index][1] else None
        readings={};scoped_readings={}
        def add(type_id, metric_id, name, value, label=None):
            if not any(s in name.lower() for s in ('dram','pcie','sm active','sms active','sm issue','warp','occup','stall','l1','l2','local','spill')):
                return
            try:
                value=float(value)
            except (ValueError,TypeError):
                return
            if not math.isfinite(value):
                return
            key=(type_id,metric_id,name)
            readings.setdefault(key,[]).append(value)
            if label is not None:scoped_readings.setdefault(label,{}).setdefault(key,[]).append(value)
        if {'GPU_METRICS', 'TARGET_INFO_GPU_METRICS'} <= tables:
            columns = {r[1] for r in db.execute('PRAGMA table_info(GPU_METRICS)')}
            names_columns = {r[1] for r in db.execute('PRAGMA table_info(TARGET_INFO_GPU_METRICS)')}
            if not {'metricId','value'} <= columns or not {'metricId','metricName'} <= names_columns:
                return {'status':'unavailable','reason':'Unrecognized Nsight metric schema; raw report retained.','metrics':[]}
            join='m.metricId=n.metricId'
            if 'typeId' in columns and 'typeId' in names_columns:
                join+=' AND m.typeId=n.typeId'
            timestamp_select='m.timestamp' if 'timestamp' in columns else 'NULL'
            for row in db.execute('SELECT n.*,m.value AS sampleValue,'+timestamp_select+' AS profileTimestamp FROM GPU_METRICS m JOIN TARGET_INFO_GPU_METRICS n ON '+join):
                add(row['typeId'] if 'typeId' in names_columns else None,row['metricId'],row['metricName'],row['sampleValue'],scope(row['profileTimestamp']))
        elif {'GENERIC_EVENTS','GENERIC_EVENT_TYPES','GENERIC_EVENT_SOURCES'} <= tables:
            # Nsight Systems 2022 exports counters as JSON generic events.
            # Admit only the GpuMetrics source, never arbitrary process metadata.
            sources={r['sourceId']:json.loads(r['data']) for r in db.execute('SELECT * FROM GENERIC_EVENT_SOURCES')
                     if json.loads(r['data']).get('Name')=='GpuMetrics'}
            types={r['typeId']:(json.loads(r['data']),sources[r['sourceId']].get('TimeSource')) for r in db.execute('SELECT * FROM GENERIC_EVENT_TYPES') if r['sourceId'] in sources}
            for type_id,(description,time_source) in types.items():
                fields={field['Name']:index for index,field in enumerate(description.get('Fields',[]))}
                for row in db.execute('SELECT rawTimestamp,timestamp,data FROM GENERIC_EVENTS WHERE typeId=?',(type_id,)):
                    label=scope(row['timestamp'] if row['timestamp'] is not None else row['rawTimestamp'] if time_source=='SessionNs' else None)
                    for name,value in json.loads(row['data']).items():
                        if name in fields:
                            add(type_id,fields[name],name,value,label)
        else:
            return {'status':'unavailable','reason':errors[0] if errors else 'Capture has no GPU hardware-counter tables.','metrics':[]}
        def summarize_readings(groups):
            metrics=[]
            for (type_id,metric_id,name),values in sorted(groups.items(),key=lambda item:str(item[0])):
                q25,_,q75=statistics.quantiles(values,n=4,method='inclusive') if len(values)>1 else [values[0]]*3
                metrics.append({'type_id':type_id,'metric_id':metric_id,'name':name,'samples':len(values),'median':statistics.median(values),'p25':q25,'p75':q75,'min':min(values),'max':max(values)})
            return metrics
        metrics=summarize_readings(readings)
        return {'status':'available' if metrics else 'unavailable',
                'reason':None if metrics else (errors[0] if errors else 'No relevant finite metric samples were recorded.'),
                'metrics':metrics,'collection_messages':errors,
                'submission_metrics':{label:summarize_readings(values) for label,values in scoped_readings.items()},
                'submission_windows':len(windows),'operation_labels_verified':False,
                'timing_scope':'GPU-wide counter samples during target queue-submit through fence-wait intervals; other GPU clients and CPU synchronization may contribute.'}
