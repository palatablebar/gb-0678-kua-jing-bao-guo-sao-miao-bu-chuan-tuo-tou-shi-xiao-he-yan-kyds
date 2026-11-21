const runId=window.samplingRun.id;
const msg=document.querySelector('#message');
async function call(url,body){
  msg.textContent='处理中…';
  const res=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await res.json();
  if(!res.ok){msg.textContent=data.message||'操作失败';throw new Error(data.code);}
  window.location.reload();
}
document.querySelector('#build-draft').addEventListener('click',()=>{
  const size=parseInt(document.querySelector('#sample-size').value,10);
  const candidate=document.querySelector('#candidate-run').value.trim();
  const body={run_id:runId,sample_size:size};
  if(candidate)body.candidate_run_id=candidate;
  call('/api/sampling/drafts',body).catch(()=>{});
});
document.querySelectorAll('button.confirm').forEach(btn=>btn.addEventListener('click',()=>
  call('/api/sampling/confirm',{run_id:runId,seq:parseInt(btn.dataset.seq,10)}).catch(()=>{})));
document.querySelectorAll('button.claim').forEach(btn=>btn.addEventListener('click',()=>{
  const by=window.prompt('请输入客服标识（领取后样本锁定，重抽不会替换）');
  if(by)call('/api/sampling/claim',{run_id:runId,parcel:btn.dataset.parcel,claimed_by:by}).catch(()=>{});
}));
