/* Runs in Chrome's isolated content-script world; reads public DOM only. */
globalThis.roleReaderPublicProfile=function(doc=document,url=location.href){
 const host=new URL(url).hostname;if(host!=='spicychat.ai')return null;
 const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
 const labelMap=new Map([['Greeting','greet'],['问候','greet'],['問候','greet'],['Personality','prompt'],['性格','prompt'],['个性','prompt'],['個性','prompt'],['Scenario','scenario'],['场景','scenario'],['場景','scenario'],['Example Dialogues','examples'],['Example Dialogue','examples'],['示例对话','examples'],['对话示例','examples'],['對話範例','examples']]);
 const extracted={};
 for(const label of doc.querySelectorAll('p,h2,h3,h4')){const key=labelMap.get(label.innerText?.trim());if(!key||!visible(label))continue;const next=label.nextElementSibling;if(!visible(next))continue;const value=next.innerText.replace(/\n?\s*(?:SHOW LESS|SHOW MORE|显示较少|显示更多)\s*$/i,'').trim();if(value)extracted[key]=value}
 const meta=k=>doc.querySelector(`meta[property="${k}"],meta[name="${k}"]`)?.content||'';
 const tagLinks=Array.from(doc.querySelectorAll('a[href*="/chatbot_tag/"]')).filter(visible),tags=tagLinks.map(a=>decodeURIComponent(new URL(a.href).pathname.split('/').pop()).replace(/-/g,' '));
 const name=(meta('og:title')||doc.title).replace(/\s*-\s*Explore this AI Chatbot.*$/i,'').trim()||doc.querySelector('h1')?.innerText||'';
 const prompt=[extracted.prompt,extracted.examples?'[Example dialogue]\n'+extracted.examples:''].filter(Boolean).join('\n\n');
 const demographic=prompt.match(/(?:\bAge\b\s*[:：=\-(]\s*|\b)(\d{1,3})\s*(?:years?[- ]old|y\/?o|岁|歳)/i)||prompt.match(/(?:\bage\b|年龄)\s*[:：=\-(]\s*(\d{1,3})/i);
 const gender=tags.some(t=>t.toLowerCase()==='female')?'Female':tags.some(t=>t.toLowerCase()==='male')?'Male':tags.some(t=>t.toLowerCase()==='non binary')?'Non-binary':'';
 return {url,definitionPublic:!!extracted.prompt,sourceRating:tags.some(t=>t.toLowerCase()==='nsfw')?'NSFW':'unknown',ageBlocked:/\b(?:underage|high school|middle school|loli|shota)\b|未成年|高中生|初中生/i.test(prompt),publicFields:{name,category:tags.join(', '),avatar:meta('og:image')||Array.from(doc.images).find(i=>visible(i)&&/avatar|头像/i.test(i.alt))?.src||'',gender,age:demographic?.[1]||'',scenario:extracted.scenario||'',greet:extracted.greet||'',intro:meta('og:description')||meta('description'),prompt,creator:doc.querySelector('a[href*="/creator/"]')?.innerText||''}};
};
