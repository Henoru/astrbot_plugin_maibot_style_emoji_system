const PLUGIN = "astrbot_plugin_maibot_style_emoji_system";
const bridge = window.AstrBotPluginPage;

const state = {
  records: [],
  active: null,
};

const grid = document.querySelector("#grid");
const stats = document.querySelector("#stats");
const searchInput = document.querySelector("#searchInput");
const statusSelect = document.querySelector("#statusSelect");
const uploadInput = document.querySelector("#uploadInput");
const maintenanceBtn = document.querySelector("#maintenanceBtn");
const editor = document.querySelector("#editor");
const editorImage = document.querySelector("#editorImage");
const descriptionInput = document.querySelector("#descriptionInput");
const tagsInput = document.querySelector("#tagsInput");
const saveBtn = document.querySelector("#saveBtn");
const closeBtn = document.querySelector("#closeBtn");

function endpoint(path) {
  return `${PLUGIN}/${path}`;
}

async function apiGet(path, params) {
  const response = await bridge.apiGet(endpoint(path), params);
  if (!response?.ok) {
    throw new Error(response?.error || "请求失败");
  }
  return response;
}

async function apiPost(path, body = {}) {
  const response = await bridge.apiPost(endpoint(path), body);
  if (!response?.ok) {
    throw new Error(response?.error || "请求失败");
  }
  return response;
}

async function refresh() {
  const [emojiResponse, statsResponse] = await Promise.all([
    apiGet("emojis", {
      q: searchInput.value.trim(),
      status: statusSelect.value,
      limit: 120,
    }),
    apiGet("stats"),
  ]);
  state.records = emojiResponse.data || [];
  renderStats(statsResponse.data);
  renderGrid();
}

function renderStats(data) {
  stats.textContent = `已领养 ${data.registered}/${data.max_registered}，收录 ${data.total}，禁用 ${data.banned}`;
}

function renderGrid() {
  grid.innerHTML = "";
  if (!state.records.length) {
    const empty = document.createElement("p");
    empty.textContent = "没有匹配的表情。";
    grid.append(empty);
    return;
  }

  for (const record of state.records) {
    const item = document.createElement("article");
    item.className = "item";
    item.innerHTML = `
      <div class="thumb">
        <img src="/api/plug/${PLUGIN}/thumbnail/${record.id}" alt="" loading="lazy" />
      </div>
      <div class="body">
        <div class="desc"></div>
        <div class="tags"></div>
        <div class="meta">#${record.id} · ${record.status} · 用 ${record.usage_count}</div>
        <div class="row">
          <button type="button" data-action="edit">编辑</button>
          <button type="button" data-action="adopt">领养</button>
          <button type="button" data-action="ban">禁用</button>
          <button type="button" data-action="delete" class="danger">删除</button>
        </div>
      </div>
    `;
    item.querySelector(".desc").textContent = record.description || "未描述";
    const tags = item.querySelector(".tags");
    for (const tag of record.emotion_tags || []) {
      const span = document.createElement("span");
      span.className = "tag";
      span.textContent = tag;
      tags.append(span);
    }
    item.addEventListener("click", (event) => handleAction(event, record));
    grid.append(item);
  }
}

async function handleAction(event, record) {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  const action = button.dataset.action;
  if (action === "edit") {
    openEditor(record);
    return;
  }
  if (action === "delete" && !window.confirm(`删除表情 #${record.id}？`)) {
    return;
  }
  await apiPost(`${action}/${record.id}`);
  await refresh();
}

function openEditor(record) {
  state.active = record;
  editorImage.src = `/api/plug/${PLUGIN}/thumbnail/${record.id}`;
  descriptionInput.value = record.description || "";
  tagsInput.value = (record.emotion_tags || []).join(", ");
  editor.showModal();
}

async function saveActive() {
  if (!state.active) {
    return;
  }
  await apiPost(`update/${state.active.id}`, {
    description: descriptionInput.value,
    emotion_tags: tagsInput.value,
  });
  editor.close();
  await refresh();
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function uploadSelected() {
  const file = uploadInput.files?.[0];
  if (!file) {
    return;
  }
  const data = await fileToDataUrl(file);
  await apiPost("upload", { name: file.name, data });
  uploadInput.value = "";
  await refresh();
}

function debounce(fn, delay) {
  let timer = 0;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), delay);
  };
}

searchInput.addEventListener("input", debounce(refresh, 250));
statusSelect.addEventListener("change", refresh);
uploadInput.addEventListener("change", uploadSelected);
maintenanceBtn.addEventListener("click", async () => {
  await apiPost("maintenance");
  await refresh();
});
saveBtn.addEventListener("click", saveActive);
closeBtn.addEventListener("click", () => editor.close());

bridge.ready().then(refresh).catch((error) => {
  stats.textContent = error.message || "加载失败";
});
