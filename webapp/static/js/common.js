const api='/api';
const tokenKey='sales_token';
let token=localStorage.getItem(tokenKey)||'';
let currentUser=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const qty=v=>v===null||v===undefined||v===''?'—':new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(Number(v));
const roleName=r=>({admin:'管理员',operator:'操作员'}[r]||r);
const permissionRank={NONE:0,VIEW:1,EDIT:2};
const permissionLevel=(module,user=currentUser)=>user?.permissions?.[module]||'NONE';
const hasPermission=(module,minimum='VIEW',user=currentUser)=>(permissionRank[permissionLevel(module,user)]||0)>=(permissionRank[minimum]||0);
const canManageUsers=(user=currentUser)=>hasPermission('user_management','EDIT',user);
function syncUserManagementVisibility(root=document,user=currentUser){
  root.querySelectorAll('[data-user-management-entry]').forEach(entry=>{entry.hidden=!canManageUsers(user);});
}
async function authFetch(url,options={}){
  const headers=new Headers(options.headers||{});
  if(token)headers.set('Authorization','Bearer '+token);
  const r=await fetch(url,{...options,headers});
  if(r.status===401){localStorage.removeItem(tokenKey);try{await fetch(api+'/auth/logout',{method:'POST'});}catch(_error){}location.href='/login';throw new Error('unauthorized');}
  return r;
}
async function requireLogin(){
  token=localStorage.getItem(tokenKey)||'';
  if(!token){location.href='/login';return null;}
  try{const r=await authFetch(api+'/auth/me');const body=await r.json();if(!r.ok)throw new Error(body.detail||'获取用户失败');currentUser=body.user;currentUser.permissions=currentUser.permissions||{};return currentUser;}catch(e){return null;}
}
async function logout(){
  localStorage.removeItem(tokenKey);
  try{await fetch(api+'/auth/logout',{method:'POST'});}finally{location.href='/login';}
}
function openChangePassword(){
  if(document.getElementById('cp-modal')){
    document.getElementById('cp-modal').style.display='flex';
    document.getElementById('cp-msg').textContent='';
    return;
  }
  const div=document.createElement('div');
  div.id='cp-modal';
  div.style.cssText='display:flex;position:fixed;inset:0;background:rgba(23,32,51,.55);z-index:100;align-items:center;justify-content:center';
  div.innerHTML=`<div style="background:#fff;border-radius:10px;padding:22px;width:400px;max-width:92vw">
    <h3 style="margin:0 0 12px;font-size:17px">修改密码</h3>
    <label style="display:block;font-size:13px;color:#43516a;margin-bottom:10px">原密码<input id="cp-old" type="password" style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:6px;margin-top:4px"></label>
    <label style="display:block;font-size:13px;color:#43516a;margin-bottom:10px">新密码（至少6位）<input id="cp-new" type="password" style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:6px;margin-top:4px"></label>
    <label style="display:block;font-size:13px;color:#43516a;margin-bottom:10px">确认新密码<input id="cp-new2" type="password" style="width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:6px;margin-top:4px"></label>
    <div id="cp-msg" style="min-height:20px;font-size:13px;color:#b42318"></div>
    <div style="display:flex;gap:8px;margin-top:12px;justify-content:flex-end">
      <button onclick="closeChangePassword()" style="background:#eaf1ff;color:#1747b8;border:0;border-radius:6px;padding:8px 12px;cursor:pointer">取消</button>
      <button onclick="submitChangePassword()" style="background:#1e5eff;color:white;border:0;border-radius:6px;padding:8px 12px;cursor:pointer">确认修改</button>
    </div></div>`;
  document.body.appendChild(div);
}
function closeChangePassword(){const el=document.getElementById('cp-modal');if(el)el.style.display='none';}
async function submitChangePassword(){
  const old=document.getElementById('cp-old').value;
  const n1=document.getElementById('cp-new').value;
  const n2=document.getElementById('cp-new2').value;
  const msg=document.getElementById('cp-msg');
  msg.style.color='#b42318';
  if(n1!==n2){msg.textContent='两次输入的新密码不一致。';return;}
  if(n1.length<6){msg.textContent='新密码至少 6 位。';return;}
  try{
    const r=await authFetch(api+'/auth/change-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({old_password:old,new_password:n1})});
    const body=await r.json();
    if(!r.ok)throw new Error(body.detail||'修改失败');
    msg.style.color='#117a43';
    msg.textContent='密码已修改，请用新密码重新登录。';
    setTimeout(()=>logout(),1200);
  }catch(e){msg.textContent=e.message;}
}
