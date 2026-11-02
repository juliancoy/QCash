#include <algorithm>
#include <pqcoin/app.h>
#include <pqcoin/app.hpp>
#include <string>
namespace {
thread_local std::string error;
template <class F> int protect(F call) noexcept {
  try {
    error.clear();
    return call();
  } catch (const std::exception &e) {
    error = e.what();
    return -1;
  } catch (...) {
    error = "native application failure";
    return -1;
  }
}
pqcoin::Application &app(void *p) {
  if (!p)
    throw std::runtime_error("null application");
  return *static_cast<pqcoin::Application *>(p);
}
} // namespace
extern "C" {
const char *pq_error() { return error.c_str(); }
void *pq_open(const char *dir, const unsigned char *genesis, size_t size) {
  void *out = nullptr;
  protect([&] {
    out = new pqcoin::Application(dir, {genesis, size});
    return 1;
  });
  return out;
}
void pq_close(void *p) { delete static_cast<pqcoin::Application *>(p); }
int pq_info(void *p, uint64_t *height, unsigned char *out) {
  return protect([&] {
    *height = app(p).committed().height;
    const auto h = app(p).app_hash();
    std::copy(h.begin(), h.end(), out);
    return 1;
  });
}
int pq_check(void *p, const unsigned char *tx, size_t size) {
  return protect([&] { return app(p).check({tx, size}) ? 1 : 0; });
}
int pq_reset(void *p) {
  return protect([&] {
    app(p).reset_trial();
    return 1;
  });
}
int pq_apply(void *p, const unsigned char *tx, size_t size) {
  return protect([&] { return app(p).apply_trial({tx, size}) ? 1 : 0; });
}
int pq_stage(void *p, uint64_t height, unsigned char *out) {
  return protect([&] {
    const auto h = app(p).stage(height);
    std::copy(h.begin(), h.end(), out);
    return 1;
  });
}
int pq_commit(void *p) {
  return protect([&] {
    app(p).commit();
    return 1;
  });
}
int pq_account(void *p, const unsigned char *owner, uint64_t *balance,
               uint64_t *nonce) {
  return protect([&] {
    pqcoin::Hash key{};
    std::copy(owner, owner + 32, key.begin());
    const auto &accounts = app(p).committed().accounts;
    auto it = accounts.find(key);
    if (it == accounts.end())
      return 0;
    *balance = it->second.balance;
    *nonce = it->second.nonce;
    return 1;
  });
}
}
