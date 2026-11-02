#include <algorithm>
#include <limits>
#include <openssl/crypto.h>
#include <openssl/kdf.h>
#include <openssl/rand.h>
#include <pqcoin/crypto.hpp>
#include <stdexcept>
#include <string_view>

namespace pqcoin {
namespace {
constexpr std::size_t max_message = 65536;
void require(bool ok) {
  if (!ok)
    throw std::runtime_error("cryptographic operation rejected");
}
struct Secret {
  std::array<unsigned char, 32> bytes{};
  ~Secret() { OPENSSL_cleanse(bytes.data(), bytes.size()); }
  Secret() = default;
  Secret(const Secret &) = delete;
  Secret &operator=(const Secret &) = delete;
};
void append(Bytes &out, std::span<const unsigned char> value) {
  out.insert(out.end(), value.begin(), value.end());
}
void label(Bytes &out, std::string_view value) {
  out.insert(out.end(), value.begin(), value.end());
  out.push_back(0);
}
void integer(Bytes &out, std::uint64_t value, unsigned width) {
  for (unsigned i = width; i > 0; --i)
    out.push_back(static_cast<unsigned char>(value >> ((i - 1) * 8)));
}
Key generate(const char *name) {
  Key k(EVP_PKEY_Q_keygen(nullptr, nullptr, name), EVP_PKEY_free);
  require(bool(k));
  return k;
}
Bytes signature_input(const Transfer &tx) {
  auto out = canonical_body(tx);
  append(out, tx.message_commitment);
  return out;
}
void derive(Secret &key, const Secret &shared, const Transfer &tx,
            const Envelope &env) {
  auto context = canonical_body(tx);
  append(context, env.kem_ciphertext);
  auto salt = hash(context);
  std::unique_ptr<EVP_PKEY_CTX, decltype(&EVP_PKEY_CTX_free)> ctx(
      EVP_PKEY_CTX_new_id(EVP_PKEY_HKDF, nullptr), EVP_PKEY_CTX_free);
  require(bool(ctx));
  constexpr unsigned char info[] = "pqcoin/payment-key/v1";
  require(EVP_PKEY_derive_init(ctx.get()) > 0 &&
          EVP_PKEY_CTX_set_hkdf_md(ctx.get(), EVP_sha3_256()) > 0 &&
          EVP_PKEY_CTX_set1_hkdf_salt(ctx.get(), salt.data(),
                                      static_cast<int>(salt.size())) > 0 &&
          EVP_PKEY_CTX_set1_hkdf_key(ctx.get(), shared.bytes.data(), 32) > 0 &&
          EVP_PKEY_CTX_add1_hkdf_info(ctx.get(), info, sizeof(info) - 1) > 0);
  std::size_t size = key.bytes.size();
  require(EVP_PKEY_derive(ctx.get(), key.bytes.data(), &size) > 0 &&
          size == 32);
}
bool equal(const Hash &a, const Hash &b) {
  return CRYPTO_memcmp(a.data(), b.data(), a.size()) == 0;
}
} // namespace
Key signing_key() { return generate("ML-DSA-65"); }
Key encryption_key() { return generate("ML-KEM-768"); }
Key import_encryption_public_key(std::span<const unsigned char> bytes) {
  require(bytes.size() == 1184);
  Key key(EVP_PKEY_new_raw_public_key_ex(nullptr, "ML-KEM-768", nullptr,
                                         bytes.data(), bytes.size()),
          EVP_PKEY_free);
  require(bool(key));
  return key;
}
Bytes public_key(const Key &key) {
  require(bool(key));
  std::size_t size = 0;
  require(EVP_PKEY_get_raw_public_key(key.get(), nullptr, &size) > 0);
  Bytes out(size);
  require(EVP_PKEY_get_raw_public_key(key.get(), out.data(), &size) > 0);
  out.resize(size);
  return out;
}
Hash hash(std::span<const unsigned char> input) {
  Hash out{};
  unsigned size = 0;
  require(EVP_Digest(input.data(), input.size(), out.data(), &size,
                     EVP_sha3_256(), nullptr) > 0 &&
          size == out.size());
  return out;
}
Hash address(std::span<const unsigned char> key) {
  Bytes input;
  label(input, "pqcoin/address/ML-DSA-65/v1");
  append(input, key);
  return hash(input);
}
Bytes canonical_body(const Transfer &tx) {
  Bytes out;
  label(out, "pqcoin/transfer/v2");
  integer(out, tx.version, 4);
  integer(out, tx.network, 4);
  append(out, tx.sender);
  append(out, tx.recipient);
  integer(out, tx.amount, 8);
  integer(out, tx.fee, 8);
  integer(out, tx.nonce, 8);
  append(out, tx.recipient_kem_key);
  append(out, tx.chain_id);
  return out;
}
Bytes canonical_envelope(const Envelope &e) {
  require(e.kem_ciphertext.size() == 1088 &&
          e.ciphertext.size() <= max_message);
  Bytes out;
  label(out, "pqcoin/payment-envelope/v1");
  append(out, e.kem_ciphertext);
  append(out, e.nonce);
  integer(out, e.ciphertext.size(), 4);
  append(out, e.ciphertext);
  append(out, e.tag);
  return out;
}
void sign(Transfer &tx, const Key &sender) {
  require(sender && EVP_PKEY_is_a(sender.get(), "ML-DSA-65"));
  tx.public_key = public_key(sender);
  require(equal(tx.sender, address(tx.public_key)));
  auto message = signature_input(tx);
  std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> ctx(EVP_MD_CTX_new(),
                                                              EVP_MD_CTX_free);
  require(bool(ctx) &&
          EVP_DigestSignInit_ex(ctx.get(), nullptr, nullptr, nullptr, nullptr,
                                sender.get(), nullptr) > 0);
  std::size_t size = 0;
  require(EVP_DigestSign(ctx.get(), nullptr, &size, message.data(),
                         message.size()) > 0);
  tx.signature.resize(size);
  require(EVP_DigestSign(ctx.get(), tx.signature.data(), &size, message.data(),
                         message.size()) > 0);
  tx.signature.resize(size);
}
bool verify(const Transfer &tx) {
  if (tx.version != 2 || tx.network != 0x50515431 || tx.amount == 0 ||
      tx.sender == tx.recipient || tx.public_key.size() != 1952 ||
      tx.signature.size() != 3309 ||
      tx.amount > std::numeric_limits<std::uint64_t>::max() - tx.fee ||
      !equal(tx.sender, address(tx.public_key)))
    return false;
  Key key(EVP_PKEY_new_raw_public_key_ex(nullptr, "ML-DSA-65", nullptr,
                                         tx.public_key.data(),
                                         tx.public_key.size()),
          EVP_PKEY_free);
  if (!key)
    return false;
  std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> ctx(EVP_MD_CTX_new(),
                                                              EVP_MD_CTX_free);
  require(bool(ctx) &&
          EVP_DigestVerifyInit_ex(ctx.get(), nullptr, nullptr, nullptr, nullptr,
                                  key.get(), nullptr) > 0);
  const auto message = signature_input(tx);
  return EVP_DigestVerify(ctx.get(), tx.signature.data(), tx.signature.size(),
                          message.data(), message.size()) == 1;
}
Envelope encrypt_message(Transfer &tx, const Key &recipient,
                         std::span<const unsigned char> message) {
  require(recipient && EVP_PKEY_is_a(recipient.get(), "ML-KEM-768") &&
          message.size() <= max_message);
  // Caller supplies the recipient's authenticated ML-KEM key; this library does
  // not establish its identity.
  tx.recipient_kem_key = hash(public_key(recipient));
  Envelope out;
  Secret shared, key;
  std::unique_ptr<EVP_PKEY_CTX, decltype(&EVP_PKEY_CTX_free)> kem(
      EVP_PKEY_CTX_new_from_pkey(nullptr, recipient.get(), nullptr),
      EVP_PKEY_CTX_free);
  require(bool(kem) && EVP_PKEY_encapsulate_init(kem.get(), nullptr) > 0);
  out.kem_ciphertext.resize(1088);
  std::size_t ct_size = 1088, ss_size = 32;
  require(EVP_PKEY_encapsulate(kem.get(), out.kem_ciphertext.data(), &ct_size,
                               shared.bytes.data(), &ss_size) > 0 &&
          ct_size == 1088 && ss_size == 32);
  derive(key, shared, tx, out);
  require(RAND_bytes(out.nonce.data(), static_cast<int>(out.nonce.size())) > 0);
  auto aad = canonical_body(tx);
  std::unique_ptr<EVP_CIPHER_CTX, decltype(&EVP_CIPHER_CTX_free)> ctx(
      EVP_CIPHER_CTX_new(), EVP_CIPHER_CTX_free);
  require(bool(ctx) &&
          EVP_EncryptInit_ex(ctx.get(), EVP_aes_256_gcm(), nullptr,
                             key.bytes.data(), out.nonce.data()) > 0);
  int size = 0, tail = 0;
  require(EVP_EncryptUpdate(ctx.get(), nullptr, &size, aad.data(),
                            static_cast<int>(aad.size())) > 0);
  out.ciphertext.resize(message.size() + 16);
  require(EVP_EncryptUpdate(ctx.get(), out.ciphertext.data(), &size,
                            message.data(),
                            static_cast<int>(message.size())) > 0);
  require(EVP_EncryptFinal_ex(ctx.get(), out.ciphertext.data() + size, &tail) >
          0);
  out.ciphertext.resize(static_cast<std::size_t>(size + tail));
  require(EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_GCM_GET_TAG, 16,
                              out.tag.data()) > 0);
  tx.message_commitment = hash(canonical_envelope(out));
  tx.signature.clear();
  return out;
}
Bytes decrypt_message(const Transfer &tx, const Key &recipient,
                      const Envelope &e) {
  require(verify(tx) && recipient &&
          EVP_PKEY_is_a(recipient.get(), "ML-KEM-768") &&
          equal(hash(public_key(recipient)), tx.recipient_kem_key) &&
          equal(hash(canonical_envelope(e)), tx.message_commitment));
  Secret shared, key;
  std::unique_ptr<EVP_PKEY_CTX, decltype(&EVP_PKEY_CTX_free)> kem(
      EVP_PKEY_CTX_new_from_pkey(nullptr, recipient.get(), nullptr),
      EVP_PKEY_CTX_free);
  require(bool(kem) && EVP_PKEY_decapsulate_init(kem.get(), nullptr) > 0);
  std::size_t ss_size = 32;
  require(EVP_PKEY_decapsulate(kem.get(), shared.bytes.data(), &ss_size,
                               e.kem_ciphertext.data(),
                               e.kem_ciphertext.size()) > 0 &&
          ss_size == 32);
  derive(key, shared, tx, e);
  auto aad = canonical_body(tx);
  std::unique_ptr<EVP_CIPHER_CTX, decltype(&EVP_CIPHER_CTX_free)> ctx(
      EVP_CIPHER_CTX_new(), EVP_CIPHER_CTX_free);
  require(bool(ctx) &&
          EVP_DecryptInit_ex(ctx.get(), EVP_aes_256_gcm(), nullptr,
                             key.bytes.data(), e.nonce.data()) > 0);
  int size = 0, tail = 0;
  require(EVP_DecryptUpdate(ctx.get(), nullptr, &size, aad.data(),
                            static_cast<int>(aad.size())) > 0);
  Bytes plain(e.ciphertext.size() + 16);
  require(EVP_DecryptUpdate(ctx.get(), plain.data(), &size, e.ciphertext.data(),
                            static_cast<int>(e.ciphertext.size())) > 0);
  auto tag = e.tag;
  require(EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_GCM_SET_TAG, 16, tag.data()) >
          0);
  if (EVP_DecryptFinal_ex(ctx.get(), plain.data() + size, &tail) <= 0) {
    OPENSSL_cleanse(plain.data(), plain.size());
    require(false);
  }
  plain.resize(static_cast<std::size_t>(size + tail));
  return plain;
}
} // namespace pqcoin
