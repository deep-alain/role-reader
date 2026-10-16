async function roleReaderExtractVisiblePage(){
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 let previous='',stable=0;
 for(let i=0;i<8;i++){const now=document.body?.innerText||'';if(now.length>previous.length-10&&now===previous)stable++;else stable=0;previous=now;if(stable>=2&&now.length>100)break;await sleep(1000)}
 const body=(document.body?.innerText||'').slice(0,250000),title=document.title;
 if(/just a moment|attention required|access denied|checking your browser|verify you are human|安全验证|访问被拒绝|人机验证|captcha/i.test(title)||body.length<800&&/verify you are human|checking your browser|cloudflare ray id|sign in to continue|log in to continue|请先登录|人机验证|访问受限/i.test(body))return {error:'页面要求登录或访问验证，已停止读取此页面。',title,visibleText:body,url:location.href,sections:[]};
 const visible=el=>!!(el&&el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden');
 const profile=globalThis.roleReaderPublicProfile?.();
 if(profile&&profile.publicFields.name&&location.hostname==='spicychat.ai'){
  const f=profile.publicFields;return {...profile,name:f.name,avatar:f.avatar,description:f.intro,tags:f.category.split(',').map(s=>s.trim()),creator:f.creator,sections:[['Name',f.name],['Category',f.category],['Gender',f.gender],['Age',f.age],['Scenario',f.scenario],['Greeting',f.greet],['Description',f.intro],['Personality',f.prompt]].filter(([,value])=>value).map(([label,value])=>({label,value})),title,url:location.href,visibleText:'',notes:'仅提取已登录页面公开显示的字段；浏览器翻译可能影响文本语言。作者隐藏的人设不读取。'}
 }
 const meta=key=>document.querySelector(`meta[property="${key}"],meta[name="${key}"]`)?.content||'';
 const names=['角色类别','类别','分类','Tags','Category','角色名称','Name','性别','Gender','角色年龄','年龄','Age','Scenario','场景','情境','角色greet','Greeting','First message','Initial message','开场白','第一条消息','角色介绍','Character bio','About this character','Description','角色描述','介绍','角色prompt','Character definition','Character personality','Personality','System prompt','角色定义','人设','性格'];
 const lower=names.map(s=>s.toLowerCase()),sections=[];let current=null;
 const finish=()=>{if(current?.values.length)sections.push({label:current.label,value:current.values.join('\n').trim()});current=null};
 for(const line of body.split('\n').map(s=>s.trim()).filter(Boolean)){
  const match=line.match(/^(.{1,40}?)[：:]\s*(.*)$/),clean=line.replace(/\s*\(?\d[\d,.]*\s*tokens?\)?$/i,'').replace(/[:：]$/,'').trim();
  const index=lower.indexOf(clean.toLowerCase()),inline=match?lower.indexOf(match[1].trim().toLowerCase()):-1;
  if(inline>=0&&/^(Gender|性别|Age|年龄|角色年龄)$/i.test(names[inline])&&match[2]){if(current)current.values.push(line);sections.push({label:names[inline],value:match[2]});continue}
  if(index>=0||inline>=0){finish();current={label:names[index>=0?index:inline],values:inline>=0&&match[2]?[match[2]]:[]};continue}
  if(/^(comments?|reviews?|similar characters?|related characters?|more from|用户评论|相关推荐|作者的其他角色|character stats|聊天统计)$/i.test(line)){finish();continue}
  if(current)current.values.push(line);
 }finish();
 const headings=[...document.querySelectorAll('h1')].filter(visible),heading=headings.find(h=>!/janitor|crushon|sign in|login|登录|loading/i.test(h.innerText));
 const ogTitle=meta('og:title').replace(/\s*[-|–]\s*(?:Janitor\s*AI|CrushOn(?:\.AI)?|Character\.AI).*$/i,'');
 const name=(heading?.innerText||ogTitle||'').trim();
 let avatar=meta('og:image');
 if(!avatar&&name){const img=[...document.images].filter(visible).find(i=>i.alt?.toLowerCase().includes(name.toLowerCase())&&i.naturalWidth>=100);avatar=img?.currentSrc||img?.src||''}
 try{avatar=avatar?new URL(avatar,location.href).href:''}catch{avatar=''}
 const jsonLd=[];for(const s of document.querySelectorAll('script[type="application/ld+json"]')){try{const j=JSON.parse(s.textContent);jsonLd.push(j)}catch{}}
 // 只读取公开页面元数据，不访问内部状态、请求体或隐藏的人设字段。
 let description=meta('og:description')||meta('description');
 const main=document.querySelector('main,[role="main"]');
 if(main&&main.innerText.length<80&&!sections.length)return {error:'页面尚未提供角色正文。请手动打开并展开公开资料后使用“读取原页面”，或导入角色卡。',url:location.href,title,visibleText:body,sections:[]};
 return {name,avatar,description,sections,title,url:location.href,jsonLd,visibleText:body,notes:'只读取可见文字与公开网页元数据。页面未显示的定义、Scenario、开场白会保留为空。'};
}
roleReaderExtractVisiblePage();
