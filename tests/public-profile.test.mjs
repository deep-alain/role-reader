import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs/promises';
const code=await fs.readFile(new URL('../extension/public-profile.js',import.meta.url),'utf8');
function read(fields,{hidden=[],tags=['Adult','Female'],title='Robin - Explore this AI Chatbot on Spicychat'}={}){
 const labels=Object.entries(fields).map(([label,value])=>({innerText:label,hidden:hidden.includes(label),getClientRects:()=>[{}],nextElementSibling:{innerText:value,getClientRects:()=>[{}]}}));
 const doc={title,images:[],querySelector:()=>null,querySelectorAll:selector=>selector.startsWith('a[')?tags.map(tag=>({href:'https://spicychat.ai/chatbot_tag/'+tag,innerText:tag,getClientRects:()=>[{}]})):labels};
 const context=vm.createContext({URL,getComputedStyle:el=>({visibility:el.hidden?'hidden':'visible'})});vm.runInContext(code,context);return context.roleReaderPublicProfile(doc,'https://spicychat.ai/chatbot/example');
}
test('translated public labels map separately and preserve a missing scenario',()=>{
 const r=read({'性格':'Age: 28. A thoughtful adult roommate.','问候':'Welcome home.','示例对话':'Hello.\nSHOW LESS'});
 assert.equal(r.publicFields.name,'Robin');assert.equal(r.publicFields.age,'28');assert.equal(r.publicFields.gender,'Female');assert.equal(r.publicFields.scenario,'');assert.match(r.publicFields.prompt,/\[Example dialogue\]/);assert.doesNotMatch(r.publicFields.prompt,/SHOW LESS/);assert.equal(r.definitionPublic,true);
});
test('a hidden personality is never returned or inferred from greeting',()=>{
 const r=read({'Personality':'Private adult definition','Greeting':'A public greeting.'},{hidden:['Personality']});
 assert.equal(r.publicFields.prompt,'');assert.equal(r.definitionPublic,false);assert.equal(r.publicFields.greet,'A public greeting.');
});
test('a numeric adult age does not override a school setting',()=>{
 const r=read({'Personality':'Age: 18. High school character.'});assert.equal(r.ageBlocked,true);
});
