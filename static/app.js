'use strict';

/* 信息终端资产查询 —— 前端逻辑（原生 JS，无外部依赖） */

const $ = (id) => document.getElementById(id);

const state = {
  config: null,
  columns: [],        // 全部字段 [{key,label}]
  listKeys: [],       // 精简视图字段
  showAllCols: false,
  page: 1,
  pageSize: 50,
  total: 0,
  pages: 1,
  sort: '',
  order: 'asc',
  items: [],
  lastParams: '',
  activeId: null,
};

/* 界面字段 id -> 后端查询参数名 */
const FILTERS = [
  ['fKeyword', 'keyword'],
  ['fAsset', 'asset'],
  ['fBuilding', 'building'],
  ['fFloor', 'floor'],
  ['fCategory', 'category'],
  ['fDeviceName', 'device_name'],
  ['fWorkType', 'work_type'],
  ['fNetwork', 'network'],
  ['fBrand', 'brand'],
  ['fWorkGroup', 'work_group_like'],
  ['fDept', 'dept_like'],
  ['fYearFrom', 'year_from'],
  ['fYearTo', 'year_to'],
];

function fieldValue(id) {
  const el = $(id);
  return el ? String(el.value || '').trim() : '';
}

function buildParams(forExport) {
  const p = new URLSearchParams();
  FILTERS.forEach(([id, key]) => {
    const value = fieldValue(id);
    if (value) p.set(key, value);
  });
  if ($('fExact').checked && fieldValue('fAsset')) p.set('exact', '1');
  if ($('fNoYear').checked) p.set('no_year', '1');
  if ($('fShowPwd').checked) p.set('show_pwd', '1');
  if (state.sort) { p.set('sort', state.sort); p.set('order', state.order); }
  if (!forExport) { p.set('page', state.page); p.set('page_size', state.pageSize); }
  return p;
}

function api(path) {
  return fetch(path).then((res) => {
    if (res.status === 401) {
      window.location.href = '/login';
      throw new Error('登录状态已失效，正在跳转登录页…');
    }
    if (!res.ok) throw new Error('请求失败（HTTP ' + res.status + '）');
    return res.json();
  });
}

async function loadConfig() {
  const cfg = await api('/api/config');
  state.config = cfg;
  state.columns = cfg.columns;
  state.listKeys = cfg.listKeys;

  fillSelect('fBuilding', cfg.facets.building);
  fillSelect('fFloor', cfg.facets.floor);
  fillSelect('fCategory', cfg.facets.category);
  fillSelect('fDeviceName', cfg.facets.device_name);
  fillSelect('fWorkType', cfg.facets.work_type);
  fillSelect('fNetwork', cfg.facets.network);
  fillSelect('fBrand', cfg.facets.brand);
  fillDatalist('dlWorkGroup', cfg.facets.work_group);
  fillDatalist('dlDept', cfg.facets.dept);

  const meta = cfg.meta || {};
  const parts = [];
  if (meta.source_file) parts.push(meta.source_file);
  if (meta.sheet_name) parts.push('标签页：' + meta.sheet_name);
  parts.push('共 ' + cfg.total + ' 条');
  if (meta.imported_at) parts.push('导入时间：' + meta.imported_at);
  $('sourceInfo').textContent = parts.join('　·　');
}

function fillSelect(id, options) {
  const select = $(id);
  if (!select || !options) return;
  const previous = select.value;
  select.innerHTML = '';
  const all = new Option('全部（不限）', '');
  select.appendChild(all);
  options.forEach((opt) => {
    const label = document.createElement('option');
    label.value = opt.value;
    label.textContent = opt.value + '（' + opt.count + '）';
    label.title = opt.value + '：' + opt.count + ' 条';
    select.appendChild(label);
  });
  select.value = previous;
  if (select.value !== previous) select.value = '';
}

function fillDatalist(id, options) {
  const list = $(id);
  if (!list || !options) return;
  list.innerHTML = options.map((o) => '<option value="' + escapeHtml(o.value) + '"></option>').join('');
}

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function visibleColumns() {
  if (state.showAllCols) return state.columns;
  const wanted = state.listKeys.length ? state.listKeys : state.columns.map((c) => c.key);
  return state.columns.filter((c) => wanted.includes(c.key));
}

function renderHead(cols) {
  const row = $('headRow');
  row.innerHTML = '';
  cols.forEach((col) => {
    const th = document.createElement('th');
    th.textContent = col.label;
    if (state.sort === col.key) {
      th.classList.add('sorted');
      th.textContent = col.label + (state.order === 'desc' ? ' ↓' : ' ↑');
    }
    th.title = '点击按此列排序';
    th.addEventListener('click', () => {
      if (state.sort === col.key) state.order = state.order === 'asc' ? 'desc' : 'asc';
      else { state.sort = col.key; state.order = 'asc'; }
      state.page = 1;
      search();
    });
    row.appendChild(th);
  });
}

function renderTable(items, cols) {
  const body = $('body');
  body.innerHTML = '';
  const fragment = document.createDocumentFragment();
  items.forEach((item) => {
    const tr = document.createElement('tr');
    if (item.id === state.activeId) tr.classList.add('active');
    cols.forEach((col) => {
      const td = document.createElement('td');
      const value = item[col.key];
      const blank = value === null || value === undefined || value === '';
      td.textContent = blank ? '—' : value;
      td.className = blank ? 'empty-cell' : '';
      td.title = blank ? '' : String(value);
      if (col.key === 'asset_no') {
        td.classList.add('asset');
        if (!blank) {
          td.title = '点击只看该资产编号的全部记录：' + value;
          td.addEventListener('click', (e) => {
            e.stopPropagation();
            $('fAsset').value = value;
            $('fExact').checked = true;
            ['fBuilding', 'fFloor', 'fCategory', 'fDeviceName', 'fWorkType', 'fNetwork', 'fBrand']
              .forEach((id) => { $(id).value = ''; });
            $('fWorkGroup').value = '';
            $('fDept').value = '';
            state.sort = '';
            state.page = 1;
            search();
          });
        }
      }
      tr.appendChild(td);
    });
    tr.addEventListener('click', () => openDetail(item.id, tr));
    fragment.appendChild(tr);
  });
  body.appendChild(fragment);
  $('resultTable').classList.toggle('hidden', items.length === 0);
  $('empty').classList.toggle('hidden', items.length !== 0);
}

function renderSummary(data, paramsText) {
  const parts = ['<strong>' + data.total + '</strong> 条记录'];
  if (data.duplicateAssetNos) {
    parts.push('有 ' + data.duplicateAssetNos + ' 个资产编号对应多条记录（点资产编号可只看这个编号）');
  }
  if (!paramsText) parts.push('未加条件，展示全部');
  $('summary').innerHTML = parts.join('　｜　');

  const stats = $('stats');
  stats.innerHTML = '';
  const facetLabel = { building: '楼号', category: '设备分类', network: '网络' };
  Object.keys(data.stats || {}).forEach((field) => {
    (data.stats[field] || []).slice(0, 8).forEach((bucket) => {
      const chip = document.createElement('button');
      chip.className = 'chip';
      chip.innerHTML = facetLabel[field] + '：' + escapeHtml(bucket.value) + ' <b>' + bucket.count + '</b>';
      chip.title = '只看 ' + facetLabel[field] + ' = ' + bucket.value;
      chip.addEventListener('click', () => applyChip(field, bucket.value));
      stats.appendChild(chip);
    });
  });
}

function applyChip(field, value) {
  const map = { building: 'fBuilding', category: 'fCategory', network: 'fNetwork' };
  const id = map[field];
  if (!id) return;
  const select = $(id);
  if (![...select.options].some((o) => o.value === value)) {
    const option = new Option(value + '（当前结果）', value);
    select.appendChild(option);
  }
  select.value = value;
  state.page = 1;
  search();
}

function renderPager(data) {
  state.pages = data.pages;
  $('pageInfo').textContent = '第 ' + data.page + ' / ' + data.pages + ' 页　（每页 ' + data.page_size + ' 条）';
  $('pageJump').max = data.pages;
  $('btnPrev').disabled = data.page <= 1;
  $('btnNext').disabled = data.page >= data.pages;
}

async function search() {
  const params = buildParams(false);
  const paramsText = params.toString();
  try {
    const data = await api('/api/devices?' + paramsText);
    state.items = data.items;
    state.total = data.total;
    state.lastParams = paramsText;
    state.pageSize = data.page_size;
    renderHead(visibleColumns());
    renderTable(data.items, visibleColumns());
    renderSummary(data, paramsText);
    renderPager(data);
  } catch (err) {
    $('summary').innerHTML = '<span class="error">' + escapeHtml(err.message) +
      '　若提示未登录，请重新输入查询密码。</span>';
  }
}

const CLOSE_MS = 200;          // 与 style.css 里 drawer-out / mask-out 的动画时长保持一致
let closeTimer = null;

function reducedMotion() {
  return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

function setClosing(closing) {
  ['detail', 'detailMask'].forEach((id) => $(id).classList.toggle('closing', closing));
}

function finishClose() {
  clearTimeout(closeTimer);
  closeTimer = null;
  setClosing(false);
  $('detail').classList.add('hidden');
  $('detailMask').classList.add('hidden');
}

async function openDetail(id, tr) {
  clearTimeout(closeTimer);          // 取消可能还在进行的收起动画
  setClosing(false);
  state.activeId = id;
  [...document.querySelectorAll('#body tr')].forEach((row) => row.classList.remove('active'));
  if (tr) tr.classList.add('active');
  const params = new URLSearchParams();
  if ($('fShowPwd').checked) params.set('show_pwd', '1');
  try {
    const data = await api('/api/detail/' + id + '?' + params.toString());
    $('detailTitle').textContent = '设备明细　' + (fieldOf(data, 'asset_no') || ('#' + data.id));
    $('detailBody').innerHTML = data.fields.map((f) => (
      '<div><dt>' + escapeHtml(f.label) + '</dt><dd class="' + (f.value ? '' : 'blank') + '">' +
      (f.value ? escapeHtml(f.value) : '（空）') + '</dd></div>'
    )).join('');
    $('detail').classList.remove('hidden');
    $('detailMask').classList.remove('hidden');
  } catch (err) {
    $('summary').innerHTML = '<span class="error">读取明细失败：' + escapeHtml(err.message) + '</span>';
  }
}

function fieldOf(detail, key) {
  const found = (detail.fields || []).find((f) => f.key === key);
  return found ? found.value : '';
}

function closeDetail() {
  const drawer = $('detail');
  // 已经收起（或正在收起）就不用再动
  if (!drawer.classList.contains('closing') && drawer.classList.contains('hidden')) return;
  state.activeId = null;
  [...document.querySelectorAll('#body tr')].forEach((row) => row.classList.remove('active'));
  if (reducedMotion()) { finishClose(); return; }
  setClosing(true);                                   // 触发抽屉滑出 + 遮罩淡出
  closeTimer = setTimeout(finishClose, CLOSE_MS + 60);
}

function resetFilters() {
  ['fKeyword', 'fAsset', 'fWorkGroup', 'fDept', 'fYearFrom', 'fYearTo'].forEach((id) => { $(id).value = ''; });
  ['fBuilding', 'fFloor', 'fCategory', 'fDeviceName', 'fWorkType', 'fNetwork', 'fBrand'].forEach((id) => { $(id).value = ''; });
  ['fExact', 'fNoYear'].forEach((id) => { $(id).checked = false; });
  state.sort = '';
  state.order = 'asc';
  state.page = 1;
  search();
}

function bindEvents() {
  $('btnSearch').addEventListener('click', () => { state.page = 1; search(); });
  $('btnReset').addEventListener('click', resetFilters);
  $('btnRefresh').addEventListener('click', async () => { await loadConfig(); search(); });
  $('btnExport').addEventListener('click', () => {
    window.location.href = '/api/export.csv?' + buildParams(true).toString();
  });
  $('btnPrev').addEventListener('click', () => { if (state.page > 1) { state.page -= 1; search(); } });
  $('btnNext').addEventListener('click', () => {
    if (state.page < state.pages) { state.page += 1; search(); }
  });
  $('btnJump').addEventListener('click', jumpToPage);
  $('pageJump').addEventListener('keydown', (e) => { if (e.key === 'Enter') jumpToPage(); });
  $('btnCloseDetail').addEventListener('click', closeDetail);
  $('detailMask').addEventListener('click', closeDetail);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDetail(); });

  $('fAllCols').addEventListener('change', (e) => {
    state.showAllCols = e.target.checked;
    renderHead(visibleColumns());
    renderTable(state.items, visibleColumns());
  });
  $('fShowPwd').addEventListener('change', () => { state.activeId = null; search(); });
  $('pageSize').addEventListener('change', (e) => {
    state.pageSize = parseInt(e.target.value, 10) || 50;
    state.page = 1;
    search();
  });

  FILTERS.forEach(([id]) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter') { state.page = 1; search(); } });
    if (el.tagName === 'SELECT') el.addEventListener('change', () => { state.page = 1; search(); });
  });
}

function jumpToPage() {
  const target = parseInt($('pageJump').value, 10);
  if (!target) return;
  state.page = Math.max(1, Math.min(target, state.pages));
  search();
}

async function boot() {
  bindEvents();
  try {
    await loadConfig();
  } catch (err) {
    $('sourceInfo').textContent = '读取失败：' + err.message;
    $('summary').innerHTML = '<span class="error">无法连接后端。请先导入数据再启动服务：' +
      '<br>　.venv\\Scripts\\python.exe import_data.py' +
      '<br>　.venv\\Scripts\\python.exe app.py --port 8000</span>';
    return;
  }
  state.page = 1;
  search();
}

document.addEventListener('DOMContentLoaded', boot);
