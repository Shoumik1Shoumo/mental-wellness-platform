# Safety-First Local Chatbot Architecture

## Goal

The emergency page chatbot runs on normal Django WSGI views and can work without a paid API key. The recommended free setup is:

```text
Crisis rules + Platform RAG + Local Gemini + Curated wellness docs
```

Gemini is optional. If Gemini is not running, the chatbot still uses local crisis rules, platform RAG, curated wellness RAG, and safe fallback responses.

## Runtime Flow

Browser:
- `/emergency/` renders the existing chatbot UI.
- The browser posts messages to `/emergency/chat/` with CSRF protection.
- `emergencyChatSessionId` is kept in the browser so the same chat session can continue.

Django:
- `POST /emergency/chat/` handles chatbot replies.
- `GET /emergency/chat/history/?session_id=...` loads prior messages.
- `POST /emergency/chat/recommend-doctors/` recommends doctors from local data.
- `POST /emergency/chat/detect-emergency/` runs local emergency detection.

Decision flow:

```text
User message
  ↓
High-risk / crisis?
  ↓ yes → hardcoded safety response + Kaan Pete Roi helpline guidance
  ↓ no
Platform-related?
  ↓ yes → RAG over platform pages, doctors, assessments, booking, dashboards, etc.
  ↓ no
General wellness?
  ↓ yes → curated wellness RAG + optional local Gemini response
  ↓ no
Local safe fallback
```

## AI Provider Options

### Free local provider: Gemini

```env
CHATBOT_AI_ENABLED=True
CHATBOT_AI_PROVIDER=gemini
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
CHATBOT_TIMEOUT=6
```

Recommended starter model:

```bash
gemini pull llama3.2:3b
```

Stronger model if the machine can handle it:

```bash
gemini pull llama3.1:8b
```

### Optional cloud provider: Gemini

Gemini remains supported, but it is not required.

```env
CHATBOT_AI_ENABLED=True
CHATBOT_AI_PROVIDER=gemini
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-1.5-flash
```

Never commit real API keys.

## Knowledge Sources

Platform RAG uses:
- static project docs in `home/rag_chatbot.py`
- page text from `home/templates/home/*.html`
- published blog posts
- doctor profiles
- active health task templates
- assessment questions
- curated platform Markdown docs in `chatbot_knowledge/platform/`

General wellness RAG uses:
- curated Markdown files in `chatbot_knowledge/wellness/`
- crisis/safety docs in `chatbot_knowledge/safety/`

After editing content, run:

```bash
python manage.py sync_chatbot_knowledge
```

## Safety Behavior

The chatbot:
- Checks crisis/self-harm/violence/medical-emergency phrases before AI calls.
- Keeps replies compact, usually 3-6 short sentences.
- Supports English, Bangla, and Banglish fallback replies.
- Remembers recent conversation context from `ChatSession` / `ChatMessage`, limited to recent user messages.
- Does not diagnose, prescribe medicine, provide unsafe medical advice, or claim to replace professional care.
- Never exposes API keys, environment variables, stack traces, or raw server errors.
- Logs emergency-risk messages to `EmergencyLog` for audit.

## Production / Local Check

Run:

```bash
python manage.py check_chatbot_ai
```

The command prints only safe success/failure messages. If Gemini or Gemini is unavailable, the chatbot still works with RAG and local fallback.
