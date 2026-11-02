"""Synthetic schema tests, never presented as measured GPU counter readings."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from read_vulkan_metrics import read_metrics


class CounterReaderTests(unittest.TestCase):
    def test_permission_denial_is_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'denied.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE StringIds(id INTEGER,value TEXT)')
                db.execute('INSERT INTO StringIds VALUES(1,?)',('GPU Metrics [0]: permission denied',))
            result=read_metrics(path)
            self.assertEqual(result['status'],'unavailable')
            self.assertEqual(result['metrics'],[])
            self.assertIn('permission denied',result['reason'])

    def test_modern_metric_ids_and_submit_window(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'modern.sqlite'
            with sqlite3.connect(path) as db:
                db.executescript('''CREATE TABLE StringIds(id INTEGER,value TEXT);
                    CREATE TABLE VULKAN_API(start INTEGER,end INTEGER,globalTid INTEGER,nameId INTEGER);
                    CREATE TABLE GPU_METRICS(typeId INTEGER,metricId INTEGER,timestamp INTEGER,value REAL);
                    CREATE TABLE TARGET_INFO_GPU_METRICS(typeId INTEGER,metricId INTEGER,metricName TEXT);
                    INSERT INTO StringIds VALUES(1,'vkQueueSubmit'),(2,'vkWaitForFences');
                    INSERT INTO VULKAN_API VALUES(10,12,3,1),(13,50,3,2);
                    INSERT INTO TARGET_INFO_GPU_METRICS VALUES(1,0,'L2 Cache Hit Rate [%]'),(2,0,'DRAM Read Throughput [%]');
                    INSERT INTO GPU_METRICS VALUES(1,0,20,75),(1,0,25,80),(2,0,30,25);''')
            r=read_metrics(path);rows={x['name']:x for x in r['metrics']}
            self.assertEqual(r['status'],'available');self.assertEqual(r['submission_windows'],1)
            self.assertEqual(rows['L2 Cache Hit Rate [%]']['median'],77.5)
            self.assertEqual(rows['L2 Cache Hit Rate [%]']['samples'],2)
            self.assertEqual(rows['DRAM Read Throughput [%]']['median'],25)
            self.assertEqual(len(r['submission_metrics']['Target Vulkan submit-to-fence windows']),2)
            self.assertIn('GPU-wide',r['timing_scope'])

    def test_generic_2022_json_events_preserve_native_values(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'legacy.sqlite'
            with sqlite3.connect(path) as db:
                db.executescript('''CREATE TABLE GENERIC_EVENT_SOURCES(sourceId INTEGER,data TEXT);
                    CREATE TABLE GENERIC_EVENT_TYPES(typeId INTEGER,sourceId INTEGER,data TEXT);
                    CREATE TABLE GENERIC_EVENTS(typeId INTEGER,rawTimestamp INTEGER,timestamp INTEGER,data TEXT);''')
                db.execute('INSERT INTO GENERIC_EVENT_SOURCES VALUES(1,?)',(json.dumps({'Name':'GpuMetrics','TimeSource':'SessionNs'}),))
                db.execute('INSERT INTO GENERIC_EVENT_TYPES VALUES(2,1,?)',(json.dumps({'Fields':[{'Name':'L2 Read Throughput'}]}),))
                db.executemany('INSERT INTO GENERIC_EVENTS VALUES(2,?,NULL,?)',[(10,json.dumps({'L2 Read Throughput':10})),(20,json.dumps({'L2 Read Throughput':30}))])
            result=read_metrics(path)
            self.assertEqual(result['status'],'available')
            self.assertEqual(result['metrics'][0]['median'],20)
            self.assertEqual(result['metrics'][0]['samples'],2)
            self.assertEqual(result['submission_windows'],0)


if __name__=='__main__':unittest.main()
