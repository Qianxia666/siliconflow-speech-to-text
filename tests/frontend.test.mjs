import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);

class ClassList {
  constructor(){ this.s=new Set(); }
  add(...xs){ xs.forEach(x=>this.s.add(x)); }
  remove(...xs){ xs.forEach(x=>this.s.delete(x)); }
  toggle(x,on){ if(on===undefined) on=!this.s.has(x); on?this.s.add(x):this.s.delete(x); return on; }
  contains(x){ return this.s.has(x); }
}
class El {
  constructor(id=''){ this.id=id; this.dataset={}; this.textContent=''; this.hidden=false; this.disabled=false; this.value=''; this.files=[]; this.classList=new ClassList(); this.attrs={}; this.listeners={}; this.style={}; this.children=[]; this.title=''; }
  querySelector(sel){ if(sel==='[data-button-label]'){ if(this.noLabel) return null; if(!this._label){ this._label=new El(); this._label.textContent=this.textContent; } return this._label; } return null; }
  setAttribute(k,v){ this.attrs[k]=String(v); }
  addEventListener(type,fn){ (this.listeners[type]??=[]).push(fn); }
  appendChild(c){ this.children.push(c); }
  removeChild(c){ this.children=this.children.filter(x=>x!==c); }
  select(){ this.selected=true; }
}
function ev(extra={}){ return {preventDefault(){}, target:null, dataTransfer:null, ...extra}; }
function response(ok,status,data,jsonError=false){ return {ok,status, async json(){ if(jsonError) throw new Error('bad json'); return data; }}; }

function setup(overrides={}){
  const ids=['transcriptionForm','audioFile','audioFileLabel','fileSummary','fileNameDisplay','fileMetaDisplay','submitBtn','statusMessage','workspaceStatus','resultContent','emptyState','activeViewLabel','resultStats','transcriptionResult','copyBtn','recalibrateBtn','summarizeBtn','generateNotesBtn','sourceTab','summaryTab','notesTab'];
  const els=Object.fromEntries(ids.map(id=>[id,new El(id)]));
  for(const id of ['submitBtn','copyBtn','recalibrateBtn','summarizeBtn','generateNotesBtn']){ els[id]._label=new El(); els[id]._label.textContent={submitBtn:'开始转录',copyBtn:'复制',recalibrateBtn:'重新校准',summarizeBtn:'生成摘要',generateNotesBtn:'生成笔记'}[id]; }
  els.sourceTab.dataset.view='source'; els.summaryTab.dataset.view='summary'; els.notesTab.dataset.view='notes';
  const body=new El('body'); body.dataset={maxUploadBytes:'1048576',maxUploadMb:'1',maxTextChars:'10',s2tConfigured:'true',calibrationConfigured:'true',summaryConfigured:'true',notesConfigured:'true',...(overrides.dataset||{})};
  if(overrides.noLabel) els.submitBtn.noLabel=true;
  const workspace=new El('workspace');
  let domReady;
  globalThis.document={body,getElementById:id=>els[id],querySelector:s=>s==='.workspace'?workspace:null,addEventListener:(t,f)=>{if(t==='DOMContentLoaded')domReady=f;},createElement:()=>new El(),execCommand:()=>true};
  globalThis.window={isSecureContext:true,setTimeout:(f)=>{f(); return 1;}};
  Object.defineProperty(globalThis,'navigator',{value:{clipboard:{writeText:async()=>{}}},writable:true,configurable:true});
  globalThis.FormData=class{constructor(){this.items=[];}append(k,v){this.items.push([k,v]);}};
  globalThis.DataTransfer=overrides.dataTransferThrows ? class{constructor(){throw new Error('no DataTransfer')}} : class{constructor(){const arr=[];this.items={add:x=>arr.push(x)};this.files=arr;}};
  const fetchQueue=[];
  globalThis.fetch=async()=>{ if(!fetchQueue.length) throw new Error('no mocked response'); const x=fetchQueue.shift(); if(x instanceof Error) throw x; return x; };
  const scriptPath=require.resolve('../static/script.js');
  delete require.cache[scriptPath];
  require(scriptPath); domReady();
  return {els,workspace,fetchQueue};
}
async function fire(el,type,event=ev()){ for(const fn of (el.listeners[type]||[])) await fn(event); }

test('front-end runtime flows and errors', async()=>{
  const {els,fetchQueue}=setup();
  els.submitBtn.noLabel=true;
  // no file / empty / oversized / valid selection
  await fire(els.audioFile,'change',ev({target:{files:[]}}));
  await fire(els.audioFile,'change',ev({target:{files:[{name:'e.wav',size:0}]}}));
  await fire(els.audioFile,'change',ev({target:{files:[{name:'big.wav',size:1048577}]}}));
  const file={name:'ok.wav',size:2048};
  await fire(els.audioFile,'change',ev({target:{files:[file]}}));
  assert.equal(els.submitBtn.disabled,false);
  await fire(els.audioFileLabel,'dragenter',ev()); await fire(els.audioFileLabel,'dragover',ev()); await fire(els.audioFileLabel,'dragleave',ev());
  await fire(els.audioFileLabel,'drop',ev({dataTransfer:{files:[file]}}));

  fetchQueue.push(response(true,200,{status:'success',transcription:'cal',raw_transcription:'raw',calibration_message:'ok',is_calibrated:true}));
  await fire(els.transcriptionForm,'submit',ev());
  assert.equal(els.transcriptionResult.textContent,'cal');

  fetchQueue.push(response(true,200,{status:'success',transcription:'recal',calibration_message:'done',is_calibrated:true}));
  await fire(els.recalibrateBtn,'click',ev());
  assert.equal(els.transcriptionResult.textContent,'recal');

  fetchQueue.push(response(true,200,{summary:'summary'}));
  await fire(els.summarizeBtn,'click',ev());
  assert.equal(els.transcriptionResult.textContent,'summary');
  await fire(els.sourceTab,'click',ev());
  await fire(els.summaryTab,'click',ev());

  fetchQueue.push(response(true,200,{notes:'# notes'}));
  await fire(els.generateNotesBtn,'click',ev());
  assert.equal(els.transcriptionResult.textContent,'# notes');
  await fire(els.notesTab,'click',ev());
  await fire(els.copyBtn,'click',ev());

  // HTTP JSON error, invalid JSON, network error paths
  fetchQueue.push(response(true,200,{status:'bad'})); await fire(els.recalibrateBtn,'click',ev());
  fetchQueue.push(response(true,200,{})); await fire(els.summarizeBtn,'click',ev());
  fetchQueue.push(response(true,200,{})); await fire(els.generateNotesBtn,'click',ev());
  fetchQueue.push(response(false,500,{error:'server'})); await fire(els.summarizeBtn,'click',ev());
  fetchQueue.push(response(true,200,{},true)); await fire(els.generateNotesBtn,'click',ev());
  fetchQueue.push(new Error('network')); await fire(els.recalibrateBtn,'click',ev());

  // transcription malformed result clears outputs
  fetchQueue.push(response(true,200,{status:'bad'})); await fire(els.transcriptionForm,'submit',ev());
  fetchQueue.push(response(true,200,{status:'success',transcription:'   ',raw_transcription:'raw'})); await fire(els.transcriptionForm,'submit',ev());
  assert.equal(els.transcriptionResult.textContent,'');

  // clipboard fallback success and failure
  globalThis.window.isSecureContext=false; globalThis.navigator.clipboard=null;
  await fire(els.copyBtn,'click',ev()); // empty path
  document.execCommand=()=>false;
  // restore some text through submit
  fetchQueue.push(response(true,200,{status:'success',transcription:'x',raw_transcription:'x',is_calibrated:false,calibration_message:'raw'})); await fire(els.transcriptionForm,'submit',ev());
  await fire(els.copyBtn,'click',ev());
});

test('clipboard permission failure falls back to execCommand copy', async()=>{
  const {els,fetchQueue}=setup();
  const file={name:'ok.wav',size:2048};
  await fire(els.audioFile,'change',ev({target:{files:[file]}}));
  fetchQueue.push(response(true,200,{status:'success',transcription:'可复制文本',raw_transcription:'可复制文本',calibration_message:'ok',is_calibrated:true}));
  await fire(els.transcriptionForm,'submit',ev());
  assert.equal(els.transcriptionResult.textContent,'可复制文本');

  let execCommandCalls=0;
  document.execCommand=()=>{ execCommandCalls+=1; return true; };
  globalThis.window.isSecureContext=true;
  globalThis.navigator.clipboard={writeText:async()=>{ throw new Error('permission denied'); }};

  await fire(els.copyBtn,'click',ev());
  assert.equal(execCommandCalls,1);
});

test('configuration-disabled, fallback-label, MB-size, and drop-fallback paths', async()=>{
  const {els}=setup({dataset:{maxUploadBytes:String(2*1024*1024),maxUploadMb:'2',s2tConfigured:'false',calibrationConfigured:'false',summaryConfigured:'false',notesConfigured:'false'},noLabel:true,dataTransferThrows:true});
  const mbFile={name:'movie.wav',size:1536*1024};
  await fire(els.transcriptionForm,'submit',ev());
  await fire(els.audioFile,'change',ev({target:{files:[mbFile]}}));
  assert.match(els.fileMetaDisplay.textContent,/1\.5 MB/);
  await fire(els.audioFileLabel,'drop',ev({dataTransfer:{files:[mbFile]}}));
  await fire(els.transcriptionForm,'submit',ev());
  await fire(els.recalibrateBtn,'click',ev());
  await fire(els.summarizeBtn,'click',ev());
  await fire(els.generateNotesBtn,'click',ev());
  await fire(els.copyBtn,'click',ev());
  assert.match(els.submitBtn.title,/尚未配置/);
  assert.match(els.recalibrateBtn.title,/尚未配置/);
  assert.match(els.summarizeBtn.title,/尚未配置/);
  assert.match(els.generateNotesBtn.title,/尚未配置/);
});
