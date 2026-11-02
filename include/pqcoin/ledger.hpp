#pragma once
#include <pqcoin/crypto.hpp>
namespace pqcoin {
struct Account {
  Hash owner{};
  std::uint64_t balance = 0, nonce = 0;
};
// Two distinct accounts; fee is removed from circulation in this prototype.
bool apply(const Transfer &tx, Account &sender, Account &recipient);
} // namespace pqcoin
