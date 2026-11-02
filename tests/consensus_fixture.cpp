#include <fstream>
#include <iostream>
#include <pqcoin/app.hpp>
#include <pqcoin/genesis.hpp>
#include <stdexcept>
namespace fs = std::filesystem;
using namespace pqcoin;
void write(const fs::path &path, const Bytes &bytes) {
  std::ofstream out(path, std::ios::binary);
  out.exceptions(std::ios::failbit | std::ios::badbit);
  out.write(reinterpret_cast<const char *>(bytes.data()),
            static_cast<std::streamsize>(bytes.size()));
  out.close();
}
int main(int argc, char **argv) {
  try {
    if (argc != 3)
      throw std::runtime_error("usage: pqcoin-consensus-fixture "
                               "BOOTSTRAP_GENESIS_BIN NEW_DIRECTORY");
    const fs::path directory(argv[2]);
    if (!fs::create_directory(directory))
      throw std::runtime_error("fixture directory exists");
    std::ifstream input(argv[1], std::ios::binary);
    if (!input)
      throw std::runtime_error("missing bootstrap genesis");
    Bytes bootstrap((std::istreambuf_iterator<char>(input)), {});
    const auto block = devnet::decode(bootstrap);
    if (!devnet::valid(block))
      throw std::runtime_error("invalid bootstrap genesis");
    auto alice = signing_key(), bob = signing_key();
    const auto from = address(public_key(alice)), to = address(public_key(bob));
    AppGenesis genesis{devnet::block_hash(block),
                       "pqcoin-dev-" + devnet::hex(from).substr(0, 16),
                       {{from, {from, 1000000, 0}}}};
    const auto encoded = encode_genesis(genesis);
    write(directory / "app-genesis.bin", encoded);
    for (unsigned i = 0; i < 4; ++i) {
      Transfer tx;
      tx.sender = from;
      tx.recipient = to;
      tx.chain_id = hash(encoded);
      tx.amount = 25;
      tx.fee = 1;
      tx.nonce = i;
      sign(tx, alice);
      write(directory / ("tx" + std::to_string(i) + ".bin"),
            encode_transfer(tx));
      if (i == 0) {
        tx.chain_id[0] ^= 1;
        sign(tx, alice);
        write(directory / "wrong-chain.bin", encode_transfer(tx));
      }
    }
    std::ofstream report(directory / "accounts.json");
    report.exceptions(std::ios::badbit | std::ios::failbit);
    report << "{\"alice\":\"" << devnet::hex(from) << "\",\"bob\":\""
           << devnet::hex(to) << "\",\"test_allocation\":1000000}\n";
    std::cout
        << "Generated ephemeral funded TEST fixture; no private keys saved\n";
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
