#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <pqcoin/genesis.hpp>
#include <stdexcept>
#include <string_view>
namespace fs = std::filesystem;
using namespace pqcoin;
namespace {
void write(const fs::path &path, std::span<const unsigned char> bytes) {
  std::ofstream out(path, std::ios::binary);
  out.exceptions(std::ios::badbit | std::ios::failbit);
  out.write(reinterpret_cast<const char *>(bytes.data()),
            static_cast<std::streamsize>(bytes.size()));
  out.close();
}
void save(const fs::path &directory, const devnet::Genesis &block) {
  if (!fs::create_directory(directory))
    throw std::runtime_error(
        "output directory already exists; refusing to replace genesis");
  write(directory / "genesis.bin", devnet::encode(block));
  const auto report =
      std::string(
          "{\n  \"network\": \"PQT1-local-development\",\n  \"height\": 0,\n  "
          "\"hash_algorithm\": \"SHA3-256\",\n  \"hash\": \"") +
      devnet::hex(devnet::block_hash(block)) +
      "\",\n  \"timestamp\": " + std::to_string(block.timestamp) +
      ",\n  \"difficulty_bits\": 16,\n  \"nonce\": " +
      std::to_string(block.nonce) +
      ",\n  \"transactions\": 0,\n  \"issued_units\": 0,\n  \"validation\": "
      "\"passed\"\n}\n";
  write(
      directory / "genesis.json",
      {reinterpret_cast<const unsigned char *>(report.data()), report.size()});
}
} // namespace
int main(int argc, char **argv) {
  try {
    if (argc != 3)
      throw std::runtime_error("usage: pqcoin-genesis mine|verify DIRECTORY");
    const fs::path directory(argv[2]);
    devnet::Genesis block;
    if (std::string_view(argv[1]) == "mine") {
      if (fs::exists(directory))
        throw std::runtime_error(
            "output directory already exists; refusing to replace genesis");
      const auto timestamp =
          std::chrono::duration_cast<std::chrono::seconds>(
              std::chrono::system_clock::now().time_since_epoch())
              .count();
      if (timestamp <= 0)
        throw std::runtime_error("invalid system clock");
      std::cout << "Mining local development genesis (16 work bits)..."
                << std::endl;
      block = devnet::mine(static_cast<std::uint64_t>(timestamp));
      if (!devnet::valid(block))
        throw std::runtime_error("mined block validation failed");
      save(directory, block);
    } else if (std::string_view(argv[1]) == "verify") {
      const auto path = directory / "genesis.bin";
      const auto expected_size = devnet::encode({}).size();
      std::ifstream in(path, std::ios::binary);
      if (!in)
        throw std::runtime_error("cannot open genesis.bin");
      Bytes bytes(expected_size + 1);
      in.read(reinterpret_cast<char *>(bytes.data()),
              static_cast<std::streamsize>(bytes.size()));
      if (in.bad() ||
          in.gcount() != static_cast<std::streamsize>(expected_size))
        throw std::runtime_error("invalid genesis length");
      bytes.resize(expected_size);
      block = devnet::decode(bytes);
      if (!devnet::valid(block))
        throw std::runtime_error("invalid genesis proof of work");
    } else
      throw std::runtime_error("unknown command");
    std::cout << "Validated block 0: " << devnet::hex(devnet::block_hash(block))
              << "\nNonce: " << block.nonce << "\nIssued units: 0\n";
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
