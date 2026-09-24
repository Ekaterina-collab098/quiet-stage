let accessCode = sessionStorage.getItem('quiet-stage-access') || '';
let csrfToken = '';
let formStep = 1;
const feedbackTemplate = 'В работе уже есть своя интонация и образ. Обрати внимание, как меняется настроение от начала к финалу. Для следующей версии можно попробовать чуть яснее обозначить переход между этими состояниями. Ты можешь доработать текст или оставить его в текущем виде: решение остается за тобой.';

const esc = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
const toast = message => { const el = document.querySelector('#toast'); el.textContent = message; el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 2800); };
const api = async (url, options = {}) => { const response = await fetch(url, options); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || 'Не удалось выполнить запрос'); return data; };

function route(name) {
  document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
  (document.querySelector(`#page-${name}`) || document.querySelector('#page-home')).classList.add('active');
  document.querySelector('.nav')?.classList.remove('open');
  document.querySelector('#menuButton')?.setAttribute('aria-expanded', 'false');
  document.querySelectorAll('.nav [data-route]').forEach(link => link.toggleAttribute('aria-current', link.dataset.route === name));
  if (name === 'status') renderStatus();
  if (name === 'moderator') checkModerator();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

const formStepCopy = {
  1: ['Шаг 1 из 3', 'Давай познакомимся', 'Настоящее имя не нужно. Выбери псевдоним, под которым модератор увидит твою работу.'],
  2: ['Шаг 2 из 3', 'Теперь твоя работа', 'Текст или аудио увидит только модератор. Черновик и рабочее название тоже подходят.'],
  3: ['Шаг 3 из 3', 'Все под твоим контролем', 'Проверь условия. После отправки ты получишь личный код для статуса и отзыва.']
};

function showFormStep(step) {
  formStep = step;
  document.querySelectorAll('[data-form-step]').forEach(item => item.classList.toggle('active', Number(item.dataset.formStep) === step));
  document.querySelectorAll('[data-wizard-dot]').forEach(item => {
    const itemStep = Number(item.dataset.wizardDot);
    item.classList.toggle('active', itemStep === step);
    item.classList.toggle('done', itemStep < step);
  });
  const [kicker, title, description] = formStepCopy[step];
  document.querySelector('#submitKicker').textContent = kicker;
  document.querySelector('#submitTitle').textContent = title;
  document.querySelector('#submitDescription').textContent = description;
  document.querySelector('#wizardBack').classList.toggle('hidden', step === 1);
  document.querySelector('#wizardNext').classList.toggle('hidden', step === 3);
  document.querySelector('#wizardSubmit').classList.toggle('hidden', step !== 3);
  showError('formError', '');
}

function currentStepIsValid() {
  const fields = document.querySelector(`[data-form-step="${formStep}"]`).querySelectorAll('input, select, textarea');
  for (const field of fields) if (!field.checkValidity()) { field.reportValidity(); return false; }
  return true;
}

function showError(id, message) { const el = document.querySelector(`#${id}`); if (el) el.textContent = message; }

async function renderStatus() {
  const empty = document.querySelector('#statusEmpty'); const content = document.querySelector('#statusContent');
  document.querySelector('#accessCodeInput').value = accessCode;
  if (!accessCode) { empty.classList.remove('hidden'); content.classList.add('hidden'); return; }
  try { const item = await api(`/api/submissions/status/${encodeURIComponent(accessCode)}`); empty.classList.add('hidden'); content.classList.remove('hidden');
    const reviewed = item.status !== 'submitted';
    content.innerHTML = `<article class="card status-card"><span class="eyebrow">${reviewed ? 'Отзыв готов' : 'Получено'}</span><h3>${esc(item.title)}</h3><div class="status-meta">${esc(item.alias)} · ${esc(item.format)} · ${esc(item.genre)}</div><div class="notice"><strong>Код отслеживания: ${esc(accessCode)}</strong><p>Сохраните его: без кода открыть статус с другого устройства или после закрытия браузера не получится.</p></div><div class="progress"><div class="progress-step done">01<br>Работа получена</div><div class="progress-step ${reviewed ? 'done' : ''}">02<br>Отзыв модератора</div><div class="progress-step ${item.status === 'shown' ? 'done' : ''}">03<br>Контролируемый показ</div><div class="progress-step ${item.nextStep ? 'done' : ''}">04<br>Следующий шаг</div></div>${reviewed ? `<div class="feedback"><h4>Первый отзыв модератора</h4><p>${esc(item.feedback)}</p></div><div class="next-actions"><button class="small-button" data-next="Доработать работу">Доработать</button><button class="small-button" data-next="Показать пилотной группе">Показать группе</button><button class="small-button" data-next="Остаться в закрытом режиме">Остаться в закрытом режиме</button><button class="small-button" data-next="Поставить на паузу">Поставить на паузу</button></div>` : '<div class="notice"><strong>Работа на проверке</strong><p>Первый отзыв появится после действия модератора. В открытый доступ работа не попадет автоматически.</p></div>'}</article>`;
    content.querySelectorAll('[data-next]').forEach(button => button.addEventListener('click', async () => { try { await api('/api/submissions/next-step', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ accessCode, nextStep: button.dataset.next }) }); toast('Следующий шаг сохранен'); renderStatus(); } catch (error) { toast(error.message); } }));
  } catch (error) { accessCode = ''; sessionStorage.removeItem('quiet-stage-access'); empty.classList.remove('hidden'); content.classList.add('hidden'); showError('lookupError', error.message); }
}

async function checkModerator() {
  const login = document.querySelector('#moderatorLogin'); const content = document.querySelector('#moderatorContent');
  try { const data = await api('/api/moderator/submissions'); csrfToken = data.csrfToken; login.classList.add('hidden'); content.classList.remove('hidden'); renderModerator(data); } catch { login.classList.remove('hidden'); content.classList.add('hidden'); }
}

function renderModerator(data) {
  const items = data.items || [];
  const contacts = data.contactRequests || [];
  const experts = data.expertApplications || [];
  const root = document.querySelector('#moderatorContent');
  root.innerHTML = `<div class="mod-tabs"><span class="tag">Ожидают проверки: ${items.filter(x => x.status === 'submitted').length}</span><span class="tag">Вопросы: ${contacts.length}</span><span class="tag">Заявки экспертов: ${experts.length}</span><button class="small-button" id="logoutButton">Выйти</button></div><h2 class="moderator-heading">Работы авторов</h2><div class="card queue">${items.length ? items.map(item => `<div class="queue-item"><div><h3>${esc(item.title)}</h3><p>${esc(item.alias)} · ${esc(item.ageGroup)} · ${esc(item.format)} · ${new Date(item.createdAt).toLocaleDateString('ru-RU')}</p><span class="tag">${item.status === 'submitted' ? 'На модерации' : item.nextStep || 'Отзыв готов'}</span></div>${item.status === 'submitted' ? `<button class="button primary review" data-id="${item.id}">Проверить</button>` : `<button class="small-button view" data-id="${item.id}">Открыть отзыв</button>`}</div>`).join('') : '<div class="empty"><h3>Очередь пуста</h3><p>Новые работы появятся после подачи.</p></div>'}</div><div class="moderator-inbox"><section><h2 class="moderator-heading">Вопросы</h2><div class="card inbox-list">${contacts.length ? contacts.map(item => `<article><strong>${esc(item.name)}</strong><a href="mailto:${esc(item.email)}">${esc(item.email)}</a><p>${esc(item.message)}</p><small>${new Date(item.created_at).toLocaleDateString('ru-RU')}</small></article>`).join('') : '<p>Новых вопросов нет.</p>'}</div></section><section><h2 class="moderator-heading">Заявки экспертов</h2><div class="card inbox-list">${experts.length ? experts.map(item => `<article><strong>${esc(item.name)} · ${esc(item.role)}</strong><a href="mailto:${esc(item.email)}">${esc(item.email)}</a><p><b>Опыт:</b> ${esc(item.experience)}</p><p><b>Мотивация:</b> ${esc(item.motivation)}</p>${item.portfolio ? `<a href="${esc(item.portfolio)}" target="_blank" rel="noopener noreferrer">Портфолио ↗</a>` : ''}<small>${new Date(item.created_at).toLocaleDateString('ru-RU')}</small></article>`).join('') : '<p>Новых заявок нет.</p>'}</div></section></div>`;
  document.querySelector('#logoutButton').onclick = async () => { await api('/api/moderator/logout', {method:'POST'}); csrfToken = ''; checkModerator(); };
  root.querySelectorAll('.review').forEach(button => button.onclick = () => openReview(button.dataset.id));
  root.querySelectorAll('.view').forEach(button => button.onclick = () => openReview(button.dataset.id));
}

async function openReview(id) {
  const items = await api('/api/moderator/submissions'); const item = items.items.find(x => x.id === id); const root = document.querySelector('#moderatorContent'); document.querySelector('#reviewBox')?.remove();
  const box = document.createElement('div'); box.id = 'reviewBox'; box.className = 'card modal-box'; box.innerHTML = `<strong>Проверка работы: ${esc(item.title)}</strong><p class="status-meta">${esc(item.alias)} · ${esc(item.ageGroup)} · ${esc(item.format)}</p><div class="work-card">${item.hasAudio ? `<audio controls preload="metadata" src="/api/moderator/submissions/${item.id}/audio"></audio>` : `<p>${esc(item.body)}</p>`}</div><label>Структурированный отзыв<textarea id="reviewText">${esc(item.feedback || feedbackTemplate)}</textarea></label><div class="form-actions"><button class="button primary" id="sendReview">Сохранить отзыв</button><button class="button ghost" id="closeReview">Отмена</button></div>`;
  root.prepend(box); document.querySelector('#closeReview').onclick = () => box.remove(); document.querySelector('#sendReview').onclick = async () => { try { await api(`/api/moderator/submissions/${id}/review`, {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken}, body:JSON.stringify({feedback:document.querySelector('#reviewText').value})}); toast('Отзыв отправлен автору'); box.remove(); checkModerator(); } catch(error) { toast(error.message); } }; box.scrollIntoView({behavior:'smooth'});
}

document.addEventListener('click', event => { const link = event.target.closest('[data-route]'); if (link) { event.preventDefault(); route(link.dataset.route); history.replaceState(null, '', `#${link.dataset.route}`); } });
document.querySelector('#menuButton').onclick = event => { const open = document.querySelector('.nav').classList.toggle('open'); event.currentTarget.setAttribute('aria-expanded', String(open)); };
document.querySelector('#wizardNext').onclick = () => { if (currentStepIsValid()) showFormStep(Math.min(3, formStep + 1)); };
document.querySelector('#wizardBack').onclick = () => showFormStep(Math.max(1, formStep - 1));
document.querySelector('#bodyField').oninput = event => document.querySelector('#counter').textContent = `${event.target.value.length} / 5000`;
document.querySelector('select[name="format"]').onchange = event => { const audio = event.target.value === 'Аудио'; document.querySelector('#audioWorkLabel').classList.toggle('hidden', !audio); document.querySelector('#textWorkLabel').classList.toggle('hidden', audio); document.querySelector('#bodyField').required = !audio; document.querySelector('#audioField').required = audio; };
document.querySelector('#ageGroup').onchange = event => { const minor = event.target.value === '14–17 лет'; document.querySelector('#guardianConsentLabel').classList.toggle('hidden', !minor); document.querySelector('#guardianConsent').required = minor; if (!minor) document.querySelector('#guardianConsent').checked = false; };
document.querySelector('#submissionForm').onsubmit = async event => { event.preventDefault(); showError('formError', ''); if (!currentStepIsValid()) return; const form = new FormData(event.target); if (!form.get('demo')) { showError('formError', 'Для локального прототипа отметьте демонстрационную работу.'); return; } try { const data = await api('/api/submissions', {method:'POST', body:form}); accessCode = data.accessCode; sessionStorage.setItem('quiet-stage-access', accessCode); event.target.reset(); document.querySelector('#audioWorkLabel').classList.add('hidden'); document.querySelector('#textWorkLabel').classList.remove('hidden'); document.querySelector('#bodyField').required = true; document.querySelector('#audioField').required = false; document.querySelector('#guardianConsentLabel').classList.add('hidden'); document.querySelector('#counter').textContent = '0 / 5000'; showFormStep(1); toast(`Код отслеживания: ${accessCode}`); route('status'); } catch(error) { showError('formError', error.message); } };
document.querySelector('#lookupButton').onclick = () => { accessCode = document.querySelector('#accessCodeInput').value.trim().toUpperCase(); sessionStorage.setItem('quiet-stage-access', accessCode); showError('lookupError', ''); renderStatus(); };
document.querySelector('#moderatorLoginButton').onclick = async () => { try { const data = await api('/api/moderator/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password:document.querySelector('#moderatorPassword').value})}); csrfToken = data.csrfToken; document.querySelector('#moderatorPassword').value = ''; checkModerator(); toast('Вход модератора выполнен'); } catch(error) { showError('moderatorLoginError', error.message); } };

document.querySelector('#questionForm').onsubmit = async event => { event.preventDefault(); showError('questionError', ''); const form = new FormData(event.target); const payload = { name: form.get('name'), email: form.get('email'), message: form.get('message'), personalData: form.has('personalData') }; try { const data = await api('/api/contact', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); event.target.reset(); toast(data.message); } catch(error) { showError('questionError', error.message); } };
document.querySelector('#expertApplicationForm').onsubmit = async event => { event.preventDefault(); showError('expertApplicationError', ''); const form = new FormData(event.target); const payload = { name: form.get('name'), email: form.get('email'), role: form.get('role'), experience: form.get('experience'), portfolio: form.get('portfolio'), motivation: form.get('motivation'), personalData: form.has('personalData') }; try { const data = await api('/api/expert-applications', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); event.target.reset(); toast(data.message); } catch(error) { showError('expertApplicationError', error.message); } };

const hash = location.hash.replace('#', ''); route(['home','how','experts','submit','status','privacy','rights','question','expert-apply','moderator'].includes(hash) ? hash : 'home');
