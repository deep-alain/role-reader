const encoder=new TextEncoder(), decoder=new TextDecoder();
const crcTable=Array.from({length:256},(_,n)=>{for(let j=0;j<8;j++)n=n&1?0xedb88320^(n>>>1):n>>>1;return n>>>0});
export function crc32(bytes){let c=0xffffffff;for(const b of bytes)c=crcTable[(c^b)&255]^(c>>>8);return(c^0xffffffff)>>>0;}
const concat=arrays=>{const a=new Uint8Array(arrays.reduce((n,v)=>n+v.length,0));let p=0;for(const v of arrays){a.set(v,p);p+=v.length}return a};
export function makeZip(files){
 const locals=[],central=[];let offset=0;
 for(const file of files){const name=encoder.encode(file.name),data=typeof file.data==='string'?encoder.encode(file.data):new Uint8Array(file.data);const crc=crc32(data);const h=new Uint8Array(30+name.length),v=new DataView(h.buffer);v.setUint32(0,0x04034b50,true);v.setUint16(4,20,true);v.setUint16(6,0x800,true);v.setUint32(14,crc,true);v.setUint32(18,data.length,true);v.setUint32(22,data.length,true);v.setUint16(26,name.length,true);h.set(name,30);locals.push(h,data);
 const ch=new Uint8Array(46+name.length),c=new DataView(ch.buffer);c.setUint32(0,0x02014b50,true);c.setUint16(4,20,true);c.setUint16(6,20,true);c.setUint16(8,0x800,true);c.setUint32(16,crc,true);c.setUint32(20,data.length,true);c.setUint32(24,data.length,true);c.setUint16(28,name.length,true);c.setUint32(42,offset,true);ch.set(name,46);central.push(ch);offset+=h.length+data.length;
 }
 const size=central.reduce((n,v)=>n+v.length,0),end=new Uint8Array(22),e=new DataView(end.buffer);e.setUint32(0,0x06054b50,true);e.setUint16(8,files.length,true);e.setUint16(10,files.length,true);e.setUint32(12,size,true);e.setUint32(16,offset,true);return concat([...locals,...central,end]);
}
export async function readZip(buffer){
 const b=new Uint8Array(buffer),v=new DataView(b.buffer,b.byteOffset,b.byteLength);let end=-1;
 for(let p=b.length-22;p>=Math.max(0,b.length-65557);p--){if(v.getUint32(p,true)===0x06054b50&&p+22+v.getUint16(p+20,true)===b.length){end=p;break}}
 if(end<0)throw new Error('无效的 ZIP / XLSX 文件');
 const count=v.getUint16(end+10,true);if(count>3000)throw new Error('压缩包内文件过多');let p=v.getUint32(end+16,true),total=0;const entries=[];
 for(let i=0;i<count;i++){
  if(p+46>b.length||v.getUint32(p,true)!==0x02014b50)throw new Error('ZIP 目录损坏');
  const flags=v.getUint16(p+8,true),method=v.getUint16(p+10,true),crc=v.getUint32(p+16,true),size=v.getUint32(p+20,true),rawSize=v.getUint32(p+24,true),nl=v.getUint16(p+28,true),el=v.getUint16(p+30,true),cl=v.getUint16(p+32,true),local=v.getUint32(p+42,true),name=decoder.decode(b.slice(p+46,p+46+nl));
  total+=rawSize;if(rawSize>60*1024*1024||total>120*1024*1024)throw new Error('解压数据超过 120 MB 安全上限');
  if(flags&1)throw new Error('不支持加密 ZIP');if(local+30>b.length||v.getUint32(local,true)!==0x04034b50)throw new Error('ZIP 数据损坏');
  const start=local+30+v.getUint16(local+26,true)+v.getUint16(local+28,true);if(start+size>b.length)throw new Error('ZIP 数据长度无效');
  entries.push({name,method,crc,rawSize,data:b.slice(start,start+size)});p+=46+nl+el+cl;
 }
 const result=new Map();for(const f of entries){
  // XLSX 导入只需要 XML；忽略图片，减少内存占用。
  if(!/\.xml$|\.rels$/i.test(f.name))continue;
  let data;if(f.method===0)data=f.data;else if(f.method===8){if(typeof DecompressionStream==='undefined')throw new Error('此浏览器不支持解压，请使用最新版 Chrome / Edge');const reader=new Blob([f.data]).stream().pipeThrough(new DecompressionStream('deflate-raw')).getReader();const chunks=[];let n=0;while(true){const x=await reader.read();if(x.done)break;n+=x.value.length;if(n>f.rawSize||n>60*1024*1024){await reader.cancel();throw new Error('解压数据超出声明长度')}chunks.push(x.value)}data=concat(chunks)}else throw new Error('不支持的 ZIP 压缩格式');
  if(data.length!==f.rawSize||crc32(data)!==f.crc)throw new Error('ZIP 完整性校验失败');result.set(f.name,decoder.decode(data));
 }return result;
}
export function readPngCard(buffer){
 const b=new Uint8Array(buffer),v=new DataView(b.buffer,b.byteOffset,b.byteLength);if(b.length<8||[137,80,78,71,13,10,26,10].some((n,i)=>b[i]!==n))throw new Error('不是有效 PNG');let p=8;const found={};
 while(p+12<=b.length){const n=v.getUint32(p),type=decoder.decode(b.slice(p+4,p+8));if(n>30*1024*1024||p+12+n>b.length)throw new Error('PNG 区块损坏');
 if(type==='tEXt'){const bytes=b.slice(p+8,p+8+n),zero=bytes.indexOf(0);if(zero>0){const key=decoder.decode(bytes.slice(0,zero));if(key==='chara'||key==='ccv3')found[key]=decoder.decode(bytes.slice(zero+1));}}
 if(type==='iTXt'){const bytes=b.slice(p+8,p+8+n),zero=bytes.indexOf(0);if(zero>0&&bytes[zero+1]===0){const key=decoder.decode(bytes.slice(0,zero));let q=zero+3;q=bytes.indexOf(0,q)+1;if(q>0)q=bytes.indexOf(0,q)+1;if(q>0&&(key==='chara'||key==='ccv3'))found[key]=decoder.decode(bytes.slice(q));}}
 p+=n+12;if(type==='IEND')break;
 }
 const value=found.ccv3||found.chara;if(!value)throw new Error('PNG 没有可读取的角色卡数据（chara / ccv3）');
 try{return JSON.parse(value)}catch{}
 try{const binary=atob(value),data=Uint8Array.from(binary,c=>c.charCodeAt(0));return JSON.parse(decoder.decode(data))}catch{throw new Error('PNG 角色卡 JSON 或 Base64 格式错误')}
}
export const xml=s=>String(s??'').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g,'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&apos;');
const col=i=>{let s='';for(i++;i;i=Math.floor((i-1)/26))s=String.fromCharCode(65+(i-1)%26)+s;return s};
export function makeXlsx(headers, rows){
 const longs=[];const main=[headers,...rows.map((r,ri)=>r.map((v,ci)=>{const t=String(v??'');if(t.length<=32700)return t;for(let p=0;p<t.length;p+=32000)longs.push([r[0],headers[ci],Math.floor(p/32000)+1,t.slice(p,p+32000)]);return t.slice(0,32000)+'\n[完整内容见“长字段”及 JSON]';}))];
 const sheets=[{name:'角色资料',rows:main}];if(longs.length)sheets.push({name:'长字段',rows:[['记录ID','字段','分段','内容'],...longs]});
 const files=[{name:'_rels/.rels',data:`<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`},
 {name:'xl/workbook.xml',data:`<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${sheets.map((s,i)=>`<sheet name="${xml(s.name)}" sheetId="${i+1}" r:id="rId${i+1}"/>`).join('')}</sheets></workbook>`},
 {name:'xl/_rels/workbook.xml.rels',data:`<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheets.map((_,i)=>`<Relationship Id="rId${i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i+1}.xml"/>`).join('')}<Relationship Id="rId${sheets.length+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`},
 {name:'xl/styles.xml',data:`<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Arial"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF245C46"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`},
 {name:'[Content_Types].xml',data:`<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${sheets.map((_,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}</Types>`}];
 for(const [i,sheet] of sheets.entries()){const last=col(sheet.rows[0].length-1),lastRow=sheet.rows.length;
 const data=`<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:${last}${lastRow}"/><sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="22"/><cols>${sheet.rows[0].map((h,c)=>`<col min="${c+1}" max="${c+1}" width="${/Scenario|greet|介绍|prompt|内容|备注|来源|错误/.test(h)?65:24}" customWidth="1"/>`).join('')}</cols><sheetData>${sheet.rows.map((r,ri)=>`<row r="${ri+1}" ht="${ri===0?30:120}" customHeight="1">${r.map((v,ci)=>`<c r="${col(ci)}${ri+1}" t="inlineStr" s="${ri===0?1:0}"><is><t xml:space="preserve">${xml(v)}</t></is></c>`).join('')}</row>`).join('')}</sheetData><autoFilter ref="A1:${last}${lastRow}"/></worksheet>`;
 files.push({name:`xl/worksheets/sheet${i+1}.xml`,data});}
 return makeZip(files);
}
