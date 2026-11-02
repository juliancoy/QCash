#include <cstdlib>
#include <pqcoin/pow.hpp>
#include <stdexcept>
extern "C" int LLVMFuzzerTestOneInput(const unsigned char *data, std::size_t size) {
  if(size<1 || size>1024*1024+1) return 0;
  auto bytes=std::span(data+1,size-1);
  pqcoin::Bytes canonical;
  try {
    switch(data[0]%3) {
    case 0:canonical=pqcoin::pow::encode_config(pqcoin::pow::decode_config(bytes));break;
    case 1:canonical=pqcoin::pow::encode_block(pqcoin::pow::decode_block(bytes));break;
    case 2:canonical=pqcoin::encode_transfer(pqcoin::decode_transfer(bytes));break;
    }
  }catch(const std::runtime_error&){return 0;}
  if(canonical.size()!=bytes.size() || !std::equal(canonical.begin(),canonical.end(),bytes.begin())) std::abort();
  return 0;
}
