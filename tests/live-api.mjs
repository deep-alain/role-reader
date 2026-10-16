// Opt-in live integration test: only metadata lengths are printed. No role content is exported.
import {readApiCharacter,searchApi} from '../extension/apis.js';
import fs from 'node:fs/promises';
const report=[];
const fetcher=process.env.RR_API_BASE?(url,options)=>fetch(process.env.RR_API_BASE+'/api/fetch?url='+encodeURIComponent(url),options):fetch;
for(const source of ['chub','charavault','botbooru']){
 const start=Date.now();
 try{const list=await searchApi(source,{query:'',page:1,limit:1},{fetcher});if(!list.items[0])throw new Error('No public results');const r=await readApiCharacter(list.items[0].url,{fetcher});report.push({source,ok:true,listEndpoint:list.endpoint,detailEndpoint:r.raw.apiEndpoint,status:r.status,lengths:Object.fromEntries(['name','avatar','scenario','greet','intro','prompt','gender','age'].map(k=>[k,r[k].length])),missing:r.missing,seconds:(Date.now()-start)/1000});}
 catch(e){report.push({source,ok:false,error:e.message,seconds:(Date.now()-start)/1000});}
 console.log(JSON.stringify(report.at(-1)));
}
await fs.mkdir(new URL('../.test-output/',import.meta.url),{recursive:true});
await fs.writeFile(new URL('../.test-output/live-api-report.json',import.meta.url),JSON.stringify({verifiedAt:new Date().toISOString(),report},null,2));
if(report.some(r=>!r.ok))process.exitCode=1;
