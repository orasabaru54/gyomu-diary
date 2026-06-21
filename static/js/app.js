// 指導区分→指導項目のマッピング（Jinja側でGUIDANCE_CATEGORIESをセット）
// diary_form.html の <script> ブロックで GUIDANCE_CATEGORIES が定義される

function updateItems(selectEl) {
  const category = selectEl.value;
  const row = selectEl.closest('.guidance-row');
  const itemSelect = row.querySelector('.item-select');

  itemSelect.innerHTML = '<option value="">選択してください</option>';

  if (category && typeof GUIDANCE_CATEGORIES !== 'undefined' && GUIDANCE_CATEGORIES[category]) {
    GUIDANCE_CATEGORIES[category].forEach(function(item) {
      const opt = document.createElement('option');
      opt.value = item;
      opt.textContent = item;
      itemSelect.appendChild(opt);
    });
  }
}

function addGuidanceRow() {
  const template = document.getElementById('guidanceRowTemplate');
  if (!template) return;

  const clone = template.content.cloneNode(true);
  document.getElementById('guidanceContainer').appendChild(clone);
}

function removeGuidanceRow(btn) {
  const row = btn.closest('.guidance-row');
  if (!row) return;

  const container = document.getElementById('guidanceContainer');
  if (container && container.querySelectorAll('.guidance-row').length <= 1) {
    // 最後の1行は中身だけクリア
    row.querySelectorAll('select').forEach(function(s) { s.value = ''; });
    row.querySelectorAll('input[type="text"]').forEach(function(i) { i.value = ''; });
    row.querySelectorAll('textarea').forEach(function(t) { t.value = ''; });
    // item selectをリセット
    const itemSel = row.querySelector('.item-select');
    if (itemSel) {
      itemSel.innerHTML = '<option value="">← 指導区分を先に選択</option>';
    }
    return;
  }
  row.remove();
}

// 日付の初期値（今日）
document.addEventListener('DOMContentLoaded', function() {
  const dateInput = document.querySelector('input[name="date"]');
  if (dateInput && !dateInput.value) {
    const today = new Date();
    const y = today.getFullYear();
    const m = String(today.getMonth() + 1).padStart(2, '0');
    const d = String(today.getDate()).padStart(2, '0');
    dateInput.value = y + '-' + m + '-' + d;
  }
});
