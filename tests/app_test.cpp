#include <fstream>
#include <iostream>
#include <pqcoin/app.hpp>
#include <pqcoin/genesis.hpp>
#include <stdexcept>
#include <unistd.h>
using namespace pqcoin;
namespace fs = std::filesystem;
void check(bool ok) {
  if (!ok)
    throw std::runtime_error("application test failed");
}
template <class F> void rejects(F f) {
  bool failed = false;
  try {
    f();
  } catch (const std::runtime_error &) {
    failed = true;
  }
  check(failed);
}
struct Temporary {
  fs::path path;
  Temporary() {
    char pattern[] = "/tmp/pqcoin-app-XXXXXX";
    const auto p = mkdtemp(pattern);
    if (!p)
      throw std::runtime_error("temporary directory failed");
    path = p;
  }
  ~Temporary() {
    std::error_code error;
    fs::remove_all(path, error);
  }
};
int main() {
  try {
    Temporary temporary;
    auto alice = signing_key(), bob = signing_key();
    auto from = address(public_key(alice)), to = address(public_key(bob));
    AppGenesis config{{}, "pqcoin-dev-unit", {{from, {from, 1000, 0}}}};
    const auto genesis = encode_genesis(config);
    check(encode_genesis(decode_genesis(genesis)) == genesis);
    auto invalid_genesis = genesis;
    invalid_genesis.push_back(0);
    rejects([&] { (void)decode_genesis(invalid_genesis); });
    Transfer tx;
    tx.sender = from;
    tx.recipient = to;
    tx.chain_id = hash(genesis);
    tx.amount = 25;
    tx.fee = 1;
    sign(tx, alice);
    const auto wire = encode_transfer(tx);
    check(wire.size() == 5472);
    check(encode_transfer(decode_transfer(wire)) == wire);
    for (auto size : {std::size_t(0), wire.size() - 1, wire.size() + 1}) {
      auto bad = wire;
      bad.resize(size);
      rejects([&] { (void)decode_transfer(bad); });
    }
    Hash committed_hash{};
    {
      Application app(temporary.path, genesis);
      check(app.committed().height == 0);
      const auto initial_hash = app.app_hash();
      check(app.check(wire));
      auto bad = wire;
      bad.back() ^= 1;
      check(!app.check(bad));
      check(app.app_hash() == initial_hash);
      auto foreign = tx;
      foreign.chain_id[0] ^= 1;
      sign(foreign, alice);
      check(verify(foreign));
      check(!app.check(encode_transfer(foreign)));
      app.reset_trial();
      check(app.apply_trial(wire));
      check(!app.apply_trial(wire));
      rejects([&] { (void)app.stage(2); });
      const auto staged_hash = app.stage(1);
      check(app.app_hash() == initial_hash);
      // Proposal/mempool calls between FinalizeBlock and Commit cannot clobber
      // staged state.
      app.reset_trial();
      check(app.check(wire));
      app.commit();
      check(app.app_hash() == staged_hash);
      check(app.committed().height == 1);
      check(app.committed().accounts.at(from).balance == 974);
      check(app.committed().accounts.at(from).nonce == 1);
      check(app.committed().accounts.at(to).balance == 25);
      check(!app.check(wire));
      committed_hash = app.app_hash();
      rejects([&] { app.commit(); });
      tx.nonce = 1;
      sign(tx, alice);
      app.reset_trial();
      check(app.apply_trial(encode_transfer(tx)));
      (void)app.stage(2);
      // Simulated crash before Commit: the staged state must not reach disk.
    }
    {
      Application restarted(temporary.path, genesis);
      check(restarted.committed().height == 1);
      check(restarted.app_hash() == committed_hash);
      check(restarted.check(encode_transfer(tx)));
      restarted.reset_trial();
      check(restarted.apply_trial(encode_transfer(tx)));
      (void)restarted.stage(2);
      restarted.commit();
      check(restarted.committed().accounts.at(from).balance == 948);
    }
    config.chain = "pqcoin-dev-other";
    const auto wrong = encode_genesis(config);
    rejects([&] { Application other(temporary.path, wrong); });
    {
      std::fstream file(temporary.path / "state.bin",
                        std::ios::in | std::ios::out | std::ios::binary);
      file.seekp(0);
      file.put('!');
      file.close();
    }
    rejects([&] { Application corrupt(temporary.path, genesis); });
    std::cout << "Canonical wire, chain binding, staged commit, crash recovery "
                 "and corruption checks passed\n";
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
