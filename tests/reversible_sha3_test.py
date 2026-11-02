"""FIPS-202 bytes, comparator boundaries, relative phase and ancilla cleanup."""
import hashlib
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'miners'))
from reversible_sha3 import Circuit,threshold_comparator,verify_phase_oracle

class ReversibleHashTests(unittest.TestCase):
    def test_real_sha3_padding_and_phase(self):
        for message in [b'',b'abc',bytes(range(135)),bytes(range(136)),bytes(i%256 for i in range(268))]:
            with self.subTest(length=len(message)):
                wanted=hashlib.sha3_256(message).digest(); number=int.from_bytes(wanted,'big')
                for target,phase in [(number,-1),(number-1,1)]:
                    result=verify_phase_oracle(message,target)
                    self.assertEqual(result['digest'],wanted.hex())
                    self.assertEqual(result['phase'],phase)
                    self.assertTrue(result['ancillas_clean'])

    def test_comparator_exhaustive_small_widths(self):
        for width in range(1,6):
            for target in range(1<<width):
                c=Circuit(); inputs=c.allocate(width); flag=threshold_comparator(c,inputs[::-1],target)
                for number in range(1<<width):
                    state=bytearray(c.qubits)
                    for bit in range(width):state[bit]=(number>>bit)&1
                    original=state[:];c.apply_basis(state)
                    self.assertEqual(state[flag],number<=target)
                    c.apply_basis(state,inverse=True);self.assertEqual(state,original)

    def test_bounds_and_wire_rejection(self):
        from reversible_sha3 import sha3_256_circuit
        for bad in [-1,513,True]:
            with self.assertRaises(ValueError):sha3_256_circuit(bad)
        c=Circuit();c.allocate(2)
        with self.assertRaises(ValueError):c.gate('cx',0,0)
        with self.assertRaises(ValueError):c.gate('x',2)
        with self.assertRaises(ValueError):threshold_comparator(c,[0],2)

if __name__=='__main__':unittest.main()
