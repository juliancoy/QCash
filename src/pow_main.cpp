#include <algorithm>
#include <arpa/inet.h>
#include <atomic>
#include <charconv>
#include <chrono>
#include <csignal>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <limits>
#include <mutex>
#include <netdb.h>
#include <openssl/pem.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>
#include <openssl/x509v3.h>
#include <poll.h>
#include <pqcoin/genesis.hpp>
#include <pqcoin/pow.hpp>
#include <stdexcept>
#include <sys/file.h>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>

using namespace pqcoin;
namespace fs = std::filesystem;
namespace p = pqcoin::pow;
namespace {
constexpr std::size_t maximum_frame = 12 * 1024 * 1024;
std::atomic<bool> stopping{false};
static_assert(std::atomic<bool>::is_always_lock_free);
void stop(int) { stopping.store(true); }
void need(bool ok, const char *message = "operation rejected") {
  if (!ok)
    throw std::runtime_error(message);
}
struct FD {
  int value;
  explicit FD(int v) : value(v) { need(v >= 0, "file/socket open failed"); }
  ~FD() {
    if (value >= 0)
      ::close(value);
  }
  FD(const FD &) = delete;
  FD &operator=(const FD &) = delete;
  FD(FD &&other) noexcept : value(other.value) { other.value = -1; }
};
std::uint64_t now() {
  auto t = std::chrono::duration_cast<std::chrono::seconds>(
               std::chrono::system_clock::now().time_since_epoch())
               .count();
  need(t > 0, "invalid system clock");
  return static_cast<std::uint64_t>(t);
}
Bytes read(const fs::path &path, std::size_t limit = 1024 * 1024) {
  need(fs::file_size(path) <= limit, "file exceeds size limit");
  std::ifstream in(path, std::ios::binary);
  need(bool(in), "cannot read file");
  Bytes out((std::istreambuf_iterator<char>(in)), {});
  need(!in.bad());
  return out;
}
void write_all(int fd, std::span<const unsigned char> bytes) {
  std::size_t pos = 0;
  while (pos < bytes.size()) {
    auto n = ::write(fd, bytes.data() + pos, bytes.size() - pos);
    if (n < 0 && errno == EINTR)
      continue;
    need(n > 0, "write failed");
    pos += static_cast<std::size_t>(n);
  }
}
void flush_directory(const fs::path &path) {
  const auto directory = path.empty() ? fs::path(".") : path;
  FD fd(::open(directory.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC));
  need(::fsync(fd.value) == 0, "directory fsync failed");
}
void create(const fs::path &path, std::span<const unsigned char> bytes) {
  FD file(::open(path.c_str(),
                 O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600));
  write_all(file.value, bytes);
  need(::fsync(file.value) == 0);
  flush_directory(path.parent_path());
}
void put(Bytes &out, std::uint64_t n, unsigned width) {
  for (unsigned i = width; i > 0; --i)
    out.push_back(static_cast<unsigned char>(n >> ((i - 1) * 8)));
}
void add(Bytes &out, std::span<const unsigned char> bytes) {
  out.insert(out.end(), bytes.begin(), bytes.end());
}
struct Reader {
  std::span<const unsigned char> bytes;
  std::size_t offset = 0;
  auto get(std::size_t n) {
    need(n <= bytes.size() - offset, "truncated peer frame");
    auto value = bytes.subspan(offset, n);
    offset += n;
    return value;
  }
  std::uint64_t integer(unsigned size) {
    std::uint64_t n = 0;
    for (auto b : get(size))
      n = (n << 8) | b;
    return n;
  }
  Hash digest() {
    Hash h{};
    auto in = get(32);
    std::copy(in.begin(), in.end(), h.begin());
    return h;
  }
  void end() { need(offset == bytes.size(), "trailing peer data"); }
};
std::uint64_t integer_argument(std::string_view text) {
  std::uint64_t value = 0;
  const auto result =
      std::from_chars(text.data(), text.data() + text.size(), value);
  need(result.ec == std::errc{} && result.ptr == text.data() + text.size(),
       "expected a whole unsigned integer");
  return value;
}
Hash unhex(std::string_view value) {
  need(value.size() == 64, "address must contain 64 hexadecimal characters");
  Hash out{};
  auto digit = [](char c) {
    if (c >= '0' && c <= '9')
      return c - '0';
    if (c >= 'a' && c <= 'f')
      return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
      return c - 'A' + 10;
    return -1;
  };
  for (std::size_t i = 0; i < 32; ++i) {
    int a = digit(value[i * 2]), b = digit(value[i * 2 + 1]);
    need(a >= 0 && b >= 0);
    out[i] = static_cast<unsigned char>((a << 4) | b);
  }
  return out;
}
Key load_key(const fs::path &path) {
  FILE *f = ::fopen(path.c_str(), "r");
  need(f, "cannot open private key");
  Key key(PEM_read_PrivateKey(f, nullptr, nullptr, nullptr), EVP_PKEY_free);
  ::fclose(f);
  need(key && EVP_PKEY_is_a(key.get(), "ML-DSA-65"),
       "expected ML-DSA-65 private key");
  return key;
}
void save_key(const fs::path &path, const Key &key) {
  FD fd(::open(path.c_str(),
               O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600));
  std::unique_ptr<BIO, decltype(&BIO_free)> bio(
      BIO_new_fd(fd.value, BIO_NOCLOSE), BIO_free);
  need(bio && PEM_write_bio_PrivateKey(bio.get(), key.get(), nullptr, nullptr,
                                       0, nullptr, nullptr) == 1);
  need(::fsync(fd.value) == 0);
  flush_directory(path.parent_path());
}
struct Store {
  fs::path home;
  FD lock;
  p::Chain chain;
  FD journal;
  bool storage_failed = false;
  std::map<Hash, Bytes> pool;
  std::map<Hash, std::pair<std::uint64_t, Bytes>> active_transactions;
  std::uint64_t pool_revision = 0;
  void update_transactions() {
    std::vector<Bytes> detached;
    for (const auto &h : chain.detached())
      for (const auto &raw : chain.at(h).block.transfers) {
        active_transactions.erase(hash(raw));
        detached.push_back(raw);
      }
    for (const auto &h : chain.attached())
      for (const auto &raw : chain.at(h).block.transfers) {
        active_transactions[hash(raw)] = {chain.at(h).block.height, raw};
        pool.erase(hash(raw));
      }
    for (auto it = pool.begin(); it != pool.end();) {
      try {
        (void)chain.candidate(chain.tip(), {}, now(), {it->second});
        ++it;
      } catch (const std::runtime_error &) {
        it = pool.erase(it);
      }
    }
    for (const auto &raw : detached) {
      try {
        (void)enqueue(raw);
      } catch (const std::runtime_error &) {
      }
    }
    ++pool_revision;
  }
  bool enqueue(const Bytes &raw) {
    need(raw.size() == 5472, "wrong transaction size");
    const auto txid = hash(raw);
    if (pool.contains(txid) || active_transactions.contains(txid))
      return false;
    need(pool.size() < 128, "mempool full");
    const auto tx = decode_transfer(raw);
    for (const auto &[_, pending] : pool)
      need(decode_transfer(pending).sender != tx.sender,
           "sender already has a pending transfer");
    (void)chain.candidate(chain.tip(), {}, now(), {raw});
    pool.emplace(txid, raw);
    ++pool_revision;
    return true;
  }
  std::vector<Bytes> pending() const {
    std::vector<Bytes> out;
    for (const auto &[_, raw] : pool)
      out.push_back(raw);
    return out;
  }
  explicit Store(const fs::path &path)
      : home(path), lock(::open((path / "lock").c_str(),
                                O_RDWR | O_CREAT | O_CLOEXEC, 0600)),
        chain(p::decode_config(read(path / "chain.bin"))),
        journal(::open((path / "blocks.log").c_str(),
                       O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600)) {
    need(::flock(lock.value, LOCK_EX | LOCK_NB) == 0,
         "node home already in use");
    auto bytes = read(path / "blocks.log", 512 * 1024 * 1024);
    std::size_t pos = 0;
    while (pos < bytes.size()) {
      if (bytes.size() - pos < 4)
        break;
      Reader r{std::span(bytes).subspan(pos)};
      auto size = r.integer(4);
      need(size <= 1024 * 1024, "corrupt journal length");
      if (bytes.size() - pos < 4 + size + 32)
        break;
      auto payload = r.get(static_cast<std::size_t>(size));
      const auto checksum = r.digest();
      need(hash(payload) == checksum, "corrupt journal checksum");
      auto block = p::decode_block(payload);
      need(chain.accept(block, now()), "invalid stored block");
      update_transactions();
      pos += 4 + static_cast<std::size_t>(size) + 32;
    }
    // A crash may leave only the final append incomplete. Corrupt complete
    // records fail closed.
    if (pos < bytes.size()) {
      need(::ftruncate(journal.value, static_cast<off_t>(pos)) == 0);
      need(::fsync(journal.value) == 0);
    }
    need(::lseek(journal.value, 0, SEEK_END) >= 0);
    flush_directory(home);
    // Replaying historical reorgs rebuilds the active index, not a durable
    // mempool.
    pool.clear();
    ++pool_revision;
  }
  bool accept(const p::Block &block) {
    need(!storage_failed, "fatal journal failure");
    if (!chain.accept(block, now()))
      return false;
    try {
      auto raw = p::encode_block(block);
      Bytes record;
      put(record, raw.size(), 4);
      add(record, raw);
      add(record, hash(raw));
      write_all(journal.value, record);
      need(::fsync(journal.value) == 0, "journal fsync failed");
    } catch (...) {
      storage_failed = true;
      throw;
    }
    update_transactions();
    return true;
  }
};
std::string status(const Store &s) {
  const auto &best = s.chain.best();
  return "{\"chain\":\"" + devnet::hex(s.chain.context()) +
         "\",\"height\":" + std::to_string(best.block.height) + ",\"tip\":\"" +
         devnet::hex(s.chain.tip()) + "\",\"state\":\"" +
         devnet::hex(p::state_hash(best.state)) + "\",\"work\":\"" +
         p::work_decimal(best.work) +
         "\",\"transport\":\"TLS1.3/X25519MLKEM768/ML-DSA-65\"}";
}
std::string transaction_status(const Store &s, const Hash &txid) {
  auto found = s.active_transactions.find(txid);
  const bool confirmed = found != s.active_transactions.end();
  const auto depth =
      confirmed ? s.chain.best().block.height - found->second.first + 1 : 0;
  return "{\"id\":\"" + devnet::hex(txid) + "\",\"status\":\"" +
         (confirmed               ? "confirmed"
          : s.pool.contains(txid) ? "pending"
                                  : "unknown") +
         "\",\"confirmations\":" + std::to_string(depth) + "}";
}
std::string wallet_account(const Store &s, const Hash &owner) {
  const auto &state = s.chain.best().state;
  std::uint64_t balance = 0, nonce = 0, immature = 0;
  if (auto it = state.accounts.find(owner); it != state.accounts.end()) {
    balance = it->second.balance;
    nonce = it->second.nonce;
  }
  for (const auto &r : state.immature)
    if (r.owner == owner) {
      need(immature <= std::numeric_limits<std::uint64_t>::max() - r.amount);
      immature += r.amount;
    }
  std::string out = "{\"address\":\"" + devnet::hex(owner) +
                    "\",\"spendable\":\"" + std::to_string(balance) +
                    "\",\"immature\":\"" + std::to_string(immature) +
                    "\",\"nonce\":\"" + std::to_string(nonce) +
                    "\",\"transactions\":[";
  std::vector<std::pair<std::uint64_t, Bytes>> records;
  for (const auto &[_, record] : s.active_transactions) {
    const auto tx = decode_transfer(record.second);
    if (tx.sender == owner || tx.recipient == owner)
      records.push_back(record);
  }
  std::sort(records.begin(), records.end(),
            [](const auto &a, const auto &b) { return a.first > b.first; });
  bool first = true;
  auto append = [&](const Bytes &raw) {
    const auto tx = decode_transfer(raw);
    if (!first)
      out += ",";
    first = false;
    auto item = transaction_status(s, hash(raw));
    item.pop_back();
    out += item + ",\"sender\":\"" + devnet::hex(tx.sender) +
           "\",\"recipient\":\"" + devnet::hex(tx.recipient) +
           "\",\"amount\":\"" + std::to_string(tx.amount) + "\",\"fee\":\"" +
           std::to_string(tx.fee) + "\"}";
  };
  for (const auto &[_, raw] : s.pool) {
    auto tx = decode_transfer(raw);
    if (tx.sender == owner || tx.recipient == owner)
      append(raw);
  }
  for (std::size_t i = 0; i < std::min<std::size_t>(records.size(), 50); ++i)
    append(records[i].second);
  return out + "]}";
}
int verify_peer(int, X509_STORE_CTX *store) {
  if (X509_STORE_CTX_get_error_depth(store) != 0)
    return 0;
  X509 *certificate = X509_STORE_CTX_get_current_cert(store);
  if (!certificate)
    return 0;
  Key key(X509_get_pubkey(certificate), EVP_PKEY_free);
  return key && EVP_PKEY_is_a(key.get(), "ML-DSA-65") &&
         X509_check_issued(certificate, certificate) == X509_V_OK &&
         X509_verify(certificate, key.get()) == 1 &&
         X509_cmp_current_time(X509_get0_notBefore(certificate)) < 0 &&
         X509_cmp_current_time(X509_get0_notAfter(certificate)) > 0;
}
using Context = std::unique_ptr<SSL_CTX, decltype(&SSL_CTX_free)>;
Context tls_context(const Key &key) {
  Context context(SSL_CTX_new(TLS_method()), SSL_CTX_free);
  need(bool(context));
  need(SSL_CTX_set_min_proto_version(context.get(), TLS1_3_VERSION) == 1 &&
       SSL_CTX_set_max_proto_version(context.get(), TLS1_3_VERSION) == 1);
  need(SSL_CTX_set1_groups_list(context.get(), "X25519MLKEM768") == 1 &&
       SSL_CTX_set1_sigalgs_list(context.get(), "mldsa65") == 1);
  need(SSL_CTX_set_ciphersuites(context.get(), "TLS_AES_256_GCM_SHA384") == 1);
  SSL_CTX_set_options(context.get(), SSL_OP_NO_TICKET);
  SSL_CTX_set_session_cache_mode(context.get(), SSL_SESS_CACHE_OFF);
  std::unique_ptr<X509, decltype(&X509_free)> certificate(X509_new(),
                                                          X509_free);
  need(bool(certificate));
  need(X509_set_version(certificate.get(), 2) == 1);
  need(ASN1_INTEGER_set(X509_get_serialNumber(certificate.get()), 1) == 1);
  need(X509_gmtime_adj(X509_getm_notBefore(certificate.get()), -60) &&
       X509_gmtime_adj(X509_getm_notAfter(certificate.get()), 86400));
  need(X509_set_pubkey(certificate.get(), key.get()) == 1);
  auto *name = X509_get_subject_name(certificate.get());
  need(X509_NAME_add_entry_by_txt(
           name, "CN", MBSTRING_ASC,
           reinterpret_cast<const unsigned char *>("pqcoin-peer"), -1, -1,
           0) == 1);
  need(X509_set_issuer_name(certificate.get(), name) == 1);
  need(X509_sign(certificate.get(), key.get(), nullptr) > 0,
       "ML-DSA certificate signing failed");
  need(SSL_CTX_use_certificate(context.get(), certificate.get()) == 1 &&
       SSL_CTX_use_PrivateKey(context.get(), key.get()) == 1 &&
       SSL_CTX_check_private_key(context.get()) == 1);
  SSL_CTX_set_verify(context.get(),
                     SSL_VERIFY_PEER | SSL_VERIFY_FAIL_IF_NO_PEER_CERT,
                     verify_peer);
  return context;
}
using Session = std::unique_ptr<SSL, decltype(&SSL_free)>;
void wait_tls(SSL *ssl, int result,
              std::chrono::steady_clock::time_point deadline) {
  const auto error = SSL_get_error(ssl, result);
  need(error == SSL_ERROR_WANT_READ || error == SSL_ERROR_WANT_WRITE,
       "TLS operation rejected");
  const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
                             deadline - std::chrono::steady_clock::now())
                             .count();
  need(remaining > 0, "TLS deadline exceeded");
  pollfd fd{SSL_get_fd(ssl),
            static_cast<short>(error == SSL_ERROR_WANT_READ ? POLLIN : POLLOUT),
            0};
  const auto result_poll = ::poll(&fd, 1, static_cast<int>(remaining));
  need(result_poll > 0, "TLS deadline exceeded");
}
void handshake(SSL *ssl, bool outgoing) {
  const auto fd = SSL_get_fd(ssl);
  const int flags = fcntl(fd, F_GETFL, 0);
  need(flags >= 0 && fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0);
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(3);
  for (;;) {
    const auto result = outgoing ? SSL_connect(ssl) : SSL_accept(ssl);
    if (result == 1)
      break;
    wait_tls(ssl, result, deadline);
  }
  const auto group = SSL_get0_group_name(ssl);
  need(group && std::string_view(group) == "X25519MLKEM768" &&
           SSL_version(ssl) == TLS1_3_VERSION,
       "hybrid TLS required");
}
void transfer(SSL *ssl, unsigned char *data, std::size_t size, bool writing) {
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(3);
  std::size_t done = 0;
  while (done < size) {
    need(std::chrono::steady_clock::now() < deadline,
         "TLS frame deadline exceeded");
    std::size_t count = 0;
    const int result = writing
                           ? SSL_write_ex(ssl, data + done, size - done, &count)
                           : SSL_read_ex(ssl, data + done, size - done, &count);
    if (result == 1) {
      need(count > 0);
      done += count;
    } else
      wait_tls(ssl, result, deadline);
  }
}
void send(SSL *ssl, const Bytes &payload) {
  need(payload.size() <= maximum_frame);
  Bytes length;
  put(length, payload.size(), 4);
  transfer(ssl, length.data(), 4, true);
  if (!payload.empty())
    transfer(ssl, const_cast<unsigned char *>(payload.data()), payload.size(),
             true);
}
Bytes receive(SSL *ssl) {
  Bytes prefix(4);
  transfer(ssl, prefix.data(), 4, false);
  Reader r{prefix};
  auto size = r.integer(4);
  need(size <= maximum_frame, "oversized peer frame");
  Bytes out(static_cast<std::size_t>(size));
  if (size)
    transfer(ssl, out.data(), out.size(), false);
  return out;
}
struct Endpoint {
  std::string host, port;
};
Endpoint endpoint(std::string_view value) {
  auto split = value.rfind(':');
  need(split != std::string_view::npos && split > 0 && split + 1 < value.size(),
       "expected HOST:PORT");
  Endpoint out{std::string(value.substr(0, split)),
               std::string(value.substr(split + 1))};
  need(out.host.size() <= 253 && out.port.size() <= 5);
  for (char c : out.port)
    need(c >= '0' && c <= '9');
  auto port = std::stoul(out.port);
  need(port > 0 && port <= 65535);
  return out;
}
FD socket_for(std::string_view value, bool listening) {
  const auto ep = endpoint(value);
  addrinfo hint{};
  hint.ai_family = AF_INET;
  hint.ai_socktype = SOCK_STREAM;
  hint.ai_flags = AI_NUMERICHOST;
  addrinfo *raw = nullptr;
  need(getaddrinfo(ep.host.c_str(), ep.port.c_str(), &hint, &raw) == 0,
       "address lookup failed");
  std::unique_ptr<addrinfo, decltype(&freeaddrinfo)> addresses(raw,
                                                               freeaddrinfo);
  FD fd(::socket(raw->ai_family, SOCK_STREAM | SOCK_CLOEXEC, 0));
  timeval timeout{2, 0};
  need(setsockopt(fd.value, SOL_SOCKET, SO_RCVTIMEO, &timeout,
                  sizeof(timeout)) == 0 &&
       setsockopt(fd.value, SOL_SOCKET, SO_SNDTIMEO, &timeout,
                  sizeof(timeout)) == 0);
  if (listening) {
    int reuse = 1;
    need(setsockopt(fd.value, SOL_SOCKET, SO_REUSEADDR, &reuse,
                    sizeof(reuse)) == 0);
    need(::bind(fd.value, raw->ai_addr, raw->ai_addrlen) == 0, "bind failed");
    need(::listen(fd.value, 16) == 0);
    need(fcntl(fd.value, F_SETFL, O_NONBLOCK) == 0);
  } else {
    const auto flags = fcntl(fd.value, F_GETFL, 0);
    need(flags >= 0 && fcntl(fd.value, F_SETFL, flags | O_NONBLOCK) == 0);
    int result = ::connect(fd.value, raw->ai_addr, raw->ai_addrlen);
    if (result < 0) {
      need(errno == EINPROGRESS, "connect failed");
      pollfd waiting{fd.value, POLLOUT, 0};
      need(::poll(&waiting, 1, 1500) > 0, "connect timeout");
      int error = 0;
      socklen_t length = sizeof(error);
      need(getsockopt(fd.value, SOL_SOCKET, SO_ERROR, &error, &length) == 0 &&
               error == 0,
           "connect failed");
    }
    need(fcntl(fd.value, F_SETFL, flags) == 0);
  }
  return fd;
}
Hash peer_fingerprint(SSL *ssl) {
  std::unique_ptr<X509, decltype(&X509_free)> cert(
      SSL_get1_peer_certificate(ssl), X509_free);
  need(bool(cert));
  Key key(X509_get_pubkey(cert.get()), EVP_PKEY_free);
  return hash(public_key(key));
}
void pin(SSL *ssl, const fs::path &home, std::string_view remote) {
  const auto directory = home / "peer-pins";
  fs::create_directories(directory);
  flush_directory(directory.parent_path());
  Bytes name(remote.begin(), remote.end());
  const auto path = directory / devnet::hex(hash(name));
  const auto fingerprint = peer_fingerprint(ssl);
  if (fs::exists(path))
    need(read(path, 32) == Bytes(fingerprint.begin(), fingerprint.end()),
         "peer identity changed; pin mismatch");
  else
    create(path, fingerprint);
}
Session client(SSL_CTX *ctx, int fd, const fs::path &home,
               std::string_view remote) {
  Session ssl(SSL_new(ctx), SSL_free);
  need(ssl && SSL_set_fd(ssl.get(), fd) == 1);
  handshake(ssl.get(), true);
  pin(ssl.get(), home, remote);
  return ssl;
}
Bytes request(SSL_CTX *ctx, const fs::path &home, std::string_view remote,
              const Bytes &payload) {
  auto fd = socket_for(remote, false);
  auto ssl = client(ctx, fd.value, home, remote);
  send(ssl.get(), payload);
  auto response = receive(ssl.get());
  need(!response.empty() && response[0] == 0, "peer rejected request");
  response.erase(response.begin());
  return response;
}
Bytes sync_request(const p::Chain &chain, const Hash &progress) {
  Bytes out{1};
  add(out, chain.context());
  auto locators = chain.locator();
  if (progress != Hash{} &&
      std::find(locators.begin(), locators.end(), progress) == locators.end())
    locators.insert(locators.begin(), progress);
  if (locators.size() > 64)
    locators.erase(locators.end() - 2);
  put(out, locators.size(), 2);
  for (const auto &h : locators)
    add(out, h);
  return out;
}
void sync(Store &store, SSL_CTX *ctx, std::string_view remote,
          std::mutex &mutex, Hash &progress) {
  Bytes message;
  {
    std::lock_guard guard(mutex);
    need(!store.storage_failed, "fatal journal failure");
    message = sync_request(store.chain, progress);
  }
  auto response = request(ctx, store.home, remote, message);
  Reader r{response};
  const auto count = r.integer(2);
  need(count <= 16);
  for (std::uint64_t i = 0; i < count; ++i) {
    auto size = r.integer(4);
    need(size <= 1024 * 1024);
    auto block = p::decode_block(r.get(static_cast<std::size_t>(size)));
    std::lock_guard guard(mutex);
    if (!store.accept(block))
      need(store.chain.at(p::id(block)).block.height == block.height,
           "invalid peer block");
    progress = p::id(block);
  }
  r.end();
  Bytes query{7};
  {
    std::lock_guard guard(mutex);
    add(query, store.chain.context());
  }
  auto pending = request(ctx, store.home, remote, query);
  Reader reader{pending};
  const auto size = reader.integer(2);
  need(size <= 128);
  for (std::uint64_t i = 0; i < size; ++i) {
    auto raw = reader.get(5472);
    std::lock_guard guard(mutex);
    try {
      (void)store.enqueue(Bytes(raw.begin(), raw.end()));
    } catch (const std::runtime_error &) {
    }
  }
  reader.end();
}
Bytes answer(Store &store, const Bytes &message) {
  need(!store.storage_failed, "fatal journal failure");
  Reader r{message};
  const auto command = r.integer(1);
  need(r.digest() == store.chain.context(), "wrong chain");
  Bytes out{0};
  if (command == 1) {
    const auto count = r.integer(2);
    need(count > 0 && count <= 64);
    std::vector<Hash> locators;
    for (std::uint64_t i = 0; i < count; ++i)
      locators.push_back(r.digest());
    r.end();
    const auto blocks = store.chain.after(locators);
    put(out, blocks.size(), 2);
    for (const auto &b : blocks) {
      auto raw = p::encode_block(b);
      put(out, raw.size(), 4);
      add(out, raw);
    }
  } else if (command == 2) {
    auto raw = r.get(message.size() - r.offset);
    auto block = p::decode_block(raw);
    need(store.accept(block), "invalid or duplicate block");
  } else if (command == 4) {
    auto raw = r.get(5472);
    r.end();
    (void)store.enqueue(Bytes(raw.begin(), raw.end()));
    const auto text = transaction_status(store, hash(raw));
    out.insert(out.end(), text.begin(), text.end());
  } else if (command == 5 || command == 6) {
    const auto value = r.digest();
    r.end();
    const auto text = command == 5 ? wallet_account(store, value)
                                   : transaction_status(store, value);
    out.insert(out.end(), text.begin(), text.end());
  } else if (command == 7) {
    r.end();
    put(out, store.pool.size(), 2);
    for (const auto &[_, raw] : store.pool)
      add(out, raw);
  } else if (command == 3) {
    r.end();
    const auto text = status(store);
    out.insert(out.end(), text.begin(), text.end());
  } else
    throw std::runtime_error("unknown peer command");
  return out;
}
void node(Store &store, std::string_view address,
          const std::vector<std::string> &peers,
          const std::optional<Hash> &miner, std::uint64_t mine_interval) {
  auto key = load_key(store.home / "peer-key.pem");
  auto ctx = tls_context(key);
  auto listener = socket_for(address, true);
  std::signal(SIGINT, stop);
  std::signal(SIGTERM, stop);
  std::signal(SIGPIPE, SIG_IGN);
  std::cout << "PoW peer " << address
            << " key=" << devnet::hex(hash(public_key(key)))
            << " (hybrid TLS, open membership)" << std::endl;
  std::mutex mutex;
  std::map<std::string, Hash> sync_progress;
  std::atomic<bool> server_failed{false};
  std::jthread server([&](std::stop_token token) {
    while (!stopping && !token.stop_requested()) {
      pollfd waiting{listener.value, POLLIN, 0};
      const auto ready = ::poll(&waiting, 1, 100);
      if (ready < 0 && errno == EINTR)
        continue;
      if (ready < 0) {
        server_failed = true;
        return;
      }
      if (ready == 0)
        continue;
      const auto incoming =
          ::accept4(listener.value, nullptr, nullptr, SOCK_CLOEXEC);
      if (incoming < 0)
        continue;
      FD fd(incoming);
      try {
        Session ssl(SSL_new(ctx.get()), SSL_free);
        need(ssl && SSL_set_fd(ssl.get(), fd.value) == 1);
        handshake(ssl.get(), false);
        auto message = receive(ssl.get());
        Bytes response;
        {
          std::lock_guard guard(mutex);
          try {
            response = answer(store, message);
          } catch (const std::runtime_error &) {
            response = {1};
          }
        }
        send(ssl.get(), response);
      } catch (const std::exception &e) {
        std::cerr << "Peer connection rejected: " << e.what() << '\n';
      }
    }
  });
  auto next_sync = std::chrono::steady_clock::now();
  p::Block candidate;
  std::uint64_t revision = 0;
  {
    std::lock_guard guard(mutex);
    candidate =
        store.chain.candidate(store.chain.tip(), miner.value_or(Hash{}), now());
    revision = store.pool_revision;
  }
  auto next_mine = std::chrono::steady_clock::now();
  while (!stopping) {
    need(!server_failed, "peer listener failed");
    if (std::chrono::steady_clock::now() >= next_sync) {
      for (const auto &peer : peers) {
        try {
          sync(store, ctx.get(), peer, mutex, sync_progress[peer]);
        } catch (const std::exception &e) {
          std::cerr << "Sync " << peer << ": " << e.what() << '\n';
        }
      }
      next_sync =
          std::chrono::steady_clock::now() + std::chrono::milliseconds(500);
    }
    {
      std::lock_guard guard(mutex);
      need(!store.storage_failed, "fatal journal failure");
      if (miner && std::chrono::steady_clock::now() >= next_mine) {
        if (candidate.parent != store.chain.tip() ||
            revision != store.pool_revision) {
          candidate = store.chain.candidate(store.chain.tip(), *miner, now(),
                                            store.pending());
          revision = store.pool_revision;
        }
        if (p::mine_batch(candidate, 2048)) {
          need(store.accept(candidate));
          std::cout << status(store) << std::endl;
          next_mine = std::chrono::steady_clock::now() +
                      std::chrono::seconds(std::max<std::uint64_t>(store.chain.config().spacing, mine_interval));
          candidate = store.chain.candidate(store.chain.tip(), *miner, now(),
                                            store.pending());
          revision = store.pool_revision;
        }
      }
    }
    if (!miner || std::chrono::steady_clock::now() < next_mine)
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }
}
} // namespace
int main(int argc, char **argv) {
  try {
    need(argc >= 3,
         "usage: qcash init HOME BOOTSTRAP [--experimental-retarget] | "
         "wallet KEYFILE | mine HOME "
         "ADDRESS COUNT [TXFILE] | status HOME | balance HOME ADDRESS | node "
         "HOME HOST:PORT [--peer HOST:PORT] [--mine-to ADDRESS] [--mine-interval SECONDS] | query HOME "
         "HOST:PORT | submit HOME HOST:PORT BLOCKFILE");
    const std::string command(argv[1]);
    if (command == "wallet") {
      need(argc == 3);
      auto key = signing_key();
      save_key(argv[2], key);
      std::cout << devnet::hex(address(public_key(key))) << '\n';
      return 0;
    }
    if (command == "identity") {
      need(argc == 3);
      auto key = load_key(fs::path(argv[2]) / "peer-key.pem");
      std::cout << devnet::hex(hash(public_key(key))) << '\n';
      return 0;
    }
    if (command == "init") {
      need(argc == 4 ||
           (argc == 5 && std::string(argv[4]) == "--experimental-retarget"));
      auto raw = read(argv[3]);
      const auto genesis = devnet::decode(raw);
      need(devnet::valid(genesis));
      need(fs::create_directory(argv[2]), "home already exists");
      fs::permissions(argv[2], fs::perms::owner_all);
      flush_directory(fs::path(argv[2]).parent_path());
      auto config =
          p::development_config(devnet::block_hash(genesis), genesis.timestamp);
      if (argc == 5)
        config.interval = 8;
      create(fs::path(argv[2]) / "chain.bin", p::encode_config(config));
      auto key = signing_key();
      save_key(fs::path(argv[2]) / "peer-key.pem", key);
      Store store(argv[2]);
      std::cout << status(store) << '\n';
      return 0;
    }
    if (command == "query" || command == "submit" ||
        command == "send-transfer" || command == "account-query" ||
        command == "transaction-query") {
      need(argc == (command == "query" ? 4 : 5));
      const fs::path home(argv[2]);
      auto config = p::decode_config(read(home / "chain.bin"));
      p::Chain chain(config);
      auto key = load_key(home / "peer-key.pem");
      auto ctx = tls_context(key);
      std::signal(SIGPIPE, SIG_IGN);
      const auto operation = command == "query"           ? 3
                             : command == "submit"        ? 2
                             : command == "send-transfer" ? 4
                             : command == "account-query" ? 5
                                                          : 6;
      Bytes message{static_cast<unsigned char>(operation)};
      add(message, chain.context());
      if (command == "submit" || command == "send-transfer")
        add(message,
            read(argv[4], command == "send-transfer" ? 5472 : 1024 * 1024));
      if (command == "account-query" || command == "transaction-query")
        add(message, unhex(argv[4]));
      auto result = request(ctx.get(), home, argv[3], message);
      std::cout << std::string(result.begin(), result.end()) << '\n';
      return 0;
    }
    if (command == "trust") {
      need(argc == 5,
           "usage: qcash trust HOME HOST:PORT PEER_FINGERPRINT");
      (void)endpoint(argv[3]);
      const auto fingerprint = unhex(argv[4]);
      const auto directory = fs::path(argv[2]) / "peer-pins";
      fs::create_directories(directory);
      flush_directory(directory.parent_path());
      const std::string remote(argv[3]);
      Bytes name(remote.begin(), remote.end());
      const auto path = directory / devnet::hex(hash(name));
      if (fs::exists(path))
        need(read(path, 32) == Bytes(fingerprint.begin(), fingerprint.end()),
             "existing peer pin differs");
      else
        create(path, fingerprint);
      std::cout << "Peer identity pinned\n";
      return 0;
    }
    Store store(argv[2]);
    if (command == "status") {
      need(argc == 3);
      std::cout << status(store) << '\n';
    } else if (command == "balance") {
      need(argc == 4);
      const auto owner = unhex(argv[3]);
      const auto &state = store.chain.best().state;
      std::uint64_t mature = 0, immature = 0, nonce = 0;
      if (auto it = state.accounts.find(owner); it != state.accounts.end()) {
        mature = it->second.balance;
        nonce = it->second.nonce;
      }
      for (const auto &r : state.immature)
        if (r.owner == owner) {
          need(immature <=
               std::numeric_limits<std::uint64_t>::max() - r.amount);
          immature += r.amount;
        }
      std::cout << "{\"spendable\":" << mature << ",\"immature\":" << immature
                << ",\"nonce\":" << nonce << "}\n";
    } else if (command == "mine") {
      need(argc == 5 || argc == 6);
      const auto miner = unhex(argv[3]);
      const auto count = integer_argument(argv[4]);
      need(count > 0 && count <= 1000);
      std::vector<Bytes> txs;
      if (argc == 6)
        txs.push_back(read(argv[5], 5472));
      for (std::uint64_t i = 0; i < count && !stopping; ++i) {
        auto block = store.chain.candidate(store.chain.tip(), miner, now(),
                                           i == 0 ? txs : std::vector<Bytes>{});
        while (!p::mine_batch(block, 10000)) {
        }
        need(store.accept(block));
        std::cout << status(store) << '\n';
      }
    } else if (command == "node") {
      need(argc >= 4);
      std::vector<std::string> peers;
      std::optional<Hash> miner;
      std::uint64_t mine_interval = 0;
      for (int i = 4; i < argc; i += 2) {
        need(i + 1 < argc);
        if (std::string_view(argv[i]) == "--peer") {
          need(peers.size() < 32);
          peers.emplace_back(argv[i + 1]);
        } else if (std::string_view(argv[i]) == "--mine-to")
          miner = unhex(argv[i + 1]);
        else if (std::string_view(argv[i]) == "--mine-interval") {
          mine_interval = integer_argument(argv[i + 1]);
          need(mine_interval >= 1 && mine_interval <= 3600, "mining interval must be 1..3600 seconds");
        } else
          need(false, "unknown node argument");
      }
      node(store, argv[3], peers, miner, mine_interval);
    } else if (command == "sign") {
      need(argc == 9, "usage: qcash sign HOME KEYFILE RECIPIENT AMOUNT "
                      "FEE NONCE OUTFILE");
      auto key = load_key(argv[3]);
      Transfer tx;
      tx.chain_id = store.chain.context();
      tx.sender = address(public_key(key));
      tx.recipient = unhex(argv[4]);
      tx.amount = integer_argument(argv[5]);
      tx.fee = integer_argument(argv[6]);
      tx.nonce = integer_argument(argv[7]);
      sign(tx, key);
      need(verify(tx));
      create(argv[8], encode_transfer(tx));
    } else
      need(false, "unknown command");
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
