"""
Transcription Text Cleaner — ported from TimSumV2ToV3.

Removes noise patterns, repetitive phrases, and excessive word occurrences
from WhisperX transcription output to improve summary quality.
"""

import re
from collections import Counter


def clean_transcription(text: str) -> str:
    """
    Clean transcription text by removing noise, repetitive phrases,
    and excessive word occurrences.
    
    This is the main entry point — call this after WhisperX transcription
    and before sending to the summarizer.
    """
    if not text or not text.strip():
        return text

    text = remove_noise_patterns(text)
    text = remove_repetitive_phrases(text)
    text = filter_excessive_words(text)
    
    # Clean up extra whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text


def remove_noise_patterns(text: str) -> str:
    """Remove common ASR hallucination / noise patterns via regex."""
    noise_patterns = [
        r'เสียงดนตรี+',           # Music noise hallucination
        r'สดที่\s*A-TECH\s*งานเลี้ยง+',  # Specific recurring hallucination
        r'สดงสดที่+',              # Repetitive pattern
        r'แสดงสดที่+',             # Display pattern
        r'\[เสียงเพลง\]',          # Music bracket
        r'\[เสียงดนตรี\]',         # Music bracket
        r'\(เสียงเพลง\)',          # Music paren
        r'\(เสียงดนตรี\)',         # Music paren
    ]

    for pattern in noise_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    return text


def remove_repetitive_phrases(
    text: str,
    min_phrase_length: int = 2,
    max_repetitions: int = 2,
) -> str:
    """
    Remove phrases that repeat more than `max_repetitions` times consecutively.
    
    Example: "สวัสดี ครับ สวัสดี ครับ สวัสดี ครับ สวัสดี ครับ" 
           → "สวัสดี ครับ สวัสดี ครับ"  (keep only 2 occurrences)
    """
    words = text.split()
    if len(words) < min_phrase_length * 2:
        return text

    result = []
    i = 0

    while i < len(words):
        found_repetition = False

        for phrase_len in range(min_phrase_length, min(6, len(words) - i + 1)):
            if i + phrase_len > len(words):
                break

            phrase = words[i:i + phrase_len]

            # Count consecutive repetitions
            repetitions = 1
            j = i + phrase_len

            while j + phrase_len <= len(words):
                next_phrase = words[j:j + phrase_len]
                if next_phrase == phrase:
                    repetitions += 1
                    j += phrase_len
                else:
                    break

            # If excessive, keep only max_repetitions
            if repetitions > max_repetitions:
                for _ in range(max_repetitions):
                    result.extend(phrase)
                i = j  # Skip all the repetitions
                found_repetition = True
                break

        if not found_repetition:
            result.append(words[i])
            i += 1

    return ' '.join(result)


def filter_excessive_words(text: str, max_occurrences: int = 3) -> str:
    """
    Filter out words that appear more than `max_occurrences` times in the
    entire text, keeping only the first N occurrences.
    
    This helps remove hallucinated words that WhisperX sometimes repeats
    throughout the entire transcript.
    """
    words = text.split()
    word_counts = Counter(words)
    seen_counts: dict[str, int] = {}
    filtered_words = []

    for word in words:
        if word_counts[word] > max_occurrences:
            seen_counts[word] = seen_counts.get(word, 0) + 1
            if seen_counts[word] <= max_occurrences:
                filtered_words.append(word)
        else:
            filtered_words.append(word)

    return ' '.join(filtered_words)
