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
Lane parity5(Lane a,Lane b,Lane c,Lane d,Lane e){
#ifdef BALANCED_PARITY
    // Two independent first-level XORs rather than a four-operation chain.
    return ((a^b)^(c^d))^e;
#else
    return a^b^c^d^e;
#endif
}
void keccak(inout Lane a[25]) {
#ifdef UNROLL_PAIRS
    for(uint pair=0;pair<12;pair++){
    [[unroll]] for(uint subround=0;subround<2;subround++){
    uint round=pair*2+subround;
#else
#ifdef PRUNE_FINAL
    for(uint round=0;round<23;round++){
#else
    for(uint round=0;round<24;round++){
#endif
#endif
#if defined(ROW_FUSED)
        // Fuse rho/pi and chi by destination row. Only five rotated lanes are
        // live at once; keep output separate until all source rows are read.
        Lane c[5],d[5],out_state[25];
        [[unroll]] for(uint x=0;x<5;x++)c[x]=parity5(a[x],a[x+5],a[x+10],a[x+15],a[x+20]);
        [[unroll]] for(uint x=0;x<5;x++)d[x]=c[(x+4)%5]^rol(c[(x+1)%5],1);
        [[unroll]] for(uint y=0;y<5;y++){
            Lane row[5];
            [[unroll]] for(uint x=0;x<5;x++){
                uint sx=(x+3*y)%5;
                uint source=sx+5*x;
                row[x]=rol(a[source]^d[sx],RHO[source]);
            }
            [[unroll]] for(uint x=0;x<5;x++)out_state[x+5*y]=row[x]^((~row[(x+1)%5])&row[(x+2)%5]);
        }
        [[unroll]] for(uint i=0;i<25;i++)a[i]=out_state[i];
#elif defined(COMPACT)
        Lane c[5];
        [[unroll]] for(uint x=0;x<5;x++)c[x]=parity5(a[x],a[x+5],a[x+10],a[x+15],a[x+20]);
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
#elif defined(COLUMN_SCHEDULE)
        // Consume each theta correction immediately in independent rotations.
        // Avoid keeping five corrections live across all 25 rho/pi operations.
        Lane c[5],b[25];
        [[unroll]] for(uint x=0;x<5;x++)c[x]=parity5(a[x],a[x+5],a[x+10],a[x+15],a[x+20]);
        [[unroll]] for(uint x=0;x<5;x++){
            Lane d=c[(x+4)%5]^rol(c[(x+1)%5],1);
            [[unroll]] for(uint y=0;y<5;y++){
                uint i=x+5*y;b[y+5*((2*x+3*y)%5)]=rol(a[i]^d,RHO[i]);
            }
        }
        [[unroll]] for(uint y=0;y<5;y++){
            [[unroll]] for(uint x=0;x<5;x++)a[x+5*y]=b[x+5*y]^((~b[(x+1)%5+5*y])&b[(x+2)%5+5*y]);
        }
#else
        Lane c[5],d[5],b[25];
        [[unroll]] for(uint x=0;x<5;x++)c[x]=parity5(a[x],a[x+5],a[x+10],a[x+15],a[x+20]);
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
#ifdef UNROLL_PAIRS
    }
#endif
#ifdef PRUNE_FINAL
    // SHA3-256 consumes only lanes 0..3 after the last permutation round.
    // Theta needs the full input state; rho/pi/chi need just destination row 0.
#ifdef ROLLING_FINAL
    // Consume each last-round rotation as soon as its chi dependencies exist.
    // Slots 1..3 are safe to overwrite: only input diagonals 0,6,12,18,24 remain.
    Lane parity[5];
    [[unroll]] for(uint x=0;x<5;x++)parity[x]=parity5(a[x],a[x+5],a[x+10],a[x+15],a[x+20]);
    Lane r0=a[0]^parity[4]^rol(parity[1],1);
    Lane r1=rol(a[6]^parity[0]^rol(parity[2],1),RHO[6]);
    Lane r2=rol(a[12]^parity[1]^rol(parity[3],1),RHO[12]);
    a[0]=r0^((~r1)&r2)^from64(RC[23]);
    Lane r3=rol(a[18]^parity[2]^rol(parity[4],1),RHO[18]);
    a[1]=r1^((~r2)&r3);
    Lane r4=rol(a[24]^parity[3]^rol(parity[0],1),RHO[24]);
    a[2]=r2^((~r3)&r4);
    a[3]=r3^((~r4)&r0);
#else
    Lane parity[5], delta[5], row[5];
    [[unroll]] for(uint x=0;x<5;x++)parity[x]=parity5(a[x],a[x+5],a[x+10],a[x+15],a[x+20]);
    [[unroll]] for(uint x=0;x<5;x++)delta[x]=parity[(x+4)%5]^rol(parity[(x+1)%5],1);
    [[unroll]] for(uint x=0;x<5;x++)row[x]=rol(a[6*x]^delta[x],RHO[6*x]);
    [[unroll]] for(uint x=0;x<4;x++)a[x]=row[x]^((~row[(x+1)%5])&row[(x+2)%5]);
    a[0]^=from64(RC[23]);
#endif
#endif
}
