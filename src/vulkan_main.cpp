#include <algorithm>
#include <charconv>
#include <chrono>
#include <csignal>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <limits>
#include <pqcoin/pow.hpp>
#include <pqcoin/vulkan_miner.hpp>
#include <stdexcept>
#include <unistd.h>
using namespace pqcoin;
namespace p = pqcoin::pow;
namespace {
volatile std::sig_atomic_t stopping = 0;
void stop(int) { stopping = 1; }
void need(bool ok, const char *message) {
  if (!ok)
    throw std::runtime_error(message);
}
std::uint64_t number(const char *text) {
  std::uint64_t n{};
  auto end = text + std::char_traits<char>::length(text);
  auto r = std::from_chars(text, end, n);
  need(r.ec == std::errc{} && r.ptr == end, "Invalid integer argument");
  return n;
}
Bytes read(const char *path) {
  std::ifstream in(path, std::ios::binary | std::ios::ate);
  need(bool(in) && in.tellg() >= 0 && in.tellg() <= 1024 * 1024,
       "Invalid work file");
  auto length = static_cast<std::size_t>(in.tellg());
  in.seekg(0);
  Bytes bytes(length);
  in.read(reinterpret_cast<char *>(bytes.data()),
          static_cast<std::streamsize>(length));
  need(bool(in), "Cannot read work file");
  return bytes;
}
void create(const char *path, const Bytes &data) {
  int fd = ::open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  need(fd >= 0, "Output must be a new private file");
  std::size_t offset = 0;
  while (offset < data.size()) {
    auto n = ::write(fd, data.data() + offset, data.size() - offset);
    if (n < 0 && errno == EINTR)
      continue;
    if (n <= 0) {
      ::close(fd);
      ::unlink(path);
      throw std::runtime_error("Cannot write solution");
    }
    offset += static_cast<std::size_t>(n);
  }
  need(::close(fd) == 0, "Cannot close solution");
}
std::string hex(const Hash &hash) {
  constexpr char alphabet[] = "0123456789abcdef";
  std::string out;
  for (auto b : hash) {
    out += alphabet[b >> 4];
    out += alphabet[b & 15];
  }
  return out;
}
std::string quoted(const std::string &text) {
  std::string out = "\"";
  for (unsigned char c : text) {
    if (c == '"' || c == '\\') {
      out += '\\';
      out += static_cast<char>(c);
    } else if (c >= 32)
      out += static_cast<char>(c);
  }
  return out + '"';
}
using Clock = std::chrono::steady_clock;
double seconds(Clock::time_point start) {
  return std::chrono::duration<double>(Clock::now() - start).count();
}
} // namespace
int main(int argc, char **argv) {
  try {
    need(argc >= 2,
         "Usage: qcash-vulkan info | solve WORK OUTPUT ATTEMPTS | bench WORK "
         "ATTEMPTS REPEATS [--device N --batch N --workgroup N --seconds N]");
    const std::string command = argv[1];
    need(command == "info" || command == "solve" || command == "bench",
         "Unknown command");
    need(command == "info" || argc >= 5,
         "Missing work/output/budget arguments");
    std::optional<std::uint32_t> device;
    std::uint32_t batch = 65536, group = 64;
    std::uint64_t limit = 120;
    for (int i = command == "info" ? 2 : 5; i < argc; i += 2) {
      need(i + 1 < argc, "Missing option value");
      const std::string option = argv[i];
      auto value = number(argv[i + 1]);
      if (option == "--seconds") {
        need(value >= 1 && value <= 300, "Seconds must be 1..300");
        limit = value;
        continue;
      }
      need(value <= std::numeric_limits<std::uint32_t>::max(),
           "Option too large");
      auto small = static_cast<std::uint32_t>(value);
      if (option == "--device")
        device = small;
      else if (option == "--batch")
        batch = small;
      else if (option == "--workgroup")
        group = small;
      else
        throw std::runtime_error("Unknown option");
    }
    need(batch > 0 && batch <= VulkanMiner::max_batch,
         "Batch must be 1..1048576");
    std::signal(SIGINT, stop);
    std::signal(SIGTERM, stop);
    auto began = Clock::now();
    VulkanMiner miner(device, group);
    std::string identity = "\"backend\":\"vulkan-sha3-midstate\",\"device\":" +
                           quoted(miner.device_name()) + ",\"device_index\":" +
                           std::to_string(miner.device_index()) +
                           ",\"workgroup\":" + std::to_string(group);
    if (command == "info") {
      std::cout << '{' << identity << "}\n";
      return 0;
    }
    auto block = p::decode_block(read(argv[2]));
    need(block.target != Hash{}, "Zero work target");
    auto budget = number(command == "solve" ? argv[4] : argv[3]);
    need(budget >= 1 && budget <= 100000000, "Attempts must be 1..100000000");
    need(block.nonce <=
             std::numeric_limits<std::uint64_t>::max() - (budget - 1),
         "Nonce budget overflows; request a fresh work template");
    miner.prepare(p::header(block));
    const double setup = seconds(began);
    if (command == "bench") {
      auto repeats = number(argv[4]);
      need(repeats >= 1 && repeats <= 20, "Repeats must be 1..20");
      // Independently verify actual shader digests before timing this template.
      auto test = miner.run(
          block.nonce,
          static_cast<std::uint32_t>(std::min<std::uint64_t>(budget, 256)),
          true);
      for (std::size_t i = 0; i < test.digests.size(); i++) {
        auto check = block;
        check.nonce += i;
        need(p::id(check) == test.digests[i], "GPU SHA3 self-check failed");
      }
      std::cout << '{' << identity << ",\"setup_seconds\":" << setup
                << ",\"hashes_per_round\":" << budget << ",\"samples\":[";
      for (std::uint64_t repeat = 0; repeat < repeats; repeat++) {
        auto started = Clock::now();
        std::uint64_t done = 0;
        double gpu = 0;
        bool timed = true;
        while (done < budget) {
          need(!stopping && seconds(began) < double(limit),
               "Benchmark cancelled or deadline reached");
          auto n = static_cast<std::uint32_t>(
              std::min<std::uint64_t>(batch, budget - done));
          auto result = miner.run(block.nonce + done, n);
          if (result.winner) {
            auto check = block;
            check.nonce += done + *result.winner;
            need(p::valid_work(check), "GPU returned invalid proof");
          }
          if (result.gpu_seconds)
            gpu += *result.gpu_seconds;
          else
            timed = false;
          done += n;
        }
        if (repeat)
          std::cout << ',';
        std::cout << "{\"wall_seconds\":" << seconds(started)
                  << ",\"gpu_seconds\":";
        if (timed)
          std::cout << gpu;
        else
          std::cout << "null";
        std::cout << '}';
      }
      std::cout << "]}\n";
      return 0;
    }
    std::uint64_t done = 0;
    double gpu = 0;
    bool timed = true;
    while (done < budget && !stopping && seconds(began) < double(limit)) {
      auto n = static_cast<std::uint32_t>(
          std::min<std::uint64_t>(batch, budget - done));
      auto result = miner.run(block.nonce + done, n);
      if (result.gpu_seconds)
        gpu += *result.gpu_seconds;
      else
        timed = false;
      auto base = done;
      done += n;
      if (stopping)
        break;
      if (result.winner) {
        block.nonce += base + *result.winner;
        need(p::valid_work(block),
             "GPU candidate failed native SHA3 verification");
        create(argv[3], p::encode_block(block));
        std::cout << '{' << identity << ",\"status\":\"solved\",\"blockId\":\""
                  << hex(p::id(block))
                  << "\",\"native_verified\":true,\"attempts\":" << done
                  << ",\"setup_seconds\":" << setup
                  << ",\"total_wall_seconds\":" << seconds(began) << "}\n";
        return 0;
      }
    }
    std::cout << '{' << identity << ",\"status\":\""
              << (stopping         ? "cancelled"
                  : done == budget ? "exhausted"
                                   : "budget_ended")
              << "\",\"attempts\":" << done << ",\"setup_seconds\":" << setup
              << ",\"total_wall_seconds\":" << seconds(began)
              << ",\"gpu_seconds\":";
    if (timed)
      std::cout << gpu;
    else
      std::cout << "null";
    std::cout << "}\n";
    return stopping ? 130 : 3;
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 2;
  }
}
