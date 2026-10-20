// One selector is shared by rendering and exporting the current list.
export function visibleRecords(records,{query='',filter='all',rating='all',order='rules'}={}){
 const visible=records.filter(r=>(filter==='all'||r.status===filter)&&(rating==='all'||(r.sourceRating||'unknown')===rating)&&[r.name,r.category,r.source].join(' ').toLowerCase().includes(query.toLowerCase()));
 if(!['original','rules'].includes(order)){
  const keys=order==='popular'?['favorites','views','downloads']:[order];
  visible.sort((a,b)=>{for(const key of keys){const diff=(b.sourceMetrics?.[key]??-1)-(a.sourceMetrics?.[key]??-1);if(diff)return diff}return 0});
 }
 return visible;
}
export function requestGate(){
 let generation=0;
 return {begin(view){return {generation:++generation,view}},accept(token,view){return token.generation===generation&&token.view===view},invalidate(){generation++}};
}
