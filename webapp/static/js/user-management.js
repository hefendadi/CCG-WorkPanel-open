const modules=[['mdm','MDM'],['sales_actual','Sales Actual'],['ordering','Ordering'],['user_management','User Management']];
const $u=id=>document.getElementById(id);
let users=[];
const defaults=role=>role==='admin'?{mdm:'EDIT',sales_actual:'EDIT',ordering:'EDIT',user_management:'EDIT'}:{mdm:'VIEW',sales_actual:'VIEW',ordering:'VIEW',user_management:'NONE'};
async function userRequest(path,options={}){
  const response=await authFetch('/api/admin/users'+path,options);
  const body=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(body.detail||'请求失败');
  return body;
}
function permissionOptions(module,value){
  const levels=module==='user_management'?['NONE','EDIT']:['NONE','VIEW','EDIT'];
  return levels.map(level=>`<option value="${level}" ${level===value?'selected':''}>${level}</option>`).join('');
}
function renderPermissionGrid(values){
  $u('permission-grid').innerHTML=modules.map(([key,label])=>`<label><span>${label}</span><select data-permission="${key}">${permissionOptions(key,values[key]||'NONE')}</select></label>`).join('');
  syncRolePermission();
}
function syncRolePermission(){
  const select=document.querySelector('[data-permission="user_management"]');
  if(!select)return;
  const admin=$u('role').value==='admin';
  if(!admin)select.value='NONE';
  select.disabled=!admin;
}
function render(){
  $u('count').textContent=`用户（${users.length}）`;
  $u('users').innerHTML=users.map((user,index)=>`<tr><td><strong>${esc(user.username)}</strong></td><td>${esc(user.display_name)}</td><td>${esc(user.role)}</td><td><span class="badge ${user.active?'':'off'}">${user.active?'启用':'停用'}</span></td>${modules.map(([key])=>`<td>${esc(user.permissions[key]||'NONE')}</td>`).join('')}<td><div class="actions"><button class="alt" data-edit="${index}">编辑</button><button class="alt" data-reset="${index}">重置密码</button><button class="${user.active?'danger':'alt'}" data-toggle="${index}">${user.active?'停用':'启用'}</button></div></td></tr>`).join('');
}
async function loadUsers(){users=await userRequest('');render();}
function openCreate(){
  $u('user-form').reset();$u('user-id').value='';$u('form-title').textContent='创建账号';$u('username').disabled=false;$u('password').required=true;$u('password-label').hidden=false;$u('active-label').hidden=true;$u('role').value='operator';renderPermissionGrid(defaults('operator'));$u('form-error').textContent='';$u('user-dialog').showModal();
}
function openEdit(index){
  const user=users[index];$u('user-id').value=user.id;$u('form-title').textContent='编辑账号';$u('username').value=user.username;$u('username').disabled=true;$u('display-name').value=user.display_name;$u('role').value=user.role;$u('active').value=String(user.active);$u('password').required=false;$u('password-label').hidden=true;$u('active-label').hidden=false;renderPermissionGrid(user.permissions);$u('form-error').textContent='';$u('user-dialog').showModal();
}
function formPermissions(){return Object.fromEntries([...document.querySelectorAll('[data-permission]')].map(el=>[el.dataset.permission,el.value]));}
$u('role').addEventListener('change',syncRolePermission);
$u('create-user').addEventListener('click',openCreate);
$u('user-form').addEventListener('submit',async event=>{
  event.preventDefault();const id=$u('user-id').value;const payload={display_name:$u('display-name').value.trim(),role:$u('role').value,permissions:formPermissions()};
  if(id)payload.active=Number($u('active').value);else{payload.username=$u('username').value.trim();payload.password=$u('password').value;}
  try{await userRequest(id?`/${id}`:'',{method:id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});$u('user-dialog').close();await loadUsers();}catch(error){$u('form-error').textContent=error.message;}
});
$u('users').addEventListener('click',async event=>{
  const edit=event.target.closest('[data-edit]');if(edit){openEdit(Number(edit.dataset.edit));return;}
  const reset=event.target.closest('[data-reset]');if(reset){const user=users[Number(reset.dataset.reset)];$u('password-user-id').value=user.id;$u('reset-password').value='';$u('password-error').textContent='';$u('password-dialog').showModal();return;}
  const toggle=event.target.closest('[data-toggle]');if(!toggle)return;const user=users[Number(toggle.dataset.toggle)];
  try{await userRequest(`/${user.id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({display_name:user.display_name,role:user.role,active:user.active?0:1,permissions:user.permissions})});await loadUsers();}catch(error){$u('page-error').textContent=error.message;}
});
$u('password-form').addEventListener('submit',async event=>{event.preventDefault();try{await userRequest(`/${$u('password-user-id').value}/reset-password`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:$u('reset-password').value})});$u('password-dialog').close();}catch(error){$u('password-error').textContent=error.message;}});
document.querySelector('[data-close]').addEventListener('click',()=> $u('user-dialog').close());
document.querySelector('[data-close-password]').addEventListener('click',()=> $u('password-dialog').close());
(async()=>{const user=await requireLogin();if(!user)return;if(!hasPermission('user_management','EDIT',user)){location.href='/';return;}$u('current-user').textContent=user.display_name;try{await loadUsers();}catch(error){$u('page-error').textContent=error.message;}})();
