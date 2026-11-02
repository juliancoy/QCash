#pragma once
#include <functional>
#include <map>
#include <pqcoin/app.hpp>
#include <string>

namespace pqcoin::pow {
struct Config {
  Hash bootstrap{};
  std::uint64_t genesis_time = 0;
  Hash maximum_target{}, initial_target{};
  std::uint32_t spacing = 1, interval = 0, maturity = 2;
  std::uint64_t reward = 50, halving_interval = 210000;
};
Config development_config(const Hash &bootstrap, std::uint64_t timestamp);
Bytes encode_config(const Config &config);
Config decode_config(std::span<const unsigned char> bytes);
struct Block {
  Hash chain{}, parent{}, target{}, miner{}, transactions{}, state{},
      extra_nonce{};
  std::uint64_t height = 0, timestamp = 0, nonce = 0;
  std::vector<Bytes> transfers;
};
Bytes header(const Block &block);
Bytes encode_block(const Block &block);
Block decode_block(std::span<const unsigned char> bytes);
Hash id(const Block &block);
bool valid_work(const Block &block);
Bytes block_work(const Hash &target);
std::string work_decimal(std::span<const unsigned char> work);
struct Reward {
  std::uint64_t unlock_height;
  Hash owner;
  std::uint64_t amount;
};
struct State {
  Accounts accounts;
  std::vector<Reward> immature;
};
Hash state_hash(const State &state);
struct Entry {
  Block block;
  Bytes work;
  State state;
};
class Chain {
public:
  explicit Chain(Config config);
  const Config &config() const { return config_; }
  const Hash &context() const { return context_; }
  const Hash &tip() const { return tip_; }
  const Entry &at(const Hash &block) const;
  const Entry &best() const { return at(tip_); }
  Hash next_target(const Hash &parent) const;
  std::uint64_t median_time(const Hash &parent) const;
  Block candidate(const Hash &parent, const Hash &miner,
                  std::uint64_t timestamp,
                  std::vector<Bytes> transfers = {}) const;
  // Unknown parent, malformed proof or invalid state: no index/state mutation.
  bool accept(const Block &block, std::uint64_t now);
  std::vector<Hash> locator() const;
  std::vector<Block> after(const std::vector<Hash> &locator,
                           std::size_t limit = 16) const;
  const std::vector<Hash> &detached() const { return detached_; }
  const std::vector<Hash> &attached() const { return attached_; }

private:
  State execute(const Block &block) const;
  Config config_;
  Hash context_{}, tip_{};
  std::map<Hash, Entry> entries_;
  std::vector<Hash> detached_, attached_;
};
// Bounded work permits cancellation/rechecking the chain tip between batches.
bool mine_batch(Block &block, std::uint64_t attempts);
} // namespace pqcoin::pow
