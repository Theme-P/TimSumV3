"""
Transcription Text Cleaner — ported from TimSumV2ToV3.

Removes known noise patterns and consecutive repetitive phrases from WhisperX
output without applying document-wide word-frequency limits.  Ordinary meeting
terms can legitimately occur hundreds of times and must never be discarded.
"""

import re


def clean_transcription(text: str) -> str:
    """
    Clean transcription text by removing noise, repetitive phrases,
    and excessive word occurrences.
    
    This is the main entry point — call this after WhisperX transcription
    and before sending to the summarizer.
    """
    if not text or not text.strip():
        return text

    # Work line-by-line so speaker transcript boundaries remain stable.  The
    # pipeline cleans canonical segments once; this also keeps the helper safe
    # for callers that pass a preformatted multi-line transcript.
    cleaned_lines = []
    for line in text.splitlines() or [text]:
        line = remove_noise_patterns(line)
        line = remove_consecutive_substring_repetition(line, min_length=15)
        line = remove_repetitive_phrases(line)
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)

def remove_consecutive_substring_repetition(text: str, min_length: int = 15) -> str:
    """
    Catch long repeating strings without spaces (common in Thai Whisper hallucinations).
    Example: "การประชุมนี้การประชุมนี้การประชุมนี้" -> "การประชุมนี้"
    """
    # Matches any substring of length >= min_length that repeats 2 or more times
    # We use non-greedy matching on the substring length to catch the shortest repeating unit first.
    pattern = rf'(.{{{min_length},}}?)\1+'
    # We replace with a single instance of the substring. 
    # To be safe, we can run it iteratively in case of overlapping patterns.
    prev_text = None
    while text != prev_text:
        prev_text = text
        text = re.sub(pattern, r'\1', text)
    return text


def join_canonical_segments(segments: list[dict]) -> str:
    """Join already-cleaned canonical segments without erasing boundaries."""
    return "\n".join(
        str(segment.get("text") or "").strip()
        for segment in segments
        if str(segment.get("text") or "").strip()
    )


def remove_noise_patterns(text: str) -> str:
    """Remove explicit ASR noise markers and known hallucination patterns."""
    noise_patterns = [
        r'\[(?:เสียงเพลง|เสียงดนตรี|music|noise|inaudible)\]',
        r'\((?:เสียงเพลง|เสียงดนตรี|music|noise|inaudible)\)',
        r'<(?:music|noise|inaudible)>',
        r'♪+',
        # Known hallucinations from old prompts / model behaviors during dead air
        r'ถอดเสียงการประชุม.*',
        r'This is a meeting transcription.*',
        r'这是一个会议记录.*',
        r'การประชุมภาษาไทย\s*อังกฤษ.*',
        r'^\s*สวัสดีครับ\s*$',
    ]

    for pattern in noise_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    return text


def remove_repetitive_phrases(
    text: str,
    min_phrase_length: int = 1,
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
    Backward-compatible no-op for the removed global frequency filter.

    Historically this function deleted every occurrence after the third one
    anywhere in the transcript, corrupting common words such as names and
    agenda terms.  Consecutive hallucinations are handled by
    :func:`remove_repetitive_phrases`; non-consecutive words are preserved.
    """
    del max_occurrences
    return text
