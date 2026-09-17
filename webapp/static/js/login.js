async function resumeSession(){
      if(!localStorage.getItem(tokenKey))return;
      const user=await requireLogin();
      if(user)location.href='/';
    }
    async function doLogin(event){
      event.preventDefault();
      const username=document.getElementById('login-user').value.trim();
      const password=document.getElementById('login-pass').value;
      const msg=document.getElementById('login-msg');
      msg.textContent='';
      try{
        const r=await fetch(api+'/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});
        const body=await r.json();
        if(!r.ok)throw new Error(body.detail||'登录失败');
        localStorage.setItem(tokenKey,body.token);
        currentUser=body.user;
        location.href='/';
      }catch(e){msg.textContent=e.message;}
    }
    resumeSession();
