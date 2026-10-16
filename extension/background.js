chrome.action.onClicked.addListener(tab => {
  chrome.tabs.create({url: chrome.runtime.getURL('app.html') + '?sourceTab=' + (tab.id ?? '')});
});
chrome.runtime.onConnect.addListener(port=>{
  if(port.name!=='role-reader-session')return;
  const owned=new Set();
  port.onMessage.addListener(message=>{if(!Number.isInteger(message.tabId))return;if(message.type==='opened')owned.add(message.tabId);if(message.type==='closed')owned.delete(message.tabId)});
  port.onDisconnect.addListener(()=>{for(const id of owned)chrome.tabs.remove(id).catch(()=>{});});
});
