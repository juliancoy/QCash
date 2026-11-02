#pragma once
#include <memory>
#include <optional>
#include <pqcoin/crypto.hpp>
#include <string>
#include <vector>
namespace pqcoin {
struct VulkanBatch {
  std::optional<std::uint32_t> winner;
  std::optional<double> gpu_seconds;
  std::vector<Hash> digests;
};
// Persistent buffers, pipeline and queue; single caller, bounded dispatches.
class VulkanMiner {
public:
  static constexpr std::uint32_t max_batch = 1048576;
  explicit VulkanMiner(std::optional<std::uint32_t> device = {},
                       std::uint32_t workgroup = 64);
  ~VulkanMiner();
  VulkanMiner(const VulkanMiner &) = delete;
  VulkanMiner &operator=(const VulkanMiner &) = delete;
  void prepare(std::span<const unsigned char> header);
  VulkanBatch run(std::uint64_t start, std::uint32_t count,
                  bool capture = false);
  std::string device_name() const;
  std::uint32_t device_index() const;
  std::uint32_t workgroup_size() const;

private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
} // namespace pqcoin
