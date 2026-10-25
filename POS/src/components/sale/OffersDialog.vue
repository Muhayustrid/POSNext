<template>
	<Dialog v-model="show" :options="{ title: __('Available Offers'), size: 'lg' }">
		<template #body-content>
			<div class="flex flex-col gap-4">
				<!-- Loading State -->
				<div v-if="loading" class="py-8 text-center">
					<div
						class="animate-spin rounded-full h-10 w-10 border-b-2 border-green-500 mx-auto"
					></div>
					<p class="mt-3 text-sm text-gray-500">{{ __("Loading offers...") }}</p>
				</div>

				<!-- Empty State -->
				<div v-else-if="eligibleOffers.length === 0" class="py-12 text-center">
					<div
						class="mx-auto flex items-center justify-center h-16 w-16 rounded-full bg-gray-100"
					>
						<svg
							class="h-8 w-8 text-gray-400"
							fill="none"
							stroke="currentColor"
							viewBox="0 0 24 24"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"
							/>
						</svg>
					</div>
					<h3 class="mt-4 text-sm font-medium text-gray-900">
						{{ __("No offers available") }}
					</h3>
					<p class="mt-2 text-xs text-gray-500">
						{{ __("Add items to your cart to see eligible offers") }}
					</p>
				</div>

				<!-- Offers List -->
				<div v-else>
					<div class="pos-dialog-bound flex flex-col gap-3 max-h-[calc(100dvh-15rem)] overflow-y-auto pe-2">
						<div
							v-for="offer in eligibleOffers"
							:key="offer.name"
							:class="[
								'relative rounded-xl p-4 transition-all duration-200 border-2',
								isOfferApplied(offer)
									? 'bg-green-50 border-green-500 shadow-md'
									: 'bg-gradient-to-r from-green-50 to-emerald-50 border-green-200 hover:border-green-400 hover:shadow-lg cursor-pointer',
								{ 'opacity-50 grayscale': offer.quota_exhausted },
							]"
						>
							<!-- Applied Badge -->
							<div
								v-if="isOfferApplied(offer)"
								class="absolute top-2 end-2 inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset bg-green-50 text-green-700 ring-green-600/20 gap-1"
							>
								<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
									<path
										fill-rule="evenodd"
										d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
										clip-rule="evenodd"
									/>
								</svg>
								<span>{{ __("APPLIED") }}</span>
							</div>

							<!-- Source Badge (Pricing Rule vs Promotional Scheme) -->
							<div
								:class="[
									'absolute top-2 inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
									isOfferApplied(offer) ? 'end-24' : 'end-2',
									offer.source === 'Pricing Rule'
										? 'bg-blue-50 text-blue-700 ring-blue-600/20'
										: 'bg-purple-50 text-purple-700 ring-purple-600/20',
								]"
							>
								{{
									offer.source === "Pricing Rule"
										? __("PRICING RULE")
										: __("PROMO SCHEME")
								}}
							</div>

							<!-- Offer Header -->
							<div class="mb-3 me-28">
								<h4 class="text-base font-bold text-gray-900 text-start">
									{{ offer.title || offer.name }}
								</h4>
								<p
									v-if="offer.description"
									class="text-xs text-gray-600 mt-1 text-start"
								>
									{{ offer.description }}
								</p>
							</div>

							<!-- Discount Display -->
							<div class="flex items-center gap-3 mb-3">
								<div
									:class="[
										'text-white px-4 py-2 rounded-lg transition-all',
										isOfferApplied(offer)
											? 'bg-green-700 ring-2 ring-green-600'
											: offer.offer === 'Give Product'
											? 'bg-purple-600'
											: offer.discount_percentage
											? 'bg-orange-600'
											: 'bg-green-600',
									]"
								>
									<div class="text-lg font-bold tabular-nums">
										<span v-if="offer.discount_percentage">{{
											__("{0}% OFF", [
												Number(offer.discount_percentage).toFixed(2),
											])
										}}</span>
										<span v-else-if="offer.discount_amount">{{
											__("{0} OFF", [formatCurrency(offer.discount_amount)])
										}}</span>
										<span v-else>{{ __("Special Offer") }}</span>
									</div>
								</div>
								<div
									v-if="offer.offer === 'Give Product'"
									class="inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset bg-purple-50 text-purple-700 ring-purple-600/20"
								>
									{{ __("+ Free Item") }}
								</div>
								<span
									v-if="offer.quota_limit > 0"
									class="inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset tabular-nums"
									:class="
										offer.quota_exhausted
											? 'bg-red-50 text-red-700 ring-red-600/20'
											: 'bg-amber-50 text-amber-700 ring-amber-600/20'
									"
								>
									{{
										offer.quota_exhausted
											? __("Quota used up")
											: __("Left {0}/{1}", [offer.quota_remaining, offer.quota_limit])
									}}
								</span>
							</div>

							<!-- Offer Details -->
							<div class="grid grid-cols-2 gap-3 mb-3">
								<!-- Min Amount -->
								<div v-if="offer.min_amt" class="flex items-center gap-2">
									<svg
										class="w-4 h-4 text-gray-500"
										fill="none"
										stroke="currentColor"
										viewBox="0 0 24 24"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z"
										/>
									</svg>
									<div>
										<p class="text-xs text-gray-500">
											{{ __("Min Purchase") }}
										</p>
										<p class="text-xs font-semibold text-gray-900 tabular-nums">
											{{ formatCurrency(offer.min_amt) }}
										</p>
									</div>
								</div>

								<!-- Min Quantity -->
								<div v-if="offer.min_qty" class="flex items-center gap-2">
									<svg
										class="w-4 h-4 text-gray-500"
										fill="none"
										stroke="currentColor"
										viewBox="0 0 24 24"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"
										/>
									</svg>
									<div>
										<p class="text-xs text-gray-500">
											{{ __("Min Quantity") }}
										</p>
										<p class="text-xs font-semibold text-gray-900">
											{{ __("{0} items", [offer.min_qty]) }}
										</p>
									</div>
								</div>

								<!-- Valid Until -->
								<div v-if="offer.valid_upto" class="flex items-center gap-2">
									<svg
										class="w-4 h-4 text-gray-500"
										fill="none"
										stroke="currentColor"
										viewBox="0 0 24 24"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
										/>
									</svg>
									<div>
										<p class="text-xs text-gray-500">
											{{ __("Valid Until") }}
										</p>
										<p class="text-xs font-semibold text-gray-900">
											{{ formatDate(offer.valid_upto) }}
										</p>
									</div>
								</div>

								<!-- Offer Type -->
								<div class="flex items-center gap-2">
									<svg
										class="w-4 h-4 text-gray-500"
										fill="currentColor"
										viewBox="0 0 20 20"
									>
										<path
											fill-rule="evenodd"
											d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z"
											clip-rule="evenodd"
										/>
									</svg>
									<div>
										<p class="text-xs text-gray-500">{{ __("Type") }}</p>
										<p class="text-xs font-semibold text-gray-900">
											{{ offer.offer || __("Discount") }}
										</p>
									</div>
								</div>
							</div>

							<!-- Progress Bar for Min Amount (only shown if not eligible) -->
							<div
								v-if="
									offer.min_amt &&
									offersStore.cartSnapshot.subtotal < offer.min_amt
								"
								class="mt-3"
							>
								<div class="flex items-center justify-between text-xs mb-1">
									<span class="text-gray-600">{{
										__("Subtotal (before tax)")
									}}</span>
									<span class="text-gray-900 font-semibold tabular-nums">
										{{ formatCurrency(offersStore.cartSnapshot.subtotal) }} /
										{{ formatCurrency(offer.min_amt) }}
									</span>
								</div>
								<div class="w-full bg-gray-200 rounded-full h-2">
									<div
										class="bg-green-600 h-2 rounded-full transition-all"
										:style="{
											width: `${Math.min(
												(offersStore.cartSnapshot.subtotal /
													offer.min_amt) *
													100,
												100
											)}%`,
										}"
									></div>
								</div>
								<p class="text-xs text-orange-600 mt-1 font-medium tabular-nums">
									{{
										__("Add {0} more to unlock", [
											formatCurrency(offersStore.getUnlockAmount(offer)),
										])
									}}
								</p>
							</div>

							<!-- Offer switch: the cashier turns an offer on or off for this cart -->
							<div class="mt-3">
								<button
									type="button"
									role="switch"
									:aria-checked="isOfferApplied(offer)"
									:disabled="applyingCode !== null"
									@click="toggleOffer(offer)"
									:class="[
										'w-full min-h-[44px] sm:min-h-[40px] py-2 px-4 rounded-lg font-semibold text-sm border flex items-center justify-between gap-2 transition-colors disabled:opacity-60 disabled:cursor-wait touch-manipulation',
										isOfferApplied(offer)
											? 'bg-green-100 text-green-800 border-green-300 hover:bg-green-200'
											: 'bg-gray-50 text-gray-700 border-gray-300 hover:bg-gray-100',
									]"
								>
									<span>
										{{
											applyingCode === offer.name
												? __("Processing...")
												: isOfferApplied(offer)
													? __("Applied")
													: __("Apply")
										}}
									</span>
									<span
										:class="[
											'relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors',
											isOfferApplied(offer) ? 'bg-green-600' : 'bg-gray-300',
										]"
									>
										<span
											:class="[
												'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all',
												isOfferApplied(offer) ? 'start-[1.125rem]' : 'start-0.5',
											]"
										></span>
									</span>
								</button>
							</div>
						</div>
					</div>
				</div>
			</div>
		</template>
		<template #actions>
			<div class="flex justify-end w-full">
				<Button variant="subtle" class="min-h-10 max-sm:min-h-11 px-4 touch-manipulation" @click="show = false">
					{{ __("Close") }}
				</Button>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { usePOSOffersStore } from "@/stores/posOffers";
import {
	DEFAULT_CURRENCY,
	DEFAULT_LOCALE,
	formatCurrency as formatCurrencyUtil,
} from "@/utils/currency";
import { Button, Dialog } from "frappe-ui";
import { computed, ref, watch } from "vue";

// Use Pinia stores
const offersStore = usePOSOffersStore();

const props = defineProps({
	modelValue: Boolean,
	subtotal: {
		type: Number,
		required: true,
		note: __("Cart subtotal BEFORE tax - used for discount calculations"),
	},
	items: Array,
	posProfile: String,
	customer: String,
	company: String,
	currency: {
		type: String,
		default: DEFAULT_CURRENCY,
	},
	appliedOffers: {
		type: Array,
		default: () => [],
	},
});

const emit = defineEmits(["update:modelValue", "apply-offer", "remove-offer"]);

const show = ref(props.modelValue);
const appliedOfferCodes = computed(() => {
	return new Set((props.appliedOffers || []).map((entry) => entry?.code).filter(Boolean));
});

// Use ALL eligible offers from store (includes both auto and manual offers)
const eligibleOffers = computed(() => offersStore.allEligibleOffersSorted);

// Loading state - check if offers are being loaded
const loading = computed(() => {
	return !offersStore.hasFetched && eligibleOffers.value.length === 0;
});

watch(
	() => props.modelValue,
	(val) => {
		show.value = val;
		if (val) resetApplyingState();
		// No need to load offers - they're already in the store
	}
);

watch(show, (val) => {
	emit("update:modelValue", val);
});

function isOfferApplied(offer) {
	return appliedOfferCodes.value.has(offer?.name);
}

// Offer whose switch is being processed (null when idle)
const applyingCode = ref(null);

function toggleOffer(offer) {
	if (!offer?.name || applyingCode.value !== null) return;
	applyingCode.value = offer.name;
	emit(isOfferApplied(offer) ? "remove-offer" : "apply-offer", offer);
}

function resetApplyingState() {
	applyingCode.value = null;
}

// The applied list changing means the store finished the switch.
watch(appliedOfferCodes, resetApplyingState);

defineExpose({ resetApplyingState });

function formatCurrency(amount) {
	return formatCurrencyUtil(Number.parseFloat(amount || 0), props.currency);
}

function formatDate(dateStr) {
	if (!dateStr) return "";
	const date = new Date(dateStr);
	return date.toLocaleDateString(DEFAULT_LOCALE, {
		month: "short",
		day: "numeric",
		year: "numeric",
	});
}
</script>
