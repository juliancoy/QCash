"""Embed build-produced SPIR-V; the miner never loads a peer-selected shader."""
import pathlib
import struct
import sys
source, output = map(pathlib.Path, sys.argv[1:])
raw = source.read_bytes()
words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
output.write_text('#pragma once\n#include <cstdint>\ninline constexpr std::uint32_t mining_spirv[] = {\n' +
                  ',\n'.join(','.join(hex(w) for w in words[i:i+12]) for i in range(0, len(words), 12)) + '\n};\n')
