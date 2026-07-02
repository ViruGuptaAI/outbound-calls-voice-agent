# Contoso Bank — Outbound Calling Agents

A voice-AI demo where **the bank calls the customer**. An operator picks an
outbound **campaign** and a **customer**, and a goal-driven AI agent initiates
the call, introduces itself, and drives the conversation toward the campaign
goal. Powered by the **Azure Voice Live API** (unified STT + LLM + TTS over a
single WebSocket).

> This is the outbound counterpart to the inbound *Virtual RM* demo. Inbound =
> customer calls in → triage routes to a specialist. **Outbound = operator
> selects a campaign + customer → the matching agent places the call.**

---

## Campaigns

| Campaign | Agent | Goal |
| --- | --- | --- |
| 🏠 Home Loan Sales | **Priya** | Pitch a pre-approved home loan / balance-transfer offer and book an application. |
| 🚗 Vehicle Loan Sales | **Kavya** | Pitch a pre-approved car loan with quick disbursal and drive to an application. |
| 💳 Credit Card Collections | **Neha** | Recover an overdue payment and secure a promise-to-pay, respectfully and RBI-compliant. |

Each agent has its own persona, voice, system prompt, tool set, and a
step-by-step **playbook** that shapes the call flow.

---

## How the demo works

You **role-play the customer being called**. Click *Start Call* — the agent
"dials out", greets you, states the purpose of the call, and asks permission to
continue. Speak into your mic to respond naturally; barge-in is supported.

The browser simulation is intentionally decoupled from the media transport, so
real telephony (e.g. Azure Communication Services) can be plugged in later
without changing the agent logic.

---

## Architecture

```
Browser (mic + speaker)
   │  PCM16 @ 24 kHz over WebSocket
   ▼
Quart relay (server/app.py)
   │  single WebSocket
   ▼
Azure Voice Live API  ──►  GPT-4.1-mini + Azure Speech STT + Dragon HD TTS
   │
   └─ tool calls ──►  CRM tools (server/crm_tools.py → SQLite crm.db)
```

- **`server/app.py`** — async relay between browser and Voice Live. Handles
  session config, the outbound opening, tool-call dispatch, hold music,
  barge-in truncation, conversation compaction, and heartbeats.
- **`server/campaigns/`** — per-campaign agent prompts, tool lists, and the
  `CAMPAIGN_REGISTRY`.
- **`server/playbooks/`** — per-campaign call scripts and the set of tool names
  each campaign is allowed to use.
- **`server/crm_tools.py`** — CRM/business functions exposed to the LLM
  (customer profile, offers, EMI calc, eligibility, collections dues,
  promise-to-pay, payment links, etc.).
- **`server/seed_db.py`** — builds and seeds the local SQLite database.
- **`server/static/`** — frontend: `index.html` (campaign + customer selection
  console), `call.html` (live call UI), `console.js`, `app.js`, and the
  `audio-processor.js` AudioWorklet.

---

## Getting started

### 1. Install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure credentials

Copy `.env.sample` to `.env` and fill in your Azure Voice Live values:

```
AZURE_VOICE_LIVE_ENDPOINT=https://<your-voice-live-foundry>.services.ai.azure.com
AZURE_VOICE_LIVE_API_KEY=<your-api-key>
VOICE_LIVE_MODEL=gpt-4.1-mini
```

Auth priority: API key → Managed Identity → Azure CLI credential.

### 3. Seed the local database

```powershell
cd server
python seed_db.py
```

This regenerates `server/crm.db` (git-ignored) with demo customers, loan
products, negotiation rules, competitor rates, and overdue collections
accounts.

### 4. Run the server

```powershell
python app.py
```

Open <http://localhost:8000>, pick a campaign and a customer, and click
**Start Call**.

---

## Notes

- The database is disposable — re-run `python seed_db.py` any time to reset it.
- Voice cannot be exercised without valid Azure Voice Live credentials in
  `.env`; the console UI and CRM APIs work regardless.
