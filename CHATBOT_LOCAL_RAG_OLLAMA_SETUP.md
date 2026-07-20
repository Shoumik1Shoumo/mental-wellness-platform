# Free Chatbot Setup: Crisis Rules + Platform RAG + Gemini + Curated Wellness Docs

This project no longer requires a paid API key for the emergency chatbot.

## Runtime flow

```text
User message
  ↓
Crisis / self-harm / violence / medical emergency rules
  ↓ yes → hardcoded safety response + Kaan Pete Roi helpline guidance
  ↓ no
Platform-related question?
  ↓ yes → RAG over platform pages, doctors, assessments, dashboard/payment/booking docs
  ↓ no
General wellness question?
  ↓ yes → curated wellness Markdown RAG + optional local Gemini response
  ↓ no
Local safe fallback
```

## Local Gemini setup

Install Gemini from https://gemini.com, then run:

```bash
gemini pull llama3.2:3b
gemini serve
```

For a stronger model on a better laptop:

```bash
gemini pull llama3.1:8b
```

## `.env` settings

Create or update `.env`:

```env
DEBUG=True
SECRET_KEY=dev-secret-key-change-me
ALLOWED_HOSTS=127.0.0.1,localhost,testserver
CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000

SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False

CHATBOT_AI_ENABLED=True
CHATBOT_AI_PROVIDER=gemini
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
CHATBOT_TIMEOUT=6
CHATBOT_MAX_MESSAGE_LENGTH=1000
CHATBOT_AUTO_SYNC_KNOWLEDGE=False
```

If Gemini is not running, the chatbot still answers using crisis rules, platform RAG, curated wellness RAG, and local fallback responses.

## Sync the knowledge base

After editing platform content, doctors, assessments, or files in `chatbot_knowledge/`, run:

```bash
python manage.py sync_chatbot_knowledge
```

Check AI connectivity:

```bash
python manage.py check_chatbot_ai
```

Run the app:

```bash
python manage.py runserver
```

## Curated wellness docs

The Markdown files live in:

```text
chatbot_knowledge/wellness/
chatbot_knowledge/safety/
chatbot_knowledge/platform/
```

Keep each file short and reviewed. Each file should include:

```text
Title
Category
Source
Source URL
Last reviewed
Summary
Suggested response
Bot guidance
Safety note
```

Do not put harmful details, medication dosages, diagnosis claims, or crisis-method details in these documents.
