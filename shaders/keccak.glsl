const uint64_t RC[24]=uint64_t[](
0x0000000000000001ul,0x0000000000008082ul,0x800000000000808aul,0x8000000080008000ul,
0x000000000000808bul,0x0000000080000001ul,0x8000000080008081ul,0x8000000000008009ul,
0x000000000000008aul,0x0000000000000088ul,0x0000000080008009ul,0x000000008000000aul,
0x000000008000808bul,0x800000000000008bul,0x8000000000008089ul,0x8000000000008003ul,
0x8000000000008002ul,0x8000000000000080ul,0x000000000000800aul,0x800000008000000aul,
0x8000000080008081ul,0x8000000000008080ul,0x0000000080000001ul,0x8000000080008008ul);
const uint RHO[25]=uint[](0,1,62,28,27,36,44,6,55,20,3,10,43,25,39,41,45,15,21,8,18,2,61,56,14);
#ifdef PAIR32
#define Lane uvec2
Lane from64(uint64_t v){return uvec2(uint(v),uint(v>>32));}
Lane rol(Lane v,uint n){
    if(n>=32){v=v.yx;n-=32;}
    if(n==0)return v;
    return (v<<n)|(v.yx>>(32-n));
}
uint word32(Lane v,uint high){return high==0?v.x:v.y;}
uint64_t to64(Lane v){return uint64_t(v.x)|(uint64_t(v.y)<<32);}
#else
#define Lane uint64_t
Lane from64(uint64_t v){return v;}
Lane rol(Lane v,uint n){return n==0?v:(v<<n)|(v>>(64-n));}
uint word32(Lane v,uint high){return uint(v>>(high*32));}
uint64_t to64(Lane v){return v;}
#endif
uint swap32(uint v){return ((v&0xffu)<<24)|((v&0xff00u)<<8)|((v>>8)&0xff00u)|(v>>24);}
const uint PI[24]=uint[](10,7,11,17,18,3,5,16,8,21,24,4,15,23,19,13,12,2,20,14,22,9,6,1);
const uint ROT[24]=uint[](1,3,6,10,15,21,28,36,45,55,2,14,27,41,56,8,25,43,62,18,39,61,20,44);
void keccak(inout Lane a[25]) {
    for(uint round=0;round<24;round++){
#ifdef COMPACT
        Lane c[5];
        [[unroll]] for(uint x=0;x<5;x++)c[x]=a[x]^a[x+5]^a[x+10]^a[x+15]^a[x+20];
        [[unroll]] for(uint x=0;x<5;x++){
            Lane d=c[(x+4)%5]^rol(c[(x+1)%5],1);
            [[unroll]] for(uint y=0;y<5;y++)a[x+5*y]^=d;
        }
        Lane t=a[1];
        [[unroll]] for(uint i=0;i<24;i++){
            uint at=PI[i];Lane saved=a[at];a[at]=rol(t,ROT[i]);t=saved;
        }
        [[unroll]] for(uint y=0;y<5;y++){
            [[unroll]] for(uint x=0;x<5;x++)c[x]=a[x+5*y];
            [[unroll]] for(uint x=0;x<5;x++)a[x+5*y]=c[x]^((~c[(x+1)%5])&c[(x+2)%5]);
        }
#else
        Lane c[5],d[5],b[25];
        [[unroll]] for(uint x=0;x<5;x++)c[x]=a[x]^a[x+5]^a[x+10]^a[x+15]^a[x+20];
        [[unroll]] for(uint x=0;x<5;x++)d[x]=c[(x+4)%5]^rol(c[(x+1)%5],1);
        [[unroll]] for(uint y=0;y<5;y++){
            [[unroll]] for(uint x=0;x<5;x++){
                uint i=x+5*y;b[y+5*((2*x+3*y)%5)]=rol(a[i]^d[x],RHO[i]);
            }
        }
        [[unroll]] for(uint y=0;y<5;y++){
            [[unroll]] for(uint x=0;x<5;x++)a[x+5*y]=b[x+5*y]^((~b[(x+1)%5+5*y])&b[(x+2)%5+5*y]);
        }
#endif
        a[0]^=from64(RC[round]);
    }
}
