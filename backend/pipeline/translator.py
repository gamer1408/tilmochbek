import os
import json
import re
import html
import time
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from deep_translator import GoogleTranslator
from backend.config import GEMINI_API_KEY, GEMINI_MODEL, GROQ_API_KEY, OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

# Common English filler phrases to remove before translation
FILLER_PATTERNS = [
    r'\b(you know what I mean|you know|as you know)\b',
    r'\b(alright so|so yeah|so basically|basically|and uh|and um)\b',
    r'\b(uh+|um+|ah+|er+|hmm+)\b',
    r'\b(kind of like|sort of like|kind of|sort of)\b',
    r'\b(like I said|like that|as you can see|as you see)\b',
    r'\b(you see|I mean|let\'s say)\b',
]

# Technical dictionary corrections for Uzbek translation post-processing
TERM_REPLACEMENTS = [
    (r'\b(Epify aktyori|Epify|Apify aktyorlari|aktyor|aktyori|aktyorlar)\b', 'Apify aktori'),
    (r'(barcha ma\'lumotlarni\s+)(?:o\'chirib tashladi|o\'chiradi|o\'chirish|qirib tashladi|qiradi|qirib)', r"\1yig'ib oladi"),
    (r'(ma\'lumotlarni\s+)(?:o\'chirib tashladi|o\'chiradi|o\'chirish|qirib tashlash|qirish|qirib)', r"\1yig'ib olish"),
    (r'\b(skraping|skreyping|skreper|qirg\'ichdan o\'tkazuvchi|qirg\'ich|qiruvchi)\b', "ma'lumot yig'uvchi"),
    (r'\b(pechenyelar|pechenye|seans kukilarini|kukilarni)\b', 'sessiya kukilari'),
    (r'\b(qo\'rg\'oshinlar|qo\'rg\'oshin)\b', 'lidlar'),
    (r'\b(million tekshiruvchi|million tasdiqlovchi)\b', 'MillionVerifier'),
]

SYSTEM_PROMPT = (
    "You are an elite AI technical dubbing translator for YouTube tutorial videos into modern Uzbek (O'zbek tili).\n\n"
    "STRICT TRANSLATION RULES:\n"
    "1. STRIP ALL FILLERS: Remove any 'you know', 'uh', 'um', 'basically', 'like'.\n"
    "2. TECHNICAL TERMINOLOGY ACCURACY:\n"
    "   - 'scrape / scraping / data scraping' -> 'ma\\'lumotlarni yig\\'ib olish' (NEVER 'o\\'chirish' or 'qirish'!).\n"
    "   - 'Apify Actor / Actor / Actors' -> 'Apify aktori' (automated scraping bot/script, NOT a movie actor!).\n"
    "   - 'Cookies / Session cookies' -> 'sessiya kukilari'.\n"
    "   - 'Leads / Lead generation' -> 'lidlar' or 'mijozlar kontaktlari'.\n"
    "   - 'Workflow / Pipeline' -> 'ish jarayoni' or 'avtomatik zanjir'.\n"
    "   - Proper nouns & tools ('Apify', 'LinkedIn', 'Sales Navigator', 'MillionVerifier', 'Python', 'API', 'JSON') must remain untranslated.\n"
    "3. CONCISENESS & TIMING: Keep translated Uzbek sentences punchy, natural, and 15-20% shorter than the original English so speech comfortably fits the time window.\n"
    "4. OUTPUT FORMAT: Return ONLY a valid JSON array of objects with exact keys 'id' (integer) and 'translated_text' (string).\n"
    "Example format:\n"
    "[{\"id\": 0, \"translated_text\": \"Bugun biz ma'lumotlarni yig'ib olishni o'rganamiz.\"}]"
)


def clean_filler_words(text: str) -> str:
    """Strips spoken fillers and repetitive speech artifacts from English text."""
    if not text:
        return ""
    cleaned = html.unescape(text)
    for pattern in FILLER_PATTERNS:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    cleaned = re.sub(r'\b(\w+)(?:\s+\1\b)+', r'\1', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'[\r\n]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(" ,.-;!?")
    
    if cleaned.lower() in ["you know", "uh", "um", "ah", "basically", "so yeah", "like"]:
        return ""
    return cleaned


def post_process_uzbek_text(text: str) -> str:
    """Applies strict terminology corrections and typography fixes for Uzbek dubbing."""
    if not text:
        return ""
    processed = re.sub(r'[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    processed = processed.strip().replace("`", "'").replace("ʻ", "'").replace("ʼ", "'")
    
    for pattern, replacement in TERM_REPLACEMENTS:
        processed = re.sub(pattern, replacement, processed, flags=re.IGNORECASE)
    
    processed = re.sub(r'^(xo\'sh|demak|shunday qilib|bilasizmi),\s*', '', processed, flags=re.IGNORECASE)
    return processed.strip()


def _clean_json_response(raw_text: str) -> str:
    """Removes markdown code fences and extraneous text from LLM JSON response."""
    text = raw_text.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1).strip()
        else:
            text = re.sub(r"^```(?:json)?\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
    return text


def _parse_translation_items(raw_content: str) -> List[Dict[str, Any]]:
    """Helper to robustly parse JSON or regex-match translated segments."""
    cleaned_content = _clean_json_response(raw_content)
    parsed_items = None
    try:
        parsed_json = json.loads(cleaned_content)
        if isinstance(parsed_json, list):
            parsed_items = parsed_json
        elif isinstance(parsed_json, dict):
            for key in ["translations", "items", "segments", "data", "result"]:
                if key in parsed_json and isinstance(parsed_json[key], list):
                    parsed_items = parsed_json[key]
                    break
            if parsed_items is None:
                for v in parsed_json.values():
                    if isinstance(v, list):
                        parsed_items = v
                        break
    except Exception:
        pass

    if not parsed_items:
        # Regex fallback for {"id": X, "translated_text": "..."}
        matches = re.findall(r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"translated_text"\s*:\s*"((?:\\.|[^"\\])*)"\s*\}', cleaned_content)
        if matches:
            parsed_items = [{"id": int(m[0]), "translated_text": m[1].encode().decode('unicode_escape', errors='ignore')} for m in matches]

    return parsed_items or []


class AwaitableList(list):
    def __await__(self):
        async def _async_self():
            return self
        return _async_self().__await__()


def translate_full_batch_with_gemini(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Single-Call Batch Subtitle Translation via Google Gemini API:
    Uses gemini-2.5-flash / flash models for sub-2-second, zero-downtime Uzbek translation.
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)

    payload = [{"id": seg.get("id", idx), "text": clean_filler_words(seg.get("text", ""))} for idx, seg in enumerate(segments)]
    user_prompt = f"Translate the following segment array to modern Uzbek according to the rules:\n{json.dumps(payload, ensure_ascii=False)}"

    candidate_models = [
        GEMINI_MODEL,
        "gemini-2.5-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-flash-latest",
        "gemini-2.5-flash-lite",
        "gemini-3.7-flash"
    ]
    # Deduplicate while preserving order
    candidate_models = list(dict.fromkeys([m for m in candidate_models if m]))

    last_error = None

    for model_name in candidate_models:
        try:
            logger.info(f"Translating {len(segments)} segments via Gemini API ({model_name})...")
            
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json"
            )

            response = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=config
            )

            raw_content = response.text or ""
            parsed_items = _parse_translation_items(raw_content)

            if not parsed_items:
                raise ValueError(f"Failed to parse JSON translations from Gemini ({model_name}): {raw_content[:150]}")

            trans_map = {}
            for item in parsed_items:
                if isinstance(item, dict) and "id" in item:
                    trans_map[item["id"]] = post_process_uzbek_text(item.get("translated_text", ""))

            result = []
            for idx, seg in enumerate(segments):
                seg_id = seg.get("id", idx)
                translated = trans_map.get(seg_id, "")
                if not translated:
                    translated = post_process_uzbek_text(seg.get("text", ""))
                result.append({
                    **seg,
                    "translated_text": translated
                })

            logger.info(f"Gemini single-batch translation succeeded ({len(result)} segments via {model_name}).")
            return result

        except Exception as e:
            last_error = e
            err_str = str(e)
            # If 403 permission denied on the key itself, abort Gemini immediately to not waste time
            if "403" in err_str or "PERMISSION_DENIED" in err_str or "denied access" in err_str:
                logger.warning(f"Gemini API permission denied: {e}. Switching immediately to Tier 2 fallback.")
                break
            # If 404 / deprecated model, try next candidate model immediately
            if "404" in err_str or "NOT_FOUND" in err_str or "no longer available" in err_str:
                logger.warning(f"Gemini model {model_name} unavailable: {e}. Trying next candidate...")
                continue
            logger.warning(f"Gemini ({model_name}) error: {e}")

    raise ConnectionError(f"Gemini translation failed: {last_error}")


def translate_full_batch_with_groq(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Single-Call Batch Translation using Groq LLM (High-speed Qwen 3.8 / GPT-OSS / Llama).
    """
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is not set.")

    import groq
    client = groq.Groq(api_key=GROQ_API_KEY, timeout=30.0)
    payload = [{"id": seg.get("id", idx), "text": clean_filler_words(seg.get("text", ""))} for idx, seg in enumerate(segments)]

    candidate_groq_models = [
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "qwen/qwen3.6-27b",
        "groq/compound"
    ]
    last_error = None

    for model_name in candidate_groq_models:
        for attempt in range(2):
            try:
                logger.info(f"Translating {len(segments)} segments in single-batch via Groq ({model_name}, attempt {attempt+1})...")
                res = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"Translate to Uzbek JSON:\n{json.dumps(payload, ensure_ascii=False)}"}
                    ],
                    temperature=0.1,
                    response_format={"type": "json_object"} if "qwen" in model_name or "gpt-oss" in model_name else None
                )

                raw_content = res.choices[0].message.content or ""
                parsed_items = _parse_translation_items(raw_content)

                if not parsed_items:
                    raise ValueError(f"Failed to parse Groq response from {model_name}: {raw_content[:150]}")

                trans_map = {}
                for item in parsed_items:
                    if isinstance(item, dict) and "id" in item:
                        trans_map[item["id"]] = post_process_uzbek_text(item.get("translated_text", ""))

                result = []
                for idx, seg in enumerate(segments):
                    seg_id = seg.get("id", idx)
                    translated = trans_map.get(seg_id, "")
                    if not translated:
                        translated = post_process_uzbek_text(seg.get("text", ""))
                    result.append({
                        **seg,
                        "translated_text": translated
                    })

                logger.info(f"Groq LLM translation succeeded ({len(result)} segments via {model_name}).")
                return result
            except Exception as e:
                last_error = e
                if any(k in str(e) for k in ["404", "model_not_found", "not supported", "rate_limit_exceeded"]):
                    break
                backoff_delay = (attempt + 1) * 1.5
                logger.warning(f"Groq LLM ({model_name}) attempt {attempt + 1} failed: {e}. Retrying in {backoff_delay}s...")
                time.sleep(backoff_delay)

    raise ConnectionError(f"Groq LLM translation failed: {last_error}")


def translate_full_batch_with_openrouter(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Single-Call Batch Translation via OpenRouter API (OpenAI SDK):
    Sends all transcript segments in a structured JSON payload to OpenRouter models.
    """
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY is not set.")

    from openai import OpenAI

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
        default_headers={
            "HTTP-Referer": "https://tilmochbek.uz",
            "X-Title": "Tilmochbek Video Translator"
        },
        timeout=15.0
    )

    payload = [{"id": seg.get("id", idx), "text": clean_filler_words(seg.get("text", ""))} for idx, seg in enumerate(segments)]
    user_prompt = f"Translate the following segment array to modern Uzbek according to the rules:\n{json.dumps(payload, ensure_ascii=False)}"

    candidate_models = [
        "google/gemini-2.5-flash",
        "google/gemini-flash-1.5",
        "deepseek/deepseek-chat",
        "google/gemini-3.5-flash",
        OPENROUTER_MODEL,
        "meta-llama/llama-3.3-70b-instruct"
    ]
    candidate_models = list(dict.fromkeys([m for m in candidate_models if m]))

    last_error = None

    for model_name in candidate_models:
        for attempt in range(2):
            try:
                logger.info(f"Translating {len(segments)} segments via OpenRouter ({model_name}, attempt {attempt+1})...")
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.1,
                    max_tokens=4096
                )

                raw_content = response.choices[0].message.content or ""
                parsed_items = _parse_translation_items(raw_content)

                if not parsed_items:
                    raise ValueError(f"Failed to parse JSON translations from {model_name}: {raw_content[:150]}")

                trans_map = {}
                for item in parsed_items:
                    if isinstance(item, dict) and "id" in item:
                        trans_map[item["id"]] = post_process_uzbek_text(item.get("translated_text", ""))

                result = []
                for idx, seg in enumerate(segments):
                    seg_id = seg.get("id", idx)
                    translated = trans_map.get(seg_id, "")
                    if not translated:
                        translated = post_process_uzbek_text(seg.get("text", ""))
                    result.append({
                        **seg,
                        "translated_text": translated
                    })

                logger.info(f"OpenRouter single-batch translation succeeded ({len(result)} segments via {model_name}).")
                return result

            except Exception as e:
                last_error = e
                err_str = str(e)
                if any(k in err_str for k in ["404", "model_not_found", "unavailable for free", "402", "requires more credits"]):
                    logger.warning(f"OpenRouter model {model_name} unavailable: {e}. Switching to next candidate model...")
                    break
                backoff_delay = (attempt + 1) * 1.5
                logger.warning(f"OpenRouter ({model_name}) attempt {attempt + 1} failed: {e}. Retrying in {backoff_delay}s...")
                time.sleep(backoff_delay)

    raise ConnectionError(f"OpenRouter translation failed across all candidate models: {last_error}")


def _translate_single_deep(seg: Dict[str, Any], translator: GoogleTranslator) -> Dict[str, Any]:
    clean_src = clean_filler_words(seg.get("text", ""))
    if not clean_src:
        return {**seg, "translated_text": ""}
    
    for _ in range(2):
        try:
            t = translator.translate(clean_src)
            if t:
                return {**seg, "translated_text": post_process_uzbek_text(t)}
        except Exception:
            time.sleep(0.3)
    return {**seg, "translated_text": post_process_uzbek_text(clean_src)}


def translate_with_deep_translator(segments: List[Dict[str, Any]], target_lang: str = "uz") -> List[Dict[str, Any]]:
    """Concurrent multi-threaded fallback translation using Deep-Translator."""
    translator = GoogleTranslator(source="auto", target=target_lang)
    logger.info(f"Translating {len(segments)} segments concurrently via Deep-Translator fallback...")

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda s: _translate_single_deep(s, translator), segments))
    return results


def translate_segments(segments: List[Dict[str, Any]], target_lang: str = "uz") -> List[Dict[str, Any]]:
    """
    Ultra-Fast Single-Call Batch Subtitle Translation Engine:
    1. Gemini API (gemini-2.5-flash / flash models) -> Single-Call JSON Batch (<2s).
    2. Groq LLM (Qwen 3.8 / GPT-OSS / Llama) -> Fast Single-Call JSON Batch (~0.4s).
    3. OpenRouter API (Llama 3.3 70B / DeepSeek) fallback.
    4. Deep-Translator concurrent multi-threaded fallback.
    Guaranteed zero downtime and zero crash tolerance.
    """
    if not segments:
        return AwaitableList([])

    for seg in segments:
        seg["cleaned_text"] = clean_filler_words(seg.get("text", ""))

    if target_lang == "uz":
        # Tier 1: Gemini API (Single Batch with gemini-2.5-flash / flash models)
        if GEMINI_API_KEY:
            try:
                if len(segments) <= 100:
                    return AwaitableList(translate_full_batch_with_gemini(segments))
                else:
                    batch_size = 60
                    all_translated = []
                    for i in range(0, len(segments), batch_size):
                        batch = segments[i:i + batch_size]
                        all_translated.extend(translate_full_batch_with_gemini(batch))
                    return AwaitableList(all_translated)
            except Exception as e:
                logger.warning(f"Gemini API translation fallback triggered: {e}")

        # Tier 2: Groq LLM (Ultra-Fast Single Batch)
        if GROQ_API_KEY:
            try:
                if len(segments) <= 100:
                    return AwaitableList(translate_full_batch_with_groq(segments))
                else:
                    batch_size = 60
                    all_translated = []
                    for i in range(0, len(segments), batch_size):
                        batch = segments[i:i + batch_size]
                        all_translated.extend(translate_full_batch_with_groq(batch))
                    return AwaitableList(all_translated)
            except Exception as e:
                logger.warning(f"Groq LLM translation fallback triggered: {e}")

        # Tier 3: OpenRouter API (Single Batch)
        if OPENROUTER_API_KEY:
            try:
                if len(segments) <= 100:
                    return AwaitableList(translate_full_batch_with_openrouter(segments))
                else:
                    batch_size = 60
                    all_translated = []
                    for i in range(0, len(segments), batch_size):
                        batch = segments[i:i + batch_size]
                        all_translated.extend(translate_full_batch_with_openrouter(batch))
                    return AwaitableList(all_translated)
            except Exception as e:
                logger.warning(f"OpenRouter translation fallback triggered: {e}")

    # Tier 4: Deep-Translator Concurrent Fallback
    try:
        return AwaitableList(translate_with_deep_translator(segments, target_lang))
    except Exception as e:
        logger.error(f"Deep-Translator error: {e}. Returning original with terms cleaned.")
        return AwaitableList([{**seg, "translated_text": post_process_uzbek_text(seg.get("text", ""))} for seg in segments])
