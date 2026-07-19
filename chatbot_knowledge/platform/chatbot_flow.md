# Chatbot Routing Flow
Category: platform
Source: Project implementation note
Source URL: /emergency/
Last reviewed: 2026-07-09

Summary: The emergency chatbot uses a safety-first hybrid flow. It checks crisis and self-harm rules first. If the message is platform-related, it retrieves platform knowledge from local database chunks. If the message is a general wellness question, it retrieves curated wellness documents and may use Ollama locally. If Ollama is disabled or unavailable, the bot uses local RAG fallback responses.

Suggested response: This chatbot uses a safety-first flow: crisis detection first, then platform RAG, then general wellness support with curated docs and optional local Ollama. It does not need a paid API key to run.
