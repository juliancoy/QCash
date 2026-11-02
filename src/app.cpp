#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <limits>
#include <pqcoin/app.hpp>
#include <stdexcept>
#include <unistd.h>

namespace pqcoin {
namespace {
constexpr std::size_t max_accounts = 4096, max_file = 512 * 1024,
                      tx_size = 5472;
void need(bool ok) {
  if (!ok)
    throw std::runtime_error("invalid application data or storage failure");
}
void put(Bytes &out, std::uint64_t n, unsigned size) {
  for (unsigned i = size; i > 0; --i)
    out.push_back(static_cast<unsigned char>(n >> ((i - 1) * 8)));
}
void add(Bytes &out, std::span<const unsigned char> data) {
  out.insert(out.end(), data.begin(), data.end());
}
void label(Bytes &out, std::string_view text) {
  out.insert(out.end(), text.begin(), text.end());
  out.push_back(0);
}
struct Reader {
  std::span<const unsigned char> bytes;
  std::size_t pos = 0;
  std::span<const unsigned char> get(std::size_t size) {
    need(size <= bytes.size() - pos);
    auto out = bytes.subspan(pos, size);
    pos += size;
    return out;
  }
  std::uint64_t number(unsigned size) {
    std::uint64_t n = 0;
    for (auto b : get(size))
      n = (n << 8) | b;
    return n;
  }
  Hash digest() {
    Hash h{};
    auto raw = get(32);
    std::copy(raw.begin(), raw.end(), h.begin());
    return h;
  }
  void expect(std::string_view text) {
    Bytes expected;
    label(expected, text);
    auto got = get(expected.size());
    need(std::equal(got.begin(), got.end(), expected.begin()));
  }
  void end() { need(pos == bytes.size()); }
};
void write_accounts(Bytes &out, const Accounts &accounts) {
  need(accounts.size() <= max_accounts);
  put(out, accounts.size(), 4);
  for (const auto &[owner, account] : accounts) {
    need(owner == account.owner);
    add(out, owner);
    put(out, account.balance, 8);
    put(out, account.nonce, 8);
  }
}
Accounts read_accounts(Reader &r) {
  const auto count = r.number(4);
  need(count <= max_accounts);
  Accounts out;
  std::optional<Hash> previous;
  std::uint64_t total = 0;
  for (std::uint64_t i = 0; i < count; ++i) {
    auto owner = r.digest();
    Account a{owner, r.number(8), r.number(8)};
    need(!previous || *previous < owner);
    previous = owner;
    need(total <= std::numeric_limits<std::uint64_t>::max() - a.balance);
    total += a.balance;
    out.emplace(owner, a);
  }
  return out;
}
struct Descriptor {
  int fd;
  ~Descriptor() {
    if (fd >= 0)
      ::close(fd);
  }
  Descriptor(const Descriptor &) = delete;
  Descriptor &operator=(const Descriptor &) = delete;
  explicit Descriptor(int value) : fd(value) { need(fd >= 0); }
};
} // namespace
Bytes encode_genesis(const AppGenesis &g) {
  need(g.chain.size() >= 12 && g.chain.size() <= 50 &&
       g.chain.starts_with("pqcoin-dev-"));
  for (const auto c : g.chain)
    need((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-');
  Bytes out;
  label(out, "pqcoin/app-genesis/v1");
  add(out, g.bootstrap);
  put(out, g.chain.size(), 4);
  out.insert(out.end(), g.chain.begin(), g.chain.end());
  write_accounts(out, g.accounts);
  return out;
}
AppGenesis decode_genesis(std::span<const unsigned char> bytes) {
  need(bytes.size() <= max_file);
  Reader r{bytes};
  r.expect("pqcoin/app-genesis/v1");
  AppGenesis out;
  out.bootstrap = r.digest();
  const auto size = r.number(4);
  need(size <= 50);
  auto chain = r.get(static_cast<std::size_t>(size));
  out.chain.assign(chain.begin(), chain.end());
  out.accounts = read_accounts(r);
  r.end();
  need(encode_genesis(out) == Bytes(bytes.begin(), bytes.end()));
  for (const auto &[_, a] : out.accounts)
    need(a.nonce == 0);
  return out;
}
Bytes encode_transfer(const Transfer &tx) {
  need(tx.public_key.size() == 1952 && tx.signature.size() == 3309);
  auto out = canonical_body(tx);
  add(out, tx.message_commitment);
  add(out, tx.public_key);
  add(out, tx.signature);
  need(out.size() == tx_size);
  return out;
}
Transfer decode_transfer(std::span<const unsigned char> bytes) {
  need(bytes.size() == tx_size);
  Reader r{bytes};
  r.expect("pqcoin/transfer/v2");
  Transfer tx;
  tx.version = static_cast<std::uint32_t>(r.number(4));
  tx.network = static_cast<std::uint32_t>(r.number(4));
  tx.sender = r.digest();
  tx.recipient = r.digest();
  tx.amount = r.number(8);
  tx.fee = r.number(8);
  tx.nonce = r.number(8);
  tx.recipient_kem_key = r.digest();
  tx.chain_id = r.digest();
  tx.message_commitment = r.digest();
  auto key = r.get(1952);
  tx.public_key.assign(key.begin(), key.end());
  auto sig = r.get(3309);
  tx.signature.assign(sig.begin(), sig.end());
  r.end();
  return tx;
}
Application::Application(const std::filesystem::path &directory,
                         std::span<const unsigned char> genesis)
    : directory_(directory) {
  const auto config = decode_genesis(genesis);
  context_ = hash(genesis);
  std::filesystem::create_directories(directory_);
  const auto path = directory_ / "state.bin";
  if (std::filesystem::exists(path)) {
    need(std::filesystem::file_size(path) <= max_file);
    std::ifstream in(path, std::ios::binary);
    need(bool(in));
    Bytes bytes((std::istreambuf_iterator<char>(in)), {});
    need(!in.bad());
    committed_ = decode_state(bytes);
  } else {
    committed_.accounts = config.accounts;
    persist(committed_);
  }
  reset_trial();
}
Bytes Application::encode_state(const Snapshot &snapshot) const {
  Bytes out;
  label(out, "pqcoin/app-state/v1");
  add(out, context_);
  put(out, snapshot.height, 8);
  write_accounts(out, snapshot.accounts);
  return out;
}
Snapshot Application::decode_state(std::span<const unsigned char> bytes) const {
  need(bytes.size() >= 32);
  const auto body = bytes.first(bytes.size() - 32);
  const auto sum = hash(body);
  need(std::equal(sum.begin(), sum.end(), bytes.end() - 32));
  Reader r{body};
  r.expect("pqcoin/app-state/v1");
  need(r.digest() == context_);
  Snapshot out;
  out.height = r.number(8);
  need(out.height <=
       static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()));
  out.accounts = read_accounts(r);
  r.end();
  return out;
}
Hash Application::app_hash() const { return hash(encode_state(committed_)); }
bool Application::execute(Accounts &accounts,
                          std::span<const unsigned char> bytes) const {
  if (bytes.size() != tx_size)
    return false;
  Transfer tx;
  try {
    tx = decode_transfer(bytes);
  } catch (const std::runtime_error &) {
    return false;
  }
  if (tx.chain_id != context_)
    return false;
  const auto source = accounts.find(tx.sender),
             dest = accounts.find(tx.recipient);
  if (source == accounts.end() ||
      (dest == accounts.end() && accounts.size() >= max_accounts))
    return false;
  auto from = source->second;
  auto to = dest == accounts.end() ? Account{tx.recipient, 0, 0} : dest->second;
  if (!apply(tx, from, to))
    return false;
  accounts[tx.sender] = from;
  accounts[tx.recipient] = to;
  return true;
}
bool Application::check(std::span<const unsigned char> tx) const {
  auto copy = committed_.accounts;
  return execute(copy, tx);
}
void Application::reset_trial() { trial_ = committed_.accounts; }
bool Application::apply_trial(std::span<const unsigned char> tx) {
  return execute(trial_, tx);
}
Hash Application::stage(std::uint64_t height) {
  need(height == committed_.height + 1 &&
       height <= static_cast<std::uint64_t>(
                     std::numeric_limits<std::int64_t>::max()));
  staged_ = Snapshot{height, trial_};
  return hash(encode_state(*staged_));
}
void Application::persist(const Snapshot &snapshot) const {
  auto bytes = encode_state(snapshot);
  const auto checksum = hash(bytes);
  add(bytes, checksum);
  // Node process holds an exclusive home lock. A stale temporary snapshot can
  // be replaced safely; the committed path is changed only by an atomic rename.
  const auto temporary = directory_ / "state.tmp";
  const auto destination = directory_ / "state.bin";
  Descriptor file(::open(temporary.c_str(),
                         O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC,
                         0600));
  std::size_t offset = 0;
  while (offset < bytes.size()) {
    const auto count =
        ::write(file.fd, bytes.data() + offset, bytes.size() - offset);
    if (count < 0 && errno == EINTR)
      continue;
    need(count > 0);
    offset += static_cast<std::size_t>(count);
  }
  need(::fsync(file.fd) == 0);
  need(::rename(temporary.c_str(), destination.c_str()) == 0);
  Descriptor directory(
      ::open(directory_.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC));
  need(::fsync(directory.fd) == 0);
}
void Application::commit() {
  need(staged_.has_value());
  persist(*staged_);
  committed_ = std::move(*staged_);
  staged_.reset();
  reset_trial();
}
} // namespace pqcoin
