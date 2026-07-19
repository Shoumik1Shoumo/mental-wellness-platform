import json
from urllib import error, request
from urllib.parse import urlencode

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Safely check configured chatbot AI connectivity without printing secrets.'

    def handle(self, *args, **options):
        enabled = bool(getattr(settings, 'CHATBOT_AI_ENABLED', False))
        provider = (getattr(settings, 'CHATBOT_AI_PROVIDER', 'ollama') or '').lower()
        timeout = getattr(settings, 'CHATBOT_TIMEOUT', 6)

        if not enabled:
            self.stdout.write(self.style.WARNING('Chatbot AI is disabled. RAG and Local fallback will be used.'))
            return

        if provider == 'ollama':
            self._check_ollama(timeout)
            return

        if provider == 'gemini':
            self._check_gemini(timeout)
            return

        self.stdout.write(self.style.WARNING('Unsupported chatbot provider configured. RAG and Local fallback will be used.'))

    def _check_ollama(self, timeout):
        base_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434').rstrip('/')
        model = getattr(settings, 'OLLAMA_MODEL', 'llama3.2:3b')
        payload = {
            'model': model,
            'stream': False,
            'messages': [{'role': 'user', 'content': 'Reply with OK.'}],
            'options': {'temperature': 0, 'num_predict': 8},
        }
        req = request.Request(
            f'{base_url}/api/chat',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with request.urlopen(req, timeout=timeout) as response:
                status_code = getattr(response, 'status', 200)
            if 200 <= status_code < 300:
                self.stdout.write(self.style.SUCCESS(f'Ollama chatbot connectivity check succeeded using model {model}.'))
            else:
                self.stdout.write(self.style.WARNING('Ollama returned a non-success response. RAG and Local fallback will be used.'))
        except (TimeoutError, error.HTTPError, error.URLError, OSError, ValueError):
            self.stdout.write(self.style.WARNING('Ollama is unavailable or the model is missing. RAG and Local fallback will be used.'))

    def _check_gemini(self, timeout):
        api_key = getattr(settings, 'GEMINI_API_KEY', '')
        model = getattr(settings, 'GEMINI_MODEL', 'gemini-1.5-flash')
        if not api_key:
            self.stdout.write(self.style.WARNING('Gemini API key is missing. RAG and Local fallback will be used.'))
            return

        url = (
            'https://generativelanguage.googleapis.com/v1beta/models/'
            f'{model}:generateContent?{urlencode({"key": api_key})}'
        )
        payload = {
            'contents': [{'role': 'user', 'parts': [{'text': 'Reply with OK.'}]}],
            'generationConfig': {'maxOutputTokens': 8, 'temperature': 0},
        }
        req = request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )

        try:
            with request.urlopen(req, timeout=timeout) as response:
                status_code = getattr(response, 'status', 200)
            if 200 <= status_code < 300:
                self.stdout.write(self.style.SUCCESS('Gemini chatbot connectivity check succeeded.'))
            else:
                self.stdout.write(self.style.WARNING('Gemini returned a non-success response. RAG and Local fallback will be used.'))
        except (TimeoutError, error.HTTPError, error.URLError, OSError, ValueError):
            self.stdout.write(self.style.WARNING('Gemini is unavailable or rejected the test request. RAG and Local fallback will be used.'))
