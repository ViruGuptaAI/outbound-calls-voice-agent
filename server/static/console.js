// ── Outbound Calling Console — campaign + customer selection ──────
let selectedCampaign = null;
let campaignsById = {};
let customersById = {};
let allCustomers = [];

const gridEl = document.getElementById('campaign-grid');
const selectEl = document.getElementById('customer-select');
const detailEl = document.getElementById('customer-detail');
const launchBtn = document.getElementById('btn-launch');
const hintEl = document.getElementById('launch-hint');
const toneEl = document.getElementById('tone-select');
const toneHintEl = document.getElementById('tone-hint');
const langEl = document.getElementById('lang-select');
const langHintEl = document.getElementById('lang-hint');

// ── Language selector (multi-select, up to 3, order = priority) ───
// The FIRST language picked is the one the bot opens in; it can switch
// among all chosen languages. Selection order is tracked so "primary"
// reflects what the operator picked first.
const LANG_LABELS = {
    english: 'English (India)', hindi: 'Hindi', marathi: 'Marathi', kannada: 'Kannada',
    telugu: 'Telugu', tamil: 'Tamil', gujarati: 'Gujarati', odia: 'Odia (Oriya)',
    bengali: 'Bengali', malayalam: 'Malayalam',
};
const MAX_LANGS = 3;
let langOrder = ['english', 'hindi']; // default selection order (primary first)

function updateLangHint() {
    if (!langHintEl) return;
    if (!langOrder.length) {
        langHintEl.textContent = 'Pick at least one language (default: English + Hindi).';
        return;
    }
    const primary = LANG_LABELS[langOrder[0]];
    const others = langOrder.slice(1).map(k => LANG_LABELS[k]);
    langHintEl.textContent = others.length
        ? `Opens in ${primary}; can also switch to ${others.join(', ')}.`
        : `The whole call will be in ${primary}.`;
}

if (langEl) {
    langEl.addEventListener('change', () => {
        const selected = Array.from(langEl.selectedOptions).map(o => o.value);
        // Preserve prior order for still-selected items, then append newly picked ones.
        langOrder = langOrder.filter(v => selected.includes(v));
        selected.forEach(v => { if (!langOrder.includes(v)) langOrder.push(v); });
        // Enforce the cap: keep the first MAX_LANGS picked; deselect the rest.
        if (langOrder.length > MAX_LANGS) langOrder = langOrder.slice(0, MAX_LANGS);
        Array.from(langEl.options).forEach(o => { o.selected = langOrder.includes(o.value); });
        updateLangHint();
    });
    updateLangHint();
}


// ── Tone selector ────────────────────────────────────────────────
// In collections, tonality escalates with contact attempts / days past due.
// The operator picks how firmly the agent should speak (server enforces an
// RBI fair-practices compliance floor so even "stern" never harasses).
const TONE_HINTS = {
    cordial: 'Warm and patient — best for a first, good-faith reminder.',
    firm: 'Direct and businesslike — for a follow-up on a rising overdue.',
    stern: 'Serious and urgent — a final notice on a seriously overdue account.',
    aggressive: 'Hardest lawful push — forceful, high-pressure delivery; still no threats or abuse.',
};

function updateToneHint() {
    toneHintEl.textContent = TONE_HINTS[toneEl.value] || '';
}

toneEl.addEventListener('change', updateToneHint);
updateToneHint();

// ── Load campaigns ───────────────────────────────────────────────
async function loadCampaigns() {
    try {
        const res = await fetch('/api/campaigns');
        const campaigns = await res.json();
        gridEl.innerHTML = '';
        campaigns.forEach(c => {
            campaignsById[c.id] = c;
            const card = document.createElement('button');
            card.className = 'campaign-card';
            card.dataset.id = c.id;
            card.style.setProperty('--campaign-color', c.color);
            card.innerHTML = `
                <div class="campaign-icon">${c.icon}</div>
                <div class="campaign-title">${c.title}</div>
                <div class="campaign-agent">Agent: ${c.agent}</div>
                <div class="campaign-desc">${c.description}</div>
            `;
            card.addEventListener('click', () => selectCampaign(c.id));
            gridEl.appendChild(card);
        });
    } catch (e) {
        gridEl.innerHTML = '<div class="campaign-loading">⚠️ Failed to load campaigns.</div>';
        console.error(e);
    }
}

function selectCampaign(id) {
    selectedCampaign = id;
    document.querySelectorAll('.campaign-card').forEach(el => {
        el.classList.toggle('selected', el.dataset.id === id);
    });
    renderCustomers();
    updateLaunchState();
}

// ── Load customers ───────────────────────────────────────────────
async function loadCustomers() {
    try {
        const res = await fetch('/api/customers');
        allCustomers = await res.json();
        allCustomers.forEach(c => { customersById[c.id] = c; });
        renderCustomers();
    } catch (e) {
        selectEl.innerHTML = '<option value="">⚠️ Failed to load customers</option>';
        console.error(e);
    }
}

// Populate the dropdown, filtered to the selected campaign's audience.
// Collections campaigns declare audience 'card_overdue' or 'loan_overdue' so
// customers with no dues for that product are hidden entirely.
function renderCustomers() {
    const camp = selectedCampaign ? campaignsById[selectedCampaign] : null;
    const audience = camp ? camp.audience : null;
    const list = audience ? allCustomers.filter(c => c[audience]) : allCustomers;

    const prev = selectEl.value;
    selectEl.innerHTML = list.length
        ? '<option value="">— Select a customer —</option>'
        : '<option value="">— No customers with dues for this campaign —</option>';
    list.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = `${c.name} · ${c.segment}${c.overdue ? ' · ⚠ Overdue' : ''}`;
        selectEl.appendChild(opt);
    });
    if (prev && list.some(c => c.id === prev)) {
        selectEl.value = prev;
    } else {
        detailEl.innerHTML = '';
    }
}

selectEl.addEventListener('change', () => {
    const c = customersById[selectEl.value];
    if (c) {
        detailEl.innerHTML = `
            <div class="cust-row"><span>Segment</span><b>${c.segment}</b></div>
            <div class="cust-row"><span>City</span><b>${c.city || '—'}</b></div>
            <div class="cust-row"><span>Phone</span><b>${c.phone || '—'}</b></div>
            ${c.overdue ? `<div class="cust-flag">⚠ ${c.card_overdue && c.loan_overdue ? 'Has overdue credit-card and vehicle-loan dues' : c.card_overdue ? 'Has an overdue credit-card balance' : c.loan_overdue ? 'Has an overdue vehicle-loan EMI' : 'Has an overdue life insurance premium'}</div>` : ''}
        `;
    } else {
        detailEl.innerHTML = '';
    }
    updateLaunchState();
});

// ── Launch ───────────────────────────────────────────────────────
function updateLaunchState() {
    const ready = selectedCampaign && selectEl.value;
    launchBtn.disabled = !ready;
    if (ready) {
        const camp = campaignsById[selectedCampaign];
        const cust = customersById[selectEl.value];
        hintEl.textContent = `${camp.agent} will call ${cust.name} for “${camp.title}”.`;
    } else {
        hintEl.textContent = 'Select a campaign and a customer to continue.';
    }
}

launchBtn.addEventListener('click', () => {
    if (!selectedCampaign || !selectEl.value) return;
    sessionStorage.setItem('outboundCall', JSON.stringify({
        campaignId: selectedCampaign,
        customerId: selectEl.value,
        tone: toneEl.value,
        languages: langOrder.length ? langOrder.slice(0, MAX_LANGS) : ['english', 'hindi'],
        campaignTitle: campaignsById[selectedCampaign].title,
        agentName: campaignsById[selectedCampaign].agent,
        campaignIcon: campaignsById[selectedCampaign].icon,
        campaignColor: campaignsById[selectedCampaign].color,
        customerName: customersById[selectEl.value].name,
    }));
    window.location.href = '/call';
});

loadCampaigns();
loadCustomers();
