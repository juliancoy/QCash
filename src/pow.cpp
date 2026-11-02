#include <algorithm>
#include <limits>
#include <openssl/bn.h>
#include <pqcoin/pow.hpp>
#include <stdexcept>

namespace pqcoin::pow {
namespace {
using Number = std::unique_ptr<BIGNUM, decltype(&BN_free)>;
void need(bool value) {
  if (!value)
    throw std::runtime_error("invalid proof-of-work data");
}
Number number() {
  Number n(BN_new(), BN_free);
  need(bool(n));
  return n;
}
Number number(std::span<const unsigned char> raw) {
  auto n = number();
  need(BN_bin2bn(raw.data(), static_cast<int>(raw.size()), n.get()) != nullptr);
  return n;
}
Bytes raw(const Number &n) {
  Bytes out(static_cast<std::size_t>(BN_num_bytes(n.get())));
  if (!out.empty())
    need(BN_bn2bin(n.get(), out.data()) == static_cast<int>(out.size()));
  return out;
}
Hash target(const Number &n) {
  Hash out{};
  need(BN_bn2binpad(n.get(), out.data(), 32) == 32);
  return out;
}
Bytes sum(std::span<const unsigned char> a, std::span<const unsigned char> b) {
  auto x = number(a), y = number(b);
  need(BN_add(x.get(), x.get(), y.get()) == 1);
  return raw(x);
}
bool greater(const Bytes &a, const Bytes &b) {
  return a.size() != b.size() ? a.size() > b.size() : a > b;
}
void put(Bytes &out, std::uint64_t n, unsigned size) {
  for (unsigned i = size; i > 0; --i)
    out.push_back(static_cast<unsigned char>(n >> ((i - 1) * 8)));
}
void add(Bytes &out, std::span<const unsigned char> bytes) {
  out.insert(out.end(), bytes.begin(), bytes.end());
}
void label(Bytes &out, std::string_view s) {
  out.insert(out.end(), s.begin(), s.end());
  out.push_back(0);
}
struct Reader {
  std::span<const unsigned char> bytes;
  std::size_t pos = 0;
  auto get(std::size_t n) {
    need(n <= bytes.size() - pos);
    auto out = bytes.subspan(pos, n);
    pos += n;
    return out;
  }
  std::uint64_t integer(unsigned n) {
    std::uint64_t out = 0;
    for (auto byte : get(n))
      out = (out << 8) | byte;
    return out;
  }
  Hash digest() {
    Hash out{};
    auto in = get(32);
    std::copy(in.begin(), in.end(), out.begin());
    return out;
  }
  void expect(std::string_view s) {
    Bytes expected;
    label(expected, s);
    auto in = get(expected.size());
    need(std::equal(in.begin(), in.end(), expected.begin()));
  }
  void end() { need(pos == bytes.size()); }
};
Hash tx_root(const std::vector<Bytes> &txs) {
  Bytes in;
  label(in, "pqcoin/pow-transactions/v1");
  put(in, txs.size(), 4);
  for (const auto &tx : txs) {
    put(in, tx.size(), 4);
    add(in, hash(tx));
  }
  return hash(in);
}
void credit(Accounts &accounts, const Hash &owner, std::uint64_t amount) {
  if (!amount)
    return;
  need(accounts.contains(owner) || accounts.size() < 1000000);
  auto &a = accounts[owner];
  a.owner = owner;
  need(a.balance <= std::numeric_limits<std::uint64_t>::max() - amount);
  a.balance += amount;
}
} // namespace
Config development_config(const Hash &anchor, std::uint64_t timestamp) {
  Config c;
  c.bootstrap = anchor;
  c.genesis_time = timestamp;
  c.maximum_target.fill(255);
  c.maximum_target[0] = 0;
  c.maximum_target[1] = 15;
  c.initial_target = c.maximum_target;
  return c;
}
Bytes encode_config(const Config &c) {
  need(c.genesis_time > 0 && c.genesis_time <= (std::uint64_t(1) << 62) &&
       c.spacing > 0 && c.spacing <= 86400 && (c.interval == 0 || c.interval >= 4) &&
       c.interval <= 1000000 && c.maturity > 0 && c.maturity <= 1000000 &&
       c.halving_interval > 0 && c.reward <= 1000000000000000ULL);
  // Total scheduled issuance is below 2 * initial reward * halving interval.
  if (c.reward)
    need(c.halving_interval <=
         std::numeric_limits<std::uint64_t>::max() / (2 * c.reward));
  need(c.maximum_target != Hash{} && c.initial_target != Hash{} &&
       c.initial_target <= c.maximum_target);
  Bytes out;
  label(out, "pqcoin/pow-chain/v1");
  add(out, c.bootstrap);
  put(out, c.genesis_time, 8);
  add(out, c.maximum_target);
  add(out, c.initial_target);
  put(out, c.spacing, 4);
  put(out, c.interval, 4);
  put(out, c.maturity, 4);
  put(out, c.reward, 8);
  put(out, c.halving_interval, 8);
  return out;
}
Config decode_config(std::span<const unsigned char> bytes) {
  Reader r{bytes};
  r.expect("pqcoin/pow-chain/v1");
  Config c;
  c.bootstrap = r.digest();
  c.genesis_time = r.integer(8);
  c.maximum_target = r.digest();
  c.initial_target = r.digest();
  c.spacing = static_cast<std::uint32_t>(r.integer(4));
  c.interval = static_cast<std::uint32_t>(r.integer(4));
  c.maturity = static_cast<std::uint32_t>(r.integer(4));
  c.reward = r.integer(8);
  c.halving_interval = r.integer(8);
  r.end();
  (void)encode_config(c);
  return c;
}
Bytes header(const Block &b) {
  Bytes out;
  label(out, "pqcoin/pow-block/v1");
  add(out, b.chain);
  add(out, b.parent);
  put(out, b.height, 8);
  put(out, b.timestamp, 8);
  add(out, b.target);
  add(out, b.miner);
  add(out, b.transactions);
  add(out, b.state);
  add(out, b.extra_nonce);
  put(out, b.nonce, 8);
  return out;
}
Bytes encode_block(const Block &b) {
  need(b.transfers.size() <= 128);
  auto out = header(b);
  put(out, b.transfers.size(), 4);
  for (const auto &tx : b.transfers) {
    need(tx.size() == 5472);
    add(out, tx);
  }
  return out;
}
Block decode_block(std::span<const unsigned char> bytes) {
  need(bytes.size() <= 1024 * 1024);
  Reader r{bytes};
  r.expect("pqcoin/pow-block/v1");
  Block b;
  b.chain = r.digest();
  b.parent = r.digest();
  b.height = r.integer(8);
  b.timestamp = r.integer(8);
  b.target = r.digest();
  b.miner = r.digest();
  b.transactions = r.digest();
  b.state = r.digest();
  b.extra_nonce = r.digest();
  b.nonce = r.integer(8);
  const auto count = r.integer(4);
  need(count <= 128);
  for (std::uint64_t i = 0; i < count; ++i) {
    auto tx = r.get(5472);
    b.transfers.emplace_back(tx.begin(), tx.end());
  }
  r.end();
  return b;
}
Hash id(const Block &b) { return hash(header(b)); }
bool valid_work(const Block &b) {
  return b.target != Hash{} && id(b) <= b.target;
}
Bytes block_work(const Hash &t) {
  need(t != Hash{});
  auto numerator = number(), denominator = number(t), out = number();
  need(BN_one(numerator.get()) &&
       BN_lshift(numerator.get(), numerator.get(), 256) &&
       BN_add_word(denominator.get(), 1));
  std::unique_ptr<BN_CTX, decltype(&BN_CTX_free)> ctx(BN_CTX_new(),
                                                      BN_CTX_free);
  need(ctx && BN_div(out.get(), nullptr, numerator.get(), denominator.get(),
                     ctx.get()));
  return raw(out);
}
std::string work_decimal(std::span<const unsigned char> work) {
  auto n = number(work);
  char *value = BN_bn2dec(n.get());
  need(value);
  std::string out(value);
  OPENSSL_free(value);
  return out;
}
Hash state_hash(const State &s) {
  Bytes out;
  label(out, "pqcoin/pow-state/v1");
  put(out, s.accounts.size(), 8);
  for (const auto &[owner, a] : s.accounts) {
    add(out, owner);
    put(out, a.balance, 8);
    put(out, a.nonce, 8);
  }
  put(out, s.immature.size(), 8);
  for (const auto &r : s.immature) {
    put(out, r.unlock_height, 8);
    add(out, r.owner);
    put(out, r.amount, 8);
  }
  return hash(out);
}
Chain::Chain(Config c) : config_(c) {
  context_ = hash(encode_config(c));
  tip_ = context_;
  Entry root;
  root.block.chain = context_;
  root.block.timestamp = c.genesis_time;
  root.block.target = c.initial_target;
  root.block.state = state_hash(root.state);
  entries_.emplace(context_, std::move(root));
}
const Entry &Chain::at(const Hash &h) const {
  auto it = entries_.find(h);
  need(it != entries_.end());
  return it->second;
}
std::uint64_t Chain::median_time(const Hash &parent) const {
  std::vector<std::uint64_t> times;
  Hash cursor = parent;
  for (unsigned i = 0; i < 11; ++i) {
    const auto &b = at(cursor).block;
    times.push_back(b.timestamp);
    if (b.height == 0)
      break;
    cursor = b.parent;
  }
  std::sort(times.begin(), times.end());
  return times[times.size() / 2];
}
Hash Chain::next_target(const Hash &parent) const {
  const auto &last = at(parent).block;
  if (last.height == 0 || config_.interval == 0)
    return config_.initial_target;
  if ((last.height + 1) % config_.interval != 0)
    return last.target;
  auto cursor = parent;
  for (std::uint32_t i = 1; i < config_.interval; ++i)
    cursor = at(cursor).block.parent;
  const auto first = at(cursor).block.timestamp;
  const std::uint64_t expected =
      std::uint64_t(config_.spacing) * config_.interval;
  auto elapsed = last.timestamp > first ? last.timestamp - first : 0;
  elapsed = std::clamp(elapsed, std::max<std::uint64_t>(1, expected / 4),
                       expected * 4);
  auto n = number(last.target);
  need(BN_mul_word(n.get(), static_cast<BN_ULONG>(elapsed)) == 1);
  need(BN_div_word(n.get(), static_cast<BN_ULONG>(expected)) !=
       static_cast<BN_ULONG>(-1));
  if (BN_is_zero(n.get()))
    need(BN_one(n.get()) == 1);
  auto maximum = number(config_.maximum_target);
  if (BN_cmp(n.get(), maximum.get()) > 0)
    return config_.maximum_target;
  return target(n);
}
State Chain::execute(const Block &b) const {
  auto s = at(b.parent).state;
  std::vector<Reward> pending;
  for (const auto &r : s.immature) {
    if (r.unlock_height <= b.height)
      credit(s.accounts, r.owner, r.amount);
    else
      pending.push_back(r);
  }
  s.immature = std::move(pending);
  std::uint64_t fees = 0;
  for (const auto &rawtx : b.transfers) {
    auto tx = decode_transfer(rawtx);
    need(tx.chain_id == context_);
    auto source = s.accounts.find(tx.sender);
    need(source != s.accounts.end());
    auto from = source->second;
    auto dest = s.accounts.find(tx.recipient);
    auto to =
        dest == s.accounts.end() ? Account{tx.recipient, 0, 0} : dest->second;
    need(dest != s.accounts.end() || s.accounts.size() < 1000000);
    need(apply(tx, from, to));
    need(fees <= std::numeric_limits<std::uint64_t>::max() - tx.fee);
    fees += tx.fee;
    s.accounts[tx.sender] = from;
    s.accounts[tx.recipient] = to;
  }
  const auto halvings = (b.height - 1) / config_.halving_interval;
  const auto subsidy = halvings >= 64 ? 0 : config_.reward >> halvings;
  need(fees <= std::numeric_limits<std::uint64_t>::max() - subsidy);
  need(b.height <=
       std::numeric_limits<std::uint64_t>::max() - config_.maturity);
  if (subsidy + fees)
    s.immature.push_back(
        {b.height + config_.maturity, b.miner, subsidy + fees});
  return s;
}
Block Chain::candidate(const Hash &parent, const Hash &miner,
                       std::uint64_t timestamp, std::vector<Bytes> txs) const {
  need(txs.size() <= 128);
  const auto &p = at(parent).block;
  need(p.height < std::numeric_limits<std::uint64_t>::max());
  Block b;
  b.chain = context_;
  b.parent = parent;
  b.height = p.height + 1;
  b.timestamp = std::max(timestamp, median_time(parent) + 1);
  b.target = next_target(parent);
  b.miner = miner;
  b.transfers = std::move(txs);
  b.transactions = tx_root(b.transfers);
  b.state = state_hash(execute(b));
  return b;
}
bool Chain::accept(const Block &b, std::uint64_t now) {
  const auto key = id(b);
  if (entries_.contains(key))
    return false;
  if (b.chain != context_ || b.height == 0 ||
      b.height > std::numeric_limits<std::uint64_t>::max() - config_.maturity ||
      !entries_.contains(b.parent) || b.transfers.size() > 128)
    return false;
  const auto &parent = at(b.parent);
  if (b.height != parent.block.height + 1 ||
      b.target != next_target(b.parent) ||
      b.timestamp <= median_time(b.parent) ||
      b.timestamp >
          now + std::min<std::uint64_t>(
                    7200, std::numeric_limits<std::uint64_t>::max() - now) ||
      !valid_work(b) || b.transactions != tx_root(b.transfers))
    return false;
  State next;
  try {
    next = execute(b);
  } catch (const std::runtime_error &) {
    return false;
  }
  if (state_hash(next) != b.state)
    return false;
  auto work = sum(parent.work, block_work(b.target));
  const bool better = greater(work, best().work);
  entries_.emplace(key, Entry{b, std::move(work), std::move(next)});
  detached_.clear();
  attached_.clear();
  if (better) {
    auto old = tip_, fresh = key;
    while (at(old).block.height > at(fresh).block.height) {
      detached_.push_back(old);
      old = at(old).block.parent;
    }
    while (at(fresh).block.height > at(old).block.height) {
      attached_.push_back(fresh);
      fresh = at(fresh).block.parent;
    }
    while (old != fresh) {
      detached_.push_back(old);
      attached_.push_back(fresh);
      old = at(old).block.parent;
      fresh = at(fresh).block.parent;
    }
    std::reverse(attached_.begin(), attached_.end());
    tip_ = key;
  }
  return true;
}
std::vector<Hash> Chain::locator() const {
  std::vector<Hash> out;
  auto cursor = tip_;
  std::uint64_t step = 1;
  for (unsigned count = 0; count < 63; ++count) {
    out.push_back(cursor);
    if (cursor == context_)
      return out;
    for (std::uint64_t i = 0; i < step && cursor != context_; ++i)
      cursor = at(cursor).block.parent;
    if (count >= 10 && step < (std::uint64_t(1) << 62))
      step *= 2;
  }
  out.push_back(context_);
  return out;
}
std::vector<Block> Chain::after(const std::vector<Hash> &locators,
                                std::size_t limit) const {
  need(locators.size() <= 64 && limit <= 16);
  std::vector<Hash> active;
  auto cursor = tip_;
  for (;;) {
    active.push_back(cursor);
    if (cursor == context_)
      break;
    cursor = at(cursor).block.parent;
  }
  std::reverse(active.begin(), active.end());
  auto begin = active.end();
  for (const auto &wanted : locators) {
    auto found = std::find(active.begin(), active.end(), wanted);
    if (found != active.end()) {
      begin = found + 1;
      break;
    }
  }
  need(begin != active.end() ||
       std::find(locators.begin(), locators.end(), tip_) != locators.end());
  std::vector<Block> out;
  for (auto it = begin; it != active.end() && out.size() < limit; ++it)
    out.push_back(at(*it).block);
  return out;
}
bool mine_batch(Block &b, std::uint64_t attempts) {
  auto encoded = header(b);
  for (std::uint64_t i = 0; i < attempts; ++i) {
    if (hash(encoded) <= b.target)
      return true;
    if (b.nonce == std::numeric_limits<std::uint64_t>::max()) {
      b.nonce = 0;
      bool carry = true;
      for (std::size_t j = b.extra_nonce.size(); j > 0 && carry; --j) {
        carry = b.extra_nonce[j - 1] == 255;
        ++b.extra_nonce[j - 1];
      }
      need(!carry);
      std::copy(b.extra_nonce.begin(), b.extra_nonce.end(), encoded.end() - 40);
    } else
      ++b.nonce;
    for (unsigned j = 0; j < 8; ++j)
      encoded[encoded.size() - 1 - j] =
          static_cast<unsigned char>(b.nonce >> (j * 8));
  }
  return false;
}
} // namespace pqcoin::pow
