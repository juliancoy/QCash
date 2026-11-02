#pragma once
#include <pqcoin/crypto.hpp>
#include <string>

namespace pqcoin::devnet {
// Isolated proof-of-work bootstrap experiment, not the proposed BFT protocol.
struct Genesis {
  std::uint64_t timestamp = 0;
  std::uint64_t nonce = 0;
};
inline constexpr unsigned difficulty_bits = 16;
Bytes encode(const Genesis &block);
Genesis decode(std::span<const unsigned char> bytes);
Hash block_hash(const Genesis &block);
bool valid(const Genesis &block);
Genesis mine(std::uint64_t timestamp);
std::string hex(std::span<const unsigned char> bytes);
} // namespace pqcoin::devnet
