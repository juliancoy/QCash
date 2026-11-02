#include <cstdint>
#include <wasm_simd128.h>
using V = v128_t;
static std::uint8_t header[268], output[4096 * 32];
static V prefix[25], tail[15];
static std::uint64_t tail15;
static constexpr std::uint64_t RC[24] = {
    0x1ULL, 0x8082ULL, 0x800000000000808aULL, 0x8000000080008000ULL,
    0x808bULL, 0x80000001ULL, 0x8000000080008081ULL, 0x8000000000008009ULL,
    0x8aULL, 0x88ULL, 0x80008009ULL, 0x8000000aULL, 0x8000808bULL,
    0x800000000000008bULL, 0x8000000000008089ULL, 0x8000000000008003ULL,
    0x8000000000008002ULL, 0x8000000000000080ULL, 0x800aULL,
    0x800000008000000aULL, 0x8000000080008081ULL, 0x8000000000008080ULL,
    0x80000001ULL, 0x8000000080008008ULL};
static constexpr unsigned RHO[25] = {0,1,62,28,27,36,44,6,55,20,3,10,43,25,39,41,45,15,21,8,18,2,61,56,14};
static inline V x(V a,V b){return wasm_v128_xor(a,b);}
static inline V rot(V a,unsigned n){return n?wasm_v128_or(wasm_i64x2_shl(a,n),wasm_u64x2_shr(a,64-n)):a;}
static inline V chi(V a,V b,V c){return x(a,wasm_v128_andnot(c,b));}
static std::uint64_t load64(const std::uint8_t *p){
    std::uint64_t v=0;
#pragma clang loop unroll(full)
    for(unsigned i=0;i<8;i++)v|=std::uint64_t(p[i])<<(8*i);
    return v;
}
static inline void theta(V *a,V *d){
    V c[5];
#pragma clang loop unroll(full)
    for(unsigned i=0;i<5;i++)c[i]=x(x(x(x(a[i],a[i+5]),a[i+10]),a[i+15]),a[i+20]);
#pragma clang loop unroll(full)
    for(unsigned i=0;i<5;i++)d[i]=x(c[(i+4)%5],rot(c[(i+1)%5],1));
}
static void permute(V *a,unsigned rounds){
#pragma clang loop unroll(disable)
    for(unsigned round=0;round<rounds;round++){
        V d[5],b[25];theta(a,d);
#pragma clang loop unroll(full)
        for(unsigned y=0;y<5;y++){
#pragma clang loop unroll(full)
            for(unsigned xx=0;xx<5;xx++)b[y+5*((2*xx+3*y)%5)]=rot(x(a[xx+5*y],d[xx]),RHO[xx+5*y]);
        }
#pragma clang loop unroll(full)
        for(unsigned y=0;y<5;y++){
#pragma clang loop unroll(full)
            for(unsigned xx=0;xx<5;xx++)a[xx+5*y]=chi(b[xx+5*y],b[(xx+1)%5+5*y],b[(xx+2)%5+5*y]);
        }
        a[0]=x(a[0],wasm_i64x2_splat(RC[round]));
    }
}
static void final_round(V *a){
    V d[5],r[5];theta(a,d);
#pragma clang loop unroll(full)
    for(unsigned i=0;i<5;i++)r[i]=rot(x(a[6*i],d[i]),RHO[6*i]);
#pragma clang loop unroll(full)
    for(unsigned i=0;i<4;i++)a[i]=chi(r[i],r[(i+1)%5],r[(i+2)%5]);
    a[0]=x(a[0],wasm_i64x2_splat(RC[23]));
}
template<unsigned Lane> static bool qualifies(V *a){
#pragma clang loop unroll(full)
    for(unsigned word=0;word<4;word++){
        auto v=std::uint64_t(wasm_i64x2_extract_lane(a[word],Lane));
        for(unsigned byte=0;byte<8;byte++){
            auto b=std::uint8_t(v>>(byte*8));auto target=header[100+word*8+byte];
            if(b<target)return true;
            if(b>target)return false;
        }
    }
    return true;
}
template<unsigned Lane> static void capture(V *a,unsigned index){
#pragma clang loop unroll(full)
    for(unsigned word=0;word<4;word++){
        auto v=std::uint64_t(wasm_i64x2_extract_lane(a[word],Lane));
#pragma clang loop unroll(full)
        for(unsigned byte=0;byte<8;byte++)output[index*32+word*8+byte]=std::uint8_t(v>>(byte*8));
    }
}
template<bool Capture> static std::uint32_t search(std::uint64_t start,std::uint32_t count){
    std::uint32_t winner=0xffffffffU;
    for(std::uint32_t i=0;i<count;i+=2){
        const std::uint64_t n0=start+i,n1=n0+1;
        V a[25];
#pragma clang loop unroll(full)
        for(unsigned j=0;j<25;j++)a[j]=prefix[j];
#pragma clang loop unroll(full)
        for(unsigned j=0;j<15;j++)a[j]=x(a[j],tail[j]);
        a[15]=x(a[15],wasm_i64x2_make(tail15|(std::uint64_t(__builtin_bswap32(n0>>32))<<32),tail15|(std::uint64_t(__builtin_bswap32(n1>>32))<<32)));
        a[16]=x(a[16],wasm_i64x2_make(0x8000000600000000ULL|__builtin_bswap32(n0),0x8000000600000000ULL|__builtin_bswap32(n1)));
        permute(a,23);final_round(a);
        if constexpr(Capture)capture<0>(a,i);
        if(winner==0xffffffffU&&qualifies<0>(a))winner=i;
        if(i+1<count){
            if constexpr(Capture)capture<1>(a,i+1);
            if(winner==0xffffffffU&&qualifies<1>(a))winner=i+1;
        }
    }
    return winner;
}
extern "C" {
std::uint8_t *get_header(){return header;}
std::uint8_t *get_output(){return output;}
void prepare(){
    V a[25];
#pragma clang loop unroll(full)
    for(unsigned i=0;i<25;i++)a[i]=wasm_i64x2_splat(i<17?load64(header+8*i):0);
    permute(a,24);
#pragma clang loop unroll(full)
    for(unsigned i=0;i<25;i++)prefix[i]=a[i];
#pragma clang loop unroll(full)
    for(unsigned i=0;i<15;i++)tail[i]=wasm_i64x2_splat(load64(header+136+8*i));
    tail15=load64(header+256)&0xffffffffULL;
}
std::uint32_t scan(std::uint32_t low,std::uint32_t high,std::uint32_t count,std::uint32_t debug){
    if(count==0||count>16777216U||(debug&&count>4096U))return 0xffffffffU;
    const auto start=std::uint64_t(low)|(std::uint64_t(high)<<32);
    if(start>UINT64_MAX-(count-1))return 0xffffffffU;
    return debug?search<true>(start,count):search<false>(start,count);
}
}
