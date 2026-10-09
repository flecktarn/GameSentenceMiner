import { writable } from 'svelte/store';
import { getGSMEndpoint } from './gsm';

export interface JapaneseSrsCard {
	id: number;
	kanji: string;
	reading: string;
	meaning: string;
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

export async function addToJapaneseSrs(lineId: string, text: string, word: string): Promise<JapaneseSrsCard> {
	const response = await fetch(getGSMEndpoint('/api/japanese-srs/add'), {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ id: lineId, text, word }),
	});
	const data = await response.json().catch(() => ({}));
	if (!response.ok) {
		throw new Error(data.error || `Request failed (HTTP ${response.status}).`);
	}
	return data.card;
}

export function describeAddedCard(card: JapaneseSrsCard) {
	const reading = card.reading && card.reading !== card.kanji ? `（${card.reading}）` : '';
	return `Added ${card.kanji}${reading} to Japanese SRS.`;
}
