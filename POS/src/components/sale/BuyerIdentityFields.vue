<!--
  ============================================================================
  BuyerIdentityFields — buyer name input + queue number chip
  ============================================================================
  Shown only when POS Settings `enable_buyer_identity` is on (the parent gates
  rendering; this component assumes it is enabled). The buyer name is free text
  for walk-in sales (D1) — it never creates or changes a Customer. The chip
  shows the NEXT queue number for the open POS Opening Shift:
  `current_queue_number + 1`, which on offline terminals is the locally-estimated
  number later reconciled with the server-allocated one (D2).

  The name lives in the invoice state (useInvoice/posCart `buyerName`) so drafts,
  offline sync, and submission payloads all read the single source of truth.
-->
<template>
	<div class="flex items-center gap-1.5 min-w-0">
		<input
			ref="buyerNameInput"
			data-test="buyer-name-input"
			type="text"
			maxlength="60"
			:value="modelValue"
			@input="onInput"
			:placeholder="__('Buyer name')"
			class="h-8 min-w-0 flex-1 px-2 text-xs border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
			:class="{ 'border-red-400 focus:ring-red-400': isBuyerNameMissing }"
			:aria-label="__('Buyer name')"
			:aria-required="settingsStore.requireBuyerName ? 'true' : 'false'"
		/>

		<!-- Queue chip: next number for the current shift (estimate while offline) -->
		<span
			v-if="nextQueueNumber"
			data-test="queue-chip"
			class="flex-shrink-0 inline-flex items-center gap-1 px-2 h-8 rounded-lg bg-blue-50 border border-blue-200 text-[11px] font-semibold text-blue-700"
			:title="__('Next queue number for this shift')"
		>
			<svg
				class="w-3 h-3"
				fill="none"
				stroke="currentColor"
				viewBox="0 0 24 24"
				stroke-width="2"
				aria-hidden="true"
			>
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
				/>
			</svg>
			{{ __("#{0}", [nextQueueNumber]) }}
		</span>

		<p
			v-if="isBuyerNameMissing"
			data-test="buyer-name-required-hint"
			class="text-[10px] font-medium text-red-600 flex-shrink-0"
		>
			{{ __("Buyer name is required") }}
		</p>
	</div>
</template>

<script setup>
import { usePOSCartStore } from "@/stores/posCart";
import { usePOSSettingsStore } from "@/stores/posSettings";
import { usePOSShiftStore } from "@/stores/posShift";
import { getNextQueueNumber, isBuyerNameRequiredButMissing } from "@/utils/buyerIdentity";
import { computed, ref, watch } from "vue";

const props = defineProps({
	// Parent-bound buyer name. When absent, the store's buyerName is the source
	// of truth (read and written directly), keeping state out of this component.
	modelValue: {
		type: String,
		default: undefined,
	},
	// Increment to request focus on the input (used when a blocked submit names this field)
	focusSignal: {
		type: Number,
		default: 0,
	},
});

const emit = defineEmits(["update:modelValue"]);

const cartStore = usePOSCartStore();
const settingsStore = usePOSSettingsStore();
const shiftStore = usePOSShiftStore();

const buyerNameInput = ref(null);

// Effective name: bound prop, or the cart store ref when no binding is given
const currentName = computed(() =>
	props.modelValue !== undefined ? props.modelValue : cartStore.buyerName
);

function onInput(event) {
	const value = event.target.value;
	emit("update:modelValue", value);
	if (props.modelValue === undefined) {
		cartStore.buyerName = value;
	}
}

// Chip source: shift store's currentShift carries `current_queue_number`
// (highest allocated for the open shift); +1 is the next sale's number.
const nextQueueNumber = computed(() => getNextQueueNumber(shiftStore.currentShift));

const isBuyerNameMissing = computed(() =>
	isBuyerNameRequiredButMissing({
		enableBuyerIdentity: true,
		requireBuyerName: settingsStore.requireBuyerName,
		buyerName: currentName.value,
	})
);

// Spec: submitting without a required buyer name focuses the field
watch(
	() => props.focusSignal,
	(counter) => {
		if (counter > 0 && isBuyerNameMissing.value) {
			buyerNameInput.value?.focus();
		}
	}
);
</script>
