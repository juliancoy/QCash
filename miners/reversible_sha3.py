"""Reversible X/CX/CCX SHA3-256 reference, with a clean threshold phase oracle.

Trace-preserving out-of-place rounds prioritize correctness over qubit economy.
Full SHA3 is tested on classical basis states; it is NOT a tractable dense QPU
simulation or the optimized circuit assumed by the separate resource model.
"""
from collections import Counter
from dataclasses import dataclass, field

ROUND_CONSTANTS = (
    0x0000000000000001,0x0000000000008082,0x800000000000808A,0x8000000080008000,
    0x000000000000808B,0x0000000080000001,0x8000000080008081,0x8000000000008009,
    0x000000000000008A,0x0000000000000088,0x0000000080008009,0x000000008000000A,
    0x000000008000808B,0x800000000000008B,0x8000000000008089,0x8000000000008003,
    0x8000000000008002,0x8000000000000080,0x000000000000800A,0x800000008000000A,
    0x8000000080008081,0x8000000000008080,0x0000000080000001,0x8000000080008008,
)
# Indexed x + 5*y. FIPS 202 lane bits and sponge bytes are little-endian.
ROTATION = (0,1,62,28,27,36,44,6,55,20,3,10,43,25,39,41,45,15,21,8,18,2,61,56,14)

@dataclass
class Circuit:
    qubits: int = 0
    gates: list = field(default_factory=list)

    def allocate(self, count):
        start = self.qubits; self.qubits += count
        return list(range(start, self.qubits))

    def gate(self, kind, *bits):
        if kind not in {'x','cx','ccx','z'} or len(bits) != {'x':1,'cx':2,'ccx':3,'z':1}[kind]:
            raise ValueError('Invalid reversible gate')
        if len(set(bits)) != len(bits) or any(type(b) is not int or not 0 <= b < self.qubits for b in bits):
            raise ValueError('Invalid or overlapping gate wires')
        self.gates.append((kind, *bits))

    def apply_basis(self, state, inverse=False):
        if len(state) != self.qubits or any(bit not in (0,1) for bit in state):
            raise ValueError('Expected one classical bit per qubit')
        phase = 1
        for gate in reversed(self.gates) if inverse else self.gates:
            kind, *b = gate
            if kind == 'x': state[b[0]] ^= 1
            elif kind == 'cx': state[b[1]] ^= state[b[0]]
            elif kind == 'ccx': state[b[2]] ^= state[b[0]] & state[b[1]]
            elif state[b[0]]: phase = -phase
        return phase

    def qiskit(self):
        # Explicit small-component export only: don't allocate a full dense hash state.
        if self.qubits > 20:
            raise ValueError('Qiskit export capped at 20 qubits; full hashes use basis verification')
        from qiskit import QuantumCircuit
        out = QuantumCircuit(self.qubits)
        for kind, *bits in self.gates: getattr(out, kind)(*bits)
        return out

    def resources(self):
        return {'qubits':self.qubits,'gates':len(self.gates),'operations':dict(Counter(g[0] for g in self.gates)),
                'implementation':'trace_preserving_reference_not_optimized'}


def keccak_f1600(circuit, state):
    if len(state) != 1600: raise ValueError('Keccak-f[1600] requires 1600 wires')
    a = state
    for constant in ROUND_CONSTANTS:
        parity = circuit.allocate(320)
        theta = circuit.allocate(1600)
        result = circuit.allocate(1600)
        for x in range(5):
            for z in range(64):
                for y in range(5): circuit.gate('cx', a[(x+5*y)*64+z], parity[x*64+z])
        for y in range(5):
            for x in range(5):
                for z in range(64):
                    index = (x+5*y)*64+z
                    circuit.gate('cx', a[index], theta[index])
                    circuit.gate('cx', parity[((x-1)%5)*64+z], theta[index])
                    circuit.gate('cx', parity[((x+1)%5)*64+(z-1)%64], theta[index])
        # Rho/pi are wire permutations, not lossy assignments or measurements.
        b = [None]*1600
        for y in range(5):
            for x in range(5):
                for z in range(64):
                    b[(y+5*((2*x+3*y)%5))*64+(z+ROTATION[x+5*y])%64] = theta[(x+5*y)*64+z]
        for y in range(5):
            for x in range(5):
                for z in range(64):
                    one=b[(x+5*y)*64+z]; two=b[((x+1)%5+5*y)*64+z]; three=b[((x+2)%5+5*y)*64+z]
                    out=result[(x+5*y)*64+z]
                    # b0 XOR ((NOT b1) AND b2) = b0 XOR b2 XOR (b1 AND b2).
                    circuit.gate('cx',one,out); circuit.gate('cx',three,out); circuit.gate('ccx',two,three,out)
        for z in range(64):
            if (constant >> z) & 1: circuit.gate('x',result[z])
        a = result
    return a


def sha3_256_circuit(length):
    if type(length) is not int or not 0 <= length <= 512:
        raise ValueError('Reference hash input must be 0..512 bytes')
    circuit = Circuit()
    inputs = circuit.allocate(length*8)
    state = circuit.allocate(1600)
    # SHA3 domain suffix 0x06 and final padding bit; a rate-aligned message
    # gets a separate padding block, as required by FIPS 202.
    padded_length = (length//136+1)*136
    padding = bytearray(padded_length); padding[length] ^= 0x06; padding[-1] ^= 0x80
    for start in range(0,padded_length,136):
        for index in range(136):
            position=start+index
            for bit in range(8):
                target=state[index*8+bit]
                if position < length: circuit.gate('cx',inputs[position*8+bit],target)
                elif (padding[position]>>bit)&1: circuit.gate('x',target)
        state=keccak_f1600(circuit,state)
    return circuit,inputs,state[:256]


def threshold_comparator(circuit, bits_msb_first, target):
    width=len(bits_msb_first)
    if not width or type(target) is not int or not 0 <= target < 1<<width:
        raise ValueError('Comparator target outside bit width')
    equality=circuit.allocate(width+1); flag=circuit.allocate(1)[0]
    circuit.gate('x',equality[0])
    for i,bit in enumerate(bits_msb_first):
        one=(target>>(width-1-i))&1
        if one:
            circuit.gate('x',bit); circuit.gate('ccx',equality[i],bit,flag); circuit.gate('x',bit)
        if not one: circuit.gate('x',bit)
        circuit.gate('ccx',equality[i],bit,equality[i+1])
        if not one: circuit.gate('x',bit)
    circuit.gate('cx',equality[-1],flag)
    return flag


def basis_state(circuit, inputs, message):
    if len(inputs) != len(message)*8: raise ValueError('Message width mismatch')
    state=bytearray(circuit.qubits)
    for i,value in enumerate(message):
        for bit in range(8): state[inputs[i*8+bit]]=(value>>bit)&1
    return state


def digest_bytes(state, outputs):
    return bytes(sum(state[outputs[i*8+bit]]<<bit for bit in range(8)) for i in range(32))


def verify_phase_oracle(message, target):
    """Execute the REAL reversible SHA3/<=/phase/uncompute path on one basis input."""
    circuit,inputs,outputs=sha3_256_circuit(len(message))
    hash_end=len(circuit.gates)
    # Digest bytes represent the threshold integer in big-endian order.
    msb=[outputs[byte*8+bit] for byte in range(32) for bit in range(7,-1,-1)]
    flag=threshold_comparator(circuit,msb,target)
    comparator=circuit.gates[hash_end:]; circuit.gates=circuit.gates[:hash_end]
    state=basis_state(circuit,inputs,message); original=state[:]
    circuit.apply_basis(state); digest=digest_bytes(state,outputs)
    check=Circuit(circuit.qubits,comparator); check.apply_basis(state)
    phase=Circuit(circuit.qubits,[('z',flag)]).apply_basis(state)
    check.apply_basis(state,inverse=True); circuit.apply_basis(state,inverse=True)
    if state != original: raise AssertionError('Oracle did not clean its ancillas/input')
    return {'digest':digest.hex(),'phase':phase,'ancillas_clean':True,'hash_resources':circuit.resources(),
            'comparator_gates':len(comparator),'execution':'classical_basis_gate_verification_not_statevector'}
