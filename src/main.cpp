#include <iostream>
#include <pqcoin/ledger.hpp>
#include <string>
int main() {
  try {
    auto sender = pqcoin::signing_key();
    auto recipient_signing = pqcoin::signing_key();
    auto recipient_encryption = pqcoin::encryption_key();
    auto recipient_public = pqcoin::import_encryption_public_key(
        pqcoin::public_key(recipient_encryption));
    pqcoin::Transfer tx;
    tx.sender = pqcoin::address(pqcoin::public_key(sender));
    tx.recipient = pqcoin::address(pqcoin::public_key(recipient_signing));
    tx.amount = 25;
    tx.fee = 1;
    const std::string message = "Payment for invoice 42";
    const auto envelope = pqcoin::encrypt_message(
        tx, recipient_public,
        {reinterpret_cast<const unsigned char *>(message.data()),
         message.size()});
    pqcoin::sign(tx, sender);
    const auto plaintext =
        pqcoin::decrypt_message(tx, recipient_encryption, envelope);
    pqcoin::Account from{tx.sender, 100, 0}, to{tx.recipient, 0, 0};
    if (!pqcoin::apply(tx, from, to) ||
        std::string(plaintext.begin(), plaintext.end()) != message)
      return 1;
    std::cout << "ML-DSA-65 transfer signature: verified\n"
              << "ML-KEM-768 / HKDF-SHA3-256 / AES-256-GCM: round trip passed\n"
              << "Transfer: 25 units; fee: 1; balances: " << from.balance
              << ", " << to.balance << '\n'
              << "In-memory prototype; no network or consensus engine.\n";
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
