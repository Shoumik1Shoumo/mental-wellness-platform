import hashlib
import html
import json
import logging
import math
import re
from pathlib import Path
from urllib import request
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.urls import reverse

from .models import (
    AssessmentQuestion,
    BlogPost,
    ChatMessage,
    ChatSession,
    ChatbotKnowledgeChunk,
    Doctor,
    DoctorPrimaryFocus,
    DoctorSchedule,
    DoctorSpecialization,
    EmergencyLog,
    HealthTaskTemplate,
)

logger = logging.getLogger(__name__)

CHATBOT_AI_ENABLED = getattr(settings, 'CHATBOT_AI_ENABLED', False)
CHATBOT_AI_PROVIDER = getattr(settings, 'CHATBOT_AI_PROVIDER', 'ollama')
GEMINI_API_KEY = getattr(settings, 'GEMINI_API_KEY', '')
GEMINI_MODEL = getattr(settings, 'GEMINI_MODEL', 'gemini-1.5-flash')
OLLAMA_BASE_URL = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434').rstrip('/')
OLLAMA_MODEL = getattr(settings, 'OLLAMA_MODEL', 'llama3.2:3b')
CHAT_TIMEOUT = getattr(settings, 'CHATBOT_TIMEOUT', 6)
CHATBOT_MAX_MESSAGE_LENGTH = getattr(settings, 'CHATBOT_MAX_MESSAGE_LENGTH', 1000)
LOCAL_EMBED_DIM = 384
LOCAL_EMBED_MODEL_NAME = 'local-hash-embedding'
SYNC_INTERVAL_SECONDS = 30
MAX_USER_HISTORY_MESSAGES = 15
MAX_HISTORY_MESSAGES = MAX_USER_HISTORY_MESSAGES * 2
MAX_STORED_MESSAGES = MAX_HISTORY_MESSAGES
MAX_CONTEXT_CHARS = 2200

PLATFORM_SOURCE_TYPES = {
    'platform_static', 'platform_manual', 'template_page', 'blog_post',
    'doctor_profile', 'health_task_template', 'assessment_question',
    'doctor_specialization', 'doctor_primary_focus',
}
WELLNESS_SOURCE_TYPES = {'wellness_doc', 'safety_doc'}

VAGUE_HELP_FILLER_WORDS = {
    'i', 'me', 'my', 'need', 'want', 'please', 'pls', 'plz', 'can', 'could',
    'you', 'help', 'support', 'assist', 'assistance', 'some', 'a', 'little',
    'with', 'for', 'now', 'urgent', 'quick', 'really', 'just',
}

DOCTOR_ASSESSMENT_INTENT_TERMS = {
    'doctor', 'doctors', 'therapist', 'therapists', 'therapy', 'psychiatrist',
    'psychologist', 'counselor', 'counsellor', 'professional help',
    'appointment', 'appointments', 'booking', 'book', 'schedule', 'consultation',
    'consult', 'recommend doctor', 'suggest doctor', 'find doctor', 'doctor recommendation',
}

BREATHING_TOOL_TERMS = {
    'breathing exercise', 'breathing tool', 'breathing section', 'breath exercise',
    'breathe exercise', 'start breathing', 'deep breathing', 'calm down exercise',
    'breathing activity', 'guided breathing', 'start exercise',
}


CRISIS_PATTERNS = [
    r'\bsuicid(?:e|al)\b',
    r'\bkill myself\b',
    r'\bself harm\b',
    r'\bend my life\b',
    r'\bwant to die\b',
    r"\bdon'?t want to live\b",
    r'\bself[-\s]?harm\b',
    r'\bhurt myself\b',
    r'\bcut myself\b',
    r'\boverdose\b',
    r'\bmedical emergency\b',
    r"\bcan'?t stay safe\b",
    r'\bextreme depression\b',
    r'\bviolent thoughts\b',
    r'\bhurt someone\b',
    r'\bkill someone\b',
    'আত্মহত্যা',
    r'নিজেকে\s*মেরে\s*ফেলব',
    r'নিজেকে\s*মেরে',
    r'মরে\s*যেতে',
    r'মরতে\s*চাই',
    r'বাঁচতে\s*চাই\s*না',
    r'নিজেকে\s*আঘাত\s*করব',
    r'নিজেকে\s*আঘাত',
    r'জীবন\s*শেষ',
    r'সব\s*শেষ\s*করে\s*দেব',
    r'মেরে\s*ফেলব',
]


PLATFORM_KEYWORDS = {
    'appointment', 'appointments', 'book', 'booking', 'doctor', 'doctors',
    'therapist', 'therapy', 'dashboard', 'assessment', 'clinical', 'phq',
    'gad', 'blog', 'blogs', 'payment', 'prescription', 'task', 'tasks',
    'hotline', 'kaan pete roi', '9612-119911', 'center', 'centers', 'schedule', 'login', 'register',
    'platform', 'website', 'service', 'services', 'consultation', 'jitsi',
    'account', 'profile', 'patient', 'admin', 'video', 'room', 'prescriptions',
}

PLATFORM_SUBSTRINGS = {
    'অ্যাপয়েন্টমেন্ট', 'ডাক্তার', 'থেরাপিস্ট', 'লগইন', 'রেজিস্টার', 'ড্যাশবোর্ড',
    'পেমেন্ট', 'প্রেসক্রিপশন', 'অ্যাসেসমেন্ট', 'জরুরি', 'হটলাইন',
}

GENERAL_WELLNESS_KEYWORDS = {
    'anxiety', 'anxious', 'worry', 'worried', 'fear', 'stress', 'stressed',
    'depression', 'depressed', 'sad', 'low', 'mood', 'sleep', 'insomnia',
    'panic', 'overwhelmed', 'lonely', 'loneliness', 'mindfulness',
    'breathing', 'coping', 'relationship', 'work', 'burnout', 'grief',
    'grounding', 'exam', 'study', 'motivation', 'self-esteem', 'calm',
}

GENERAL_WELLNESS_SUBSTRINGS = {
    'উদ্বেগ', 'দুশ্চিন্তা', 'আতঙ্ক', 'মন খারাপ', 'বিষণ্ণ', 'ঘুম', 'স্ট্রেস',
    'চাপ', 'একাকী', 'ভয়', 'ক্লান্ত', 'panic', 'tension', 'mon kharap',
}

STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
    'how', 'i', 'in', 'is', 'it', 'me', 'my', 'of', 'on', 'or', 'the',
    'this', 'to', 'what', 'when', 'where', 'who', 'why', 'with', 'you',
    'your', 'do', 'does', 'can', 'could', 'should', 'would', 'am',
    'feel', 'feeling', 'felt', 'before', 'after', 'really', 'very',
}

TOKEN_SYNONYMS = {
    'anxious': 'anxiety',
    'worried': 'worry',
    'worries': 'worry',
    'stressed': 'stress',
    'stressful': 'stress',
    'depressed': 'depression',
    'sadness': 'sad',
    'lonely': 'loneliness',
    'panicking': 'panic',
    'panicked': 'panic',
    'exams': 'exam',
    'tests': 'exam',
    'studying': 'study',
    'sleepless': 'sleep',
    'insomnia': 'sleep',
}

SYMPTOM_KEYWORDS = {
    'anxiety': {
        'en': ['anxiety', 'anxious', 'panic', 'panic attack', 'worry', 'fear', 'nervous'],
        'bn': ['উদ্বেগ', 'দুশ্চিন্তা', 'আতঙ্ক', 'ভয়', 'প্যানিক'],
        'doctor_terms': ['anxiety', 'panic', 'psychiatrist', 'therapist', 'counselor'],
    },
    'depression': {
        'en': ['depression', 'depressed', 'sad', 'hopeless', 'empty', 'numb'],
        'bn': ['বিষণ্ণ', 'ডিপ্রেশন', 'মন খারাপ', 'হতাশ', 'শূন্য'],
        'doctor_terms': ['depression', 'mood', 'psychiatrist', 'therapist', 'counselor'],
    },
    'sleep': {
        'en': ['sleep', 'insomnia', 'nightmare', 'cannot sleep', "can't sleep", 'cant sleep', 'sleepless'],
        'bn': ['ঘুম', 'অনিদ্রা', 'ঘুমাতে পারি না', 'দুঃস্বপ্ন'],
        'doctor_terms': ['sleep', 'insomnia', 'psychiatrist', 'therapist'],
    },
    'stress': {
        'en': ['stress', 'stressed', 'overwhelmed', 'burnout', 'pressure'],
        'bn': ['চাপ', 'স্ট্রেস', 'অতিরিক্ত চাপ', 'ক্লান্ত'],
        'doctor_terms': ['stress', 'burnout', 'counselor', 'therapist'],
    },
    'trauma': {
        'en': ['trauma', 'ptsd', 'flashback', 'abuse', 'assault'],
        'bn': ['ট্রমা', 'আঘাতজনিত', 'নির্যাতন', 'ফ্ল্যাশব্যাক'],
        'doctor_terms': ['trauma', 'ptsd', 'therapist', 'clinical psychologist'],
    },
    'addiction': {
        'en': ['addiction', 'alcohol', 'drug', 'substance', 'relapse'],
        'bn': ['আসক্তি', 'মাদক', 'অ্যালকোহল', 'নেশা'],
        'doctor_terms': ['addiction', 'substance', 'psychiatrist', 'counselor'],
    },
    'ocd': {
        'en': ['ocd', 'obsession', 'compulsion', 'intrusive thoughts'],
        'bn': ['ওসিডি', 'বারবার চিন্তা', 'বাধ্যতামূলক'],
        'doctor_terms': ['ocd', 'obsessive', 'psychiatrist', 'therapist'],
    },
    'relationship': {
        'en': ['relationship', 'breakup', 'family', 'spouse', 'marriage'],
        'bn': ['সম্পর্ক', 'বিচ্ছেদ', 'পরিবার', 'সঙ্গী', 'বিয়ে'],
        'doctor_terms': ['relationship', 'family', 'therapist', 'counselor'],
    },
}


def is_crisis_query(text):
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in CRISIS_PATTERNS)


def detect_crisis_terms(text):
    lowered = text.lower()
    return [pattern for pattern in CRISIS_PATTERNS if re.search(pattern, lowered)]


def _message_words(text):
    return re.findall(r'[a-z0-9]+', (text or '').lower())


def is_vague_help_request(message):
    words = _message_words(message)
    if not words:
        return False
    lowered = ' '.join(words)
    exact_vague_messages = {
        'help', 'help me', 'i need help', 'need help', 'please help',
        'can you help', 'can you help me', 'i need support', 'need support',
        'support', 'i need assistance', 'assist me', 'please support me',
    }
    if lowered in exact_vague_messages:
        return True
    has_help_word = bool({'help', 'support', 'assist', 'assistance'} & set(words))
    meaningful_words = set(words) - VAGUE_HELP_FILLER_WORDS
    return has_help_word and len(words) <= 7 and not meaningful_words


def vague_help_response(language='en'):
    if language == 'bn':
        return (
            'আমি আপনার সাথে আছি। আপনি কী ধরনের সাহায্য চান—প্ল্যাটফর্ম ব্যবহার, অ্যাসেসমেন্ট পূরণ, '
            'অ্যাপয়েন্টমেন্ট বুকিং, স্ট্রেস/উদ্বেগ সাপোর্ট, নাকি জরুরি সহায়তা? '
            'তাৎক্ষণিক বিপদে থাকলে এখনই Kaan Pete Roi helpline-এ +880 9612-119911 নম্বরে যোগাযোগ করুন বা কাছের জরুরি বিভাগে যান।'
        )
    if language == 'banglish':
        return (
            'Ami ekhane achi. Apni kon dhoroner help chan—platform use, assessment fill up, '
            'appointment booking, stress/anxiety support, naki urgent help? '
            'Immediate danger thakle ekhon-i Kaan Pete Roi helpline +880 9612-119911 e contact korun ba nearest emergency department e jan.'
        )
    return (
        "I'm here with you. What kind of help do you need—using the platform, completing the assessment, "
        "booking an appointment, stress/anxiety support, or urgent help? "
        "If you are in immediate danger, contact Kaan Pete Roi helpline at +880 9612-119911 or go to the nearest emergency department now."
    )


def has_doctor_or_booking_intent(message):
    lowered = (message or '').lower()
    if any(term in lowered for term in ['consultation room', 'jitsi', 'video room']):
        return False
    return any(term in lowered for term in DOCTOR_ASSESSMENT_INTENT_TERMS)


def assessment_guidance_text(language='en', emergency=False):
    if emergency:
        if language == 'bn':
            return 'নিরাপদ হওয়ার পরে প্ল্যাটফর্মে অ্যাসেসমেন্ট প্রশ্নগুলো পূরণ করলে উপযুক্ত ডাক্তার/থেরাপিস্ট অপশন খুঁজতে সাহায্য করবে।'
        if language == 'banglish':
            return 'Safe howar por platform er assessment questions fill up korle suitable doctor/therapist option pete help korbe.'
        return 'After you are safe, completing the platform assessment questionnaire can help guide you toward suitable doctor or therapist options.'
    if language == 'bn':
        return 'উপযুক্ত ডাক্তার/থেরাপিস্ট পেতে আগে অ্যাসেসমেন্ট প্রশ্নগুলো সম্পূর্ণ করুন। আপনার উত্তরগুলো প্ল্যাটফর্মকে আপনার প্রয়োজন বুঝে ভালোভাবে গাইড করতে সাহায্য করে।'
    if language == 'banglish':
        return 'Suitable doctor/therapist pete age assessment questions complete korun. Apnar answers platform ke apnar need bujhte and better option guide korte help kore.'
    return 'To find suitable doctor or therapist options, please complete the assessment questionnaire first. Your answers help the platform understand your needs and guide you toward more relevant support.'


def assessment_first_response(message='', language='en'):
    base = assessment_guidance_text(language)
    lowered = (message or '').lower()
    booking_terms = ['book', 'booking', 'appointment', 'schedule', 'consultation']
    if language == 'bn':
        if any(term in lowered for term in booking_terms):
            return f'{base} এরপর Doctors/appointment অংশে গিয়ে উপলব্ধ সময় বেছে অ্যাপয়েন্টমেন্ট বুক করুন।'
        return base
    if language == 'banglish':
        if any(term in lowered for term in booking_terms):
            return f'{base} Tarpor Doctors/appointment section e giye available time choose kore appointment book korun.'
        return base
    if any(term in lowered for term in booking_terms):
        return f'{base} After completing it, go to the Doctors/appointment section, choose an available time, and continue with booking.'
    return base


def is_breathing_tool_request(message):
    lowered = (message or '').lower()
    return any(term in lowered for term in BREATHING_TOOL_TERMS)


def breathing_tool_response(language='en'):
    if language == 'bn':
        return (
            'এই Emergency Support পেজের Breathing Exercise অংশটি ব্যবহার করুন। ' 
            'নিচে Breathing Exercise সেকশনে গিয়ে “Start Exercise” চাপুন। ' 
            'এটি আপনাকে ধীরে শ্বাস নেওয়ার ধাপগুলো দেখিয়ে শরীর ও মন শান্ত করতে সাহায্য করবে।'
        )
    if language == 'banglish':
        return (
            'Ei Emergency Support page er Breathing Exercise section use korun. ' 
            'Niche Breathing Exercise section e giye “Start Exercise” click korun. ' 
            'Eta apnake slow breathing guide korbe, jeta calm down korte help korte pare.'
        )
    return (
        'You can use the Breathing Exercise section on this Emergency Support page. ' 
        'Scroll down to the Breathing Exercise section and click “Start Exercise.” ' 
        'It will guide you through slow breathing to help calm your body and mind.'
    )


def professional_support_note(language='en'):
    if language == 'bn':
        return 'এটি রোগ নির্ণয় নয়। সমস্যা তীব্র, দীর্ঘস্থায়ী বা দৈনন্দিন জীবনে বাধা দিলে অ্যাসেসমেন্ট পূরণ করে পেশাদার সহায়তার পরবর্তী ধাপ নিন।'
    if language == 'banglish':
        return 'Eta diagnosis na. Problem jodi intense, long-term, ba daily life e effect kore, assessment complete kore professional support er next step nin.'
    return 'If this feels severe, keeps happening, or affects daily life, complete the assessment questionnaire and consider professional support.'


def crisis_response(language='en', doctors=None):
    doctor_text = assessment_guidance_text(language, emergency=True)
    if language == 'bn':
        return (
            'আপনি আমাকে বলেছেন বলে ধন্যবাদ। এটা জরুরি মনে হচ্ছে, আর এখন আপনার নিরাপত্তাই সবচেয়ে গুরুত্বপূর্ণ। '
            'যদি আপনি নিজেকে আঘাত করার ঝুঁকিতে থাকেন, নিরাপদ থাকতে না পারেন, অথবা এটি মেডিক্যাল ইমার্জেন্সি হয়, এখনই জরুরি সেবা নিন বা নিকটস্থ জরুরি বিভাগে যান। '
            'Kaan Pete Roi helpline-এ +880 9612-119911 নম্বরে যোগাযোগ করতে পারেন। সম্ভব হলে ক্ষতি করতে পারে এমন জিনিস থেকে দূরে যান এবং একজন বিশ্বস্ত মানুষকে আপনার পাশে থাকতে বলুন। '
            f'{doctor_text}'
        )
    if language == 'banglish':
        return (
            'Apni bolechen bole dhonnobad. Eta urgent mone hocche, ar ekhon apnar safety shobcheye important. '
            'Jodi nijeke harm korar risk thake, safe thakte na paren, ba medical emergency hoy, ekhon-i emergency help nin. '
            'Kaan Pete Roi helpline +880 9612-119911 e contact korte paren, ba nearest emergency department e jan. '
            f'{doctor_text}'
        )
    return (
        "I'm really glad you told me. This sounds urgent, and your safety matters most right now. "
        "If you might hurt yourself, feel unable to stay safe, or this is a medical emergency, please call emergency services now or go to the nearest emergency room. "
        "You can also contact Kaan Pete Roi helpline at +880 9612-119911. "
        "If possible, move away from anything you could use to harm yourself and contact a trusted person to stay with you while you get help. "
        f'{doctor_text}'
    )


def classify_query(message):
    base_category = classify_query_without_retrieval(message)
    if base_category == 'platform_specific':
        return base_category

    words = set(tokenize(message))
    lowered = message.lower()
    if words & GENERAL_WELLNESS_KEYWORDS or any(term in lowered or term in message for term in GENERAL_WELLNESS_SUBSTRINGS):
        return 'general_wellness'

    top_platform_context = retrieve_context(
        message, top_k=1, minimum_score=0.24, source_types=PLATFORM_SOURCE_TYPES
    )
    if top_platform_context:
        return 'platform_specific'

    top_wellness_context = retrieve_context(
        message, top_k=1, minimum_score=0.04, source_types=WELLNESS_SOURCE_TYPES
    )
    if top_wellness_context:
        return 'general_wellness'

    return 'general_wellness'


def classify_query_without_retrieval(message):
    lowered = message.lower()
    words = set(re.findall(r'[a-z0-9]+', lowered))
    if words & PLATFORM_KEYWORDS or any(term in message for term in PLATFORM_SUBSTRINGS):
        return 'platform_specific'
    return 'general_wellness'


def retrieve_context(query, top_k=4, minimum_score=0.18, source_types=None):
    query_embedding, embedding_model = embed_text(query)
    query_tokens = set(tokenize(query))
    queryset = ChatbotKnowledgeChunk.objects.exclude(embedding=[])
    if source_types:
        queryset = queryset.filter(source_type__in=source_types)
    scored = []
    for chunk in queryset:
        vector_score = cosine_similarity(query_embedding, chunk.embedding)
        lexical_score = lexical_similarity(query_tokens, f"{chunk.title} {chunk.content}")
        if embedding_model == 'local-hash-embedding':
            if lexical_score == 0 and vector_score < 0.35:
                continue
            score = (lexical_score * 0.85) + (vector_score * 0.15)
        else:
            score = (vector_score * 0.75) + (lexical_score * 0.25)
        if score >= minimum_score:
            scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            'score': round(score, 4),
            'title': chunk.title,
            'url': chunk.url,
            'content': chunk.content,
            'source_type': chunk.source_type,
        }
        for score, chunk in scored[:top_k]
    ]


def generate_platform_response(message, contexts, history=None, language='en', doctors=None):
    if should_use_direct_platform_answer(message):
        return platform_context_fallback(message, contexts, language, doctors)

    context_text = '\n\n'.join(
        f"Source: {context['title'] or context['source_type']} ({context['url'] or 'internal'})\n{context['content']}"
        for context in contexts
    )
    system_prompt = build_system_prompt(language, platform_mode=True)
    user_prompt = (
        f"Recent conversation:\n{format_history(history or [])}\n\n"
        f"Platform context:\n{context_text[:MAX_CONTEXT_CHARS]}\n\n"
        "Assessment-first rule: If the user wants doctor, therapist, appointment, booking, consultation, or professional support guidance, tell them to complete the assessment questionnaire first. Do not list doctor names directly.\n\n"
        f"User question: {message}"
    )
    response = ai_chat(system_prompt, user_prompt)
    return compact_response(response) or platform_context_fallback(message, contexts, language, doctors)


def should_use_direct_platform_answer(message):
    lowered = message.lower()
    direct_terms = [
        'appointment', 'appointments', 'book', 'booking', 'schedule',
        'service', 'services', 'offer', 'offers',
        'assessment', 'phq', 'gad', 'screening',
        'hotline', 'emergency', 'crisis', 'center',
        'recommendation', 'recommendations', 'consultation', 'jitsi',
        'room', 'video', 'payment', 'demo', 'prescription', 'task',
        'tasks', 'daily', 'dashboard', 'dashboards',
    ]
    return any(term in lowered for term in direct_terms) or (
        'doctor' in lowered and 'recommend' in lowered
    )


def build_system_prompt(language='en', platform_mode=False):
    if language == 'bn':
        language_rule = "Reply in Bangla because the user is writing in Bangla."
    elif language == 'banglish':
        language_rule = "Reply in simple Banglish because the user is using Banglish."
    else:
        language_rule = "Reply in English unless the user switches language."
    source_rule = (
        "You are the offline AI assistant for a Mental Wellness Platform. "
        "Use only the provided platform context for platform-specific facts. "
        if platform_mode
        else "You are an offline AI mental health support assistant. "
    )
    return (
        f"{source_rule}"
        "Your personality is calm, caring, professional, emotionally supportive, and friendly. "
        "You are not a licensed doctor and must not claim to diagnose, prescribe medicine, or replace therapy. "
        "Use recent conversation context so the user does not need to repeat themselves. "
        "For mental health topics, validate feelings, ask one gentle follow-up when useful, and suggest professional support when symptoms are persistent, severe, or impairing daily life. "
        "If the user asks for a doctor, therapist, appointment, booking, consultation, or professional support, instruct them to complete the assessment questionnaire first instead of listing doctor names. "
        "For crisis, self-harm, suicide, violence, or medical emergency, prioritize immediate safety and Kaan Pete Roi helpline at +880 9612-119911. "
        "Do not provide harmful instructions, methods, medication dosages, or dangerous medical advice. "
        "Keep responses compact: 3 to 6 short sentences. "
        "Never reveal API keys, environment variables, stack traces, or server errors. "
        f"{language_rule}"
    )


def generate_general_response(message, history=None, language='en', doctors=None, contexts=None):
    contexts = contexts if contexts is not None else retrieve_wellness_context(message)
    context_text = format_contexts_for_prompt(contexts)
    system_prompt = build_system_prompt(language)
    user_prompt = (
        f"Recent conversation:\n{format_history(history or [])}\n\n"
        f"Curated wellness context:\n{context_text or 'No matching curated wellness document was found.'}\n\n"
        "Assessment-first rule: Do not list doctor names. If the user wants doctor/therapist/appointment guidance, tell them to complete the assessment questionnaire first so the platform can guide them toward suitable support.\n\n"
        f"User message: {message}"
    )
    response = compact_response(ai_chat(system_prompt, user_prompt))
    if response and not is_low_quality_reply(response):
        return response
    return wellness_context_fallback(message, contexts, language, doctors)


def retrieve_wellness_context(message, top_k=4):
    return retrieve_context(
        message,
        top_k=top_k,
        minimum_score=0.04,
        source_types=WELLNESS_SOURCE_TYPES,
    )


def format_contexts_for_prompt(contexts):
    if not contexts:
        return ''
    parts = []
    for context in contexts:
        title = context.get('title') or context.get('source_type') or 'Knowledge source'
        url = context.get('url') or 'local curated document'
        parts.append(f"Source: {title} ({url})\n{context.get('content', '')}")
    return '\n\n'.join(parts)[:MAX_CONTEXT_CHARS]


def chatbot_reply(message, user=None, session_id=None, django_session_key=''):
    message = sanitize_message(message)
    language = detect_language(message)
    chat_session = get_or_create_chat_session(session_id, user, django_session_key, language)
    history = get_recent_history(chat_session)
    history_text = ' '.join(
        [message] + [item.content for item in history if item.role == 'user']
    )
    crisis = is_crisis_query(message)
    vague_help = (not crisis) and is_vague_help_request(message)
    breathing_tool_request = (not crisis) and (not vague_help) and is_breathing_tool_request(message)
    doctor_or_booking_intent = (not crisis) and (not vague_help) and (not breathing_tool_request) and has_doctor_or_booking_intent(message)
    symptoms = extract_symptoms_from_history(history_text)
    recommended_doctors = recommend_doctors_for_symptoms(symptoms, crisis=crisis, limit=3) if crisis else []
    if crisis:
        category = 'crisis'
    elif vague_help:
        category = 'clarification'
    elif breathing_tool_request:
        category = 'platform_specific'
    elif doctor_or_booking_intent:
        category = 'platform_specific'
    else:
        category = classify_query(message)
    contexts = []
    used_rag = False

    if not is_duplicate_recent_user_message(chat_session, message):
        ChatMessage.objects.create(
            session=chat_session,
            role='user',
            content=message,
            category=category,
            metadata={'language': language, 'symptoms': symptoms},
        )

    if crisis:
        response = crisis_response(language, recommended_doctors)
        EmergencyLog.objects.create(
            session=chat_session,
            user=user if getattr(user, 'is_authenticated', False) else None,
            message=f'[redacted crisis message, length={len(message)}]',
            detected_terms=detect_crisis_terms(message),
            risk_level='critical',
            recommended_action='Contact Kaan Pete Roi helpline at +880 9612-119911 or go to the nearest emergency department.',
        )
    elif vague_help:
        response = vague_help_response(language)
    elif breathing_tool_request:
        response = breathing_tool_response(language)
    elif doctor_or_booking_intent:
        response = assessment_first_response(message, language)
    elif category == 'platform_specific':
        try:
            contexts = retrieve_context(message)
        except Exception as exc:
            logger.warning('Chatbot knowledge lookup unavailable: %s', exc)
            contexts = []
        used_rag = bool(contexts)
        if contexts:
            response = generate_platform_response(message, contexts, history, language, recommended_doctors)
        else:
            response = platform_context_fallback(message, [], language, recommended_doctors)
    else:
        try:
            contexts = retrieve_wellness_context(message)
        except Exception as exc:
            logger.warning('Wellness knowledge lookup unavailable: %s', exc)
            contexts = []
        used_rag = bool(contexts)
        response = generate_general_response(message, history, language, recommended_doctors, contexts)

    sources = [_source_payload(context) for context in contexts]
    ChatMessage.objects.create(
        session=chat_session,
        role='assistant',
        content=response,
        category=category,
        metadata={
            'language': language,
            'used_rag': used_rag,
            'doctors': serialize_doctors(recommended_doctors),
            'sources': sources,
        },
    )
    trim_chat_session_messages(chat_session)
    update_session_title(chat_session, message, language)
    return {
        'response': response,
        'category': category,
        'used_rag': used_rag,
        'session_id': str(chat_session.id),
        'language': language,
        'is_emergency': crisis,
        'doctors': serialize_doctors(recommended_doctors),
        'sources': sources,
    }


def safe_fallback_response(message, language='en', doctors=None):
    if is_crisis_query(message):
        return crisis_response(language, doctors)

    lowered = message.lower()
    doctor_text = assessment_guidance_text(language) if has_doctor_or_booking_intent(message) else ''
    if language == 'bn':
        if any(word in lowered for word in ['anxiety', 'panic', 'anxious']) or any(term in message for term in SYMPTOM_KEYWORDS['anxiety']['bn']):
            return f'এটা খুব অস্বস্তিকর লাগতে পারে। একটু ধীরে শ্বাস নিন: ৪ গণনা করে শ্বাস নিন, ৬ গণনা করে ছাড়ুন। উদ্বেগ বারবার হলে অ্যাসেসমেন্ট পূরণ করে পেশাদার সহায়তার পরবর্তী ধাপ নেওয়া সহায়ক হতে পারে। {doctor_text}'
        if any(word in lowered for word in ['sad', 'depressed', 'hopeless']) or any(term in message for term in SYMPTOM_KEYWORDS['depression']['bn']):
            return f'আপনার কষ্টটা সত্যিই ভারী শোনাচ্ছে। আপনি একা নন। একজন বিশ্বস্ত মানুষকে জানান এবং প্রয়োজন হলে অ্যাসেসমেন্ট পূরণ করে পেশাদার সহায়তার পরবর্তী ধাপ নেওয়া ভালো হতে পারে। {doctor_text}'
        if any(word in lowered for word in ['stress', 'overwhelmed', 'burnout']) or any(term in message for term in SYMPTOM_KEYWORDS['stress']['bn']):
            return f'একসাথে অনেক চাপ এলে এমন লাগা স্বাভাবিক। এখন একটি ছোট কাজ বেছে নিন, একটু বিরতি নিন, এবং ধীরে শ্বাস নিন। চাপ দীর্ঘদিন থাকলে পেশাদার সাহায্য নিন। {doctor_text}'
        if any(term in lowered for term in ['sleep', 'insomnia', 'cannot sleep', 'cant sleep']) or any(term in message for term in SYMPTOM_KEYWORDS['sleep']['bn']):
            return f'ঘুম না হওয়া শরীর ও মনের জন্য খুব ক্লান্তিকর। আজ রাতে আলো কমিয়ে, স্ক্রিন থেকে বিরতি নিয়ে, ধীরে শ্বাস নেওয়ার চেষ্টা করুন। সমস্যা চলতে থাকলে অ্যাসেসমেন্ট পূরণ করে পেশাদার সহায়তার পরবর্তী ধাপ নেওয়া ভালো। {doctor_text}'
        return f'আমি আপনার সাথে আছি। কী হয়েছে আরেকটু বলবেন? আমি সহানুভূতির সাথে সাহায্য করার চেষ্টা করব। {doctor_text}'

    if language == 'banglish':
        if any(word in lowered for word in ['anxiety', 'panic', 'tension']):
            return f'Eta khub uncomfortable lagte pare. Ektu slow breathing try korun: 4 count inhale, 6 count exhale. Anxiety bar bar daily life e problem korle assessment complete kore professional support er next step neya helpful hote pare. {doctor_text}'
        if any(word in lowered for word in ['sad', 'depressed', 'mon kharap', 'hopeless']):
            return f'Apnar kosto ta heavy shonacche. Apni eka na. Trusted karo sathe kotha bolun, ar eta jodi choltei thake tahole assessment complete kore professional support er next step nite paren. {doctor_text}'
        if any(word in lowered for word in ['stress', 'overwhelmed', 'pressure']):
            return f'Onek pressure ekshathe ele emon laga normal. Akhon ekta choto next step choose korun, ektu pause nin, and slow breathing korun. Long-term stress hole professional support helpful. {doctor_text}'
        return f'Ami ekhane achi. Ki hocche aro ektu bolle ami short kore support korte parbo. {doctor_text}'

    if any(word in lowered for word in ['anxiety', 'panic', 'anxious']):
        return f"That sounds really uncomfortable. Try slowing your breathing for a moment: breathe in for 4 counts, then out for 6. If anxiety keeps interfering with daily life, completing the assessment questionnaire can help guide the next support step. {doctor_text}"
    if any(word in lowered for word in ['sad', 'depressed', 'hopeless']):
        return f"I'm sorry you're feeling this heaviness. You do not have to sort it out alone. Consider reaching out to someone you trust, and if this has been lasting or worsening, completing the assessment questionnaire can help guide you toward suitable support. {doctor_text}"
    if any(word in lowered for word in ['stress', 'overwhelmed', 'burnout']):
        return f"It makes sense to feel overloaded when too much is landing at once. Pick one small next step, pause anything non-urgent, and take a few slow breaths. Professional support can also help if stress is becoming hard to manage. {doctor_text}"
    if any(term in lowered for term in ['sleep', 'insomnia', 'cannot sleep', 'cant sleep', "can't sleep"]):
        return f"Not being able to sleep can make everything feel harder. Try keeping the room dark and cool, put screens away for a bit, and do a few slow breaths. If sleep problems continue, completing the assessment questionnaire can help guide you toward suitable support. {doctor_text}"
    return f"I'm here with you. Tell me a little more about what's going on, and I'll do my best to support you. {doctor_text}"


def platform_context_fallback(message, contexts, language='en', doctors=None):
    lowered = message.lower()
    if any(word in lowered for word in ['book', 'appointment', 'schedule']):
        return assessment_first_response(message, language)
    if any(word in lowered for word in ['account', 'login', 'register', 'password', 'signup', 'sign up']):
        if language == 'bn':
            return 'অ্যাকাউন্টের জন্য Register পেজে গিয়ে patient বা doctor হিসেবে সাইন আপ করুন। লগইন করার পর আপনার role অনুযায়ী dashboard খুলবে। পাসওয়ার্ড বা Google login সমস্যায় login পেজের নির্দেশনা অনুসরণ করুন বা admin/support এ জানান।'
        if language == 'banglish':
            return 'Account er jonno Register page theke patient ba doctor hisebe sign up korun. Login korle role onujayi dashboard open hobe. Password ba Google login issue hole login page er instruction follow korun or support/admin ke bolun.'
        return 'Use the Register page to create a patient or doctor account. After login, the platform opens the dashboard for your role. For password or Google login trouble, follow the login page guidance or contact support/admin.'
    if any(word in lowered for word in ['service', 'services', 'offer', 'offers']):
        if language == 'bn':
            return 'এই প্ল্যাটফর্মে পেশাদার কনসালটেশন, মানসিক স্বাস্থ্য অ্যাসেসমেন্ট, ডাক্তার খোঁজা, অ্যাপয়েন্টমেন্ট বুকিং, ওয়েলনেস ব্লগ, প্রেসক্রিপশন, দৈনিক স্বাস্থ্য টাস্ক এবং Kaan Pete Roi helpline সাপোর্ট আছে।'
        if language == 'banglish':
            return 'Ei platform e mental health assessment, doctor recommendation, appointment booking, Jitsi consultation, demo payment, prescription, daily tasks, dashboards, blogs, and Kaan Pete Roi helpline support ache.'
        return (
            'The platform supports mental wellness care through professional consultations, mental health assessments, '
            'doctor discovery, appointment booking, published wellness blogs, prescriptions, daily health tasks, '
            'and emergency guidance that directs immediate danger to Kaan Pete Roi helpline at +880 9612-119911.'
        )
    if any(word in lowered for word in ['assessment', 'phq', 'gad', 'screening']):
        if language == 'bn':
            return 'প্ল্যাটফর্মে ডিপ্রেশন ও উদ্বেগ স্ক্রিনিংয়ের জন্য PHQ-9 এবং GAD-7 অ্যাসেসমেন্ট আছে। অ্যাসেসমেন্ট শেষে উপযুক্ত ডাক্তার রেকমেন্ডেশন পাওয়া যায়।'
        if language == 'banglish':
            return 'Assessment page e core mental health questions answer korle platform severity bujhte help kore and suitable doctor recommend kore. Dynamic follow-up questions need hole same flow e ashe.'
        return (
            'The platform offers mental health assessments, including PHQ-9 and GAD-7 screening for depression and anxiety. '
            'After completing an assessment, the platform can show severity information and recommend suitable doctors.'
        )
    if any(word in lowered for word in ['hotline', 'emergency', 'crisis', 'center']):
        if language == 'bn':
            return 'জরুরি সহায়তার জন্য emergency পেজ ব্যবহার করুন এবং Kaan Pete Roi helpline-এ +880 9612-119911 নম্বরে যোগাযোগ করুন। তাৎক্ষণিক বিপদ হলে নিকটস্থ ইমার্জেন্সি বিভাগে যান।'
        if language == 'banglish':
            return 'Emergency hole emergency page use korun and Kaan Pete Roi helpline +880 9612-119911 e contact korun. Immediate danger thakle nearest emergency department e jan.'
        return (
            'For urgent support, use the emergency page and contact Kaan Pete Roi helpline at +880 9612-119911. '
            'If there is immediate danger or a medical emergency, contact local emergency services or go to the nearest emergency room.'
        )
    if any(word in lowered for word in ['recommendation', 'recommendations', 'doctor recommend']):
        return assessment_first_response(message, language)
    if any(word in lowered for word in ['consultation', 'jitsi', 'room', 'video']):
        if language == 'bn':
            return 'ভিডিও কনসালটেশন Jitsi দিয়ে চলে এবং পেজটি AJAX polling ব্যবহার করে। রুম সময়ের ১০ মিনিট আগে খোলে, ডাক্তার শুরু করলে রোগী ভিডিওতে ঢুকতে পারে।'
        if language == 'banglish':
            return 'Consultation room Jitsi use kore, ar live status AJAX polling diye update hoy. Room schedule er 10 min age open hoy, doctor start korle patient video te join korte pare.'
        return 'Video consultations use Jitsi with AJAX polling for live status. The room opens 10 minutes before schedule, and patients wait until the doctor starts or joins the session.'
    if any(word in lowered for word in ['payment', 'demo', 'pay']):
        if language == 'bn':
            return 'এই প্ল্যাটফর্মে এখন ডেমো পেমেন্ট বুকিং ফ্লো আছে। বাস্তব টাকা চার্জ করা হয় না, কিন্তু বুকিং কনফার্মেশনের মতো অভিজ্ঞতা দেখানো হয়।'
        if language == 'banglish':
            return 'Payment ekhane demo booking flow. Real taka charge hoy na, kintu booking confirmation er experience test kora jay.'
        return 'This project uses a demo payment booking flow. It does not charge real money, but it keeps the booking confirmation experience available for testing.'
    if any(word in lowered for word in ['prescription', 'task', 'tasks', 'daily']):
        if language == 'bn':
            return 'কনসালটেশন শেষে ডাক্তার প্রেসক্রিপশন ও দৈনিক টাস্ক দিতে পারেন। রোগী ড্যাশবোর্ড থেকে এগুলো দেখতে ও টাস্ক সম্পন্ন হিসেবে মার্ক করতে পারে।'
        if language == 'banglish':
            return 'Consultation seshe doctor prescription and daily tasks dite pare. Patient dashboard theke prescription dekhte parbe and task complete mark korte parbe.'
        return 'After a consultation, doctors can save prescriptions and assign daily tasks. Patients can view prescriptions and mark assigned tasks from their dashboard.'
    if any(word in lowered for word in ['dashboard', 'dashboards']):
        if language == 'bn':
            return 'রোল অনুযায়ী আলাদা ড্যাশবোর্ড আছে: patient, doctor, এবং admin। Patient dashboard appointment, task, prescription ও assessment flow দেখায়।'
        if language == 'banglish':
            return 'Role onujayi dashboard alada: patient, doctor, admin. Patient dashboard e appointments, tasks, prescriptions, and assessment access thake.'
        return 'The platform has role-based dashboards for patients, doctors, and admins. Patient dashboards show appointments, tasks, prescriptions, and assessment access.'

    if not contexts:
        return safe_fallback_response(message, language, doctors)

    best = contexts[0]
    answer = best['content'].strip()
    if len(answer) > 700:
        answer = answer[:700].rsplit(' ', 1)[0] + '...'
    prefix = 'প্ল্যাটফর্ম তথ্য থেকে যা পেলাম: ' if language == 'bn' else 'Here is what I found in the platform information: '
    return compact_response(f'{prefix}{answer}')


def wellness_context_fallback(message, contexts, language='en', doctors=None):
    if is_crisis_query(message):
        return crisis_response(language, doctors)
    if is_breathing_tool_request(message):
        return breathing_tool_response(language)
    if not contexts:
        return safe_fallback_response(message, language, doctors)

    best = contexts[0]
    content = re.sub(r'\s+', ' ', best.get('content', '')).strip()
    suggested = extract_doc_section(content, 'Suggested response') or extract_doc_section(content, 'Quick support')
    if not suggested:
        suggested = content

    suggested = clean_suggested_text(strip_doc_metadata(suggested))
    if is_low_quality_reply(suggested):
        return safe_fallback_response(message, language, doctors)
    if len(suggested) > 520:
        suggested = suggested[:520].rsplit(' ', 1)[0] + '...'

    note = professional_support_note(language)
    assessment_note = f" {assessment_guidance_text(language)}" if has_doctor_or_booking_intent(message) else ''
    return compact_response(f"{suggested} {note}{assessment_note}")


def is_low_quality_reply(text):
    cleaned = re.sub(r'[^a-z0-9\s]', ' ', (text or '').lower())
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    if not cleaned:
        return True
    bad_replies = {
        'try', 'try this', 'okay', 'ok', 'sure', 'yes', 'no', 'i understand',
        'i hear you', 'this is not a diagnosis',
    }
    if cleaned in bad_replies:
        return True
    words = cleaned.split()
    return len(words) <= 2 and words[0] in {'try', 'okay', 'ok', 'sure'}


def clean_suggested_text(text):
    text = (text or '').strip()
    repeated_starts = [
        'A helpful next step is:',
        'One helpful next step is:',
        'One small step you can try is:',
        'A small first step could be:',
        'A small next step could be:',
        'Try this:',
    ]
    changed = True
    while changed:
        changed = False
        for phrase in repeated_starts:
            if text.lower().startswith(phrase.lower()):
                text = text[len(phrase):].strip(' :-')
                changed = True
    return text


def extract_doc_section(content, heading):
    pattern = rf'{re.escape(heading)}\s*:?\s*(.*?)(?:\s+[A-Z][A-Za-z ]{{2,}}\s*:|$)'
    match = re.search(pattern, content, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ''


def strip_doc_metadata(text):
    text = re.sub(r'\b(Category|Sources?|Source URL|Last reviewed|Bot guidance|Safety note)\s*:[^\.]+\.?', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip(' -')
    return text


def ai_chat(system_prompt, user_prompt):
    if not getattr(settings, 'CHATBOT_AI_ENABLED', False):
        return ''
    provider = (getattr(settings, 'CHATBOT_AI_PROVIDER', 'ollama') or '').lower()
    if provider == 'ollama':
        return ollama_chat(system_prompt, user_prompt)
    if provider == 'gemini':
        return gemini_chat(system_prompt, user_prompt)
    logger.warning('Unsupported chatbot provider %s; using local fallback.', provider)
    return ''


def ollama_chat(system_prompt, user_prompt):
    base_url = getattr(settings, 'OLLAMA_BASE_URL', OLLAMA_BASE_URL).rstrip('/')
    model = getattr(settings, 'OLLAMA_MODEL', OLLAMA_MODEL)
    timeout = getattr(settings, 'CHATBOT_TIMEOUT', CHAT_TIMEOUT)
    if not model:
        return ''

    url = f'{base_url}/api/chat'
    payload = {
        'model': model,
        'stream': False,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'options': {
            'temperature': 0.25,
            'num_predict': 220,
        },
    }
    req = request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
        text = data.get('message', {}).get('content', '') or data.get('response', '')
        return compact_response(text)
    except Exception as exc:
        logger.warning('Ollama chatbot unavailable; using RAG/local fallback: %s', exc)
        return ''


def gemini_chat(system_prompt, user_prompt):
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    model = getattr(settings, 'GEMINI_MODEL', 'gemini-1.5-flash')
    timeout = getattr(settings, 'CHATBOT_TIMEOUT', 6)
    if not api_key:
        return ''

    url = (
        'https://generativelanguage.googleapis.com/v1beta/models/'
        f'{model}:generateContent?{urlencode({"key": api_key})}'
    )
    payload = {
        'systemInstruction': {'parts': [{'text': system_prompt}]},
        'contents': [{'role': 'user', 'parts': [{'text': user_prompt}]}],
        'generationConfig': {
            'temperature': 0.35,
            'maxOutputTokens': 220,
        },
    }
    req = request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
        parts = data.get('candidates', [{}])[0].get('content', {}).get('parts', [])
        text = ' '.join(part.get('text', '') for part in parts).strip()
        return compact_response(text)
    except Exception as exc:
        logger.warning('Gemini chatbot unavailable; using local fallback: %s', exc)
        return ''


def compact_response(text):
    text = re.sub(r'\s+', ' ', (text or '')).strip()
    if not text:
        return ''
    diagnosis_disclaimer = 'This is not a diagnosis or a ' + 'replacement for therapy'
    text = text.replace(f'{diagnosis_disclaimer}.', '')
    text = text.replace(diagnosis_disclaimer, '')
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^[,;:.!?\-]+\s*', '', text)
    text = re.sub(r'(?i)(api key|gemini_api_key|secret_key)\s*[:=]\s*\S+', '[hidden]', text)
    sentences = re.split(r'(?<=[.!?।])\s+', text)
    compact = ' '.join(sentence for sentence in sentences[:6] if sentence).strip()
    return compact[:900].strip()


def sanitize_message(message):
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ' ', message or '')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    max_length = getattr(settings, 'CHATBOT_MAX_MESSAGE_LENGTH', CHATBOT_MAX_MESSAGE_LENGTH)
    return cleaned[:max_length]


def embed_text(text):
    return local_hash_embedding(text), LOCAL_EMBED_MODEL_NAME


def get_or_create_chat_session(session_id=None, user=None, django_session_key='', language='en'):
    session = None
    if session_id:
        try:
            candidate = ChatSession.objects.filter(pk=session_id).first()
        except (ValueError, TypeError):
            candidate = None
        if candidate:
            if getattr(user, 'is_authenticated', False):
                if candidate.user_id in {None, user.pk}:
                    session = candidate
            elif candidate.user_id is None and candidate.session_key == (django_session_key or ''):
                session = candidate

    if session is None:
        session = ChatSession.objects.create(
            user=user if getattr(user, 'is_authenticated', False) else None,
            session_key=django_session_key or '',
            language=language,
        )
    else:
        changed_fields = []
        if language and session.language != language:
            session.language = language
            changed_fields.append('language')
        if getattr(user, 'is_authenticated', False) and session.user_id != user.pk:
            session.user = user
            changed_fields.append('user')
        if django_session_key and session.session_key != django_session_key:
            session.session_key = django_session_key
            changed_fields.append('session_key')
        if changed_fields:
            session.save(update_fields=changed_fields + ['last_activity_at'])

    return session


def update_session_title(chat_session, message, language):
    if chat_session.title:
        return
    title = message[:80].strip()
    chat_session.title = title or ('নতুন চ্যাট' if language == 'bn' else 'New chat')
    chat_session.save(update_fields=['title', 'language', 'last_activity_at'])


def get_recent_history(chat_session, limit=MAX_HISTORY_MESSAGES):
    messages = list(chat_session.messages.order_by('-created_at')[:limit])
    return list(reversed(messages))


def is_duplicate_recent_user_message(chat_session, message, seconds=10):
    recent = chat_session.messages.filter(role='user').order_by('-created_at').first()
    if not recent or recent.content != message:
        return False
    age = (time_now() - recent.created_at).total_seconds()
    return age <= seconds


def trim_chat_session_messages(chat_session, max_messages=MAX_STORED_MESSAGES):
    extra_ids = list(
        chat_session.messages.order_by('-created_at').values_list('id', flat=True)[max_messages:]
    )
    if extra_ids:
        ChatMessage.objects.filter(id__in=extra_ids).delete()


def time_now():
    from django.utils import timezone

    return timezone.now()


def format_history(messages):
    if not messages:
        return "No previous messages."
    lines = []
    user_messages = [message for message in messages if message.role == 'user'][-MAX_USER_HISTORY_MESSAGES:]
    for message in user_messages:
        content = re.sub(r'\s+', ' ', message.content).strip()
        lines.append(f"user: {content[:450]}")
    return '\n'.join(lines)


def detect_language(text):
    bangla_chars = len(re.findall(r'[\u0980-\u09FF]', text or ''))
    if bangla_chars >= 2:
        return 'bn'
    lowered = (text or '').lower()
    banglish_terms = {
        'ami', 'amar', 'korte', 'hobe', 'ki', 'keno', 'valo', 'bhalo',
        'mon', 'kharap', 'ghum', 'lagche', 'hocche', 'ache', 'nai',
    }
    if len(set(re.findall(r'[a-z]+', lowered)) & banglish_terms) >= 2:
        return 'banglish'
    return 'en'


def translate_static(text, language):
    if language == 'bn':
        translations = {
            "I'm here with you. What would you like to talk about?": "আমি আপনার সাথে আছি। আপনি কী নিয়ে কথা বলতে চান?",
        }
        return translations.get(text, text)
    return text


def extract_symptoms_from_history(history_or_text):
    if isinstance(history_or_text, str):
        text = history_or_text
    else:
        text = ' '.join(message.content for message in history_or_text if message.role == 'user')
    lowered = text.lower()
    symptoms = set()
    for symptom, config in SYMPTOM_KEYWORDS.items():
        if any(term in lowered for term in config['en']) or any(term in text for term in config['bn']):
            symptoms.add(symptom)
    return sorted(symptoms)


def recommend_doctors_for_symptoms(symptoms, crisis=False, limit=3):
    if not symptoms and not crisis:
        return []

    doctors = Doctor.objects.filter(is_available=True).select_related('user').prefetch_related('specializations', 'primary_focuses')
    scored = []
    desired_terms = set()
    for symptom in symptoms:
        desired_terms.update(SYMPTOM_KEYWORDS.get(symptom, {}).get('doctor_terms', []))
    if crisis:
        desired_terms.update(['psychiatrist', 'emergency', 'crisis'])

    for doctor in doctors:
        searchable = ' '.join([
            doctor.name or '',
            doctor.specialty or '',
            doctor.primary_focus or '',
            doctor.qualification or '',
            doctor.bio or '',
            ' '.join(doctor.expertise_tags or []),
        ]).lower()
        score = 0
        matched_terms = []
        for term in desired_terms:
            if term and term.lower() in searchable:
                score += 4
                matched_terms.append(term)
        for symptom in symptoms:
            if symptom in searchable:
                score += 3
                matched_terms.append(symptom)
        if crisis and doctor.emergency_support:
            score += 8
            matched_terms.append('emergency support')
        if doctor.available_online:
            score += 1
        score += min(doctor.years_of_experience, 20) / 10
        score += min(doctor.availability_score, 5) / 2

        if score > 0:
            scored.append((score, doctor, sorted(set(matched_terms))))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            'id': doctor.pk,
            'name': doctor.name or doctor.user.get_full_name() or doctor.user.username,
            'specializations': doctor.specialization_labels,
            'primary_focuses': doctor.primary_focus_labels,
            'experience_years': doctor.years_of_experience,
            'available_online': doctor.available_online,
            'emergency_support': doctor.emergency_support,
            'consultation_fee': str(doctor.consultation_fee) if doctor.consultation_fee is not None else '',
            'profile_url': reverse('doctor_details', kwargs={'doctor_id': doctor.pk}),
            'score': round(score, 2),
            'matched_terms': matched_terms,
        }
        for score, doctor, matched_terms in scored[:limit]
    ]


def serialize_doctors(doctors):
    return doctors or []


def doctor_recommendation_text(doctors, language='en', emergency=False):
    # Keep this helper for older call sites, but do not expose direct doctor names in chatbot text.
    # The intended flow is assessment first, then doctor/therapist options from the platform.
    if not doctors and not emergency:
        return ''
    return assessment_guidance_text(language, emergency=emergency)


def get_chat_history(session_id, user=None, limit=50):
    session = ChatSession.objects.filter(pk=session_id).first()
    if not session:
        return None
    if session.user_id and getattr(user, 'is_authenticated', False) and session.user_id != user.pk:
        return None
    messages = session.messages.order_by('-created_at')[:limit]
    return {
        'session_id': str(session.id),
        'language': session.language,
        'messages': [
            {
                'role': message.role,
                'content': message.content,
                'category': message.category,
                'metadata': message.metadata,
                'created_at': message.created_at.isoformat(),
            }
            for message in reversed(list(messages))
        ],
    }


def detect_emergency_payload(message):
    language = detect_language(message)
    is_crisis = is_crisis_query(message)
    return {
        'is_emergency': is_crisis,
        'risk_level': 'critical' if is_crisis else 'low',
        'language': language,
        'detected_terms': detect_crisis_terms(message) if is_crisis else [],
        'message': crisis_response(language) if is_crisis else translate_static("I'm here with you. What would you like to talk about?", language),
    }


def ensure_knowledge_base_synced(force=False):
    if not force and ChatbotKnowledgeChunk.objects.exists():
        return {'created': 0, 'updated': 0, 'deleted': 0, 'unchanged': ChatbotKnowledgeChunk.objects.count()}
    return sync_knowledge_base()


@transaction.atomic
def sync_knowledge_base(source_filter=None):
    docs = collect_chatbot_documents()
    seen_keys = set()
    summary = {'created': 0, 'updated': 0, 'deleted': 0, 'unchanged': 0}

    for doc in docs:
        if source_filter and doc['source_type'] not in source_filter:
            continue
        chunks = chunk_text(doc['content'])
        for index, chunk in enumerate(chunks):
            seen_keys.add((doc['source_type'], doc['source_id'], index))
            embedding_input = f"{doc['title']}\n{chunk}"
            content_hash = hashlib.sha256(embedding_input.encode('utf-8')).hexdigest()
            existing = ChatbotKnowledgeChunk.objects.filter(
                source_type=doc['source_type'],
                source_id=doc['source_id'],
                chunk_index=index,
            ).first()
            if (
                existing
                and existing.content_hash == content_hash
                and existing.embedding_model == LOCAL_EMBED_MODEL_NAME
            ):
                summary['unchanged'] += 1
                continue
            embedding, embedding_model = embed_text(embedding_input)
            _, created = ChatbotKnowledgeChunk.objects.update_or_create(
                source_type=doc['source_type'],
                source_id=doc['source_id'],
                chunk_index=index,
                defaults={
                    'title': doc['title'][:255],
                    'url': doc.get('url', '')[:500],
                    'content': chunk,
                    'content_hash': content_hash,
                    'embedding': embedding,
                    'embedding_model': embedding_model,
                },
            )
            if created:
                summary['created'] += 1
            else:
                summary['updated'] += 1

    if not source_filter:
        for chunk in ChatbotKnowledgeChunk.objects.all():
            key = (chunk.source_type, chunk.source_id, chunk.chunk_index)
            if key not in seen_keys:
                chunk.delete()
                summary['deleted'] += 1

    return summary


def collect_chatbot_documents():
    docs = collect_platform_documents()
    docs.extend(collect_curated_knowledge_documents())
    return docs


def collect_platform_documents():
    docs = [
        {
            'source_type': 'platform_static',
            'source_id': 'emergency_resources',
            'title': 'Emergency Support Resources',
            'url': '/emergency/',
            'content': (
                "Emergency Mental Health Support. AI Emergency Support is available on the emergency page. "
                "Kaan Pete Roi helpline is available at +880 9612-119911. Crisis steps include stop, stay safe, think again, talk to someone, take care of your body, get help, and make a plan. "
                "Users can call the helpline, find a therapist, visit centers, and use the 3-3-3 breathing exercise."
            ),
        },
        {
            'source_type': 'platform_static',
            'source_id': 'appointments',
            'title': 'Appointments and Doctors',
            'url': '/patient/doctors/',
            'content': (
                "Patients can browse doctors, view doctor profiles, book appointments, choose consultation types, and manage appointments from the patient dashboard. "
                "Doctors have specializations, primary focus areas, schedules, consultation fees, online availability, and emergency support status."
            ),
        },
        {
            'source_type': 'platform_static',
            'source_id': 'consultation_room',
            'title': 'Video Consultation Room',
            'url': '/patient/appointments/',
            'content': (
                "Video consultations use a Jitsi room inside a normal Django page. "
                "The room uses AJAX polling for live status, opens 10 minutes before the scheduled time, allows patients to wait until the doctor joins or starts, and limits rejoining to 3 attempts per participant. "
                "If the doctor leaves after the session starts and does not return within 3 minutes, the consultation can be completed automatically. "
                "If nobody joins, the room remains available for 30 minutes from the scheduled start time."
            ),
        },
        {
            'source_type': 'platform_static',
            'source_id': 'assessments',
            'title': 'Mental Health Assessments',
            'url': '/patient/clinical-assessment/',
            'content': (
                "The platform includes PHQ-9 and GAD-7 clinical assessments for depression and anxiety screening. "
                "Assessment results can recommend doctors and identify emergency risk when responses indicate possible self-harm."
            ),
        },
        {
            'source_type': 'platform_static',
            'source_id': 'core_assessment_and_recommendations',
            'title': 'Core Assessment and Doctor Recommendations',
            'url': '/patient/assessment/',
            'content': (
                "The core mental health assessment has seven permanent Likert-scale questions for depression, anxiety, sleep, energy, and self-esteem. "
                "Dynamic follow-up questions remain in the recommendation engine. "
                "Doctor recommendations use assessment signals, condition focus, specialties, availability, and doctor profile matching."
            ),
        },
        {
            'source_type': 'platform_static',
            'source_id': 'demo_payment',
            'title': 'Demo Payment Flow',
            'url': '/patient/appointments/',
            'content': (
                "The booking payment flow is a demo flow for the university project. "
                "It does not charge real money and uses configured test verification to confirm bookings."
            ),
        },
        {
            'source_type': 'platform_static',
            'source_id': 'prescriptions_daily_tasks_dashboards',
            'title': 'Prescriptions, Daily Tasks, and Dashboards',
            'url': '/patient/dashboard/',
            'content': (
                "Doctors can save consultation notes, prescriptions, and daily health tasks after consultations. "
                "Patients can view prescriptions and complete daily tasks from the patient dashboard. "
                "The platform has role-based dashboards for patients, doctors, and admins."
            ),
        },
        {
            'source_type': 'platform_static',
            'source_id': 'accounts_and_login',
            'title': 'Accounts, Login, and Dashboards',
            'url': '/login/',
            'content': (
                "Users can register and log in as patients, doctors, or admins. "
                "After login, the platform redirects each user to the correct role-based dashboard. "
                "Google OAuth can be configured by environment variables, and support/admin can help with account access issues."
            ),
        },
    ]
    docs.extend(collect_template_documents())
    docs.extend(collect_database_documents())
    return [doc for doc in docs if doc.get('content', '').strip()]


def collect_curated_knowledge_documents():
    knowledge_dir = Path(settings.BASE_DIR) / 'chatbot_knowledge'
    docs = []
    if not knowledge_dir.exists():
        return docs

    for markdown_path in sorted(knowledge_dir.rglob('*.md')):
        raw = markdown_path.read_text(encoding='utf-8', errors='ignore')
        content = clean_markdown_text(raw)
        if not content:
            continue
        relative = markdown_path.relative_to(knowledge_dir)
        source_type = source_type_for_knowledge_path(relative)
        metadata = extract_markdown_metadata(raw)
        docs.append({
            'source_type': source_type,
            'source_id': str(relative).replace('\\', '/'),
            'title': metadata.get('title') or markdown_path.stem.replace('_', ' ').title(),
            'url': metadata.get('source_url', ''),
            'content': content,
        })
    return docs


def source_type_for_knowledge_path(relative_path):
    first_part = relative_path.parts[0].lower() if relative_path.parts else ''
    if first_part == 'wellness':
        return 'wellness_doc'
    if first_part == 'safety':
        return 'safety_doc'
    if first_part == 'platform':
        return 'platform_manual'
    return 'wellness_doc'


def extract_markdown_metadata(raw):
    metadata = {}
    lines = raw.splitlines()
    for line in lines[:20]:
        stripped = line.strip()
        if stripped.startswith('#') and 'title' not in metadata:
            metadata['title'] = stripped.lstrip('#').strip()
            continue
        if ':' not in stripped:
            continue
        key, value = stripped.split(':', 1)
        normalized = key.strip().lower().replace(' ', '_')
        if normalized in {'title', 'source', 'source_url', 'category', 'last_reviewed'}:
            metadata[normalized] = value.strip()
    return metadata


def clean_markdown_text(raw):
    text = re.sub(r'```[\s\S]*?```', ' ', raw)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'^\s*#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', text)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def collect_template_documents():
    template_dir = Path(settings.BASE_DIR) / 'home' / 'templates' / 'home'
    docs = []
    for template_path in template_dir.glob('*.html'):
        raw = template_path.read_text(encoding='utf-8', errors='ignore')
        text = extract_template_text(raw)
        if not text:
            continue
        source_id = f"{template_path.stem}:{int(template_path.stat().st_mtime)}"
        docs.append({
            'source_type': 'template_page',
            'source_id': source_id,
            'title': f"{template_path.stem.replace('_', ' ').title()} Page",
            'url': f"/{'' if template_path.stem == 'homepage' else template_path.stem + '/'}",
            'content': text,
        })
    return docs


def collect_database_documents():
    docs = []

    for blog in BlogPost.objects.filter(status='published').select_related('author__user'):
        docs.append({
            'source_type': 'blog_post',
            'source_id': str(blog.pk),
            'title': blog.title,
            'url': reverse('blog_detail', kwargs={'slug': blog.slug}),
            'content': f"{blog.title}\n{blog.excerpt}\n{blog.content}\nCategory: {blog.get_category_display()}",
        })

    doctors = Doctor.objects.select_related('user').prefetch_related('specializations', 'primary_focuses')
    for doctor in doctors:
        schedule = []
        for item in DoctorSchedule.objects.filter(doctor=doctor, is_available=True):
            schedule.append(f"{item.get_day_of_week_display()} {item.start_time}-{item.end_time}")
        docs.append({
            'source_type': 'doctor_profile',
            'source_id': str(doctor.pk),
            'title': f"Doctor Profile: {doctor.name or doctor.user.get_full_name()}",
            'url': reverse('doctor_details', kwargs={'doctor_id': doctor.pk}),
            'content': (
                f"Doctor name: {doctor.name or doctor.user.get_full_name()}. "
                f"Specializations: {doctor.specialty}. Primary focus: {doctor.primary_focus}. "
                f"Qualification: {doctor.qualification}. Experience: {doctor.years_of_experience} years. "
                f"Bio: {doctor.bio}. Clinic: {doctor.clinic_name or ''} {doctor.clinic_address or ''}. "
                f"Available online: {doctor.available_online}. Emergency support: {doctor.emergency_support}. "
                f"Consultation fee: {doctor.consultation_fee or 'not listed'}. Schedule: {', '.join(schedule) or 'not listed'}."
            ),
        })

    for template in HealthTaskTemplate.objects.filter(is_active=True):
        docs.append({
            'source_type': 'health_task_template',
            'source_id': str(template.pk),
            'title': f"Health Task: {template.title}",
            'url': '/patient/dashboard/',
            'content': f"{template.title}. Category: {template.get_category_display()}. {template.description}",
        })

    for question in AssessmentQuestion.objects.all():
        docs.append({
            'source_type': 'assessment_question',
            'source_id': str(question.pk),
            'title': f"Assessment Question: {question.category}",
            'url': '/patient/assessment/',
            'content': f"Assessment question category {question.category}: {question.question_text}",
        })

    for specialization in DoctorSpecialization.objects.filter(is_active=True):
        docs.append({
            'source_type': 'doctor_specialization',
            'source_id': str(specialization.pk),
            'title': f"Doctor Specialization: {specialization.label}",
            'url': '/patient/doctors/',
            'content': f"Doctor specialization option: {specialization.label} ({specialization.value}).",
        })

    for focus in DoctorPrimaryFocus.objects.filter(is_active=True):
        docs.append({
            'source_type': 'doctor_primary_focus',
            'source_id': str(focus.pk),
            'title': f"Doctor Primary Focus: {focus.label}",
            'url': '/patient/doctors/',
            'content': f"Doctor primary focus option: {focus.label} ({focus.value}).",
        })

    return docs


def extract_template_text(raw):
    cleaned = re.sub(r'<script[\s\S]*?</script>', ' ', raw, flags=re.IGNORECASE)
    cleaned = re.sub(r'<style[\s\S]*?</style>', ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'{%[\s\S]*?%}|{{[\s\S]*?}}|{#[\s\S]*?#}', ' ', cleaned)
    cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()


def chunk_text(text, max_chars=1800, overlap=120):
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = text.rfind('. ', start, end)
            if boundary > start + 300:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def local_hash_embedding(text, dimensions=LOCAL_EMBED_DIM):
    vector = [0.0] * dimensions
    tokens = re.findall(r'[a-z0-9]{2,}', text.lower())
    for token in tokens:
        digest = hashlib.md5(token.encode('utf-8')).digest()
        index = int.from_bytes(digest[:4], 'big') % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    return normalize_vector(vector)


def normalize_vector(vector):
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if not norm:
        return values
    return [value / norm for value in values]


def cosine_similarity(left, right):
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    return sum(float(left[index]) * float(right[index]) for index in range(size))


def tokenize(text):
    tokens = []
    for token in re.findall(r'[a-z0-9]{2,}', text.lower()):
        token = TOKEN_SYNONYMS.get(token, token)
        if len(token) > 4 and token.endswith('s') and token not in {'stress'}:
            token = token[:-1]
        if token not in STOPWORDS:
            tokens.append(token)
    return tokens


def lexical_similarity(query_tokens, text):
    if not query_tokens:
        return 0.0
    doc_tokens = set(tokenize(text))
    if not doc_tokens:
        return 0.0
    overlap = len(query_tokens & doc_tokens)
    return overlap / math.sqrt(len(query_tokens) * len(doc_tokens))


def _source_payload(context):
    return {
        'title': context.get('title', ''),
        'url': context.get('url', ''),
        'score': context.get('score', 0),
    }


def schedule_knowledge_sync():
    try:
        sync_knowledge_base()
    except Exception:
        logger.exception('Knowledge sync failed.')

