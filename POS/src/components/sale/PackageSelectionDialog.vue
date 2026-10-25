<template>
	<Dialog v-model="isOpen" :options="{ title: __('Choose Package Options'), size: '4xl' }">
		<template #body-content>
			<div v-if="pkg" class="package-options py-1 flex flex-col sm:flex-row items-start gap-5">
				<!-- Option list with item rows -->
				<div class="package-options-list flex-1 min-w-0 w-full flex flex-col gap-4">
					<div
						v-for="(group, idx) in pkg.groups"
						:key="group.group_key"
						:class="idx > 0 ? 'border-t border-gray-200 pt-4' : ''"
					>
						<div class="flex items-start justify-between gap-3 mb-2.5">
							<div class="min-w-0">
								<p
									:id="`package-group-${group.group_key}`"
									class="text-sm font-semibold text-gray-900 break-words"
								>
									{{ group.label }}
								</p>
								<p v-if="group.description" class="text-xs text-gray-500 mt-0.5 break-words">
									{{ group.description }}
								</p>
								<p class="text-xs text-gray-600 mt-0.5">
									{{ groupRule(group) }}
								</p>
							</div>
							<span
								class="text-xs font-bold px-2 py-1 rounded-md whitespace-nowrap shrink-0"
								:class="groupState[group.group_key]?.satisfied
									? 'bg-green-100 text-green-700'
									: 'bg-orange-100 text-orange-700'"
							>
								{{ groupState[group.group_key]?.picked || 0 }} / {{ group.max_qty }}
							</span>
						</div>

						<div
							class="flex flex-col gap-2"
							role="group"
							:aria-labelledby="`package-group-${group.group_key}`"
						>
							<div
								v-for="option in optionsForGroup(group.group_key)"
								:key="option.option_id"
								class="flex items-center gap-3 rounded-lg border p-2.5 sm:p-3 transition-colors motion-reduce:transition-none"
								:class="picked(group.group_key, option.option_id) > 0
									? 'border-blue-500 bg-blue-50/70'
									: 'border-gray-200 bg-white hover:border-gray-300'"
							>
								<!-- Thumbnail -->
								<div
									class="w-12 h-12 rounded-lg bg-gray-100 flex items-center justify-center overflow-hidden shrink-0 border border-gray-200"
								>
									<img
										v-if="option.image"
										:src="option.image"
										:alt="option.item_name || option.item_code"
										loading="lazy"
										class="w-full h-full object-cover"
									/>
									<svg
										v-else
										class="w-6 h-6 text-gray-400"
										fill="none"
										stroke="currentColor"
										viewBox="0 0 24 24"
										aria-hidden="true"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="1.5"
											d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2 2v12a2 2 0 002 2z"
										/>
									</svg>
								</div>

								<!-- Name & price adjustment -->
								<div class="flex-1 min-w-0">
									<p class="text-sm font-medium text-gray-900 break-words leading-snug">
										{{ option.item_name || option.item_code }}
									</p>
									<p
										class="text-xs mt-0.5"
										:class="option.price_adjustment
											? 'text-blue-600 font-semibold'
											: 'text-gray-500'"
									>
										{{ priceAdjustmentLabel(option) }}
									</p>
								</div>

								<!-- Single-pick groups use a plain toggle; multi-pick groups use a stepper -->
								<Button
									v-if="isSinglePick(group)"
									class="package-pick shrink-0 px-3"
									:aria-pressed="picked(group.group_key, option.option_id) > 0"
									:label="`${__('Choose')}: ${option.item_name || option.item_code}`"
									:variant="picked(group.group_key, option.option_id) ? 'solid' : 'subtle'"
									theme="blue"
									size="sm"
									@click="togglePick(group, option)"
								>
									{{ picked(group.group_key, option.option_id) ? __("Chosen") : __("Choose") }}
								</Button>

								<div v-else class="flex items-center gap-1 shrink-0">
									<Button
										variant="subtle"
										size="sm"
										:disabled="picked(group.group_key, option.option_id) <= 0"
										:label="`${__('Decrease')}: ${option.item_name || option.item_code}`"
										class="package-step"
										@click="decrement(group, option)"
									>
										&minus;
									</Button>
									<span class="w-7 text-center tabular-nums text-sm font-semibold text-gray-900">
										{{ picked(group.group_key, option.option_id) }}
									</span>
									<Button
										variant="subtle"
										size="sm"
										:disabled="!canIncrement(group, option)"
										:label="`${__('Increase')}: ${option.item_name || option.item_code}`"
										class="package-step"
										@click="increment(group, option)"
									>
										+
									</Button>
								</div>
							</div>
						</div>
					</div>
				</div>

				<!-- Summary panel: Ringkasan Paket -->
				<div
					class="package-summary-panel w-full sm:w-72 md:w-80 shrink-0 sm:sticky sm:top-0 flex flex-col gap-3.5 rounded-xl border border-gray-200 bg-gray-50/70 p-4"
				>
					<div class="flex items-center justify-between pb-2 border-b border-gray-200">
						<h4 class="text-sm font-semibold text-gray-900">
							{{ __("Package Summary") }}
						</h4>
					</div>

					<!-- Package name + base price -->
					<div class="space-y-1">
						<p class="text-sm font-semibold text-gray-900 break-words leading-snug">
							{{ pkg.package_name }}
						</p>
						<p v-if="pkg.description" class="text-xs text-gray-500 break-words">
							{{ pkg.description }}
						</p>
						<p class="text-xs text-gray-600">
							{{ __("Base price") }}:
							<span class="font-medium text-gray-900">{{ formatCurrency(pkg.base_price) }}</span>
						</p>
					</div>

					<!-- Always-included items -->
					<div v-if="pkg.items?.length" class="rounded-lg bg-white border border-gray-200 p-2.5">
						<p class="text-xs font-semibold text-gray-700 mb-1.5">
							{{ __("Included in this package") }}
						</p>
						<ul class="flex flex-col gap-1">
							<li
								v-for="row in pkg.items"
								:key="row.item_code"
								class="flex items-center justify-between gap-2 text-xs text-gray-600"
							>
								<span class="flex-1 min-w-0 break-words">{{ row.item_name || row.item_code }}</span>
								<span class="text-gray-500 tabular-nums font-medium shrink-0">x{{ row.qty }}</span>
							</li>
						</ul>
					</div>

					<!-- Per-group selected options with qty -->
					<div v-if="pkg.groups?.length" class="flex flex-col gap-2.5">
						<div
							v-for="group in pkg.groups"
							:key="`summary-group-${group.group_key}`"
							class="text-xs"
						>
							<div class="flex items-center justify-between text-gray-500 mb-1">
								<span class="font-medium truncate">{{ group.label }}</span>
								<span
									class="text-[11px] tabular-nums font-medium"
									:class="groupState[group.group_key]?.satisfied ? 'text-green-600' : 'text-orange-600'"
								>
									{{ groupState[group.group_key]?.picked || 0 }}/{{ group.max_qty }}
								</span>
							</div>
							<ul
								v-if="selectedOptionsForGroup(group.group_key).length"
								class="flex flex-col gap-1 pl-2 border-l-2 border-blue-400"
							>
								<li
									v-for="opt in selectedOptionsForGroup(group.group_key)"
									:key="opt.option_id"
									class="flex items-center justify-between gap-2 text-gray-800"
								>
									<span class="flex-1 min-w-0 break-words">{{ opt.item_name || opt.item_code }}</span>
									<div class="flex items-center gap-1 shrink-0">
										<span
											v-if="opt.price_adjustment"
											class="text-[11px] text-blue-600 font-medium"
										>
											{{ priceAdjustmentLabel(opt) }}
										</span>
										<span class="font-semibold text-gray-900 tabular-nums">
											x{{ picked(group.group_key, opt.option_id) }}
										</span>
									</div>
								</li>
							</ul>
							<p v-else class="text-xs text-gray-400 italic pl-2 border-l-2 border-gray-200">
								{{ __("None selected") }}
							</p>
						</div>
					</div>

					<!-- Validation feedback -->
					<p
						v-if="validationError"
						role="status"
						class="text-xs text-orange-700 flex items-start gap-1.5 bg-orange-50 border border-orange-200 rounded-lg p-2.5"
					>
						<svg class="w-4 h-4 flex-shrink-0 mt-0.5 text-orange-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
							/>
						</svg>
						<span class="flex-1 min-w-0 break-words leading-snug">{{ validationError }}</span>
					</p>

					<!-- Running total -->
					<div class="flex items-center justify-between gap-2 border-t border-gray-200 pt-3 mt-auto">
						<span class="text-xs sm:text-sm font-semibold text-gray-700">{{ __("Package Total") }}</span>
						<span class="text-base sm:text-lg font-bold tabular-nums text-gray-900">
							{{ formatCurrency(runningTotal) }}
						</span>
					</div>
				</div>
			</div>
		</template>

		<template #actions>
			<div class="package-actions flex gap-2 w-full">
				<Button class="flex-1" variant="subtle" @click="cancel">
					{{ __("Cancel") }}
				</Button>
				<Button
					class="flex-1"
					variant="solid"
					theme="blue"
					:loading="confirming"
					:disabled="!localQuote.valid || confirming"
					@click="confirm"
				>
					{{ __("Add to Cart") }}
				</Button>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { usePOSPackagesStore } from "@/stores/posPackages";
import { DEFAULT_CURRENCY, formatCurrency as formatCurrencyUtil } from "@/utils/currency";
import { pickedQty, quotePackageLocally } from "@/utils/packageQuote";
import { Button, Dialog } from "frappe-ui";
import { computed, ref, watch } from "vue";

const props = defineProps({
	modelValue: Boolean,
	pkg: Object,
	posProfile: String,
	currency: {
		type: String,
		default: DEFAULT_CURRENCY,
	},
});

const emit = defineEmits(["update:modelValue", "package-selected"]);

const packagesStore = usePOSPackagesStore();

const isOpen = computed({
	get: () => props.modelValue,
	set: (value) => emit("update:modelValue", value),
});

/** group_key -> option_id -> qty */
const selections = ref({});
const confirming = ref(false);

/** Live preview. The server re-quotes on confirm and again on invoice validate. */
const localQuote = computed(() => quotePackageLocally(props.pkg, selections.value));

const validationError = computed(() => (localQuote.value.valid ? null : localQuote.value.error));

/**
 * Price shown while choosing. Mirrors the quote formula but ignores validity, so
 * an incomplete selection still shows what has been picked so far instead of 0.
 */
const runningTotal = computed(() => {
	let total = Number(props.pkg?.base_price) || 0;

	for (const [, picks] of Object.entries(selections.value)) {
		for (const [optionId, qty] of Object.entries(picks || {})) {
			const option = (props.pkg?.options || []).find((o) => o.option_id === optionId);
			if (option) total += (Number(option.price_adjustment) || 0) * (Number(qty) || 0);
		}
	}

	return total;
});

const groupState = computed(() => {
	const state = {};
	for (const group of props.pkg?.groups || []) {
		const picks = selections.value[group.group_key] || {};
		const total = pickedQty(picks);
		state[group.group_key] = {
			picked: total,
			satisfied: total >= (group.min_qty || 0) && total <= group.max_qty,
		};
	}
	return state;
});

function optionsForGroup(groupKey) {
	return (props.pkg?.options || []).filter((o) => o.group_key === groupKey);
}

function selectedOptionsForGroup(groupKey) {
	return optionsForGroup(groupKey).filter((o) => picked(groupKey, o.option_id) > 0);
}

function isSinglePick(group) {
	return group.max_qty === 1;
}

function picked(groupKey, optionId) {
	return selections.value[groupKey]?.[optionId] || 0;
}

function setPick(groupKey, optionId, qty) {
	const groupPicks = { ...(selections.value[groupKey] || {}) };
	if (qty > 0) {
		groupPicks[optionId] = qty;
	} else {
		delete groupPicks[optionId];
	}
	selections.value = { ...selections.value, [groupKey]: groupPicks };
}

function togglePick(group, option) {
	const isChosen = picked(group.group_key, option.option_id) > 0;
	// Single-pick groups hold at most one option, so choosing replaces the previous.
	selections.value = {
		...selections.value,
		[group.group_key]: isChosen ? {} : { [option.option_id]: 1 },
	};
}

function canIncrement(group, option) {
	const groupPicks = selections.value[group.group_key] || {};
	if (pickedQty(groupPicks) >= group.max_qty) return false;

	const optionMax = option.max_qty || 0;
	return !optionMax || picked(group.group_key, option.option_id) < optionMax;
}

function increment(group, option) {
	if (!canIncrement(group, option)) return;
	setPick(group.group_key, option.option_id, picked(group.group_key, option.option_id) + 1);
}

function decrement(group, option) {
	setPick(group.group_key, option.option_id, picked(group.group_key, option.option_id) - 1);
}

function groupRule(group) {
	const min = group.min_qty || 0;
	const max = group.max_qty;

	if (min === max) return __("Choose exactly {0}", [max]);
	if (min === 0) return __("Choose up to {0} (optional)", [max]);
	return __("Choose {0} to {1}", [min, max]);
}

function priceAdjustmentLabel(option) {
	const adjustment = Number(option.price_adjustment) || 0;
	if (!adjustment) return __("Included");
	const sign = adjustment > 0 ? "+" : "-";
	return `${sign}${formatCurrency(Math.abs(adjustment))}`;
}

function formatCurrency(value) {
	return formatCurrencyUtil(value || 0, props.currency);
}

/** Pre-select mandatory single-option groups so the common case is one tap. */
function resetSelections() {
	const initial = {};
	for (const group of props.pkg?.groups || []) {
		const options = optionsForGroup(group.group_key);
		if (group.min_qty === group.max_qty && group.max_qty === 1 && options.length === 1) {
			initial[group.group_key] = { [options[0].option_id]: 1 };
		} else {
			initial[group.group_key] = {};
		}
	}
	selections.value = initial;
}

watch(
	() => [props.modelValue, props.pkg?.name],
	([open]) => {
		if (open) resetSelections();
	},
	{ immediate: true }
);

function cancel() {
	isOpen.value = false;
}

async function confirm() {
	if (!localQuote.value.valid || confirming.value) return;

	confirming.value = true;
	try {
		const quote = await packagesStore.quote(props.pkg, selections.value, props.posProfile);
		if (!quote.valid) return;

		emit("package-selected", { quote, pkg: props.pkg });
		isOpen.value = false;
	} finally {
		confirming.value = false;
	}
}
</script>

<style scoped>
.package-options {
	max-height: calc(100dvh - 15rem);
	overflow-y: auto;
	overscroll-behavior: contain;
}

.package-options :deep(.package-pick),
.package-options :deep(.package-step),
.package-actions :deep(button) {
	min-height: 44px;
}

.package-options :deep(.package-step) {
	min-width: 44px;
	padding-inline: 0;
}

.package-options :deep(button:focus-visible),
.package-actions :deep(button:focus-visible) {
	outline: 2px solid currentColor;
	outline-offset: 2px;
}
</style>
