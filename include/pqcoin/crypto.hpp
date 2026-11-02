#pragma once
#include <array>
#include <cstdint>
#include <memory>
#include <openssl/evp.h>
#include <span>
#include <vector>

namespace pqcoin {
using Bytes = std::vector<unsigned char>;
using Hash = std::array<unsigned char, 32>;
using Key = std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)>;
Key signing_key();
Key encryption_key();
Key import_encryption_public_key(std::span<const unsigned char> bytes);
Bytes public_key(const Key &key);
Hash hash(std::span<const unsigned char> input);
Hash address(std::span<const unsigned char> public_key);
struct Transfer {
  std::uint32_t version = 2;
  std::uint32_t network = 0x50515431; // PQT1: local prototype only.
  Hash sender{}, recipient{};
  std::uint64_t amount = 0, fee = 0, nonce = 0;
  Hash recipient_kem_key{}, chain_id{}, message_commitment{};
  Bytes public_key, signature;
};
struct Envelope {
  Bytes kem_ciphertext;
  std::array<unsigned char, 12> nonce{};
  Bytes ciphertext;
  std::array<unsigned char, 16> tag{};
};
Bytes canonical_body(const Transfer &tx);
Bytes canonical_envelope(const Envelope &envelope);
void sign(Transfer &tx, const Key &sender);
bool verify(const Transfer &tx);
Envelope encrypt_message(Transfer &tx, const Key &recipient,
                         std::span<const unsigned char> message);
Bytes decrypt_message(const Transfer &tx, const Key &recipient,
                      const Envelope &envelope);
} // namespace pqcoin
