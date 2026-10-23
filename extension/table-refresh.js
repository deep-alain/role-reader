// Keep existing text nodes when polling only changes background metadata.
const rowCaches=new WeakMap();
export function reconcileRows(container,items,{key,version,create}){
 const previous=rowCaches.get(container)||new Map(),next=new Map();
 let cursor=container.firstChild;
 for(const item of items){
  const id=key(item),stamp=version(item),cached=previous.get(id);
  const node=cached?.stamp===stamp?cached.node:create(item);
  next.set(id,{stamp,node});
  if(node===cursor)cursor=cursor.nextSibling;
  else container.insertBefore(node,cursor);
 }
 while(cursor){const extra=cursor;cursor=cursor.nextSibling;container.removeChild(extra)}
 rowCaches.set(container,next);
}

// A pending refresh reads the newest model once dragging/selection/editing ends.
export function selectionSafeRefresh({document,window,render,schedule=callback=>setTimeout(callback,0)}){
 let dragging=false,pending=false,scheduled=false,disposed=false;
 const listeners=[];
 function blocked(){
  if(dragging||document.querySelector('dialog[open], details.record-more[open]'))return true;
  if(document.getSelection()?.isCollapsed===false)return true;
  const input=document.activeElement;
  return typeof input?.selectionStart==='number'&&input.selectionEnd>input.selectionStart;
 }
 function flush(){scheduled=false;if(disposed||!pending||blocked())return;pending=false;render()}
 function wake(){if(!disposed&&pending&&!scheduled){scheduled=true;schedule(flush)}}
 function listen(target,type,fn){target.addEventListener(type,fn,true);listeners.push(()=>target.removeEventListener(type,fn,true))}
 listen(document,'pointerdown',event=>{if(event.button===0&&event.isPrimary!==false)dragging=true});
 for(const type of ['pointerup','pointercancel','blur'])listen(window,type,()=>{dragging=false;wake()});
 listen(window,'focus',wake);
 for(const type of ['selectionchange','keyup','input','focusout','close','toggle'])listen(document,type,wake);
 return {request({immediate=false}={}){pending=true;if(immediate&&!disposed){pending=false;render()}else wake()},dispose(){disposed=true;for(const remove of listeners)remove()}};
}
