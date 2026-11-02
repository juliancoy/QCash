#include <iostream>
#include <limits>
#include <pqcoin/pow.hpp>
#include <pqcoin/vulkan_miner.hpp>
#include <random>
#include <stdexcept>
using namespace pqcoin;
namespace p = pqcoin::pow;
void check(bool value) {
  if (!value)
    throw std::runtime_error("Vulkan hash/search check failed");
}
template <class F> void rejects(F f) {
  try {
    f();
  } catch (const std::runtime_error &) {
    return;
  }
  throw std::runtime_error("Invalid GPU request accepted");
}
int main() {
  try {
    unsigned verified = 0;
    for (const auto *memory : {"mapped", "mapped-local", "staged"}) {
      for (const auto *variant :
           {"scalar64", "pair32", "compact64", "compact32"}) {
        VulkanMiner gpu({}, 64, variant, false, memory);
        check(gpu.memory_topology().find("\"policy\":\"" + std::string(memory) +
                                         "\"") != std::string::npos);
        rejects([&] { gpu.run(0, 1); });
        for (bool prepare_gpu : {false, true}) {
          std::mt19937_64 rng(71);

          for (unsigned trial = 0; trial < 4; trial++) {
            p::Block block;
            for (auto *h :
                 {&block.chain, &block.parent, &block.target, &block.miner,
                  &block.transactions, &block.state, &block.extra_nonce})
              for (auto &b : *h)
                b = static_cast<unsigned char>(rng());
            block.height = rng();
            block.timestamp = rng();
            for (auto start :
                 {std::uint64_t(0), std::uint64_t(0xfffffffcULL),
                  std::numeric_limits<std::uint64_t>::max() - 256}) {
              block.nonce = start;
              gpu.prepare(p::header(block), prepare_gpu);
              auto result = gpu.run(start, 257, true, 65);
              // Reusing the command buffer must take new nonce parameters and
              // retain the prepared GPU midstate, rather than permuting the
              // prefix again.
              auto again = gpu.run(start, 257, true, 65, false);
              auto final = gpu.run(start, 257, true, 65, true);
              check(final.digests == result.digests);
              check(again.digests == result.digests &&
                    again.winner == result.winner);
              std::optional<std::uint32_t> winner;
              for (std::uint32_t i = 0; i < 257; i++) {
                auto b = block;
                b.nonce += i;
                auto digest = p::id(b);
                check(digest == result.digests[i]);
                if (digest <= block.target && !winner)
                  winner = i;
                verified++;
              }
              check(result.winner == winner);
            }
          }
          p::Block block;
          block.target.fill(255);
          gpu.prepare(p::header(block), prepare_gpu);
          check(gpu.run(0, 65).winner == 0);
          // Changing a template target must refresh its first-block midstate
          // too.
          block.target.fill(0);
          block.target.back() = 1;
          gpu.prepare(p::header(block), prepare_gpu);
          check(!gpu.run(0, 65).winner);
          rejects([&] { gpu.run(0, 0); });
          rejects([&] { gpu.run(0, VulkanMiner::max_batch + 1); });
          rejects(
              [&] { gpu.run(std::numeric_limits<std::uint64_t>::max(), 2); });
          rejects([&] { gpu.run(0, 4097, true); });
          rejects([&] { gpu.prepare(Bytes(267)); });
        }
        std::cout
            << "Passed memory " << memory << " shader " << variant
            << " CPU/GPU preparation, command reuse and queued dispatches\n";
      }
    }
    std::cout << "Verified " << verified
              << " GPU SHA3 digests, target search, nonce carry, bounds on "
              << "Vulkan GPU" << '\n';
    return 0;
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
