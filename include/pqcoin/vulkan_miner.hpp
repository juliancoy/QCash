#pragma once
#include <filesystem>
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
  std::uint64_t explicit_upload_bytes = 0, explicit_readback_bytes = 0;
};
// Persistent buffers, pipeline and queue; single caller, bounded dispatches.
class VulkanMiner {
public:
  static constexpr std::uint32_t max_dispatch = 1048576;
  static constexpr std::uint32_t max_batch = max_dispatch * 16;
  explicit VulkanMiner(std::optional<std::uint32_t> device = {},
                       std::uint32_t workgroup = 64,
                       std::string shader = "pair32", bool diagnostics = false,
                       std::string memory = "mapped");
  ~VulkanMiner();
  VulkanMiner(const VulkanMiner &) = delete;
  VulkanMiner &operator=(const VulkanMiner &) = delete;
  void prepare(std::span<const unsigned char> header, bool gpu = true);
  VulkanBatch run(std::uint64_t start, std::uint32_t count,
                  bool capture = false, std::uint32_t dispatch_size = 262144,
                  bool coalesced_capture = false);
  std::string memory_topology() const;
  void dump_pipeline(const std::filesystem::path &directory) const;
  std::string pipeline_statistics() const;
  std::uint64_t command_recordings() const;
  std::string device_name() const;
  std::uint32_t device_index() const;
  std::uint32_t workgroup_size() const;

private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
} // namespace pqcoin
