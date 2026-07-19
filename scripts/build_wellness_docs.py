"""Build auto-cleaned wellness RAG Markdown files from official public sources.

Run from the project root:
    python scripts/build_wellness_docs.py
    python manage.py sync_chatbot_knowledge

The script uses only Python standard-library modules. If local Ollama is running,
it asks Ollama to convert cleaned page text into safe, concise RAG notes. If
Ollama is unavailable, it still creates a conservative draft from extracted text.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib import error, request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "chatbot_knowledge" / "wellness" / "auto_generated"

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_DOC_BUILD_TIMEOUT", "90"))

SOURCES = [
    {
        "title": "Anxiety, fear and panic",
        "url": "https://www.nhs.uk/mental-health/conditions/anxiety-fear-panic/",
        "category": "wellness/anxiety",
        "source": "NHS",
    },
    {
        "title": "Panic disorder",
        "url": "https://www.nhs.uk/mental-health/conditions/panic-disorder/",
        "category": "wellness/panic",
        "source": "NHS",
    },
    {
        "title": "Sleep and mental health",
        "url": "https://www.nhs.uk/every-mind-matters/mental-health-issues/sleep/",
        "category": "wellness/sleep",
        "source": "NHS",
    },
    {
        "title": "Five steps to mental wellbeing",
        "url": "https://www.nhs.uk/mental-health/self-help/guides-tools-and-activities/five-steps-to-mental-wellbeing/",
        "category": "wellness/wellbeing",
        "source": "NHS",
    },
    {
        "title": "Anxiety disorders",
        "url": "https://www.nimh.nih.gov/health/topics/anxiety-disorders",
        "category": "wellness/anxiety_facts",
        "source": "NIMH",
    },
    {
        "title": "Depression",
        "url": "https://www.nimh.nih.gov/health/topics/depression",
        "category": "wellness/low_mood",
        "source": "NIMH",
    },
    {
        "title": "Stress management guide",
        "url": "https://www.who.int/publications/i/item/9789240003927",
        "category": "wellness/stress",
        "source": "WHO",
    },
    {
        "title": "Suicide warning signs",
        "url": "https://www.samhsa.gov/mental-health/suicidal-behavior/warning-signs",
        "category": "wellness/safety_warning_signs",
        "source": "SAMHSA",
    },
]

NOISY_LINE_PATTERNS = [
    r"cookie", r"privacy", r"terms", r"subscribe", r"newsletter", r"sign in",
    r"menu", r"navigation", r"skip to", r"search", r"share", r"print", r"facebook",
    r"twitter", r"instagram", r"youtube", r"related", r"advert", r"©", r"copyright",
]


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value or "wellness_doc"


def download_url(url: str) -> str:
    req = request.Request(
        url,
        headers={
            "User-Agent": "MentalWellnessPlatformRAGBuilder/1.0 (+local university project)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with request.urlopen(req, timeout=45) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="ignore")


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript|svg|form|nav|footer|header)[^>]*>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|section|article|li|h[1-6])>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[\t\r\f\v]+", " ", text)
    return text


def clean_page_text(raw_html: str) -> str:
    text = html_to_text(raw_html)
    seen = set()
    cleaned_lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 35:
            continue
        lowered = line.lower()
        if any(re.search(pattern, lowered) for pattern in NOISY_LINE_PATTERNS):
            continue
        if line in seen:
            continue
        seen.add(line)
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned[:9000]


def ollama_markdown(source: dict, cleaned_text: str) -> str:
    system_prompt = (
        "You create safe RAG knowledge notes for a mental wellness chatbot. "
        "Use only the provided source text. Do not diagnose, prescribe medicine, or provide crisis methods. "
        "Write concise Markdown with exactly these sections: Summary, Quick support, Suggested response, Bot guidance, Safety note. "
        "For doctor/therapist suggestions, tell users to complete the platform assessment questionnaire first."
    )
    user_prompt = f"""
Title: {source['title']}
Source: {source['source']}
URL: {source['url']}
Category: {source['category']}

Source text:
{cleaned_text}
""".strip()
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {"temperature": 0.1, "num_predict": 650},
    }
    req = request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=OLLAMA_TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8"))
    return (data.get("message", {}) or {}).get("content", "").strip()


def fallback_markdown(source: dict, cleaned_text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", cleaned_text).strip())
    summary = " ".join(sentences[:4])[:900].strip()
    if not summary:
        summary = "This source gives general mental-wellness information and self-help guidance."
    return f"""
Summary: {summary}

Quick support: Validate the user's feeling, suggest one small safe step, and keep advice simple. Encourage rest, breathing, grounding, journaling, or trusted-person support when relevant.

Suggested response: That sounds difficult. Try one small step right now: pause, breathe slowly, and focus on the next safe thing you can do. If this keeps happening or affects daily life, complete the platform assessment questionnaire so the platform can guide you toward suitable support.

Bot guidance: Do not diagnose. Do not prescribe medicine. Do not list doctor names directly. For doctor or therapist guidance, ask the user to complete the assessment questionnaire first.

Safety note: If the user mentions suicide, self-harm, violence, overdose, or immediate danger, ignore this wellness note and use the crisis safety response with Kaan Pete Roi Helpline at +880 9612-119911.
""".strip()


def build_document(source: dict, cleaned_text: str) -> str:
    try:
        body = ollama_markdown(source, cleaned_text)
        if not body or len(body) < 200:
            raise ValueError("Ollama returned an empty or too-short document")
    except (TimeoutError, error.HTTPError, error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"  Ollama unavailable for '{source['title']}', using conservative fallback: {exc}")
        body = fallback_markdown(source, cleaned_text)

    today = _dt.date.today().isoformat()
    return f"""# {source['title']}
Category: {source['category']}
Source: {source['source']}
Source URL: {source['url']}
Last reviewed: {today}
Auto generated: true

{body.strip()}
"""


def output_path_for(source: dict) -> Path:
    category_leaf = source["category"].split("/")[-1]
    return OUTPUT_DIR / f"{slugify(category_leaf)}.md"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for source in SOURCES:
        print(f"Fetching: {source['title']} ({source['source']})")
        try:
            raw = download_url(source["url"])
            cleaned = clean_page_text(raw)
            if len(cleaned) < 300:
                raise ValueError("not enough useful article text after cleaning")
            markdown = build_document(source, cleaned)
            path = output_path_for(source)
            path.write_text(markdown, encoding="utf-8")
            print(f"  wrote {path.relative_to(PROJECT_ROOT)}")
            written += 1
        except Exception as exc:
            print(f"  skipped: {exc}", file=sys.stderr)
    print(f"Done. Wrote {written} generated wellness docs.")
    print("Next: python manage.py sync_chatbot_knowledge")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
