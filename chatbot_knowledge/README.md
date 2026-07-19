# Chatbot Knowledge Base

This folder contains local Markdown documents for the free chatbot stack:

1. Crisis rules are checked first in code.
2. Platform questions use RAG over platform pages, doctors, assessments, and static project docs.
3. General wellness questions use curated Markdown wellness docs plus Ollama when enabled.
4. If Ollama is not running, the chatbot still uses RAG-based local fallback responses.

After editing these files, run:

```bash
python manage.py sync_chatbot_knowledge
```

For the free local AI model, install Ollama and run:

```bash
ollama pull llama3.2:3b
ollama serve
```

Then set `.env`:

```env
CHATBOT_AI_ENABLED=True
CHATBOT_AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
```
