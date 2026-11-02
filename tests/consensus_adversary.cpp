#include <cmath>
#include <iostream>
#include <pqcoin/pow.hpp>
#include <stdexcept>
using namespace pqcoin;
namespace p = pqcoin::pow;
void require(bool ok) { if (!ok) throw std::runtime_error("adversarial consensus regression"); }
struct Result { std::string honest_work, private_work; double honest_cost=0, private_cost=0; bool displaced=false; };
Result scenario(bool fixed) {
  auto config=p::development_config({},1000);
  config.interval=fixed?0:4; config.spacing=10;
  p::Chain chain(config);
  Hash miner{}; miner[0]=1;
  const auto root=chain.context();
  Result result;
  auto append=[&](Hash parent, std::uint64_t timestamp, double &cost) {
    auto block=chain.candidate(parent,miner,timestamp);
    require(p::header(block).size()==268);
    while(!p::mine_batch(block,10000)) {}
    require(chain.accept(block,100000));
    cost+=std::sqrt(std::stod(p::work_decimal(p::block_work(block.target))));
    return p::id(block);
  };
  auto honest=root;
  for(unsigned i=1;i<=16;++i) honest=append(honest,1000+i*40/3,result.honest_cost);
  result.honest_work=p::work_decimal(chain.at(honest).work);
  auto attacker=root;
  for(unsigned i=1;i<=8;++i) attacker=append(attacker,1000+i,result.private_cost);
  result.private_work=p::work_decimal(chain.at(attacker).work);
  result.displaced=chain.tip()==attacker;
  if(fixed) {require(!result.displaced); require(chain.tip()==honest);}
  else {require(result.displaced);require(result.private_cost<result.honest_cost);}
  p::Chain replay(config);
  for(const auto &block:chain.after({root},16)) require(replay.accept(block,100000));
  require(replay.tip()==chain.tip());
  require(p::state_hash(replay.best().state)==p::state_hash(chain.best().state));
  return result;
}
void emit(const Result &r) {
 std::cout<<"{\"honest_blocks\":16,\"private_blocks\":8,\"honest_work\":\""<<r.honest_work
 <<"\",\"private_work\":\""<<r.private_work<<"\",\"honest_sqrt_work_proxy\":"<<r.honest_cost
 <<",\"private_sqrt_work_proxy\":"<<r.private_cost<<",\"private_fork_selected\":"<<(r.displaced?"true":"false")<<"}";
}
int main(){try {
 auto legacy=scenario(false),fixed=scenario(true);
 require(legacy.honest_work=="65536" && legacy.private_work=="143360");
 std::cout<<"{\"status\":\"reproduced_with_native_valid_proofs\",\"retarget\":";emit(legacy);
 std::cout<<",\"fixed_target\":";emit(fixed);
 std::cout<<",\"limitation\":\"sqrt(work) is an idealized Grover query proxy; no hardware timing, race probability, or all-quantum security proof\"}\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
