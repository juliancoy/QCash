#include <iostream>
#include <limits>
#include <pqcoin/pow.hpp>
#include <stdexcept>
using namespace pqcoin;
namespace p = pqcoin::pow;
unsigned checks = 0;
void check(bool ok) {
  ++checks;
  if (!ok)
    throw std::runtime_error("PoW test failed at " + std::to_string(checks));
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
p::Block mined(p::Chain &chain, const Hash &parent, const Hash &miner,
               std::uint64_t time, std::vector<Bytes> txs = {}) {
  auto b = chain.candidate(parent, miner, time, std::move(txs));
  while (!p::mine_batch(b, 10000)) {
  }
  check(chain.accept(b, 100000));
  return b;
}
int main() {
  try {
    auto config = p::development_config({}, 1000);
    config.maximum_target.fill(255);
    config.initial_target = config.maximum_target;
    config.interval = 4;
    config.spacing = 10;
    config.halving_interval = 4;
    auto excessive = config;
    excessive.reward = 1000000000000000ULL;
    excessive.halving_interval = std::numeric_limits<std::uint64_t>::max();
    rejects([&] { (void)p::encode_config(excessive); });
    const auto encoded = p::encode_config(config);
    check(p::encode_config(p::decode_config(encoded)) == encoded);
    auto bad_config = encoded;
    bad_config.push_back(0);
    rejects([&] { (void)p::decode_config(bad_config); });
    check(p::work_decimal(p::block_work(config.maximum_target)) == "1");
    Hash tiny{};
    tiny.back() = 1;
    check(p::work_decimal(p::block_work(tiny)) ==
          "57896044618658097711785492504343953926634992332820282019728792003956"
          "564819968");
    auto alice = signing_key(), bob = signing_key();
    const auto owner = address(public_key(alice)),
               recipient = address(public_key(bob));
    p::Chain chain(config);
    const auto root = chain.context();
    auto first = mined(chain, root, owner, 1040);
    check(chain.best().state.accounts.empty());
    check(chain.best().state.immature.size() == 1);
    auto wire = p::encode_block(first);
    check(p::encode_block(p::decode_block(wire)) == wire);
    for (auto size : {std::size_t(0), wire.size() - 1, wire.size() + 1}) {
      auto bad = wire;
      bad.resize(size);
      rejects([&] { (void)p::decode_block(bad); });
    }
    Transfer tx;
    tx.chain_id = root;
    tx.sender = owner;
    tx.recipient = recipient;
    tx.amount = 10;
    tx.fee = 1;
    sign(tx, alice);
    rejects([&] {
      (void)chain.candidate(chain.tip(), owner, 1080, {encode_transfer(tx)});
    });
    auto second = mined(chain, chain.tip(), owner, 1080);
    auto third = mined(chain, chain.tip(), owner, 1120, {encode_transfer(tx)});
    check(chain.best().state.accounts.at(owner).balance == 39);
    check(chain.best().state.accounts.at(recipient).balance == 10);
    check(chain.best().state.immature.back().amount == 51);
    rejects([&] {
      (void)chain.candidate(chain.tip(), owner, 1160, {encode_transfer(tx)});
    });
    auto fourth = mined(chain, chain.tip(), owner, 1160);
    auto fifth = mined(chain, chain.tip(), owner, 1200);
    check(chain.best().state.immature.back().amount == 25);
    check(chain.best().state.accounts.at(owner).balance == 140);
    auto changed = chain.candidate(chain.tip(), owner, 1240);
    changed.state[0] ^= 1;
    while (!p::mine_batch(changed, 10000)) {
    }
    auto old = chain.tip();
    check(!chain.accept(changed, 100000));
    check(chain.tip() == old);
    auto future = chain.candidate(chain.tip(), owner, 107201);
    while (!p::mine_batch(future, 10000)) {
    }
    check(!chain.accept(future, 100000));
    auto timestamp = chain.candidate(chain.tip(), owner, 1240);
    timestamp.timestamp = chain.median_time(chain.tip());
    while (!p::mine_batch(timestamp, 10000)) {
    }
    check(!chain.accept(timestamp, 100000));
    auto foreign = chain.candidate(chain.tip(), owner, 1240);
    foreign.chain[0] ^= 1;
    while (!p::mine_batch(foreign, 10000)) {
    }
    check(!chain.accept(foreign, 100000));
    auto unknown = chain.candidate(chain.tip(), owner, 1240);
    unknown.parent.fill(7);
    while (!p::mine_batch(unknown, 10000)) {
    }
    check(!chain.accept(unknown, 100000));
    // A shorter branch with faster production earns a harder target and more
    // work.
    auto sixth = mined(chain, chain.tip(), owner, 1240);
    check(chain.best().block.height == 6);
    auto b1 = mined(chain, root, recipient, 1001);
    check(chain.best().block.height == 6);
    auto b2 = mined(chain, p::id(b1), recipient, 1002);
    auto b3 = mined(chain, p::id(b2), recipient, 1003);
    auto b4 = mined(chain, p::id(b3), recipient, 1004);
    check(chain.tip() == p::id(b4));
    check(chain.best().block.height == 4);
    check(chain.detached().size() == 6 && chain.attached().size() == 4);
    check(!chain.best().state.accounts.contains(owner));
    check(chain.best().state.accounts.at(recipient).balance == 100);
    check(p::work_decimal(chain.best().work) == "7");
    auto wrong_target = chain.candidate(chain.tip(), recipient, 1005);
    wrong_target.target = config.maximum_target;
    while (!p::mine_batch(wrong_target, 10000)) {
    }
    check(!chain.accept(wrong_target, 100000));
    // Target clamps: fast periods increase difficulty at most 4x; slow ones
    // ease at most 4x.
    check(b4.target[0] == 63);
    check(fourth.target == config.maximum_target);
    auto locators = chain.locator();
    check(locators.back() == root);
    check(chain.after({root}).size() == 4);
    check(chain.after({chain.tip()}).empty());
    p::Chain replay(config);
    for (const auto &block : chain.after({root}))
      check(replay.accept(block, 100000));
    check(replay.tip() == chain.tip());
    check(p::state_hash(replay.best().state) ==
          p::state_hash(chain.best().state));
    // A new chain configuration invalidates the old work and transaction
    // identity.
    auto other_config = config;
    ++other_config.spacing;
    p::Chain other(other_config);
    check(other.context() != root);
    check(!other.accept(first, 100000));
    // Known easy threshold: the nonce search really meets a 12-bit target.
    p::Chain realistic(p::development_config({}, 1000));
    check(realistic.config().interval == 0);
    for (unsigned interval : {1U, 2U, 3U}) {
      auto invalid = realistic.config();
      invalid.interval = interval;
      rejects([&] { (void)p::encode_config(invalid); });
    }
    auto work = realistic.candidate(realistic.tip(), owner, 1001);
    while (!p::mine_batch(work, 10000)) {
    }
    check(p::valid_work(work));
    auto altered = work;
    altered.target = tiny;
    check(!realistic.accept(altered, 100000));
    check(realistic.accept(work, 100000));
    check(realistic.next_target(realistic.tip()) == realistic.config().initial_target);
    check(p::id(work)[0] == 0 && (p::id(work)[1] & 0xf0) == 0);
    auto rollover = realistic.candidate(realistic.tip(), owner, 1001);
    rollover.target = tiny;
    rollover.nonce = std::numeric_limits<std::uint64_t>::max();
    while (p::valid_work(rollover))
      ++rollover.extra_nonce[0];
    check(!p::mine_batch(rollover, 1));
    check(rollover.nonce == 0 && rollover.extra_nonce.back() == 1);
    std::cout << checks << " PoW checks passed\n";
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
