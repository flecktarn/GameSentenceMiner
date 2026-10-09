<script lang="ts">
	import {
		mdiCardPlusOutline,
		mdiClockOutline,
		mdiContentSave,
		mdiContentSaveCheck,
		mdiCreationOutline,
		mdiHistory,
		mdiMenu,
		mdiPlay,
		mdiStop,
		mdiTrophy,
	} from '@mdi/js';
	import { createEventDispatcher, onDestroy, onMount, tick } from 'svelte';
	import { fly } from 'svelte/transition';
	import {
		alwaysScrollToNewest$,
		displayVertical$,
		enableLineAnimation$,
		preserveWhitespace$,
		reverseLineOrder$,
		lineIDs$,
		lineData$,
		autoTranslateLines$,
		blurAutoTranslatedLines$,
		milestoneLines$,
		timedOutIDs$,
		unblurTLTimer$,
		showGSMCheckboxes$,
		showScreenshotButton$,
		showAudioButton$,
		showTrimVideoButton$,
		showSaveClipButton$,
		showTranslateButton$,
		settingsOpen$,
	} from '../stores/stores';
	import type { LineItem, LineItemEditEvent } from '../types';
	import {
		dummyFn,
		getAutoScrollStick,
		isScrolledToEnd,
		newLineCharacter,
		shouldAutoScroll,
		updateScroll,
	} from '../util';
	import Icon from './Icon.svelte';
	import AIHelp from './AIHelp.svelte';
	import { formatMegabytes, getGSMEndpoint } from '../gsm';
	import {
		addedWords$,
		addToJapaneseSrs,
		describeAddedCard,
		japaneseSrsEnabled$,
		lookupWord,
		rememberAddedWords,
		splitLine,
		type DictionaryEntry,
		type WordSegment,
	} from '../japanese-srs';

	export let line: LineItem;
	export let index: number;
	export let isLast: boolean;
	export let pipWindow: Window | undefined = undefined;
	export let audioLineId = '';
	export let audioIsPlaying = false;
	export let audioPendingLineId = '';
	export let isSavingClip = false;
	export let isClipSaved = false;
	export let clipSizeBytes: number | undefined = undefined;

	export function deselect() {
		isSelected = false;
	}

	export function getIdIfSelected(range: Range) {
		return isSelected || range.intersectsNode(paragraph) ? line.id : undefined;
	}

	const dispatch = createEventDispatcher<{
		deselected: string;
		selected: string;
		edit: LineItemEditEvent;
		audioToggle: { lineId: string; text: string };
		videoTrim: { lineId: string; text: string };
		saveClip: { lineId: string };
		deleteClip: { lineId: string };
		openClipFolder: { lineId: string };
	}>();

	let paragraph: HTMLElement;
	let originalText = '';
	let previousLineText = line.text;
	let componentMounted = false;
	let lineTextMounted = false;
	let autoTranslationRevision = -1;
	let isSelected = false;
	let isEditable = false;
	// One popover for the line's menus: the ☰ actions, or the saved clip's own menu.
	let openMenu: 'actions' | 'clip' | null = null;
	let menuAnchor: HTMLElement | undefined;
	let aiHelpOpen = false;
	let actionsMenuButton: HTMLButtonElement;
	let actionsMenuPopover: HTMLElement;
	let actionsMenuStyle = 'visibility: hidden;';
	let aiError = '';
	let srsWord = '';
	let srsBusy = false;
	let srsMessage = '';
	let srsError = false;
	let srsMessageTimer: ReturnType<typeof setTimeout> | undefined;
	// The line split into vocabulary, so words can be clicked and added to the SRS.
	let segments: WordSegment[] | null = null;
	let segmentsFor = '';
	let wordPopover: {
		segment: WordSegment;
		style: string;
		entry?: DictionaryEntry | null;
		lookupError?: string;
		looking: boolean;
	} | null = null;
	$: if ($japaneseSrsEnabled$ && line.text !== segmentsFor) {
		loadSegments(line.text);
	}

	async function loadSegments(text: string) {
		segmentsFor = text;
		const result = await splitLine(text);
		if (segmentsFor === text) {
			segments = result;
		}
	}

	function isInSrs(segment: WordSegment, added: Set<string>) {
		return !!segment.in_srs || added.has(segment.base || '');
	}

	function openWord(event: MouseEvent, segment: WordSegment) {
		// A drag that selected text isn't a click on the word.
		if (!getActionsWindow().getSelection()?.isCollapsed) return;
		event.stopPropagation();
		const view = getActionsWindow();
		const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
		const left = Math.min(Math.max(8, rect.left), view.innerWidth - 268);
		const below = rect.bottom + 6 + 170 <= view.innerHeight;
		const position = below ? `top: ${Math.round(rect.bottom + 6)}px;` : `bottom: ${Math.round(view.innerHeight - rect.top + 6)}px;`;
		const popover = { segment, style: `left: ${Math.round(left)}px; ${position}`, looking: true };
		wordPopover = popover;
		lookupWord(segment.base || segment.text).then(
			(entry) => {
				if (wordPopover === popover) wordPopover = { ...popover, entry, looking: false };
			},
			(error: Error) => {
				if (wordPopover === popover) wordPopover = { ...popover, lookupError: error.message, looking: false };
			},
		);
		getActionsDocument().addEventListener('click', closeWordPopover, false);
		getActionsDocument().addEventListener('keydown', wordPopoverKeyHandler, false);
		getActionsDocument().addEventListener('scroll', closeWordPopover, true);
	}

	function closeWordPopover() {
		wordPopover = null;
		getActionsDocument().removeEventListener('click', closeWordPopover, false);
		getActionsDocument().removeEventListener('keydown', wordPopoverKeyHandler, false);
		getActionsDocument().removeEventListener('scroll', closeWordPopover, true);
	}

	function wordPopoverKeyHandler(event: KeyboardEvent) {
		if (event.key === 'Escape') closeWordPopover();
	}

	function addWordFromPopover(segment: WordSegment) {
		closeWordPopover();
		void addSelectionToJapaneseSrs(segment.base);
	}
	$: actionsMenuOpen = openMenu === 'actions';
	$: isAudioLine = audioLineId === line.id;
	$: isAudioPending = audioPendingLineId === line.id;
	$: audioButtonTitle = isAudioPending ? 'Preparing audio...' : isAudioLine && audioIsPlaying ? 'Stop audio' : 'Play audio';
	$: isActiveGSMLine = line.gsmStatus === 'active' || (!line.gsmStatus && $lineIDs$?.includes(line.id));
	$: isTimedOutGSMLine = line.gsmStatus === 'timed_out' || (!line.gsmStatus && $timedOutIDs$.includes(line.id));
	$: clipSizeLabel = clipSizeBytes ? formatMegabytes(clipSizeBytes) : '';
	$: canAskAI = !!line.id && (isActiveGSMLine || isTimedOutGSMLine || isClipSaved || line.gsmStatus === 'external');

	$: isVerticalDisplay = !pipWindow && $displayVertical$;
	$: if (line.text !== previousLineText) {
		previousLineText = line.text;
		void followUpdatedLineText();
	}
	$: if (
		componentMounted &&
		line.recordState === 'frozen' &&
		!line.sessionBackfill &&
		$autoTranslateLines$ &&
		Number(line.revision ?? 0) > autoTranslationRevision
	) {
		autoTranslationRevision = Number(line.revision ?? 0);
		handleAction(line.id, 'TL', $blurAutoTranslatedLines$, true);
	}

	onMount(() => {
		componentMounted = true;
		void revealLineText();
	});

	onDestroy(() => {
		componentMounted = false;
		document.removeEventListener('click', clickOutsideHandler, false);
		removeActionsMenuListeners();
		closeWordPopover();
		dispatch('edit', { inEdit: false });
	});

	async function revealLineText() {
		// Svelte 5 builds each line off-DOM before inserting its wrapper. Reveal the
		// text after attachment so extension MutationObservers receive a connected
		// child-list mutation, matching the insertion behavior of the legacy build.
		await tick();
		if (!componentMounted) {
			return;
		}
		lineTextMounted = true;
		await tick();
		if (!componentMounted || !isLast) {
			return;
		}

		// Keep the reader's position unless continuous following is enabled.
		if (shouldAutoScroll($alwaysScrollToNewest$, getAutoScrollStick(!!pipWindow))) {
			updateScroll(
				pipWindow || window,
				paragraph.parentElement?.parentElement ?? null,
				$reverseLineOrder$,
				isVerticalDisplay,
				$enableLineAnimation$ ? 'smooth' : 'auto',
			);
		}
		if (isActiveGSMLine && !line.recordState && !line.sessionBackfill && $autoTranslateLines$) {
			handleAction(line.id, 'TL', $blurAutoTranslatedLines$);
		}
	}

	async function followUpdatedLineText() {
		if (!lineTextMounted || !isLast || !paragraph) {
			return;
		}
		// Revisions reuse this component, so onMount cannot follow growing speech
		// transcripts. Capture the reader's position before the keyed text rerenders.
		const view = pipWindow || window;
		const container = paragraph.parentElement?.parentElement ?? null;
		if (
			!shouldAutoScroll(
				$alwaysScrollToNewest$,
				isScrolledToEnd(view, container, $reverseLineOrder$, isVerticalDisplay),
			)
		) {
			return;
		}
		await tick();
		if (!componentMounted || !isLast) {
			return;
		}
		updateScroll(view, container, $reverseLineOrder$, isVerticalDisplay, $enableLineAnimation$ ? 'smooth' : 'auto');
	}

	function getActionsWindow() {
		return pipWindow || window;
	}

	function getActionsDocument() {
		return getActionsWindow().document;
	}

	function removeActionsMenuListeners() {
		getActionsDocument().removeEventListener('click', actionsMenuClickOutsideHandler, false);
		getActionsDocument().removeEventListener('keydown', actionsMenuKeyHandler, false);
		getActionsWindow().removeEventListener('resize', closeActionsMenu, false);
		getActionsDocument().removeEventListener('scroll', closeActionsMenu, true);
	}

	function closeActionsMenu() {
		openMenu = null;
		actionsMenuStyle = 'visibility: hidden;';
		removeActionsMenuListeners();
	}

	function positionActionsMenu() {
		if (!menuAnchor || !actionsMenuPopover) {
			return;
		}

		const view = getActionsWindow();
		const buttonRect = menuAnchor.getBoundingClientRect();
		const menuRect = actionsMenuPopover.getBoundingClientRect();
		const viewportGap = 8;
		const menuGap = 5;
		const maxLeft = Math.max(viewportGap, view.innerWidth - menuRect.width - viewportGap);
		const left = Math.min(maxLeft, Math.max(viewportGap, buttonRect.right - menuRect.width));
		const fitsBelow = buttonRect.bottom + menuGap + menuRect.height <= view.innerHeight - viewportGap;
		const top = fitsBelow
			? buttonRect.bottom + menuGap
			: Math.max(viewportGap, buttonRect.top - menuGap - menuRect.height);

		actionsMenuStyle = `left: ${Math.round(left)}px; top: ${Math.round(top)}px; visibility: visible;`;
	}

	function toggleMenu(event: MouseEvent, menu: 'actions' | 'clip') {
		event.stopPropagation();
		const wasOpen = openMenu === menu;
		closeActionsMenu();
		if (wasOpen) {
			return;
		}

		openMenu = menu;
		menuAnchor = event.currentTarget as HTMLElement;
		tick().then(() => {
			positionActionsMenu();
			getActionsDocument().addEventListener('click', actionsMenuClickOutsideHandler, false);
			getActionsDocument().addEventListener('keydown', actionsMenuKeyHandler, false);
			getActionsWindow().addEventListener('resize', closeActionsMenu, false);
			getActionsDocument().addEventListener('scroll', closeActionsMenu, true);
		});
	}

	function actionsMenuClickOutsideHandler(event: MouseEvent) {
		const target = event.target as Node;
		if (!actionsMenuPopover?.contains(target) && !menuAnchor?.contains(target)) {
			closeActionsMenu();
		}
	}

	function actionsMenuKeyHandler(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			closeActionsMenu();
		}
	}

	function handleDblClick(event: MouseEvent) {
		if (pipWindow) {
			return;
		}

		window.getSelection()?.removeAllRanges();

		if (event.ctrlKey || event.metaKey) {
			if (isSelected) {
				isSelected = false;
				dispatch('deselected', line.id);
			} else {
				isSelected = true;
				dispatch('selected', line.id);
			}
		} else {
			originalText = paragraph.innerText;
			isEditable = true;

			dispatch('edit', { inEdit: true });

			document.addEventListener('click', clickOutsideHandler, false);

			tick().then(() => {
				paragraph.focus();
			});
		}
	}

	function clickOutsideHandler(event: MouseEvent) {
		const target = event.target as Node;

		if (!paragraph.contains(target)) {
			isEditable = false;
			document.removeEventListener('click', clickOutsideHandler, false);

			dispatch('edit', {
				inEdit: false,
				data: { originalText, newText: paragraph.innerText, lineIndex: index, line },
			});
		}
	}

	async function toggleCheckbox(id: string) {
		try {
			const res = await fetch(getGSMEndpoint('/update_checkbox'), {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ id }),
			});
			if (!res.ok) {
				throw new Error(`HTTP error! Status: ${res.status}`);
			}
		} catch (error) {
			console.error('Error updating checkbox:', error);
		}
	}

	function handleAudioToggle() {
		closeActionsMenu();
		dispatch('audioToggle', { lineId: line.id, text: line.text });
	}

	function handleVideoTrim() {
		closeActionsMenu();
		dispatch('videoTrim', { lineId: line.id, text: line.text });
	}

	function handleSaveClip() {
		closeActionsMenu();
		dispatch('saveClip', { lineId: line.id });
	}

	function handleOpenClipFolder() {
		closeActionsMenu();
		dispatch('openClipFolder', { lineId: line.id });
	}

	function handleDeleteClip() {
		closeActionsMenu();
		if (getActionsWindow().confirm('Move this clip to the trash? You can restore it from there.')) {
			dispatch('deleteClip', { lineId: line.id });
		}
	}

	function openAIHelp() {
		closeActionsMenu();
		aiHelpOpen = true;
	}

	function closeAIHelp() {
		aiHelpOpen = false;
		tick().then(() => actionsMenuButton?.focus());
	}

	async function handleDeleteFromStats() {
		if (!getActionsWindow().confirm('Delete this line from stats?')) {
			return;
		}

		closeActionsMenu();
		try {
			const response = await fetch(getGSMEndpoint('/api/delete-sentence-lines'), {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ line_ids: [line.id] }),
			});
			if (!response.ok) {
				throw new Error(`HTTP error! Status: ${response.status}`);
			}
		} catch (error) {
			console.error(`Error deleting line ${line.id} from stats:`, error);
		}
	}

	function selectedWordInLine() {
		const selection = getActionsWindow().getSelection();
		if (!selection || selection.isCollapsed || !paragraph) {
			return '';
		}
		if (!paragraph.contains(selection.anchorNode) || !paragraph.contains(selection.focusNode)) {
			return '';
		}
		return selection.toString().trim();
	}

	function showSrsMessage(message: string, isError: boolean) {
		clearTimeout(srsMessageTimer);
		srsMessage = message;
		srsError = isError;
		if (!isError) {
			srsMessageTimer = setTimeout(() => (srsMessage = ''), 4000);
		}
	}

	// Runs on mousedown, before the click clears the reader's selection.
	function captureSrsWord() {
		srsWord = selectedWordInLine();
	}

	export async function addSelectionToJapaneseSrs(word = selectedWordInLine()) {
		if (srsBusy) {
			return;
		}
		if (!word) {
			showSrsMessage('Select a word in this line first, then add it to Japanese SRS.', true);
			return;
		}
		srsBusy = true;
		showSrsMessage(`Adding ${word}…`, false);
		try {
			const result = await addToJapaneseSrs(line.id, line.text, word);
			rememberAddedWords(word, result.card.kanji, result.card.reading);
			showSrsMessage(describeAddedCard(result), false);
		} catch (error) {
			showSrsMessage((error as Error).message || 'Could not add the card. Check that GSM is running.', true);
		} finally {
			srsBusy = false;
		}
	}

	function handleAction(id: string, action: string, blurTranslate: boolean = false, automatic: boolean = false) {
		closeActionsMenu();
		if (action === 'TL') aiError = '';
		const endpoints: Record<string, string> = {
			TL: '/translate-line',
			Screenshot: '/get-screenshot',
		};
		const endpoint = endpoints[action];
		if (!endpoint) {
			return;
		}
		fetch(getGSMEndpoint(endpoint), {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ id, text: line.text, automatic }),
		})
			.then(async (response) => {
				if (!response.ok) {
					const data = await response.json().catch(() => ({}));
					throw new Error(data.error || `Request failed (HTTP ${response.status}).`);
				}
				return response.json();
			})
			.then((data) => {
				if (action === 'TL') {
					// Capture before render so a delayed translation respects the selected scroll behavior.
					const shouldFollowTranslation =
						isLast &&
						shouldAutoScroll(
							$alwaysScrollToNewest$,
							isScrolledToEnd(
								pipWindow || window,
								paragraph.parentElement?.parentElement ?? null,
								$reverseLineOrder$,
								isVerticalDisplay,
							),
						);

					line.translation = data['TL'];
					if (blurTranslate) {
						line.blurTranslation = true;
					} else {
						line.blurTranslation = false;
					}
					if ($unblurTLTimer$ > 0 && line.blurTranslation) {
						setTimeout(() => {
							line.blurTranslation = false;
						}, $unblurTLTimer$ * 1000);
					}

					if (!line.text.endsWith('\n')) {
						line.text += '\n';
					}
					if (line.index !== undefined) {
						$lineData$[line.index] = line;
					}
					if (shouldFollowTranslation) {
						tick().then(() => {
							const behavior = $enableLineAnimation$ ? 'smooth' : 'auto';
							paragraph?.scrollIntoView({
								behavior,
								block: $reverseLineOrder$ ? 'start' : 'end',
								inline: isVerticalDisplay ? 'end' : 'nearest',
							});
							// Scroll a bit more down
							(pipWindow || window).scrollBy(0, 50);
						});
					}
				}
			})
			.catch((error) => {
				if (action === 'TL') aiError = error.message || 'Translation failed. Check that GSM is running and retry.';
				console.error(`Error performing ${action} action for event ID: ${id}`, error);
			});
	}
</script>

{#key line.text}
	<div class="textline2">
		{#if $showGSMCheckboxes$}
			<input
				type="checkbox"
				class="multi-line-checkbox"
				class:invisible={!isActiveGSMLine}
				id="multi-line-checkbox-{line.id}"
				aria-label={line.id}
				on:change={() => toggleCheckbox(line.id)}
			/>
		{/if}
		{#if lineTextMounted}
			<p
				class="my-2 cursor-pointer border-2"
				class:py-4={!isVerticalDisplay}
				class:px-2={!isVerticalDisplay}
				class:py-2={isVerticalDisplay}
				class:px-4={isVerticalDisplay}
				class:border-transparent={!isSelected}
				class:cursor-text={isEditable}
				class:border-primary={isSelected}
				class:border-accent-focus={isEditable}
				class:whitespace-pre-wrap={$preserveWhitespace$}
				contenteditable={isEditable}
				on:dblclick={handleDblClick}
				on:keyup={dummyFn}
				bind:this={paragraph}
				in:fly={{ x: isVerticalDisplay ? 100 : -100, duration: $enableLineAnimation$ ? 250 : 0 }}
			>
				{#if segments && !isEditable}{#each segments as segment}{#if segment.base}<span class="srs-word" class:in-srs={isInSrs(segment, $addedWords$)} role="button" tabindex="-1" title={isInSrs(segment, $addedWords$) ? `${segment.base} · in your SRS` : segment.base} on:click={(event) => openWord(event, segment)} on:keyup={dummyFn}>{segment.text}</span>{:else}{segment.text}{/if}{/each}{:else}{line.text}{/if}
				{#if line.translation}
					<span
						class:blur-translation={line.blurTranslation}
						style="color: #888; padding-bottom: 16px; padding-top: 16px; width: 100%; {line.blurTranslation
							? 'filter: blur(8px); transition: filter 0.2s;'
							: ''}"
						on:mouseenter={line.blurTranslation
							? function (event: MouseEvent) {
									const target = event.currentTarget as HTMLElement;
									target.style.filter = 'blur(0px)';
									target.style.transition = 'filter 0.3s';
								}
							: undefined}
						on:mouseleave={line.blurTranslation
							? function (event: MouseEvent) {
									(event.currentTarget as HTMLElement).style.filter = 'blur(8px)';
								}
							: undefined}
					>
						<i>{line.translation}</i>
					</span>
				{/if}
			</p>
		{/if}
		<div class="line-actions-container" class:hidden={$settingsOpen$}>
			{#if line.excludedFromStats}
				<div
					class="line-badge unselectable"
					title="This line was relayed while GSM text intake was paused, so GSM did not count it toward stats or trigger overlay processing."
					tabindex="-1"
				>
					Not in GSM stats
				</div>
			{/if}
			{#if isActiveGSMLine || isClipSaved}
				<div class="textline-buttons unselectable">
					{#if $showScreenshotButton$}
						<button
							class="hide-on-mobile action-button"
							on:click={() => handleAction(line.id, 'Screenshot')}
							title="Screenshot"
							tabindex="-1"
						>
							&#x1F4F7;
						</button>
					{/if}
					{#if $showTrimVideoButton$}
						<button
							class="hide-on-mobile action-button"
							on:click={handleVideoTrim}
							title="Save cropped replay"
							tabindex="-1"
						>
							🎬
						</button>
					{/if}
					{#if isClipSaved}
						<button
							class="action-button clip-status"
							class:menu-open={openMenu === 'clip'}
							on:click={(event) => toggleMenu(event, 'clip')}
							title="Clip saved for later"
							aria-label="Clip saved for later"
							aria-expanded={openMenu === 'clip'}
							tabindex="-1"
						>
							<Icon path={mdiContentSaveCheck} width="16px" height="16px" />
						</button>
					{:else if $showSaveClipButton$}
						<!-- Kept visible on small screens: saving from a phone or tablet is the main use. -->
						<button
							class="action-button"
							on:click={handleSaveClip}
							title={isSavingClip ? 'Saving…' : 'Save clip for later'}
							aria-label="Save clip for later"
							tabindex="-1"
							disabled={isSavingClip}
						>
							<Icon path={mdiContentSave} width="16px" height="16px" />
						</button>
					{/if}
					{#if $showAudioButton$}
						<button
							class="hide-on-mobile action-button"
							class:audio-active={isAudioLine && audioIsPlaying}
							on:click={handleAudioToggle}
							title={audioButtonTitle}
							tabindex="-1"
							disabled={isAudioPending}
						>
							<Icon path={isAudioLine && audioIsPlaying ? mdiStop : mdiPlay} width="16px" height="16px" />
						</button>
					{/if}
					{#if $showTranslateButton$}
						<button
							class="action-button"
							on:click={() => handleAction(line.id, 'TL')}
							title="Translate"
							tabindex="-1"
						>
							🌐
						</button>
					{/if}
				</div>
			{:else if isTimedOutGSMLine}
				<div
					class="line-indicator unselectable"
					title="Line is outside replay buffer"
					tabindex="-1"
					style="color: #666;"
				>
					<Icon path={mdiClockOutline} width="32px" height="32px" />
				</div>
				{#if $showTranslateButton$}
					<button
						class="action-button"
						on:click={() => handleAction(line.id, 'TL')}
						title="Translate"
						style="margin-left: 5px;"
						tabindex="-1"
					>
						🌐
					</button>
				{/if}
			{:else if line.gsmStatus === 'external'}
				{#if $showTranslateButton$}
					<button
						class="action-button"
						on:click={() => handleAction(line.id, 'TL')}
						title="Translate"
						tabindex="-1"
					>
						🌐
					</button>
				{/if}
			{:else}
				<!-- Show different icon for lines that are from before GSM was started. -->
				<div
					class="line-indicator unselectable"
					title="Line is from before GSM was started"
					tabindex="-1"
					style="color: #666;"
				>
					<Icon path={mdiHistory} width="32px" height="32px" />
				</div>
			{/if}
			{#if $japaneseSrsEnabled$ && line.id}
				<button
					class="action-button"
					on:mousedown={captureSrsWord}
					on:click={() => addSelectionToJapaneseSrs(srsWord)}
					title="Add the selected word to Japanese SRS (Alt+J)"
					aria-label="Add the selected word to Japanese SRS"
					tabindex="-1"
					disabled={srsBusy}
				>
					<Icon path={mdiCardPlusOutline} width="16px" height="16px" />
				</button>
			{/if}
			{#if canAskAI}
				<div class="actions-menu">
					<button
						class="action-button menu-toggle"
						class:menu-open={actionsMenuOpen}
						on:click={(event) => toggleMenu(event, 'actions')}
						title="More line actions"
						aria-label="More line actions"
						aria-expanded={actionsMenuOpen}
						bind:this={actionsMenuButton}
					>
						<Icon path={mdiMenu} width="16px" height="16px" />
					</button>
				</div>
			{/if}
			{#if openMenu}
				<div
					class="actions-menu-popover"
					class:clip-menu={openMenu === 'clip'}
					style={actionsMenuStyle}
					bind:this={actionsMenuPopover}
				>
					{#if openMenu === 'clip'}
						<div class="clip-menu-info" title="Disk space used by this clip">
							<span aria-hidden="true">💾</span>
							<span>Clip{clipSizeLabel ? ` · ${clipSizeLabel}` : ''}</span>
						</div>
						<button on:click={handleOpenClipFolder} title="Open clip folder">
							<span aria-hidden="true">📂</span>
							<span>Open clip folder</span>
						</button>
						<button on:click={handleDeleteClip} title="Delete clip">
							<span aria-hidden="true">🗑️</span>
							<span>Delete clip</span>
						</button>
					{:else}
						<button on:click={openAIHelp}>
							<Icon path={mdiCreationOutline} width="16px" height="16px" />
							<span>Ask AI</span>
						</button>
						{#if isActiveGSMLine || isClipSaved}
							<button on:click={() => handleAction(line.id, 'Screenshot')}>
								<span aria-hidden="true">📷</span>
								<span>Screenshot</span>
							</button>
							<button on:click={handleVideoTrim}>
								<span aria-hidden="true">🎬</span>
								<span>Save cropped replay</span>
							</button>
							{#if !isClipSaved}
								<button
									on:click={handleSaveClip}
									disabled={isSavingClip}
									title="Keep a replay clip of this line so a card can be made later"
								>
									<span aria-hidden="true">💾</span>
									<span>{isSavingClip ? 'Saving…' : 'Save clip for later'}</span>
								</button>
							{/if}
							<button on:click={handleAudioToggle} disabled={isAudioPending}>
								<Icon
									path={isAudioLine && audioIsPlaying ? mdiStop : mdiPlay}
									width="16px"
									height="16px"
								/>
								<span>{audioButtonTitle}</span>
							</button>
						{/if}
						<button on:click={() => handleAction(line.id, 'TL')} title="Translate">
							<span aria-hidden="true">🌐</span>
							<span>Translate</span>
						</button>
						{#if isActiveGSMLine}
							<button on:click={handleDeleteFromStats}>
								<span aria-hidden="true">🗑️</span>
								<span>Delete from stats</span>
							</button>
						{/if}
					{/if}
				</div>
			{/if}
		</div>
	</div>
{/key}
{#if wordPopover}
	<div class="srs-word-popover" style={wordPopover.style} on:click|stopPropagation={dummyFn} on:keyup={dummyFn} role="dialog" tabindex="-1">
		<div class="srs-word-popover-word">
			{wordPopover.entry?.kanji || wordPopover.segment.base}
			{#if wordPopover.entry?.reading && wordPopover.entry.reading !== wordPopover.entry.kanji}
				<span class="srs-word-popover-reading">{wordPopover.entry.reading}</span>
			{/if}
		</div>
		{#if wordPopover.segment.base !== wordPopover.segment.text}
			<div class="srs-word-popover-note">as 「{wordPopover.segment.text}」 in this line</div>
		{/if}
		{#if wordPopover.looking}
			<div class="srs-word-popover-note">Looking up…</div>
		{:else if wordPopover.entry}
			<div class="srs-word-popover-meaning">{wordPopover.entry.meaning || 'No definition found.'}</div>
			{#if wordPopover.entry.parts_of_speech || wordPopover.entry.jlpt}
				<div class="srs-word-popover-note">
					{[wordPopover.entry.parts_of_speech, wordPopover.entry.jlpt?.toUpperCase().replace('JLPT-', 'JLPT ')]
						.filter(Boolean)
						.join(' · ')}
				</div>
			{/if}
		{:else if wordPopover.lookupError}
			<div class="srs-word-popover-note">Couldn't look it up: {wordPopover.lookupError}</div>
		{:else}
			<div class="srs-word-popover-note">Not found in the dictionary.</div>
		{/if}
		{#if isInSrs(wordPopover.segment, $addedWords$)}
			<div class="srs-word-popover-note in-srs">✓ Already in your SRS</div>
		{/if}
		<button disabled={srsBusy} on:click={() => wordPopover && addWordFromPopover(wordPopover.segment)}>
			{isInSrs(wordPopover.segment, $addedWords$) ? 'Add again' : 'Add to Japanese SRS'}
		</button>
	</div>
{/if}
{#if srsMessage}
	<p role={srsError ? 'alert' : 'status'} class="mx-4 text-sm" class:text-error={srsError}>{srsMessage}</p>
{/if}
{#if aiError}
	<p role="alert" class="mx-4 text-sm">{aiError} <a class="underline" href="https://docs.gamesentenceminer.com/docs/features/ai-features" target="_blank" rel="noreferrer">AI setup guide</a></p>
{/if}
{#if canAskAI && aiHelpOpen && !$settingsOpen$}
	{#key `${line.id}:${line.text.trim()}`}
		<AIHelp id={line.id} text={line.text} on:close={closeAIHelp} />
	{/key}
{/if}
{@html newLineCharacter}
{#if $milestoneLines$.has(line.id)}
	<div
		class="flex justify-center text-xs my-2 py-2 border-primary border-dashed milestone"
		class:border-x-2={$displayVertical$}
		class:border-y-2={!$displayVertical$}
		class:py-4={!isVerticalDisplay}
		class:px-2={!isVerticalDisplay}
		class:py-2={isVerticalDisplay}
		class:px-4={isVerticalDisplay}
	>
		<div class="flex items-center">
			<Icon class={$displayVertical$ ? '' : 'mr-2'} path={mdiTrophy}></Icon>
			<span class:mt-2={$displayVertical$}>{$milestoneLines$.get(line.id)}</span>
		</div>
	</div>
{/if}

<style>
	.srs-word {
		cursor: pointer;
		border-bottom: 1px dotted rgb(160 160 160 / 70%);
		border-radius: 2px;
	}

	.srs-word:hover {
		background: rgb(96 150 255 / 22%);
	}

	.srs-word.in-srs {
		border-bottom: 1px solid rgb(78 157 108 / 85%);
	}

	.srs-word-popover {
		position: fixed;
		z-index: 70;
		width: 260px;
		padding: 8px;
		border: 1px solid #555;
		border-radius: 4px;
		background: #222;
		box-shadow: 0 4px 14px rgb(0 0 0 / 35%);
		color: #fff;
		font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
		font-size: 12px;
		line-height: 1.3;
	}

	.srs-word-popover-word {
		font-size: 20px;
		font-weight: 700;
	}

	.srs-word-popover-reading {
		margin-left: 6px;
		color: #9cc8ef;
		font-size: 14px;
		font-weight: 600;
	}

	.srs-word-popover-meaning {
		margin-top: 6px;
		color: #eee;
		font-size: 13px;
		display: -webkit-box;
		-webkit-line-clamp: 4;
		line-clamp: 4;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.srs-word-popover-note {
		margin-top: 2px;
		color: #aaa;
	}

	.srs-word-popover-note.in-srs {
		color: #7ccf98;
	}

	.srs-word-popover button {
		width: 100%;
		margin-top: 8px;
		padding: 6px 8px;
		border: 0;
		border-radius: 3px;
		background: #3f7fb0;
		color: #fff;
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}

	.srs-word-popover button:disabled {
		opacity: 0.5;
		cursor: default;
	}

	p:focus-visible {
		outline: none;
	}

	.multi-line-checkbox {
		transform: scale(1.5);
		margin-right: 10px;
		background-color: #00ffff !important; /* Cyan/Electric Blue */
		border: 4px solid #00ffff; /* Keep the border the same color */
	}

	.multi-line-checkbox.invisible {
		visibility: hidden;
	}

	.action-button {
		background-color: #333;
		color: #fff;
		border: 1px solid #555;
		padding: 6px 10px;
		font-size: 10px;
		border-radius: 4px;
		cursor: pointer;
		transition: background-color 0.2s ease;
		user-select: none;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-height: 28px;
		min-width: 28px;
	}

	.action-button:hover {
		background-color: #444;
		cursor: pointer;
	}

	.action-button.audio-active {
		background-color: #1f5f4f;
		border-color: #2f8a73;
	}

	.action-button.menu-open {
		background-color: #444;
		border-color: #777;
	}

	.action-button:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.textline-buttons {
		margin-left: auto; /* Align buttons to the right */
		display: flex;
		gap: 10px;
	}

	.actions-menu {
		position: relative;
		display: inline-flex;
	}

	.actions-menu-popover {
		position: fixed;
		z-index: 70;
		display: flex;
		width: min(172px, calc(100vw - 16px));
		flex-direction: column;
		overflow: hidden;
		border: 1px solid #555;
		border-radius: 4px;
		background: #222;
		box-shadow: 0 4px 14px rgb(0 0 0 / 35%);
		font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
		font-size: 12px;
		font-weight: 400;
		line-height: 1.2;
		letter-spacing: normal;
		writing-mode: horizontal-tb;
	}

	.actions-menu-popover button {
		display: flex;
		align-items: center;
		gap: 7px;
		min-height: 30px;
		border: 0;
		border-bottom: 1px solid #444;
		background: transparent;
		color: #fff;
		padding: 6px 8px;
		font: inherit;
		text-align: left;
		white-space: nowrap;
		cursor: pointer;
	}

	.actions-menu-popover button:last-child {
		border-bottom: 0;
	}

	.actions-menu-popover button:hover {
		background: #3a3a3a;
	}

	.actions-menu-popover button:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	/* Hide only buttons with .hide-on-mobile on mobile devices */
	@media (max-width: 800px) {
		.hide-on-mobile {
			display: none !important;
		}
	}

	.textline2 {
		margin: 15px 0;
		padding: 15px;
		display: flex;
		align-items: center;
		gap: 15px;
	}

	.unselectable,
	.unselectable * {
		user-select: none !important;
		-webkit-user-select: none !important;
		-moz-user-select: none !important;
		-ms-user-select: none !important;
	}

	.line-indicator {
		display: flex;
		align-items: center;
		opacity: 0.6;
		transition: opacity 0.2s ease;
		margin-left: 8px;
		/* cursor: help; */
		margin-left: auto;
		user-select: none; /* Make text unselectable */
		gap: 10px;
	}

	.line-indicator:hover {
		opacity: 1;
	}

	.clip-status {
		color: var(--color-success);
	}

	.clip-menu-info {
		display: flex;
		align-items: center;
		gap: 7px;
		padding: 7px 10px;
		opacity: 0.7;
	}

	.line-actions-container {
		margin-left: auto;
		min-width: 128px; /* Reserve minimum space for icons */
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 10px;
	}

	.line-badge {
		background: rgba(255, 193, 7, 0.15);
		border: 1px solid rgba(255, 193, 7, 0.45);
		color: #f5d76e;
		border-radius: 999px;
		padding: 4px 10px;
		font-size: 11px;
		line-height: 1.2;
		white-space: nowrap;
	}

	.hidden {
		visibility: hidden;
	}
</style>
