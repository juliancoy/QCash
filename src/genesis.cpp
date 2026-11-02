#include <algorithm>
#include <limits>
#include <pqcoin/genesis.hpp>
#include <stdexcept>
#include <string_view>

namespace pqcoin::devnet {
namespace {
void number(Bytes &out, std::uint64_t value, unsigned width) {
  for (unsigned i = width; i > 0; --i)
    out.push_back(static_cast<unsigned char>(value >> ((i - 1) * 8)));
}
Bytes text(std::string_view value) {
  Bytes out(value.begin(), value.end());
  out.push_back(0);
  return out;
}
constexpr std::string_view domain = "pqcoin/dev-genesis/v1";
} // namespace
Bytes encode(const Genesis &block) {
  auto out = text(domain);
  number(out, 1, 4);            // version
  number(out, 0x50515431, 4);   // local PQT1 network
  number(out, 0, 8);            // height: genesis
  out.insert(out.end(), 32, 0); // previous block: none
  for (const auto &commitment :
       {hash(text("pqcoin/devnet/empty-transactions/v1")),
        hash(text("pqcoin/devnet/empty-accounts/v1")),
        hash(text("PQCoin local development genesis"))})
    out.insert(out.end(), commitment.begin(), commitment.end());
  number(out, block.timestamp, 8);
  number(out, difficulty_bits, 4);
  number(out, block.nonce, 8);
  return out;
}
Genesis decode(std::span<const unsigned char> bytes) {
  const auto skeleton = encode({});
  if (bytes.size() != skeleton.size())
    throw std::runtime_error("invalid genesis length");
  // The only variable fields are timestamp and nonce; all chain parameters,
  // commitments and the work threshold must match this development protocol.
  const auto timestamp_offset = bytes.size() - 20;
  auto read = [&](std::size_t offset) {
    std::uint64_t result = 0;
    for (std::size_t i = 0; i < 8; ++i)
      result = (result << 8) | bytes[offset + i];
    return result;
  };
  Genesis block{read(timestamp_offset), read(bytes.size() - 8)};
  const auto expected = encode(block);
  if (!std::equal(bytes.begin(), bytes.end(), expected.begin()))
    throw std::runtime_error("invalid genesis parameters");
  return block;
}
Hash block_hash(const Genesis &block) { return hash(encode(block)); }
bool valid(const Genesis &block) {
  if (!block.timestamp)
    return false;
  const auto digest = block_hash(block);
  return digest[0] == 0 && digest[1] == 0;
}
Genesis mine(std::uint64_t timestamp) {
  if (!timestamp)
    throw std::runtime_error("genesis requires a timestamp");
  Genesis block{timestamp, 0};
  auto header = encode(block);
  // Reuse the canonical header; only the final eight nonce bytes change.
  for (;;) {
    const auto digest = hash(header);
    if (digest[0] == 0 && digest[1] == 0)
      return block;
    if (block.nonce == std::numeric_limits<std::uint64_t>::max())
      throw std::runtime_error("nonce space exhausted");
    ++block.nonce;
    for (unsigned i = 0; i < 8; ++i)
      header[header.size() - 1 - i] =
          static_cast<unsigned char>(block.nonce >> (i * 8));
  }
}
std::string hex(std::span<const unsigned char> bytes) {
  constexpr char digits[] = "0123456789abcdef";
  std::string out;
  out.reserve(bytes.size() * 2);
  for (const auto byte : bytes) {
    out.push_back(digits[byte >> 4]);
    out.push_back(digits[byte & 15]);
  }
  return out;
}
} // namespace pqcoin::devnet
