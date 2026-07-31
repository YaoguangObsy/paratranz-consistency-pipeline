const $ = (sel) => document.querySelector(sel);

function setStatus(msg, isError) {
  const el = $('#statusBar');
  el.textContent = msg || '';
  el.style.color = isError ? '#dc2626' : '#1d4ed8';
  if (msg) setTimeout(() => { if (el.textContent === msg) el.textContent = ''; }, 4000);
}

// ---------------------------------------------------------------- tabs ---
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.add('hidden'));
    btn.classList.add('active');
    $('#tab-' + btn.dataset.tab).classList.remove('hidden');
    if (btn.dataset.tab === 'push') loadPushLog();
  });
});

// -------------------------------------------------------------- config ---
async function loadConfig() {
  const r = await fetch('/api/config').then(r => r.json());
  $('#cfgProjectId').value = r.project_id ?? '';
  $('#cfgToken').placeholder = r.has_token ? `已保存 (${r.token_masked})，留空则不修改` : '未配置';
}
$('#cfgSaveBtn').addEventListener('click', async () => {
  const body = { token: $('#cfgToken').value.trim(), project_id: $('#cfgProjectId').value };
  const r = await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json());
  $('#cfgStatus').textContent = r.ok ? '已保存' : ('失败: ' + r.error);
  $('#cfgToken').value = '';
  loadConfig();
});

// --------------------------------------------------------------- fetch ---
$('#fetchHiddenBtn').addEventListener('click', async () => {
  $('#fetchHiddenResult').textContent = '拉取中…';
  const stage = parseInt($('#hiddenStage').value || '-1', 10);
  const r = await fetch('/api/fetch/hidden-keys', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stage }) }).then(r => r.json());
  $('#fetchHiddenResult').textContent = r.ok ? `完成，共 ${r.count} 条，写入 ${r.path}` : ('失败: ' + r.error);
});
$('#fetchTermsBtn').addEventListener('click', async () => {
  $('#fetchTermsResult').textContent = '拉取中…';
  const r = await fetch('/api/fetch/terms', { method: 'POST' }).then(r => r.json());
  $('#fetchTermsResult').textContent = r.ok ? `完成，共 ${r.count} 条，写入 ${r.path}` : ('失败: ' + r.error);
});
$('#fetchArtifactsBtn').addEventListener('click', async () => {
  $('#fetchArtifactsResult').textContent = '触发导出中，Paratranz 打包可能要几秒到一两分钟，请稍候…';
  const r = await fetch('/api/fetch/artifacts', { method: 'POST' }).then(r => r.json());
  $('#fetchArtifactsResult').textContent = r.ok
    ? `完成，合并了 ${r.file_count} 个 CSV，共 ${r.key_count} 条 key，写入 ${r.path}`
    : ('失败: ' + r.error);
});

// --------------------------------------------------------- report regen --
$('#regenReportBtn').addEventListener('click', async () => {
  await flushChanges(); // 先把网页里防抖还没落盘的编辑写进 CSV，避免被刷新覆盖
  $('#regenReportResult').textContent = '生成中，可能需要几秒到几十秒…';
  const body = {
    source: $('#rgSource').value.trim(),
    translated: $('#rgTranslated').value.trim(),
    glossary: $('#rgGlossary').value.trim(),
    exclude_keys: $('#rgExcludeKeys').value.trim(),
  };
  const r = await fetch('/api/reports/regenerate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(r => r.json());
  if (!r.ok) { $('#regenReportResult').textContent = '失败: ' + r.error; return; }
  $('#regenReportResult').textContent =
    `完成，决策表共 ${r.total_rows} 行` +
    (r.used_glossary ? '（含术语表比对）' : '（未提供术语表，跳过）') +
    (r.used_exclude_keys ? '（已按 excluded_keys.txt 过滤）' : '（未提供隐藏key列表，跳过）');
  setStatus('报告已刷新');
  loadDecisions();
});

// -------------------------------------------------------------- undo -----
// 只保留最近一步操作，够用且实现简单；每次新的可撤销操作会覆盖上一个。
let undoEntry = null; // { label, restoreRows: [{row_id, field, value}, ...] }

function pushUndo(label, restoreRows) {
  if (!restoreRows || !restoreRows.length) return;
  undoEntry = { label, restoreRows };
  const btn = $('#undoBtn');
  btn.disabled = false;
  btn.title = `撤销「${label}」`;
}

async function doUndo() {
  if (!undoEntry) return;
  const entry = undoEntry;
  if (!confirm(`撤销「${entry.label}」？会把涉及的 ${new Set(entry.restoreRows.map(r => r.row_id)).size} 行恢复到操作之前的值。`)) return;
  const r = await fetch('/api/decisions/restore', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows: entry.restoreRows }),
  }).then(r => r.json());
  if (r.ok) {
    setStatus(`已撤销「${entry.label}」，恢复 ${r.updated} 行`);
    undoEntry = null;
    $('#undoBtn').disabled = true;
    $('#undoBtn').title = '';
    loadDecisions();
  } else {
    setStatus('撤销失败: ' + r.error, true);
  }
}
$('#undoBtn').addEventListener('click', doUndo);

// ----------------------------------------------------------- decisions ---
let table;
let pendingChanges = {};   // key -> {decision, final_translation, reviewer_note}
let saveTimer = null;

function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushChanges, 800);
}

async function flushChanges() {
  const rows = Object.entries(pendingChanges).map(([row_id, v]) => ({ row_id, ...v }));
  if (!rows.length) return;
  pendingChanges = {};
  setStatus(`保存中 (${rows.length} 行)…`);
  const r = await fetch('/api/decisions/save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows }),
  }).then(r => r.json());
  setStatus(r.ok ? `已自动保存 ${r.updated} 行` : '保存失败', !r.ok);
}
window.addEventListener('beforeunload', () => { if (Object.keys(pendingChanges).length) flushChanges(); });

async function loadDecisions() {
  const data = await fetch('/api/decisions').then(r => r.json());
  $('#rowCount').textContent = `共 ${data.total} 行`;
  if (!table) {
    table = new Tabulator('#grid', {
      data: data.rows,
      height: '68vh',
      layout: 'fitDataFill',
      index: 'row_id',
      selectableRows: true,
      initialSort: [{ column: 'excluded', dir: 'asc' }],
      rowFormatter: (row) => {
        const el = row.getElement();
        if (row.getData().excluded) el.classList.add('row-excluded');
        else el.classList.remove('row-excluded');
      },
      placeholder: '暂无数据 — 请先跑 3_build_review_sheet.py 生成 review_decisions.csv',
      columns: [
        {
          // 自带的 titleFormatter:'rowSelection' 点头部checkbox是对"全表"生效的，
          // 跟当前筛选无关；这里换成自定义的，只作用于当前筛选后可见（active）的行。
          formatter: 'rowSelection', hozAlign: 'center', headerSort: false, width: 40, frozen: true,
          titleFormatter: (cell) => {
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.title = '全选/取消全选 当前筛选结果';
            box._syncing = false;
            box.addEventListener('click', (e) => {
              e.stopPropagation();
              const activeRows = table.getRows('active');
              if (box.checked) table.selectRow(activeRows);
              else table.deselectRow(activeRows);
            });
            table.on('rowSelectionChanged', () => {
              const active = table.getRows('active');
              const selected = new Set(table.getSelectedRows());
              const allSelected = active.length > 0 && active.every(r => selected.has(r));
              box.checked = allSelected;
              box.indeterminate = !allSelected && active.some(r => selected.has(r));
            });
            table.on('dataFiltered', () => {
              const active = table.getRows('active');
              const selected = new Set(table.getSelectedRows());
              const allSelected = active.length > 0 && active.every(r => selected.has(r));
              box.checked = allSelected;
              box.indeterminate = !allSelected && active.some(r => selected.has(r));
            });
            return box;
          },
        },
        { title: 'key', field: 'key', width: 80, headerFilter: 'input', headerFilterPlaceholder: '…' },
        {
          title: 'source_report', field: 'source_report', width: 80,
          headerFilter: 'list',
          headerFilterParams: { values: { '': '全部', duplicate_source: 'duplicate_source', glossary: 'glossary' }, clearable: true },
          headerFilterFunc: '=',
        },
        { title: 'group_or_term', field: 'group_or_term', width: 100, headerFilter: 'input', headerFilterPlaceholder: '…' },
        { title: '原文', field: 'source_text', width: 260, formatter: 'textarea', headerFilter: 'input', headerFilterPlaceholder: '…' },
        { title: '现译文', field: 'current_translation', width: 220, formatter: 'textarea', headerFilter: 'input', headerFilterPlaceholder: '…' },
        { title: '建议译文', field: 'suggested_translation', width: 220, formatter: 'textarea', headerFilter: 'input', headerFilterPlaceholder: '…' },
        { title: 'confidence', field: 'confidence', width: 100, headerFilter: 'input', headerFilterPlaceholder: '…' },
        {
          title: 'decision', field: 'decision', width: 60,
          editor: 'list', editorParams: { values: { '': '(空)', accept: 'accept', reject: 'reject', edit: 'edit' } },
          formatter: (cell) => cell.getValue() || '',
          headerFilter: 'list',
          headerFilterParams: { values: { '': '全部', __empty__: '(空)', accept: 'accept', reject: 'reject', edit: 'edit' }, clearable: true },
          headerFilterFunc: (headerValue, rowValue) => {
            if (headerValue === '' || headerValue == null) return true;
            if (headerValue === '__empty__') return !rowValue;
            return rowValue === headerValue;
          },
        },
        { title: 'final_translation', field: 'final_translation', width: 220, editor: 'textarea', formatter: 'textarea', headerFilter: 'input', headerFilterPlaceholder: '…' },
        { title: 'reviewer_note', field: 'reviewer_note', width: 220, editor: 'input', headerFilter: 'input', headerFilterPlaceholder: '…' },
        {
          title: '排除', field: 'excluded', width: 90, hozAlign: 'center', editor: false,
          formatter: (cell) => cell.getValue() ? '已排除' : '',
          headerFilter: 'list',
          headerFilterParams: { values: { '': '全部', '1': '已排除', '0': '未排除' }, clearable: true },
          headerFilterFunc: (headerValue, rowValue) => {
            if (headerValue === '' || headerValue == null) return true;
            return headerValue === '1' ? !!rowValue : !rowValue;
          },
        },
      ],
    });
    table.on('cellEdited', (cell) => {
      const row = cell.getRow().getData();
      const field = cell.getField();
      const oldValue = cell.getOldValue();
      if (['decision', 'final_translation', 'reviewer_note'].includes(field)) {
        pushUndo(`编辑 ${row.row_id} 的 ${field}`, [{ row_id: row.row_id, field, value: oldValue || '' }]);
      }
      pendingChanges[row.row_id] = {
        decision: row.decision || '',
        final_translation: row.final_translation || '',
        reviewer_note: row.reviewer_note || '',
      };
      scheduleSave();
    });
    table.on('rowSelectionChanged', (data) => {
      $('#selCount').textContent = data.length ? `已选 ${data.length} 行` : '';
    });
  } else {
    table.setData(data.rows);
  }
  applyFilters();
}

function getFilteredRowIds() {
  return table.getData('active').map(r => r.row_id);
}
function getSelectedRowIds() {
  return table.getSelectedData().map(r => r.row_id);
}
function rowIdsForScope() {
  const scope = $('#bulkScope').value;
  const ids = scope === 'selected' ? getSelectedRowIds() : getFilteredRowIds();
  if (!ids.length) alert(scope === 'selected' ? '没有勾选任何行。' : '当前筛选结果为空。');
  return ids;
}

$('#reloadBtn').addEventListener('click', async () => { await flushChanges(); loadDecisions(); });

$('#searchBox').addEventListener('input', applyFilters);
$('#decisionFilter').addEventListener('change', applyFilters);
$('#showExcluded').addEventListener('change', applyFilters);
$('#showExcluded').checked = true;

function applyFilters() {
  if (!table) return;
  // 按空白切词，每个词分别做 AND 匹配（谁都可能落在不同字段里），
  // 而不是要求整个查询串（含空格）原样连续出现——原来的写法一旦
  // 输入里带空格几乎必然匹配不到。同时 trim 掉首尾空白。
  const tokens = $('#searchBox').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const decFilter = $('#decisionFilter').value; // '__all__' | '' | accept | reject | edit
  const showExcluded = $('#showExcluded').checked;
  table.setFilter((data) => {
    if (!showExcluded && data.excluded) return false;
    if (decFilter !== '__all__' && (data.decision || '') !== decFilter) return false;
    if (tokens.length) {
      const hay = (data.key + ' ' + data.source_text + ' ' + data.current_translation + ' ' + data.group_or_term).toLowerCase();
      if (!tokens.every(t => hay.includes(t))) return false;
    }
    return true;
  });
  $('#selCount').textContent = table.getSelectedData().length ? `已选 ${table.getSelectedData().length} 行` : '';
}

// -------------------------------------------------------- bulk decision --
document.querySelectorAll('[data-bulk-decision]').forEach(btn => {
  btn.addEventListener('click', async () => {
    const decision = btn.dataset.bulkDecision;
    const row_ids = rowIdsForScope();
    if (!row_ids.length) return;
    const label = decision || '(空)';
    if (!confirm(`将 ${row_ids.length} 行的 decision 设为「${label}」？`)) return;
    // 撤销要恢复的是"操作前"的值，所以要在真正发请求之前，从表格当前数据里取旧值
    const prevRows = row_ids.map(k => ({ row_id: k, field: 'decision', value: (table.getRow(k)?.getData().decision) || '' }));
    const r = await fetch('/api/decisions/bulk-decision', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ row_ids, decision }),
    }).then(r => r.json());
    if (r.ok) {
      pushUndo(`批量设置 decision → ${label}（${r.updated} 行）`, prevRows);
      setStatus(`已更新 ${r.updated} 行`); loadDecisions();
    }
    else setStatus('失败: ' + r.error, true);
  });
});

// -------------------------------------------------------------- exclude --
async function bulkExclude(excluded) {
  const rowIds = rowIdsForScope();   // 用回之前定义的函数名
  if (!rowIds.length) return;
  const verb = excluded ? '标记排除' : '取消排除';
  if (!confirm(`对 ${rowIds.length} 行执行「${verb}」？排除的行仍会保留在表里、可编辑，只是不会被推送；随时可以撤销。`)) return;
  const prevRows = rowIds.map(id => ({ row_id: id, field: 'excluded', value: (table.getRow(id)?.getData().excluded) || '' }));
  const r = await fetch('/api/decisions/bulk-exclude', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ row_ids: rowIds, excluded }),
  }).then(r => r.json());
  if (r.ok) {
    pushUndo(`批量${verb}（${r.updated} 行）`, prevRows);
    setStatus(`已${verb} ${r.updated} 行`); loadDecisions();
  }
  else setStatus('失败: ' + r.error, true);
}
$('#bulkExcludeBtn').addEventListener('click', () => bulkExclude(true));
$('#bulkUnexcludeBtn').addEventListener('click', () => bulkExclude(false));

// 新增：按术语精确匹配排除（在已加载到浏览器的全表数据里找，跟当前筛选/勾选无关）
async function excludeByTerm(excluded) {
  const term = $('#termExcludeInput').value.trim();
  if (!term) { alert('请输入术语（完全匹配 group_or_term 列）'); return; }
  const rowIds = table.getData().filter(r => r.group_or_term === term).map(r => r.row_id);
  if (!rowIds.length) { alert(`没有找到 group_or_term 精确等于「${term}」的行。`); return; }
  const verb = excluded ? '标记排除' : '取消排除';
  if (!confirm(`对术语「${term}」匹配到的 ${rowIds.length} 行执行「${verb}」？`)) return;
  const prevRows = rowIds.map(id => ({ row_id: id, field: 'excluded', value: (table.getRow(id)?.getData().excluded) || '' }));
  const r = await fetch('/api/decisions/bulk-exclude', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ row_ids: rowIds, excluded }),
  }).then(r => r.json());
  if (r.ok) {
    pushUndo(`按术语「${term}」${verb}（${r.updated} 行）`, prevRows);
    setStatus(`已${verb} ${r.updated} 行`); loadDecisions();
  } else setStatus('失败: ' + r.error, true);
}
$('#termExcludeBtn').addEventListener('click', () => excludeByTerm(true));
$('#termUnexcludeBtn').addEventListener('click', () => excludeByTerm(false));

// ------------------------------------------------------ find & replace ---
function updateFrScopeHint() {
  const scope = $('#frScope').value;
  if (scope === 'selected') $('#frScopeHint').textContent = `将只作用于当前勾选的 ${getSelectedRowIds().length} 行`;
  else if (scope === 'filtered') $('#frScopeHint').textContent = `将只作用于当前筛选出的 ${getFilteredRowIds().length} 行`;
  else $('#frScopeHint').textContent = '将作用于全表所有行';
}
$('#frScope').addEventListener('change', updateFrScopeHint);
$('#findReplaceBtn').addEventListener('click', () => {
  $('#frModal').classList.remove('hidden');
  updateFrScopeHint();
});
$('#frCloseBtn').addEventListener('click', () => $('#frModal').classList.add('hidden'));

async function runFindReplace(dryRun) {
  const scope = $('#frScope').value;
  let row_ids = null;
  if (scope === 'selected') {
    row_ids = getSelectedRowIds();
    if (!row_ids.length) { $('#frResult').textContent = '没有勾选任何行。'; return; }
  } else if (scope === 'filtered') {
    row_ids = getFilteredRowIds();
    if (!row_ids.length) { $('#frResult').textContent = '当前筛选结果为空。'; return; }
  }
  const body = {
    field: $('#frField').value,
    find: $('#frFind').value,
    replace: $('#frReplace').value,
    use_regex: $('#frRegex').checked,
    only_decision: $('#frOnlyDecision').value,
    row_ids: row_ids,
    dry_run: dryRun,
  };
  const r = await fetch('/api/decisions/find-replace', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json());
  if (!r.ok) { $('#frResult').textContent = '错误: ' + r.error; return; }
  let html = `<p>匹配 ${r.match_count} 行${r.applied ? '，已应用' : '（预览，未写入）'}</p>`;
  html += '<ul>' + r.preview.map(m => `<li><b>${m.row_id}</b>: "${m.before}" → "${m.after}"</li>`).join('') + '</ul>';
  $('#frResult').innerHTML = html;
  if (r.applied) {
    if (r.preview.length) {
      pushUndo(`查找替换 ${body.field}（${r.match_count} 行）`,
        r.preview.map(m => ({ row_id: m.row_id, field: body.field, value: m.before })));  // 原来是 key: m.key
      if (r.match_count > r.preview.length) {
        setStatus(`已应用替换 ${r.match_count} 行；注意：撤销只覆盖预览里展示的前 ${r.preview.length} 行，其余需要手动改回`, true);
      }
    }
    await loadDecisions();
  }
}
$('#frPreviewBtn').addEventListener('click', () => runFindReplace(true));
$('#frApplyBtn').addEventListener('click', () => {
  if (confirm('确认对该范围执行替换？此操作会直接写入 review_decisions.csv。')) runFindReplace(false);
});

// --------------------------------------------------------------- push ----
async function doPush(dryRun) {
  if (!dryRun && !confirm('确认真实提交到 Paratranz？此操作不可撤销。')) return;
  $('#pushSummary').textContent = dryRun ? '演练中…' : '提交中，请勿关闭页面…';
  const r = await fetch('/api/push', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dry_run: dryRun }) }).then(r => r.json());
  if (!r.ok) { $('#pushSummary').textContent = '失败: ' + r.error; return; }
  $('#pushSummary').textContent =
    `${dryRun ? '[演练] ' : ''}共 ${r.total} 行待推送，其中 ${r.not_found} 条未在远程找到` +
    (r.skipped_excluded ? `，另有 ${r.skipped_excluded} 条因已排除被跳过` : '') +
    (dryRun ? '' : `，成功 ${r.reset_count} 条并已在决策表中重置`);
  renderPushLog(r.log);
  if (!dryRun) loadDecisions();
}
$('#dryRunBtn').addEventListener('click', () => doPush(true));
$('#pushBtn').addEventListener('click', () => doPush(false));

let pushTable;
function renderPushLog(rows) {
  if (!pushTable) {
    pushTable = new Tabulator('#pushLogGrid', {
      data: rows, height: '50vh', layout: 'fitColumns',
      columns: [
        { title: 'key', field: 'key', width: 160 },
        { title: 'old_translation', field: 'old_translation' },
        { title: 'new_translation', field: 'new_translation' },
        { title: 'status', field: 'status', width: 140 },
        { title: 'http_code', field: 'http_code', width: 90 },
      ],
    });
  } else {
    pushTable.setData(rows);
  }
}
async function loadPushLog() {
  const r = await fetch('/api/push-log').then(r => r.json());
  if (r.rows && r.rows.length) renderPushLog(r.rows);
}

// --------------------------------------------------------------- init ----
loadConfig();
loadDecisions();