// Extract the pure water-balance functions from index.html and test them (V3).
const fs=require('fs');
const src=fs.readFileSync(''+require('path').join(__dirname,'..','index.html')+'','utf8');
const a=src.indexOf('const FAO56_PARAMS='), b=src.indexOf('// ── Irrigation / rain log');
const code=src.slice(a,b).replace(/window\./g,'globalThis.');
const mod={}; new Function('mod',code+';mod.P=FAO56_PARAMS;mod.step=fao56Step;mod.taw=fao56TAW;mod.vwc=fao56ToVWC;mod.kc=ndviToKc;')(mod);
const {P,step,taw,vwc,kc}=mod; let ok=0; const eq=(n,x,y,t=1e-6)=>{ if(Math.abs(x-y)>t){console.log('FAIL',n,x,y);process.exitCode=1}else{ok++;console.log('ok  ',n,x)} };
// hand calcs with FC28 WP13 Zr0.4 p0.5  => TAW=60 mm, RAW=30 mm
eq('TAW',taw(),60); 
eq('dry day, Dr below RAW: ETc=Kc*ET0',step(10,{et0:5,kc:0.8}),14);
eq('Ks<1 above RAW: Dr=45 -> Ks=0.5',step(45,{et0:5,kc:1}),47.5);
eq('rain <5mm ignored',step(10,{rain:4.9,et0:0}),10);
eq('rain 20mm -> 18 effective (10% runoff)',step(30,{rain:20,et0:0}),12);
eq('clamp at 0 (cannot go below FC)',step(5,{rain:50,et0:0}),0);
eq('near TAW Ks=1/30 throttles ET (59 -> 59.333)',step(59,{et0:10,kc:1}),59.3333333333);
eq('hard clamp at TAW',step(60,{et0:10,kc:1}),60);
eq('hard clamp at TAW with big ET',step(55,{et0:400,kc:1}),60);
eq('irrigation subtracts',step(20,{irrig:12,et0:0}),8);
eq('VWC at Dr=0 is FC',vwc(0),28); eq('VWC at Dr=TAW is WP',vwc(60),13);
eq('Kc stale -> fallback',kc(0.5,25),0.6); eq('Kc missing -> fallback',kc(null,2),0.6);
eq('Kc NDVI .5',kc(0.5,2),0.825); eq('Kc low NDVI clamped to kcMin',kc(0.0,2),0.3);
// multi-day: 10 dry days of ET0=5, Kc=0.8 from field capacity, then check never exceeds TAW & monotonic
let Dr=0; for(let i=0;i<30;i++){const n=step(Dr,{et0:5,kc:0.8}); if(n<Dr-1e-9){console.log('FAIL not monotonic');process.exitCode=1} Dr=n;} if(!(Dr>58&&Dr<=60)){console.log('FAIL asymptote',Dr);process.exitCode=1}else{ok++;console.log('ok   30 dry days approach TAW without exceeding it',Dr)}
console.log(ok,'checks passed');
