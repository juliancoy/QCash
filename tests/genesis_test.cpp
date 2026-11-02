#include <iostream>
#include <pqcoin/genesis.hpp>
#include <stdexcept>
using namespace pqcoin;
void check(bool condition) {
  if (!condition)
    throw std::runtime_error("genesis test failed");
}
int main() {
  try {
    const auto block = devnet::mine(1793552400);
    check(devnet::valid(block));
    const auto bytes = devnet::encode(block);
    const auto decoded = devnet::decode(bytes);
    check(decoded.timestamp == block.timestamp && decoded.nonce == block.nonce);
    for (std::size_t i = 0; i < bytes.size(); ++i) {
      // Timestamp and nonce are intentionally variable. Every fixed byte is
      // checked.
      if ((i >= bytes.size() - 20 && i < bytes.size() - 12) ||
          i >= bytes.size() - 8)
        continue;
      auto bad = bytes;
      bad[i] ^= 1;
      bool rejected = false;
      try {
        (void)devnet::decode(bad);
      } catch (const std::runtime_error &) {
        rejected = true;
      }
      check(rejected);
    }
    auto bad_work = block;
    do {
      ++bad_work.nonce;
    } while (devnet::valid(bad_work));
    check(!devnet::valid(bad_work));
    check(!devnet::valid({0, block.nonce}));
    for (const auto size :
         {std::size_t(0), bytes.size() - 1, bytes.size() + 1}) {
      auto bad = bytes;
      bad.resize(size);
      bool rejected = false;
      try {
        (void)devnet::decode(bad);
      } catch (const std::runtime_error &) {
        rejected = true;
      }
      check(rejected);
    }
    std::cout << "Genesis mining, canonical decoding, parameter tampering and "
                 "work checks passed\n";
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
