#include <limits>
#include <pqcoin/ledger.hpp>
namespace pqcoin {
bool apply(const Transfer &tx, Account &sender, Account &recipient) {
  if (&sender == &recipient || !verify(tx) || tx.sender != sender.owner ||
      tx.recipient != recipient.owner || tx.nonce != sender.nonce ||
      sender.nonce == std::numeric_limits<std::uint64_t>::max() ||
      sender.balance < tx.amount + tx.fee ||
      recipient.balance > std::numeric_limits<std::uint64_t>::max() - tx.amount)
    return false;
  sender.balance -= tx.amount + tx.fee;
  recipient.balance += tx.amount;
  ++sender.nonce;
  return true;
}
} // namespace pqcoin
