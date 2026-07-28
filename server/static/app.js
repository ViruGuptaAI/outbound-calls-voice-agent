// ── State ────────────────────────────────────────────────────────
let ws = null;
let audioContext = null;
let mediaStream = null;
let scriptProcessor = null;
let isPlaying = false;
let audioQueue = [];
let callTimer = null;
let callStart = null;

const SAMPLE_RATE = 24000;
const BUFFER_SIZE = 4096;

// ── Call context (from selection console via sessionStorage) ──────
const CALL = (() => {
    try { return JSON.parse(sessionStorage.getItem('outboundCall')) || {}; }
    catch (_) { return {}; }
})();
const CAMPAIGN_ID = CALL.campaignId || 'home_loan';
const CUSTOMER_ID = CALL.customerId || 'rajesh';
const CALL_TONE = CALL.tone || 'cordial';
const AGENT_NAME = CALL.agentName || 'Agent';
const CAMPAIGN_COLOR = CALL.campaignColor || '#4299e1';

// Short labels for displaying the selected tone in the call sidebar.
const TONE_LABELS = {
    cordial: '🙂 Cordial',
    firm: '💬 Firm',
    stern: '⚠️ Stern',
    aggressive: '🔴 Aggressive',
};

// Populate the call header/sidebar from context
document.addEventListener('DOMContentLoaded', () => {
    if (!CALL.campaignId) { window.location.href = '/'; return; }
    const set = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
    set('call-icon', CALL.campaignIcon || '📞');
    set('call-campaign', CALL.campaignTitle || 'Campaign');
    set('call-agent', 'Agent: ' + AGENT_NAME);
    set('cust-name', CALL.customerName || CUSTOMER_ID);
    set('current-agent-label', AGENT_NAME);
    set('call-tone', TONE_LABELS[CALL_TONE] || TONE_LABELS.cordial);
    const badge = document.getElementById('agent-badge');
    if (badge) {
        badge.textContent = (CALL.campaignIcon || '📞') + ' ' + AGENT_NAME;
        badge.style.background = CAMPAIGN_COLOR;
        badge.style.boxShadow = `0 2px 10px ${CAMPAIGN_COLOR}44`;
    }
    const card = document.getElementById('call-card');
    if (card) card.style.setProperty('--campaign-color', CAMPAIGN_COLOR);
});

// ── Audio visualizer ─────────────────────────────────────────────
const visualizerEl = document.getElementById('visualizer');
const NUM_BARS = 20;
for (let i = 0; i < NUM_BARS; i++) {
    const bar = document.createElement('div');
    bar.className = 'visualizer-bar';
    bar.style.height = '4px';
    visualizerEl.appendChild(bar);
}

function animateVisualizer(active) {
    const bars = visualizerEl.querySelectorAll('.visualizer-bar');
    if (!active) {
        bars.forEach(b => b.style.height = '4px');
        return;
    }
    bars.forEach(b => {
        b.style.height = `${4 + Math.random() * 30}px`;
    });
}

// ── Start conversation ───────────────────────────────────────────
async function startConversation() {
    document.getElementById('btn-start').disabled = true;
    document.getElementById('btn-stop').disabled = false;

    // Clear messages
    const messagesEl = document.getElementById('messages');
    messagesEl.innerHTML = '';
    addSystemMessage('📞 Dialing ' + (CALL.customerName || 'customer') + '…');

    // Microphone access
    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                sampleRate: SAMPLE_RATE,
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
            }
        });
    } catch (err) {
        addSystemMessage('⚠️ Microphone access denied. Please allow microphone access.');
        document.getElementById('btn-start').disabled = false;
        document.getElementById('btn-stop').disabled = true;
        return;
    }

    // Audio context for recording
    audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });

    // Initialize AudioWorklet for playback (must be done after user gesture)
    await initAudioWorklet();

    const source = audioContext.createMediaStreamSource(mediaStream);
    scriptProcessor = audioContext.createScriptProcessor(BUFFER_SIZE, 1, 1);

    scriptProcessor.onaudioprocess = (e) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            const float32 = e.inputBuffer.getChannelData(0);
            const pcm16 = new Int16Array(float32.length);
            for (let i = 0; i < float32.length; i++) {
                const s = Math.max(-1, Math.min(1, float32[i]));
                pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            ws.send(pcm16.buffer);
        }
    };

    source.connect(scriptProcessor);
    scriptProcessor.connect(audioContext.destination);

    // WebSocket connection
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/web/ws`);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
        // Send campaign + customer selection (and tone) as the first message
        ws.send(JSON.stringify({ campaignId: CAMPAIGN_ID, customerId: CUSTOMER_ID, tone: CALL_TONE }));

        updateStatus('connected');
        addSystemMessage('✅ Call connected — the agent is on the line.');
        callStart = Date.now();
        callTimer = setInterval(updateDuration, 1000);
    };

    ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
            // Raw PCM16 audio from agent — play through worklet
            stopHoldMusic(); // Kill hold music if playing
            playAudio(event.data);
            animateVisualizer(true);
            showSpeakingIndicator(true);
            clearTimeout(window._speakTimeout);
            window._speakTimeout = setTimeout(() => {
                animateVisualizer(false);
                showSpeakingIndicator(false);
            }, 300);
        } else {
            // JSON control message
            try {
                const msg = JSON.parse(event.data);
                handleControlMessage(msg);
            } catch (e) {
                console.warn('Non-JSON text message:', event.data);
            }
        }
    };

    ws.onclose = () => {
        updateStatus('disconnected');
        addSystemMessage('Call ended.');
        cleanup();
    };

    ws.onerror = () => {
        addSystemMessage('⚠️ Connection error.');
        cleanup();
    };
}

// ── Handle control messages ──────────────────────────────────────
function handleControlMessage(msg) {
    switch (msg.Kind) {
        case 'StopAudio':
            stopPlayback();
            break;

        case 'AgentTranscription':
            clearToolStatus();
            addAgentMessage(msg.Text, msg.Agent);
            break;

        case 'ReplaceLastAgent':
            replaceLastAgentMessage(msg.Text, msg.Agent);
            break;

        case 'UserTranscription':
            clearToolStatus();
            addUserMessage(msg.Text);
            break;

        case 'AgentSwitch':
            handleAgentSwitch(msg.AgentKey, msg.Agent);
            break;

        case 'PlayHoldMusic':
            playHoldMusic(msg.Duration || 5);
            break;

        case 'ToolStatus':
            addToolStatus(msg.Label);
            break;

        case 'EndCall':
            handleEndCall(msg.Reason, msg.GraceMs);
            break;

        case 'Escalation':
            addEscalationBanner(msg.Ticket, msg.Priority, msg.Type, msg.EmailTo);
            break;

        // Legacy compat
        case 'Transcription':
            addAgentMessage(msg.Text, '');
            break;
    }
}

// ── Agent info (single campaign agent — no handoffs in outbound) ──
function handleAgentSwitch(agentKey, agentName) {
    const name = agentName || AGENT_NAME;
    const badge = document.getElementById('agent-badge');
    if (badge) {
        badge.textContent = (CALL.campaignIcon || '📞') + ' ' + name;
        badge.style.background = CAMPAIGN_COLOR;
        badge.style.boxShadow = `0 2px 10px ${CAMPAIGN_COLOR}44`;
    }
    const label = document.getElementById('current-agent-label');
    if (label) label.textContent = name;
}

function showSpeakingIndicator(active) {
    const el = document.getElementById('speaking-indicator');
    const label = document.getElementById('speaking-label');
    if (active) {
        el.classList.add('active');
        label.textContent = `${AGENT_NAME} Speaking`;
    } else {
        el.classList.remove('active');
    }
}

// ── Audio playback (AudioWorklet ring buffer — instant barge-in) ─────
let workletNode = null;

async function initAudioWorklet() {
    if (!audioContext || workletNode) return;
    await audioContext.audioWorklet.addModule('/static/audio-processor.js');
    workletNode = new AudioWorkletNode(audioContext, 'audio-processor');
    workletNode.connect(audioContext.destination);
}

function playAudio(pcm16Buffer) {
    if (!audioContext || !workletNode) return;
    const int16 = new Int16Array(pcm16Buffer);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
        float32[i] = int16[i] / 0x8000;
    }
    workletNode.port.postMessage({ pcm: float32 });
}

function stopPlayback() {
    // Instantly clear the ring buffer — zero latency barge-in
    if (workletNode) {
        workletNode.port.postMessage({ clear: true });
    }
    showSpeakingIndicator(false);
    animateVisualizer(false);
}

// ── Hold music (uses separate AudioContext at native sample rate) ─
let holdMusicCtx = null;
let holdMusicSource = null;

function playHoldMusic(duration = 5) {
    stopHoldMusic();
    try {
        // Use a separate AudioContext at native sample rate (not 24kHz)
        holdMusicCtx = new AudioContext();
        const sr = holdMusicCtx.sampleRate;
        const len = Math.floor(sr * duration);
        const buf = holdMusicCtx.createBuffer(1, len, sr);
        const data = buf.getChannelData(0);
        // Pleasant hold melody: C4-E4-G4-C5-G4-E4 arpeggio
        const notes = [
            { freq: 261.63, start: 0, end: 1.2 },
            { freq: 329.63, start: 0.8, end: 2.0 },
            { freq: 392.00, start: 1.6, end: 2.8 },
            { freq: 523.25, start: 2.4, end: 3.6 },
            { freq: 392.00, start: 3.2, end: 4.4 },
            { freq: 329.63, start: 4.0, end: 5.0 },
        ];
        for (let i = 0; i < len; i++) {
            const t = i / sr;
            let sample = 0;
            for (const n of notes) {
                if (t >= n.start && t <= n.end) {
                    const noteT = t - n.start;
                    const noteDur = n.end - n.start;
                    const env = Math.min(1, noteT / 0.08) * Math.min(1, (noteDur - noteT) / 0.15);
                    sample += Math.sin(2 * Math.PI * n.freq * t) * 0.35 * env;
                }
            }
            data[i] = sample;
        }
        holdMusicSource = holdMusicCtx.createBufferSource();
        holdMusicSource.buffer = buf;
        const gain = holdMusicCtx.createGain();
        gain.gain.value = 0.7;
        holdMusicSource.connect(gain);
        gain.connect(holdMusicCtx.destination);
        holdMusicSource.start();
        console.log('[HoldMusic] Playing for', duration, 'seconds at', sr, 'Hz');
        addSystemMessage('🎵 Please hold...');
    } catch (e) {
        console.error('[HoldMusic] Failed:', e);
    }
}

function stopHoldMusic() {
    if (holdMusicSource) {
        try { holdMusicSource.stop(); } catch (_) {}
        holdMusicSource = null;
    }
    if (holdMusicCtx) {
        try { holdMusicCtx.close(); } catch (_) {}
        holdMusicCtx = null;
    }
}

// ── Message helpers ──────────────────────────────────────────────
function addSystemMessage(text) {
    const el = document.createElement('div');
    el.className = 'message system';
    el.textContent = text;
    appendMessage(el);
}

function addAgentMessage(text, agent) {
    const el = document.createElement('div');
    el.className = 'message agent';
    if (agent) {
        const label = document.createElement('div');
        label.className = 'agent-label';
        label.textContent = agent;
        el.appendChild(label);
    }
    const content = document.createElement('div');
    content.className = 'agent-content';
    content.textContent = text;
    el.appendChild(content);
    appendMessage(el);
}

function replaceLastAgentMessage(text, agent) {
    const container = document.getElementById('messages');
    const agents = container.querySelectorAll('.message.agent');
    if (agents.length === 0) return;
    const last = agents[agents.length - 1];
    const content = last.querySelector('.agent-content');
    if (content) {
        content.textContent = text;
    }
}

function addUserMessage(text) {
    const el = document.createElement('div');
    el.className = 'message user';
    el.textContent = text;
    appendMessage(el);
}

// ── Tool status (ChatGPT-style workflow steps) ───────────────────
function addToolStatus(label) {
    const container = document.getElementById('messages');
    let group = container.querySelector('.tool-status-group:last-child');

    // If the last element isn't a tool-status-group, or an agent message came in between, create new
    if (!group || group.nextElementSibling) {
        group = document.createElement('div');
        group.className = 'tool-status-group';
        container.appendChild(group);
    }

    const step = document.createElement('div');
    step.className = 'tool-step';
    step.innerHTML = `<span class="tool-step-spinner"></span><span class="tool-step-label">${label}</span>`;
    group.appendChild(step);

    // Animate in
    requestAnimationFrame(() => step.classList.add('visible'));

    container.scrollTop = container.scrollHeight;
}

function clearToolStatus() {
    const container = document.getElementById('messages');
    container.querySelectorAll('.tool-status-group').forEach(group => {
        group.querySelectorAll('.tool-step').forEach(step => {
            step.classList.add('done');
            step.querySelector('.tool-step-spinner')?.classList.add('done');
        });
        // Fade out after a short delay
        setTimeout(() => {
            group.classList.add('fade-out');
            setTimeout(() => group.remove(), 400);
        }, 600);
    });
}

function addHandoffMessage(text) {
    // Legacy fallback — rich version is used via addRichHandoffMessage
    const el = document.createElement('div');
    el.className = 'message handoff';
    el.innerHTML = `<div class="handoff-banner-header">Agent Handoff</div><div class="handoff-banner-body">${text}</div>`;
    appendMessage(el);
}

function appendMessage(el) {
    const container = document.getElementById('messages');
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
}

// ── Status helpers ───────────────────────────────────────────────
function updateStatus(state) {
    const el = document.getElementById('conn-status');
    if (state === 'connected') {
        el.textContent = 'Connected';
        el.className = 'status-value connected';
    } else {
        el.textContent = 'Disconnected';
        el.className = 'status-value disconnected';
    }
}

function updateDuration() {
    if (!callStart) return;
    const elapsed = Math.floor((Date.now() - callStart) / 1000);
    const m = Math.floor(elapsed / 60);
    const s = elapsed % 60;
    document.getElementById('call-duration').textContent =
        `${m}:${s.toString().padStart(2, '0')}`;
}

// ── Stop / cleanup ───────────────────────────────────────────────
function stopConversation() {
    if (ws) ws.close();
    cleanup();
    addSystemMessage('Call ended by user.');
}

// ── Server-initiated graceful end (agent hung up / idle timeout) ──
function handleEndCall(reason, graceMs) {    addSystemMessage('📞 ' + (reason || 'Ending the call…'));
    // Stop capturing and sending mic audio immediately so we don't keep the
    // line open, but keep the audio context alive so the agent's farewell
    // finishes playing before we tear everything down.
    if (scriptProcessor) { try { scriptProcessor.disconnect(); } catch (_) {} scriptProcessor = null; }
    if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
    }
    const grace = Math.max(500, Math.min(graceMs || 4000, 15000));
    setTimeout(() => {
        if (ws) { try { ws.close(); } catch (_) {} }
        cleanup();
        addSystemMessage('Call ended.');
    }, grace);
}

// ── Escalation banner (handoff to a human agent) ─────────────────
function addEscalationBanner(ticket, priority, type, emailTo) {
    const resolved = type === 'resolved_handoff';
    const title = resolved ? 'Handed off to a human agent' : 'Escalated to a human agent';
    const icon = resolved ? '📨' : '🚨';
    const parts = [];
    if (ticket) parts.push('Ticket ' + ticket);
    if (priority) parts.push('Priority: ' + String(priority).toUpperCase());
    if (emailTo) parts.push('Emailed ' + emailTo);
    const el = document.createElement('div');
    el.className = 'message handoff';
    el.innerHTML =
        `<div class="handoff-banner-header">${icon} ${title}</div>` +
        `<div class="handoff-banner-body">${parts.join(' · ')}</div>`;
    appendMessage(el);
}

function cleanup() {
    document.getElementById('btn-start').disabled = false;
    document.getElementById('btn-stop').disabled = true;
    updateStatus('disconnected');

    if (callTimer) { clearInterval(callTimer); callTimer = null; }
    if (scriptProcessor) { scriptProcessor.disconnect(); scriptProcessor = null; }
    if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
    }
    if (audioContext) { audioContext.close(); audioContext = null; }
    ws = null;
    workletNode = null;
}
