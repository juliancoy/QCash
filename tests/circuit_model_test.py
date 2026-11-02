import importlib.util
from pathlib import Path
import unittest
p=Path(__file__).resolve().parents[1]/'tools/quantum_circuit_estimate.py'
s=importlib.util.spec_from_file_location('circuit',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class CircuitTests(unittest.TestCase):
    def test_published_counts_and_uncompute(self):
        r=m.resources(2**244-1)
        self.assertEqual(r['hash_t_count'],998400)
        self.assertEqual(r['hash_t_depth'],864)
        self.assertEqual(r['predicate_toffolis'],1000)
        self.assertEqual(r['diffusion_toffolis'],636)
        self.assertEqual(r['t_count'],1009852)
        self.assertEqual(r['success_fraction'],2**-12)
    def test_cost_scaling(self):
        a=m.scenario(48,1000,1e8);b=m.scenario(50,1000,1e8)
        self.assertAlmostEqual(b['iterations']/a['iterations'],2,places=5)
        self.assertGreater(a['ideal_success_probability'],.999)
    def test_factory_bottleneck(self):
        a=m.scenario(48,1,1e6);b=m.scenario(48,1,2e6)
        self.assertEqual(a['conditional_run_seconds_floor'],2*b['conditional_run_seconds_floor'])
    def test_invalid_target(self):
        for t in [0,-1,2**256]:
            with self.assertRaises(ValueError):m.resources(t)
if __name__=='__main__':unittest.main()
