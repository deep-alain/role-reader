import {pageToRecord} from '../extension/core.js';
const page=await roleReaderExtractVisiblePage();
const record=pageToRecord(page,location.href),checks={status:record.status,age:record.age,gender:record.gender,promptKeepsDemographics:record.prompt.includes('Age: 27'),promptKeepsPersonality:record.prompt.includes('耐心、谨慎'),hiddenExcluded:!page.visibleText.includes('这里是隐藏设定'),commentsExcluded:!record.intro.includes('这里是评论')};
const output=document.createElement('pre');output.id='testResult';output.textContent=JSON.stringify({checks,record},null,2);document.body.append(output);
