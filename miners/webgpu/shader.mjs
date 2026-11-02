// Generate constant-index state accesses so WGSL never dynamically indexes
// a private Keccak state. u32 pairs implement portable exact 64-bit operations.
export const RC = ['1','8082','800000000000808a','8000000080008000','808b','80000001','8000000080008081','8000000000008009','8a','88','80008009','8000000a','8000808b','800000000000008b','8000000000008089','8000000000008003','8000000000008002','8000000000000080','800a','800000008000000a','8000000080008081','8000000000008080','80000001','8000000080008008'].map(x=>BigInt('0x'+x));
const RHO=[0,1,62,28,27,36,44,6,55,20,3,10,43,25,39,41,45,15,21,8,18,2,61,56,14];
const pair=n=>`vec2<u32>(${Number(n&0xffffffffn)}u,${Number(n>>32n)}u)`;
function rot(v,n){
  const p=n>=32?`(${v}).yx`:v; n%=32;
  return n?`((${p} << vec2<u32>(${n}u)) | ((${p}).yx >> vec2<u32>(${32-n}u)))`:p;
}
function theta(){
 let s='';
 for(let x=0;x<5;x++)s+=`let c${x}=a[${x}]^a[${x+5}]^a[${x+10}]^a[${x+15}]^a[${x+20}];\n`;
 for(let x=0;x<5;x++)s+=`let d${x}=c${(x+4)%5}^${rot(`c${(x+1)%5}`,1)};\n`;
 return s;
}
function fullRound(){
 let s=theta();
 for(let y=0;y<5;y++)for(let x=0;x<5;x++){const i=x+5*y;s+=`let b${y+5*((2*x+3*y)%5)}=${rot(`(a[${i}]^d${x})`,RHO[i])};\n`;}
 for(let y=0;y<5;y++)for(let x=0;x<5;x++)s+=`a[${x+5*y}]=b${x+5*y}^((~b${(x+1)%5+5*y})&b${(x+2)%5+5*y});\n`;
 return s;
}
export function shader({workgroup=64,pruned=true}={}){
 if(![32,64,128,256].includes(workgroup))throw RangeError('Invalid workgroup');
 const constants=RC.map((r,i)=>`case ${i}u: { return ${pair(r)}; }`).join('\n');
 let prefix=Array.from({length:25},(_,i)=>`a[${i}]=${i<17?`vec2<u32>(header[${2*i}],header[${2*i+1}])`:'vec2<u32>(0u)'};`).join('\n');
 let mining=Array.from({length:25},(_,i)=>`a[${i}]=midstate[${i}];`).join('\n');
 for(let i=0;i<17;i++){
  let lo=i===16?'swap(nonceLo)':`header[${34+i*2}]`;
  let hi=i===15?'swap(nonceHi)':i===16?'0x80000006u':`header[${35+i*2}]`;
  mining+=`a[${i}]^=vec2<u32>(${lo},${hi});\n`;
 }
 const last=theta()+Array.from({length:5},(_,x)=>`let r${x}=${rot(`(a[${6*x}]^d${x})`,RHO[6*x])};`).join('\n')+Array.from({length:4},(_,x)=>`a[${x}]=r${x}^((~r${(x+1)%5})&r${(x+2)%5});`).join('\n')+`a[0]^=${pair(RC[23])};`;
 let output='var less=false; var equal=true;\n';
 for(let i=0;i<8;i++)output+=`let w${i}=swap(a[${Math.floor(i/2)}].${i%2?'y':'x'});\nif CAPTURE { result.digests[index*8u+${i}u]=w${i}; }\nlet t${i}=swap(header[${25+i}]);\nif equal && w${i}<t${i} { less=true; }\nif w${i}!=t${i} { equal=false; }\n`;
 return `override CAPTURE:bool=false;
@group(0) @binding(0) var<storage,read> header:array<u32>;
@group(0) @binding(1) var<storage,read_write> midstate:array<vec2<u32>>;
struct Params {low:u32,high:u32,count:u32,pad:u32}
@group(0) @binding(2) var<uniform> params:Params;
struct Result {winner:atomic<u32>,p0:u32,p1:u32,p2:u32,digests:array<u32>}
@group(0) @binding(3) var<storage,read_write> result:Result;
fn swap(v:u32)->u32 {return ((v&255u)<<24u)|((v&65280u)<<8u)|((v>>8u)&65280u)|(v>>24u);}
fn rc(round:u32)->vec2<u32> {switch round {${constants}\ndefault:{return vec2<u32>(0u);}}}
@compute @workgroup_size(1) fn prepare(){var a:array<vec2<u32>,25>;${prefix}
for(var round=0u;round<24u;round++){${fullRound()}a[0]^=rc(round);}
${Array.from({length:25},(_,i)=>`midstate[${i}]=a[${i}];`).join('\n')}}
@compute @workgroup_size(1) fn reset(){atomicStore(&result.winner,0xffffffffu);}
@compute @workgroup_size(${workgroup}) fn mine(@builtin(global_invocation_id) id:vec3<u32>){
let index=params.pad+id.x;if index>=params.count{return;}
let nonceLo=params.low+index;let nonceHi=params.high+select(0u,1u,nonceLo<params.low);
var a:array<vec2<u32>,25>;${mining}
for(var round=0u;round<${pruned?23:24}u;round++){${fullRound()}a[0]^=rc(round);}
${pruned?last:''}
${output}if less || equal {atomicMin(&result.winner,index);}}
`;
}
