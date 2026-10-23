import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
const source=await fs.readFile(new URL('../extension/app.js',import.meta.url),'utf8');
const helpers=source.slice(source.indexOf('function portraitStatus('),source.indexOf('function concisePreferenceReason('));
test('downloaded but uncertain portraits are distinct from network failures',()=>{
 const context=vm.createContext({});vm.runInContext(helpers,context);
 assert.match(context.portraitStatus({avatarAsset:{review:'pending'},pendingReason:'人像或画风判断不清'}),/头像已读取.*人像或画风/);
 assert.match(context.portraitStatus({avatarError:'network'}),/读取失败/);
 assert.match(context.portraitStatus({avatarAsset:{review:'approved'}}),/已核验/);
});
test('failed original preview cannot enable approval and a successful reload restores it',()=>{
 const context=vm.createContext({});vm.runInContext(helpers,context);
 const image={},caption={},review={disabled:false};
 context.loadReviewImage(image,'/preview',caption,review);assert.equal(review.disabled,true);
 image.onerror();assert.equal(review.disabled,true);assert.match(caption.textContent,/加载失败/);
 context.loadReviewImage(image,'/preview?v=new',caption,review);assert.equal(review.disabled,true);
 image.onload();assert.equal(review.disabled,false);assert.match(caption.textContent,/原图已加载/);
});
