// Independent, deliberately simple host reference. Used only for winners/tests.
const MASK=(1n<<64n)-1n;
const RC=['1','8082','800000000000808a','8000000080008000','808b','80000001','8000000080008081','8000000000008009','8a','88','80008009','8000000a','8000808b','800000000000008b','8000000000008089','8000000000008003','8000000000008002','8000000000000080','800a','800000008000000a','8000000080008081','8000000000008080','80000001','8000000080008008'].map(x=>BigInt('0x'+x));
const R=[0,1,62,28,27,36,44,6,55,20,3,10,43,25,39,41,45,15,21,8,18,2,61,56,14];
const rol=(v,n)=>((v<<BigInt(n))|(v>>BigInt(64-n)))&MASK;
export function sha3(bytes){
 const a=Array(25).fill(0n),padded=new Uint8Array(Math.ceil((bytes.length+1)/136)*136);
 padded.set(bytes);padded[bytes.length]=6;padded[padded.length-1]|=128;
 for(let offset=0;offset<padded.length;offset+=136){
  for(let i=0;i<136;i++)a[i>>3]^=BigInt(padded[offset+i])<<BigInt((i%8)*8);
  for(const rc of RC){
   const c=Array.from({length:5},(_,x)=>a[x]^a[x+5]^a[x+10]^a[x+15]^a[x+20]);
   const d=c.map((_,x)=>c[(x+4)%5]^rol(c[(x+1)%5],1)),b=Array(25);
   for(let y=0;y<5;y++)for(let x=0;x<5;x++)b[y+5*((2*x+3*y)%5)]=rol(a[x+5*y]^d[x],R[x+5*y]);
   for(let y=0;y<5;y++)for(let x=0;x<5;x++)a[x+5*y]=(b[x+5*y]^((~b[(x+1)%5+5*y])&b[(x+2)%5+5*y]))&MASK;
   a[0]^=rc;
  }
 }
 return Uint8Array.from({length:32},(_,i)=>Number((a[i>>3]>>BigInt(i%8*8))&255n));
}
export function qualifies(digest,target){
 for(let i=0;i<32;i++){if(digest[i]<target[i])return true;if(digest[i]>target[i])return false;}
 return true;
}
export const hex=bytes=>Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');
