#pragma once
#include <filesystem>
#include <map>
#include <optional>
#include <pqcoin/ledger.hpp>
#include <string>

namespace pqcoin {
using Accounts = std::map<Hash, Account>;
struct AppGenesis {
  Hash bootstrap{};
  std::string chain;
  Accounts accounts;
};
Bytes encode_genesis(const AppGenesis &genesis);
AppGenesis decode_genesis(std::span<const unsigned char> bytes);
Bytes encode_transfer(const Transfer &tx);
Transfer decode_transfer(std::span<const unsigned char> bytes);
struct Snapshot {
  std::uint64_t height = 0;
  Accounts accounts;
};
class Application {
public:
  Application(const std::filesystem::path &directory,
              std::span<const unsigned char> genesis);
  Hash context() const { return context_; }
  const Snapshot &committed() const { return committed_; }
  Hash app_hash() const;
  bool check(std::span<const unsigned char> tx) const;
  void reset_trial();
  bool apply_trial(std::span<const unsigned char> tx);
  Hash stage(std::uint64_t height);
  void commit();

private:
  bool execute(Accounts &accounts, std::span<const unsigned char> tx) const;
  Bytes encode_state(const Snapshot &snapshot) const;
  Snapshot decode_state(std::span<const unsigned char> bytes) const;
  void persist(const Snapshot &snapshot) const;
  std::filesystem::path directory_;
  Hash context_{};
  Snapshot committed_;
  Accounts trial_;
  std::optional<Snapshot> staged_;
};
} // namespace pqcoin
