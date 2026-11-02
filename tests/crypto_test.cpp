#include <iostream>
#include <limits>
#include <pqcoin/ledger.hpp>
#include <stdexcept>
using namespace pqcoin;
namespace {
unsigned checks = 0;
void check(bool ok) {
  ++checks;
  if (!ok)
    throw std::runtime_error("test failed at check " + std::to_string(checks));
}
template <class F> void rejects(F operation) {
  bool rejected = false;
  try {
    operation();
  } catch (const std::runtime_error &) {
    rejected = true;
  }
  check(rejected);
}
} // namespace
int main() {
  try {
    auto alice = signing_key(), bob = signing_key();
    auto kem = encryption_key(), wrong_kem = encryption_key();
    auto kem_public = import_encryption_public_key(public_key(kem));
    Transfer tx;
    tx.sender = address(public_key(alice));
    tx.recipient = address(public_key(bob));
    tx.amount = 25;
    tx.fee = 1;
    const Bytes text{'h', 'e', 'l', 'l', 'o'};
    auto env = encrypt_message(tx, kem_public, text);
    sign(tx, alice);
    check(verify(tx));
    check(decrypt_message(tx, kem, env) == text);
    // Fixed-width, big-endian canonical encoding, independent of platform
    // layouts.
    const auto body = canonical_body(tx);
    check(body.size() == 179 && body[19] == 0 && body[22] == 2 &&
          body[98] == 25);
    for (int field = 0; field < 13; ++field) {
      auto bad = tx;
      switch (field) {
      case 0:
        ++bad.version;
        break;
      case 1:
        ++bad.network;
        break;
      case 2:
        bad.sender[0] ^= 1;
        break;
      case 3:
        bad.recipient[0] ^= 1;
        break;
      case 4:
        ++bad.amount;
        break;
      case 5:
        ++bad.fee;
        break;
      case 6:
        ++bad.nonce;
        break;
      case 7:
        bad.recipient_kem_key[0] ^= 1;
        break;
      case 8:
        bad.message_commitment[0] ^= 1;
        break;
      case 9:
        bad.public_key[0] ^= 1;
        break;
      case 10:
        bad.signature[0] ^= 1;
        break;
      case 12:
        bad.chain_id[0] ^= 1;
        break;
      case 11:
        bad.signature.pop_back();
        break;
      }
      check(!verify(bad));
    }
    for (int field = 0; field < 5; ++field) {
      auto bad = env;
      switch (field) {
      case 0:
        bad.kem_ciphertext[0] ^= 1;
        break;
      case 1:
        bad.nonce[0] ^= 1;
        break;
      case 2:
        bad.ciphertext[0] ^= 1;
        break;
      case 3:
        bad.tag[0] ^= 1;
        break;
      case 4:
        bad.kem_ciphertext.pop_back();
        break;
      }
      rejects([&] { (void)decrypt_message(tx, kem, bad); });
      // Re-signing the altered commitment bypasses the outer signature check,
      // exercising KEM/AEAD rejection of the modified envelope as well.
      if (field != 4) {
        auto changed = tx;
        changed.message_commitment = hash(canonical_envelope(bad));
        sign(changed, alice);
        rejects([&] { (void)decrypt_message(changed, kem, bad); });
      }
    }
    rejects([&] { (void)decrypt_message(tx, wrong_kem, env); });
    rejects([&] {
      auto t = tx;
      sign(t, bob);
    });
    auto empty = tx;
    const auto empty_env = encrypt_message(empty, kem_public, {});
    sign(empty, alice);
    check(decrypt_message(empty, kem, empty_env).empty());
    auto large = tx;
    Bytes maximum(65536, 0x42);
    const auto maximum_env = encrypt_message(large, kem_public, maximum);
    sign(large, alice);
    check(decrypt_message(large, kem, maximum_env) == maximum);
    maximum.push_back(0);
    rejects([&] { (void)encrypt_message(large, kem_public, maximum); });
    Account from{tx.sender, 100, 0}, to{tx.recipient, 0, 0};
    check(apply(tx, from, to));
    check(from.balance == 74 && to.balance == 25 && from.nonce == 1);
    check(!apply(tx, from, to));
    check(from.balance == 74 && to.balance == 25 && from.nonce == 1);
    from = {tx.sender, 25, 0};
    to = {tx.recipient, 0, 0};
    check(!apply(tx, from, to));
    check(from.balance == 25 && to.balance == 0 && from.nonce == 0);
    from.balance = 100;
    to.balance = std::numeric_limits<std::uint64_t>::max();
    check(!apply(tx, from, to));
    check(from.balance == 100 && from.nonce == 0 &&
          to.balance == std::numeric_limits<std::uint64_t>::max());
    auto overflow = tx;
    overflow.amount = std::numeric_limits<std::uint64_t>::max();
    sign(overflow, alice);
    check(!verify(overflow));
    auto zero = tx;
    zero.amount = 0;
    sign(zero, alice);
    check(!verify(zero));
    check(!apply(tx, from, from));
    auto exhausted = tx;
    exhausted.nonce = std::numeric_limits<std::uint64_t>::max();
    sign(exhausted, alice);
    from = {tx.sender, 100, exhausted.nonce};
    to = {tx.recipient, 0, 0};
    check(!apply(exhausted, from, to));
    check(from.balance == 100 && from.nonce == exhausted.nonce &&
          to.balance == 0);
    auto impostor = tx;
    impostor.sender = tx.recipient;
    check(!apply(impostor, from, to));

    std::cout << checks << " checks passed\n";
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
