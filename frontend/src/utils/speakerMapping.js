const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/**
 * Replace complete speaker labels in free text without letting labels such as
 * "คนพูด 1" corrupt "คนพูด 10". The surrounding characters are captured
 * instead of using lookbehind so this also works in older evergreen browsers.
 */
export function replaceSpeakerLabels(text, mapping) {
    if (typeof text !== 'string' || !mapping) return text

    const entries = Object.entries(mapping)
        .filter(([source, target]) => source && typeof target === 'string' && target.trim())
        .sort(([left], [right]) => right.length - left.length)

    if (entries.length === 0) return text

    const replacements = Object.fromEntries(entries)
    const alternatives = entries.map(([source]) => escapeRegExp(source)).join('|')
    const pattern = new RegExp(`(^|[^\\p{L}\\p{N}_])(${alternatives})(?![\\p{L}\\p{N}_])`, 'gu')

    return text.replace(pattern, (match, prefix, speaker) => (
        `${prefix}${replacements[speaker] || speaker}`
    ))
}

function aggregateSpeakerMetric(metric, mapping) {
    const aggregated = {}

    for (const [speaker, value] of Object.entries(metric || {})) {
        const mappedSpeaker = mapping[speaker] || speaker
        const numericValue = Number(value)
        aggregated[mappedSpeaker] = (aggregated[mappedSpeaker] || 0)
            + (Number.isFinite(numericValue) ? numericValue : 0)
    }

    return aggregated
}

/**
 * Return a mapped clone of a processing result. The original worker result is
 * intentionally left untouched so changing names remains reversible in the UI.
 */
export function applySpeakerMapping(result, mapping) {
    if (!result || !mapping || Object.keys(mapping).length === 0) return result

    const mapped = JSON.parse(JSON.stringify(result))
    const transcript = mapped.transcript || {}

    transcript.segments = (transcript.segments || []).map((segment) => ({
        ...segment,
        speaker: mapping[segment.speaker] || segment.speaker,
    }))
    transcript.combined_text = replaceSpeakerLabels(transcript.combined_text || '', mapping)

    const speakerSummary = transcript.speaker_summary || {}
    transcript.speaker_summary = {
        ...speakerSummary,
        speaking_time: aggregateSpeakerMetric(speakerSummary.speaking_time, mapping),
        word_count: aggregateSpeakerMetric(speakerSummary.word_count, mapping),
    }

    mapped.transcript = transcript
    mapped.summary = replaceSpeakerLabels(mapped.summary || '', mapping)

    if (Array.isArray(mapped.agendas)) {
        mapped.agendas = mapped.agendas.map((agenda) => ({
            ...agenda,
            speakers: Array.isArray(agenda.speakers)
                ? agenda.speakers.map((speaker) => mapping[speaker] || speaker)
                : agenda.speakers,
        }))
    }

    return mapped
}
