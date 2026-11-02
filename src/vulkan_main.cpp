#include <algorithm>
#include <charconv>
#include <chrono>
#include <csignal>
#include <ctime>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <limits>
#include <poll.h>
#include <pqcoin/pow.hpp>
#include <pqcoin/vulkan_miner.hpp>
#include <sstream>
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

struct Options {
  std::optional<std::uint32_t> device;
  std::uint32_t batch = 262144, group = 64, depth = 4, profile_min_seconds = 0;
  std::uint64_t seconds = 120;
  std::string shader = "final32", memory = "mapped", capture_layout = "off",
              dump_shader;
  bool gpu_prepare = true, diagnostics = false, memory_explicit = false;
};
Hash unhash(const std::string &text) {
  need(text.size() == 64, "Invalid work identity");
  Hash out{};
  for (unsigned i = 0; i < 32; i++) {
    auto digit = [](char c) -> unsigned {
      if (c >= '0' && c <= '9')
        return unsigned(c - '0');
      if (c >= 'a' && c <= 'f')
        return unsigned(c - 'a' + 10);
      throw std::runtime_error("Invalid hex");
    };
    out[i] = static_cast<unsigned char>((digit(text[2 * i]) << 4) |
                                        digit(text[2 * i + 1]));
  }
  need(out != Hash{}, "Zero work identity");
  return out;
}
std::string path_from_hex(const std::string &text) {
  need(!text.empty() && text.size() <= 8192 && text.size() % 2 == 0,
       "Invalid request path");
  std::string out;
  for (std::size_t i = 0; i < text.size(); i += 2) {
    unsigned v{};
    auto r = std::from_chars(text.data() + i, text.data() + i + 2, v, 16);
    need(r.ec == std::errc{} && r.ptr == text.data() + i + 2 && v != 0,
         "Invalid path encoding");
    out += static_cast<char>(v);
  }
  return out;
}
void budget_check(const p::Block &block, std::uint64_t budget) {
  need(block.target != Hash{}, "Zero work target");
  need(budget >= 1 && budget <= 100000000, "Attempts must be 1..100000000");
  need(block.nonce <= std::numeric_limits<std::uint64_t>::max() - (budget - 1),
       "Nonce budget overflows; request fresh work");
}
std::string identity(VulkanMiner &miner, const Options &o,
                     bool detailed = true) {
  return "\"backend\":\"vulkan-sha3-midstate\",\"device\":" +
         quoted(miner.device_name()) +
         ",\"device_index\":" + std::to_string(miner.device_index()) +
         ",\"workgroup\":" + std::to_string(o.group) +
         ",\"shader\":" + quoted(o.shader) +
         ",\"queue_depth\":" + std::to_string(o.depth) +
         ",\"prefix_preparation\":" + quoted(o.gpu_prepare ? "gpu" : "cpu") +
         ",\"memory_policy\":" + quoted(o.memory) +
         (detailed ? ",\"memory_topology\":" + miner.memory_topology()
                   : std::string()) +
         ",\"capture_layout\":" + quoted(o.capture_layout) +
         (detailed ? ",\"pipeline_statistics\":" + miner.pipeline_statistics()
                   : std::string());
}
std::string
solve(VulkanMiner &miner, p::Block block, const char *output,
      std::uint64_t budget, const Options &o,
      Clock::time_point worker_deadline = Clock::time_point::max()) {
  auto began = Clock::now();
  auto cpu_began = std::clock();
  auto deadline =
      std::min(worker_deadline, began + std::chrono::seconds(o.seconds));
  budget_check(block, budget);
  miner.prepare(p::header(block), o.gpu_prepare);
  std::uint64_t done = 0, waits = 0;
  double gpu = 0;
  bool timed = true;
  while (done < budget && !stopping && Clock::now() < deadline) {
    auto n = static_cast<std::uint32_t>(std::min<std::uint64_t>(
        std::uint64_t(o.batch) * o.depth, budget - done));
    auto result = miner.run(block.nonce + done, n, false, o.batch);
    waits++;
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
      auto digest = p::id(block);
      need(digest <= block.target,
           "GPU candidate failed native SHA3 verification");
      create(output, p::encode_block(block));
      std::ostringstream out;
      out << "\"status\":\"solved\",\"blockId\":\"" << hex(digest)
          << "\",\"native_verified\":true,\"attempts\":" << done
          << ",\"job_seconds\":" << seconds(began)
          << ",\"host_waits\":" << waits << ",\"host_cpu_seconds\":"
          << double(std::clock() - cpu_began) / CLOCKS_PER_SEC;
      return out.str();
    }
  }
  std::ostringstream out;
  out << "\"status\":\""
      << (stopping         ? "cancelled"
          : done == budget ? "exhausted"
                           : "budget_ended")
      << "\",\"attempts\":" << done << ",\"job_seconds\":" << seconds(began)
      << ",\"host_waits\":" << waits << ",\"host_cpu_seconds\":"
      << double(std::clock() - cpu_began) / CLOCKS_PER_SEC
      << ",\"gpu_seconds\":";
  if (timed)
    out << gpu;
  else
    out << "null";
  return out.str();
}
// Private inherited stdin only: bounded length-prefixed requests, never a port.
bool read_exact(char *destination, std::size_t bytes,
                Clock::time_point deadline) {
  std::size_t done = 0;
  while (done < bytes) {
    if (stopping || Clock::now() >= deadline)
      return false;
    pollfd fd{STDIN_FILENO, POLLIN, 0};
    int ready = ::poll(&fd, 1, 100);
    if (ready < 0 && errno == EINTR)
      continue;
    need(ready >= 0, "Worker input failed");
    if (!ready)
      continue;
    auto n = ::read(STDIN_FILENO, destination + done, bytes - done);
    if (n < 0 && errno == EINTR)
      continue;
    if (n == 0) {
      need(done == 0, "Truncated worker request");
      return false;
    }
    need(n > 0, "Worker input failed");
    done += static_cast<std::size_t>(n);
  }
  return true;
}
void serve(VulkanMiner &miner, const Options &o, Clock::time_point began) {
  auto deadline = began + std::chrono::seconds(o.seconds);
  std::cout << '{' << identity(miner, o)
            << ",\"status\":\"ready\",\"protocol\":\"qcash-vulkan-worker-v1\","
               "\"worker_pid\":"
            << ::getpid() << ",\"setup_seconds\":" << seconds(began) << '}'
            << std::endl;
  std::uint64_t previous = 0;
  for (unsigned job = 0; job < 1000000 && !stopping; job++) {
    unsigned char size[4];
    if (!read_exact(reinterpret_cast<char *>(size), 4, deadline))
      break;
    std::uint32_t length = 0;
    for (auto b : size)
      length = (length << 8) | b;
    need(length > 0 && length <= 16384, "Worker frame exceeds bound");
    std::string frame(length, '\0');
    need(read_exact(frame.data(), length, deadline), "Truncated worker frame");
    std::istringstream input(frame);
    std::string id_text, budget_text, seconds_text, chain, reward, work, out,
        extra;
    need(bool(input >> id_text >> budget_text >> seconds_text >> chain >>
              reward >> work >> out) &&
             !(input >> extra),
         "Malformed worker request");
    auto id = number(id_text.c_str());
    need(id > previous, "Worker request ID must increase");
    previous = id;
    try {
      auto budget = number(budget_text.c_str()),
           job_limit = number(seconds_text.c_str());
      need(job_limit >= 1 && job_limit <= 300, "Invalid job deadline");
      Options options = o;
      options.seconds = job_limit;
      auto block = p::decode_block(read(path_from_hex(work).c_str()));
      need(block.chain == unhash(chain) && block.miner == unhash(reward),
           "Work identity mismatch");
      auto result = solve(miner, block, path_from_hex(out).c_str(), budget,
                          options, deadline);
      std::cout << '{' << identity(miner, o, false) << ',' << result
                << ",\"request_id\":" << id << ",\"worker_pid\":" << ::getpid()
                << '}' << std::endl;
    } catch (const std::exception &) {
      std::cout << "{\"status\":\"failed\",\"request_id\":" << id
                << ",\"worker_pid\":" << ::getpid()
                << ",\"error\":\"Work rejected or device operation failed\"}"
                << std::endl;
    }
  }
}
} // namespace
int main(int argc, char **argv) {
  try {
    need(argc >= 2, "Usage: qcash-vulkan info | serve | solve WORK OUTPUT "
                    "ATTEMPTS | bench WORK ATTEMPTS REPEATS [options]");
    std::string command = argv[1];
    bool service = command == "serve";
    need(command == "info" || service || command == "solve" ||
             command == "bench",
         "Unknown command");
    need(command == "info" || service || argc >= 5, "Missing work arguments");
    Options o;
    for (int i = (command == "info" || service) ? 2 : 5; i < argc; i += 2) {
      need(i + 1 < argc, "Missing option value");
      std::string option = argv[i], value = argv[i + 1];
      if (option == "--memory") {
        need(value == "mapped" || value == "mapped-local" || value == "staged",
             "Unknown memory policy");
        o.memory = value;
        o.memory_explicit = true;
        continue;
      }
      if (option == "--capture-layout") {
        need(value == "off" || value == "aos" || value == "soa",
             "Unknown capture layout");
        o.capture_layout = value;
        continue;
      }
      if (option == "--dump-shader") {
        o.dump_shader = value;
        o.diagnostics = true;
        continue;
      }
      if (option == "--shader") {
        need(value == "scalar64" || value == "pair32" || value == "compact64" ||
                 value == "compact32" || value == "row32" ||
                 value == "unroll32" || value == "final32",
             "Unknown shader");
        o.shader = value;
        continue;
      }
      if (option == "--prepare") {
        need(value == "cpu" || value == "gpu", "Unknown preparation backend");
        o.gpu_prepare = value == "gpu";
        continue;
      }
      auto n = number(value.c_str());
      need(n <= std::numeric_limits<std::uint32_t>::max(), "Option too large");
      auto small = static_cast<std::uint32_t>(n);
      if (option == "--device")
        o.device = small;
      else if (option == "--profile-min-seconds") {
        need(n <= 15, "Profiling window must be 0..15 seconds");
        o.profile_min_seconds = small;
      } else if (option == "--batch")
        o.batch = small;
      else if (option == "--workgroup")
        o.group = small;
      else if (option == "--diagnostics") {
        need(n <= 1, "Diagnostics must be 0 or 1");
        o.diagnostics = n != 0;
      } else if (option == "--queue-depth")
        o.depth = small;
      else if (option == "--seconds") {
        need(n >= 1 && n <= (service ? 3600U : 300U), "Invalid deadline");
        o.seconds = n;
      } else
        throw std::runtime_error("Unknown option");
    }
    need(o.batch > 0 && o.batch <= VulkanMiner::max_dispatch && o.depth >= 1 &&
             o.depth <= 16,
         "Invalid batch or queue depth");
    if (o.capture_layout != "off" && !o.memory_explicit)
      o.memory = "staged";
    need(command == "bench" || o.profile_min_seconds == 0,
         "Profiling window is benchmark-only");
    need(command == "bench" || o.capture_layout == "off",
         "Digest capture option is benchmark-only");
    std::signal(SIGINT, stop);
    std::signal(SIGTERM, stop);
    std::signal(SIGPIPE, SIG_IGN);
    auto began = Clock::now();
    VulkanMiner miner(o.device, o.group, o.shader, o.diagnostics, o.memory);
    if (!o.dump_shader.empty())
      miner.dump_pipeline(o.dump_shader);
    auto ident = identity(miner, o);
    if (command == "info") {
      std::cout << '{' << ident << "}\n";
      return 0;
    }
    if (service) {
      serve(miner, o, began);
      return stopping ? 130 : 0;
    }
    auto block = p::decode_block(read(argv[2]));
    auto budget = number(command == "solve" ? argv[4] : argv[3]);
    budget_check(block, budget);
    if (command == "bench") {
      auto repeats = number(argv[4]);
      need(repeats >= 1 && repeats <= 20, "Repeats must be 1..20");
      const bool capture = o.capture_layout != "off";
      need(!capture || budget <= 4096,
           "Capture benchmark requires at most 4096 attempts");
      miner.prepare(p::header(block), o.gpu_prepare);
      auto test = miner.run(
          block.nonce,
          static_cast<std::uint32_t>(std::min<std::uint64_t>(budget, 256)),
          true, o.batch, o.capture_layout == "soa");
      for (std::size_t i = 0; i < test.digests.size(); i++) {
        auto check = block;
        check.nonce += i;
        need(p::id(check) == test.digests[i], "GPU SHA3 self-check failed");
      }
      // Warm the measured command shape so steady-state results include reuse.
      auto warm_count = static_cast<std::uint32_t>(
          std::min<std::uint64_t>(std::uint64_t(o.batch) * o.depth, budget));
      miner.run(block.nonce, warm_count, capture, o.batch,
                o.capture_layout == "soa");
      std::cout << '{' << ident << ",\"setup_seconds\":" << seconds(began)
                << ",\"hashes_per_round\":" << budget << ",\"samples\":[";
      auto profile_started = Clock::now();
      for (std::uint64_t repeat = 0;
           repeat < repeats ||
           seconds(profile_started) < double(o.profile_min_seconds);
           repeat++) {
        auto started = Clock::now();
        auto cpu = std::clock();
        std::uint64_t done = 0, waits = 0, records = miner.command_recordings(),
                      uploads = 0, readbacks = 0;
        double gpu = 0;
        bool timed = true;
        while (done < budget) {
          need(!stopping && seconds(began) < double(o.seconds),
               "Benchmark cancelled or deadline reached");
          auto n = static_cast<std::uint32_t>(std::min<std::uint64_t>(
              std::uint64_t(o.batch) * o.depth, budget - done));
          auto result = miner.run(block.nonce + done, n, capture, o.batch,
                                  o.capture_layout == "soa");
          waits++;
          uploads += result.explicit_upload_bytes;
          readbacks += result.explicit_readback_bytes;
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
                  << ",\"host_cpu_seconds\":"
                  << double(std::clock() - cpu) / CLOCKS_PER_SEC
                  << ",\"host_waits\":" << waits
                  << ",\"explicit_upload_bytes\":" << uploads
                  << ",\"explicit_readback_bytes\":" << readbacks
                  << ",\"command_recordings\":"
                  << miner.command_recordings() - records
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
    const auto setup = seconds(began);
    auto result = solve(miner, block, argv[3], budget, o);
    std::cout << '{' << ident << ',' << result << ",\"setup_seconds\":" << setup
              << ",\"total_wall_seconds\":" << seconds(began) << "}\n";
    return stopping                                         ? 130
           : result.find("\"solved\"") != std::string::npos ? 0
                                                            : 3;
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 2;
  }
}
