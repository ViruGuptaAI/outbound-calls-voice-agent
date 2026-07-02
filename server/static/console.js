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
            ${c.overdue ? `<div class="cust-flag">⚠ ${c.card_overdue && c.loan_overdue ? 'Has overdue credit-card and vehicle-loan dues' : c.card_overdue ? 'Has an overdue credit-card balance' : 'Has an overdue vehicle-loan EMI'}</div>` : ''}
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
