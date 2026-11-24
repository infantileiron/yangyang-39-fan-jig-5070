import { download, get, importFile, post } from '../api/client.js';
import { table } from '../components/common/data.js';
import { intro } from '../components/common/frame.js';
import { fieldControl, formError } from '../components/common/forms.js';
import { correctionStatusName, Role } from '../constants/enums.js';
import { bindDownloads, bindForm, notify } from '../hooks/actions.js';
import { formValues } from '../types/form.js';
import { esc, timeLabel } from '../utils/html.js';

const correctionFields = [
  {key:'trial', label:'扇面试片号', example:'PX-X07', type:'text'},
  {key:'basis_warp', label:'原读数·边缘最大翘边（mm）', example:'1.8', type:'number', min:0, max:40, required:false},
  {key:'basis_energy', label:'原读数·单片定型能耗（Wh）', example:'13.2', type:'number', min:0.1, max:500, required:false},
  {key:'corrected_warp', label:'更正后·边缘最大翘边（mm）', type:'number', min:0, max:40, required:false},
  {key:'corrected_energy', label:'更正后·单片定型能耗（Wh）', type:'number', min:0.1, max:500, required:false},
];

function correctionPayload(form) {
  const values = formValues(form, correctionFields);
  const basis = {};
  const corrected = {};
  if (values.basis_warp !== undefined) basis.warp = values.basis_warp;
  if (values.basis_energy !== undefined) basis.energy = values.basis_energy;
  if (values.corrected_warp !== undefined) corrected.warp = values.corrected_warp;
  if (values.corrected_energy !== undefined) corrected.energy = values.corrected_energy;
  return {trial: values.trial, basis, corrected};
}

function correctionTable(items, writable, supervisor) {
  const columns = [
    {label:'更正号', key:'id'},
    {label:'试片号', key:'trial'},
    {label:'原读数', html: row => esc(`翘边 ${row.basis.warp ?? '—'}／耗电 ${row.basis.energy ?? '—'}`)},
    {label:'更正读数', html: row => esc(`翘边 ${row.corrected.warp ?? '—'}／耗电 ${row.corrected.energy ?? '—'}`)},
    {label:'状态', html: row => `<span class="status status-corr-${esc(row.status)}">${esc(correctionStatusName[row.status] || row.status)}</span>`},
    {label:'登记人', key:'created_by'},
    {label:'登记时间', html: row => timeLabel(row.created_at)},
  ];
  if (supervisor) {
    columns.push({label:'核实', html: row => row.status === 'pending'
      ? `<button class="verify-corr" data-id="${row.id}">核实更正</button>` : '—'});
  }
  return table(columns, items, '暂无读数更正。补抄耗电或发现读数有误时，带原读数登记更正。');
}

export async function observationPage(spec, user, route) {
  const offset = Math.max(0, Number.parseInt(route.id, 10) || 0);
  const [{total, items}, corrections] = await Promise.all([
    get(`/observations?offset=${offset}&limit=20`),
    get('/corrections'),
  ]);
  const writable = user.role !== Role.VIEWER;
  const supervisor = user.role === Role.SUPERVISOR;
  return `${intro('数据台账', spec.source, `<span class="count">${total} <small>条${esc(spec.record_name)}</small></span>`)}
    ${writable ? `<div class="tools"><details><summary>登记一条${esc(spec.record_name)}</summary><form id="record-form"><div class="form-grid">${spec.fields.map(field => fieldControl(field)).join('')}</div>${formError}<button class="primary">保存记录</button></form></details>
    <details><summary>按模板导入 CSV</summary><form id="import-form"><p>使用 UTF-8 CSV，每次最多 500 条。重复行自动跳过。</p><button type="button" data-download="/observations/template">下载模板</button><label class="field"><span>CSV 文件</span><input type="file" name="file" accept=".csv,text/csv" required></label>${formError}<button class="primary">导入文件</button></form></details>
    <details><summary>登记读数更正（补抄／复测）</summary><form id="correction-form"><p>更正必须带原读数；同一试片号加原读数重复提交不会新增记录。已核实更正会锁定，晚到的旧记录不能冲掉。</p><div class="form-grid">${correctionFields.map(field => fieldControl(field)).join('')}</div>${formError}<button class="primary">提交更正</button></form></details></div>` : ''}
    <section class="panel"><div class="panel-heading"><h2>读数更正台账<span class="count">${corrections.length} <small>条</small></span></h2></div>
    ${correctionTable(corrections, writable, supervisor)}</section>
    <section class="panel"><div class="panel-heading"><h2>${esc(spec.record_name)}明细</h2>${writable ? '<a class="text-link" href="#analysis">发起分析 →</a>' : ''}</div>
    ${table([{key:'id',label:'记录号'},...spec.fields], items.map(item => ({id:item.id,...item.data})), '暂无观测。分析员可先导入 CSV 或登记样本。')}
    ${total ? `<div class="pagination"><span>第 ${offset + 1}–${Math.min(total,offset + 20)} 条</span>${offset > 0 ? `<a href="#observations/${Math.max(0, offset-20)}">上一页</a>` : ''}${offset+20 < total ? `<a href="#observations/${offset+20}">下一页</a>` : ''}</div>` : ''}</section>`;
}

export function wireObservations(root, spec, refresh) {
  bindDownloads(root, download);
  bindForm(root.querySelector('#record-form'), async form => {
    await post('/observations', formValues(form, spec.fields));
    notify('记录已保存');
    if (location.hash === '#observations' || !location.hash) refresh();
    else location.hash = '#observations';
  });
  bindForm(root.querySelector('#import-form'), async form => {
    const report = await importFile(form.elements.file.files[0]);
    notify(`已导入 ${report.added} 条 · 跳过 ${report.skipped} 条重复记录`);
    refresh();
  });
  bindForm(root.querySelector('#correction-form'), async form => {
    const saved = await post('/corrections', correctionPayload(form));
    notify(saved.deduplicated ? `相同更正已存在（#${saved.id}），未新增记录` : `更正 #${saved.id} 已登记，等待核实`);
    refresh();
  });
  root.querySelectorAll('.verify-corr').forEach(button => {
    button.addEventListener('click', async () => {
      const note = prompt('请填写核实依据（可留空，取消则不核实）');
      if (note === null) return;
      button.disabled = true;
      try {
        const saved = await post(`/corrections/${button.dataset.id}/verify`, {note});
        notify(`更正 #${saved.id} 已核实并锁定`);
        refresh();
      } catch (err) {
        notify(err.message, true);
        button.disabled = false;
      }
    });
  });
}
