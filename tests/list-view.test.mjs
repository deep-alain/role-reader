import test from 'node:test';
import assert from 'node:assert/strict';
import {visibleRecords,requestGate} from '../extension/list-view.js';

test('render and export share filters and sorted visible records without changing the shelf',()=>{
 const shelf=[{name:'Alpha',status:'complete',sourceRating:'NSFW',sourceMetrics:{messages:2}},{name:'Beta',status:'partial',sourceRating:'SFW'},{name:'Alpha 2',status:'complete',sourceRating:'NSFW',sourceMetrics:{messages:5}}];
 const visible=visibleRecords(shelf,{query:'ALPHA',filter:'complete',rating:'NSFW',order:'messages'});
 assert.deepEqual(visible.map(r=>r.name),['Alpha 2','Alpha']);assert.equal(shelf[0].name,'Alpha');
 assert.equal(visibleRecords(shelf,{query:'absent'}).length,0);
});
test('late requests cannot erase the current view at the same revision',()=>{
 const gate=requestGate(),old=gate.begin('pool'),fresh=gate.begin('used');
 assert.equal(gate.accept(fresh,'used'),true);assert.equal(gate.accept(old,'used'),false);assert.equal(gate.accept(fresh,'pending'),false);
});
test('a mutation invalidates outstanding polls and only the latest poll is accepted',()=>{
 const gate=requestGate(),before=gate.begin('pool');gate.invalidate();assert.equal(gate.accept(before,'pool'),false);
 const a=gate.begin('pool'),b=gate.begin('pool');assert.equal(gate.accept(a,'pool'),false);assert.equal(gate.accept(b,'pool'),true);
});
