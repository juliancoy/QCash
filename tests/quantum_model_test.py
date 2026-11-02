#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'tools/quantum_pow_model.py'
spec=importlib.util.spec_from_file_location('quantum_model',path)
model=importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)

class QuantumModelTest(unittest.TestCase):
    def estimate(self,bits=48,rate=1000000,processors=1,coherence=1000000):
        return model.estimate(bits,rate,processors,coherence,20000,1000)
    def test_difficulty_scaling(self):
        a=self.estimate(48)['expected_seconds'];b=self.estimate(50)['expected_seconds']
        self.assertAlmostEqual(b/a,2,places=5)
    def test_iteration_rate_scaling(self):
        self.assertAlmostEqual(self.estimate(rate=2000000)['expected_seconds']*2,self.estimate()['expected_seconds'])
    def test_parallel_budget(self):
        a=self.estimate();b=self.estimate(processors=4)
        self.assertEqual(b['estimated_physical_qubits'],4*a['estimated_physical_qubits'])
        self.assertLess(b['expected_seconds'],a['expected_seconds'])
        self.assertGreater(b['expected_seconds'],a['expected_seconds']/4)
    def test_coherence_cost(self):
        a=self.estimate(80);b=self.estimate(80,coherence=1)
        self.assertGreater(b['expected_seconds'],a['expected_seconds'])
        self.assertLessEqual(b['iterations_per_run_per_processor'],1000000)
    def test_extreme_targets_stay_finite(self):
        for bits in [1,12,64,128,256]:
            for processors in [1,10**12]:
                result=self.estimate(bits,processors=processors)
                self.assertGreater(result['success_probability_per_parallel_run'],0)
                self.assertLessEqual(result['success_probability_per_parallel_run'],1)
                self.assertGreater(result['expected_seconds'],0)

if __name__=='__main__':unittest.main()
