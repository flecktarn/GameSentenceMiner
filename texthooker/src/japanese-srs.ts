import { writable } from 'svelte/store';
import { getGSMEndpoint } from './gsm';

export interface JapaneseSrsCard {
	id: number;
	kanji: string;
	reading: string;
	meaning: string;
	image_url?: string | null;
}

export interface JapaneseSrsAddResult {
	card: JapaneseSrsCard;
	warning?: string;
}

// Shows the "add to Japanese SRS" action only once it's turned on in GSM settings.
export const japaneseSrsEnabled$ = writable(false);

export async function refreshJapaneseSrsStatus() {
	try {
		const response = await fetch(getGSMEndpoint('/api/japanese-srs/status'));
		if (response.ok) {
			japaneseSrsEnabled$.set(!!(await response.json()).enabled);
		}
	} catch {
		// GSM not reachable (e.g. standalone texthooker); leave the action hidden.
	}
}

export async function addToJapaneseSrs(lineId: string, text: string, word: string): Promise<JapaneseSrsAddResult> {
	const response = await fetch(getGSMEndpoint('/api/japanese-srs/add'), {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ id: lineId, text, word }),
	});
	const data = await response.json().catch(() => ({}));
	if (!response.ok) {
		throw new Error(data.error || `Request failed (HTTP ${response.status}).`);
	}
	return { card: data.card, warning: data.warning || '' };
}

export function describeAddedCard({ card, warning }: JapaneseSrsAddResult) {
	const reading = card.reading && card.reading !== card.kanji ? `（${card.reading}）` : '';
	const shot = card.image_url ? ' with a screenshot' : '';
	return `Added ${card.kanji}${reading} to Japanese SRS${shot}.${warning ? ` ${warning}` : ''}`;
}

export interface WordSegment {
	text: string;
	/** Dictionary form; only set on vocabulary that can be added. */
	base?: string;
	pos?: string;
	in_srs?: boolean;
}

// Words added this session, so every line marks them without re-splitting.
export const addedWords$ = writable(new Set<string>());

export function rememberAddedWords(...words: (string | undefined)[]) {
	addedWords$.update((set) => {
		const next = new Set(set);
		words.forEach((w) => w && next.add(w));
		return next;
	});
}

const SPLIT_CACHE_LIMIT = 500;
const splitCache = new Map<string, Promise<WordSegment[] | null>>();
let pendingSplits: { text: string; resolve: (segments: WordSegment[] | null) => void }[] = [];
let splitTimer: ReturnType<typeof setTimeout> | undefined;

// Lines that render together (e.g. history on page load) share one request.
async function flushSplits() {
	splitTimer = undefined;
	while (pendingSplits.length) {
		const batch = pendingSplits.splice(0, 100);
		let lines: (WordSegment[] | null)[] = [];
		try {
			const response = await fetch(getGSMEndpoint('/api/japanese-srs/tokenize'), {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ texts: batch.map((item) => item.text) }),
			});
			if (response.ok) {
				lines = (await response.json()).lines || [];
			}
		} catch {
			// Fall back to plain text for these lines.
		}
		batch.forEach((item, i) => {
			const segments = lines[i] || null;
			if (!segments) splitCache.delete(item.text); // Retry next time instead of caching the failure.
			item.resolve(segments);
		});
	}
}

/** Split a line into segments (exactly covering it), or null if word splitting is unavailable. */
export function splitLine(text: string): Promise<WordSegment[] | null> {
	const cached = splitCache.get(text);
	if (cached) return cached;
	const promise = new Promise<WordSegment[] | null>((resolve) => {
		pendingSplits.push({ text, resolve });
		splitTimer ??= setTimeout(flushSplits, 30);
	});
	splitCache.set(text, promise);
	if (splitCache.size > SPLIT_CACHE_LIMIT) {
		splitCache.delete(splitCache.keys().next().value as string);
	}
	return promise;
}
