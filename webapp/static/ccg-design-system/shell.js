/** CCGtools-open Global Shell V1 — shared navigation and account chrome only. */
(function initCCGShell(global) {
  'use strict';

  const DENSITY_STORAGE_KEY = 'ccgtools.ui.density';
  const SIDEBAR_STORAGE_KEY = 'ccgtools.ui.sidebar.collapsed';

  const icons = {
    overview: '<path d="M3 11 12 3l9 8"/><path d="M5 10v10h14V10M9 20v-6h6v6"/>',
    customers: '<circle cx="9" cy="8" r="3"/><path d="M3 20c0-4 2-6 6-6s6 2 6 6M16 6a3 3 0 0 1 0 6M17 14c2.7.4 4 2.3 4 6"/>',
    products: '<path d="m4 7 8-4 8 4-8 4-8-4Z"/><path d="m4 7 8 4 8-4v10l-8 4-8-4V7Z"/>',
    skus: '<path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    reference: '<path d="M4 6h16M4 12h10M4 18h7"/>',
    import: '<path d="M12 3v12m0-12 4 4m-4-4L8 7"/><path d="M4 14v6h16v-6"/>',
    sales: '<path d="M4 19V9m6 10V5m6 14v-7m4 7H2"/>',
    ordering: '<path d="M4 5h16v14H4z"/><path d="M8 3v4m8-4v4M4 10h16"/>',
    users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6m3-3h-6"/>',
  };

  const groups = [
    { label: '', items: [
      { id: 'overview', label: 'Overview', href: '/', icon: 'overview' },
    ] },
    { label: 'MASTER DATA', items: [
      { id: 'customers', label: 'Customers', href: '/mdm/customers', icon: 'customers', permission: 'mdm', spa: true },
      { id: 'products', label: 'Products', href: '/mdm/products', icon: 'products', permission: 'mdm', spa: true },
      { id: 'skus', label: 'SKUs', href: '/mdm/skus', icon: 'skus', permission: 'mdm', spa: true },
      { id: 'reference-data', label: 'Reference Data', href: '/mdm/references/channels', icon: 'reference', permission: 'mdm', spa: true },
      { id: 'import-center', label: 'Import Center', href: '/mdm/import-center', icon: 'import', permission: 'mdm', spa: true },
    ] },
    { label: 'SALES', items: [
      { id: 'sales-actual', label: 'Sales Actual', href: '/sales/actual', icon: 'sales', permission: 'sales_actual' },
    ] },
    { label: 'PLANNING', items: [
      { id: 'ordering', label: 'Ordering', href: '/ordering', icon: 'ordering', permission: 'ordering' },
    ] },
    { label: 'SYSTEM', items: [
      { id: 'users', label: 'Users', href: '/admin/users', icon: 'users', userManagement: true },
    ] },
  ];

  function routeInfo(pathname) {
    const path = String(pathname || '/').replace(/\/+$/, '') || '/';
    if (path === '/') return { id: 'overview', group: '', title: 'Overview' };
    if (path === '/admin/users') return { id: 'users', group: 'System', title: 'Users', userManagement: true };
    if (path.startsWith('/sales/actual')) return { id: 'sales-actual', group: 'Sales', title: 'Sales Actual', permission: 'sales_actual' };
    if (path.startsWith('/ordering')) return { id: 'ordering', group: 'Planning', title: 'Ordering', permission: 'ordering' };
    if (/^\/mdm\/(?:product-|sku-|goods-)?import-center(?:\/|$)/.test(path)) return { id: 'import-center', group: 'Master Data', title: 'Import Center', permission: 'mdm' };
    if (path.startsWith('/mdm/customers')) return { id: 'customers', group: 'Master Data', title: 'Customers', permission: 'mdm' };
    if (path.startsWith('/mdm/products')) return { id: 'products', group: 'Master Data', title: 'Products', permission: 'mdm' };
    if (path.startsWith('/mdm/skus')) return { id: 'skus', group: 'Master Data', title: 'SKUs', permission: 'mdm' };
    if (path.startsWith('/mdm/references')) return { id: 'reference-data', group: 'Master Data', title: 'Reference Data', permission: 'mdm' };
    if (path === '/mdm') return { id: '', group: 'Master Data', title: 'Overview', permission: 'mdm' };
    return { id: '', group: '', title: 'Workspace' };
  }

  function icon(name) {
    return `<span class="ccg-nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${icons[name] || ''}</svg></span>`;
  }

  function navMarkup() {
    return groups.map((group) => `<div class="ccg-nav-group" data-ccg-nav-group>
      ${group.label ? `<div class="ccg-nav-group-title">${group.label}</div>` : ''}
      ${group.items.map((item) => `<a class="ccg-nav-item" href="${item.href}" data-ccg-route="${item.id}"
        ${item.permission ? `data-ccg-permission="${item.permission}" hidden` : ''}
        ${item.userManagement ? 'data-user-management-entry data-ccg-user-management hidden' : ''}
        ${item.spa ? `data-nav="${item.href}"` : ''} title="${item.label}">
        ${icon(item.icon)}<span class="ccg-nav-text">${item.label}</span></a>`).join('')}
    </div>`).join('');
  }

  function footerMarkup() {
    return `<span class="ccg-user-avatar" id="user-avatar" aria-hidden="true">—</span>
      <span class="ccg-user-copy"><strong id="user-name">加载中</strong><small id="user-role">—</small></span>
      <span class="ccg-account-actions">
        <button class="ccg-account-action" type="button" data-ccg-action="password" aria-label="修改密码" title="修改密码">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 17v4M8 21h8"/><rect x="5" y="9" width="14" height="9" rx="2"/><path d="M8 9V6a4 4 0 0 1 8 0v3"/></svg>
        </button>
        <button class="ccg-account-action" type="button" data-ccg-action="logout" aria-label="退出登录" title="退出登录">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M10 17l5-5-5-5M15 12H3M15 3h5v18h-5"/></svg>
        </button>
      </span>`;
  }

  function sidebarMarkup() {
    return `<aside class="ccg-sidebar" id="app-sidebar" aria-label="CCGtools-open 主导航">
      <div class="ccg-sidebar-header">
        <a class="ccg-brand" href="/" aria-label="返回 CCGtools-open 首页"><span class="ccg-brand-mark" aria-hidden="true">C</span><span class="ccg-brand-title">CCGtools-open</span></a>
        <button class="ccg-sidebar-toggle" id="sidebar-toggle" type="button" data-ccg-action="sidebar-toggle" aria-expanded="true" aria-controls="app-sidebar" aria-label="折叠侧边栏" title="折叠或展开侧边栏">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 5h16M4 12h16M4 19h16"/></svg>
        </button>
      </div>
      <nav class="ccg-sidebar-nav" aria-label="业务模块">${navMarkup()}</nav>
      <div class="ccg-sidebar-footer">${footerMarkup()}</div>
    </aside>`;
  }

  function shellMarkup() {
    return `${sidebarMarkup()}<section class="ccg-main">
      <div class="ccg-top-header">
        <nav class="ccg-breadcrumb" aria-label="当前位置"><a href="/">CCGtools-open</a><span class="ccg-breadcrumb-separator">/</span><span id="ccg-breadcrumb-group" hidden></span><span class="ccg-breadcrumb-separator" id="ccg-breadcrumb-group-separator" hidden>/</span><strong class="ccg-breadcrumb-current" id="topbar-title">Workspace</strong></nav>
        <div class="ccg-header-status"><span id="readonly-indicator" class="ccg-readonly" hidden>只读</span></div>
      </div>
      <main class="ccg-workspace" id="ccg-workspace"></main>
    </section>`;
  }

  function ensureShell() {
    let shell = document.getElementById('app-shell');
    if (shell) {
      if (!shell.querySelector('.ccg-sidebar')) shell.insertAdjacentHTML('afterbegin', sidebarMarkup());
      const nav = shell.querySelector('.ccg-sidebar-nav');
      const footer = shell.querySelector('.ccg-sidebar-footer');
      if (nav) nav.innerHTML = navMarkup();
      if (footer) footer.innerHTML = footerMarkup();
      const oldToggle = shell.querySelector('[data-action="sidebar-toggle"]');
      if (oldToggle) {
        oldToggle.removeAttribute('data-action');
        oldToggle.dataset.ccgAction = 'sidebar-toggle';
      }
      return shell;
    }

    const content = document.querySelector('[data-ccg-shell-content]');
    if (!content) return null;
    shell = document.createElement('div');
    shell.className = 'ccg-shell';
    shell.id = 'app-shell';
    shell.dataset.density = 'comfortable';
    shell.innerHTML = shellMarkup();
    document.body.classList.add('ccg-page');
    content.classList.add('ccg-legacy-content');
    document.body.insertBefore(shell, document.body.firstChild);
    shell.querySelector('#ccg-workspace').appendChild(content);
    return shell;
  }

  function setDensity(value, persist = true) {
    const density = value === 'compact' ? 'compact' : 'comfortable';
    const shell = document.getElementById('app-shell');
    if (shell) shell.dataset.density = density;
    document.querySelectorAll('[data-action="ui-density"]').forEach((button) => {
      const active = button.dataset.densityValue === density;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    if (persist) localStorage.setItem(DENSITY_STORAGE_KEY, density);
    return density;
  }

  function setSidebarCollapsed(collapsed, persist = true) {
    const value = Boolean(collapsed);
    const sidebar = document.getElementById('app-sidebar');
    const toggle = document.getElementById('sidebar-toggle');
    if (sidebar) sidebar.classList.toggle('is-collapsed', value);
    if (toggle) {
      toggle.setAttribute('aria-expanded', String(!value));
      toggle.setAttribute('aria-label', value ? '展开侧边栏' : '折叠侧边栏');
    }
    if (persist) localStorage.setItem(SIDEBAR_STORAGE_KEY, value ? '1' : '0');
    return value;
  }

  function applyPreferences() {
    setDensity(localStorage.getItem(DENSITY_STORAGE_KEY), false);
    setSidebarCollapsed(localStorage.getItem(SIDEBAR_STORAGE_KEY) === '1', false);
  }

  function setPage(title, group) {
    const info = routeInfo(location.pathname);
    const current = title || info.title;
    const section = group === undefined ? info.group : group;
    const groupNode = document.getElementById('ccg-breadcrumb-group');
    const separator = document.getElementById('ccg-breadcrumb-group-separator');
    const titleNode = document.getElementById('topbar-title');
    if (groupNode) { groupNode.textContent = section || ''; groupNode.hidden = !section; }
    if (separator) separator.hidden = !section;
    if (titleNode) titleNode.textContent = current;
    document.title = `${current} · CCGtools-open`;
    document.querySelectorAll('[data-ccg-route]').forEach((link) => {
      const active = Boolean(info.id) && link.dataset.ccgRoute === info.id;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
  }

  function itemVisible(item, user) {
    if (item.permission) return typeof hasPermission === 'function' && hasPermission(item.permission, 'VIEW', user);
    if (item.userManagement) return typeof canManageUsers === 'function' && canManageUsers(user);
    return true;
  }

  function updateUser(user) {
    if (!user) return;
    const name = user.display_name || user.username || '用户';
    const avatar = document.getElementById('user-avatar');
    const nameNode = document.getElementById('user-name');
    const roleNode = document.getElementById('user-role');
    if (avatar) avatar.textContent = String(name).slice(0, 1).toUpperCase();
    if (nameNode) nameNode.textContent = name;
    const info = routeInfo(location.pathname);
    const level = info.permission && user.permissions ? user.permissions[info.permission] : '';
    if (roleNode) roleNode.textContent = `${typeof roleName === 'function' ? roleName(user.role) : user.role}${level ? ` · ${level}` : ''}`;
    document.querySelectorAll('[data-ccg-route]').forEach((link) => {
      const descriptor = groups.flatMap((group) => group.items).find((item) => item.id === link.dataset.ccgRoute);
      link.hidden = !descriptor || !itemVisible(descriptor, user);
    });
    document.querySelectorAll('[data-ccg-nav-group]').forEach((group) => {
      group.hidden = !group.querySelector('.ccg-nav-item:not([hidden])');
    });
    const readonly = document.getElementById('readonly-indicator');
    if (readonly) readonly.hidden = !(info.permission && level === 'VIEW');
  }

  function bind(shell) {
    if (!shell || shell.dataset.ccgBound === '1') return;
    shell.dataset.ccgBound = '1';
    shell.addEventListener('click', (event) => {
      const target = event.target.closest('[data-ccg-action], [data-action="ui-density"]');
      if (!target) return;
      const action = target.dataset.ccgAction || target.dataset.action;
      if (action === 'sidebar-toggle') {
        const sidebar = document.getElementById('app-sidebar');
        setSidebarCollapsed(!sidebar?.classList.contains('is-collapsed'));
      } else if (action === 'ui-density') setDensity(target.dataset.densityValue);
      else if (action === 'password' && typeof openChangePassword === 'function') openChangePassword();
      else if (action === 'logout' && typeof logout === 'function') logout();
    });
  }

  async function mount() {
    const shell = ensureShell();
    if (!shell) return null;
    bind(shell);
    applyPreferences();
    setPage();
    if (typeof requireLogin === 'function') {
      const user = await requireLogin();
      if (user) updateUser(user);
    }
    return shell;
  }

  global.CCGShell = {
    mount, routeInfo, itemVisible, setPage, updateUser, setDensity,
    setSidebarCollapsed, applyPreferences, DENSITY_STORAGE_KEY, SIDEBAR_STORAGE_KEY,
  };

  global.CCGShellReady = global.__CCG_SHELL_TEST_NO_AUTO_INIT__ ? Promise.resolve(null) : mount();
}(globalThis));
