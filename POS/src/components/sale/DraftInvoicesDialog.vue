<template>
	<!-- Main Dialog -->
	<PosDialogShell
		v-model="show"
		:title="__('Draft Invoices')"
		:subtitle="drafts.length ? __('{0} saved draft(s) · tap one to continue', [drafts.length]) : ''"
		icon="file-text"
	>
		<!-- Empty State -->
		<div v-if="drafts.length === 0" class="h-full flex flex-col items-center justify-center text-center py-12">
			<div class="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mb-3">
				<FeatherIcon name="file-text" class="h-8 w-8 text-gray-400" />
			</div>
			<p class="text-sm font-medium text-gray-900">{{ __("No draft invoices") }}</p>
			<p class="text-xs text-gray-500 mt-1">
				{{ __("Save invoices as drafts to continue later") }}
			</p>
		</div>

		<!-- Drafts List -->
		<div v-else class="grid grid-cols-1 lg:grid-cols-2 gap-2">
			<div
				v-for="draft in drafts"
				:key="draft.draft_id"
				role="button"
				tabindex="0"
				class="group bg-white border border-gray-200 rounded-xl p-3 hover:border-blue-400 hover:bg-blue-50/30 transition-colors cursor-pointer flex flex-col gap-2"
				@click="$emit('load-draft', draft)"
				@keydown.enter="$emit('load-draft', draft)"
			>
				<div class="flex items-start justify-between gap-2">
					<div class="min-w-0">
						<p class="text-sm font-semibold text-gray-900 truncate">
							{{ draftCustomer(draft) || __("Walk-in Customer") }}
						</p>
						<p class="text-xs text-gray-500 flex items-center gap-1 mt-0.5">
							<FeatherIcon name="clock" class="w-3 h-3" />
							{{ formatDateTime(draft.created_at) }}
							<span class="text-gray-300">·</span>
							{{ __("{0} item(s)", [draft.items?.length || 0]) }}
						</p>
					</div>
					<span class="text-base font-bold text-gray-900 tabular-nums shrink-0">
						{{ formatCurrency(calculateTotal(draft.items)) }}
					</span>
				</div>

				<div v-if="draft.items && draft.items.length > 0" class="flex flex-wrap gap-1">
					<span
						v-for="(item, idx) in draft.items.slice(0, 3)"
						:key="idx"
						class="text-[11px] bg-gray-100 text-gray-700 px-2 py-0.5 rounded-md"
					>
						{{ item.item_name }} × {{ item.quantity || item.qty }}
					</span>
					<span v-if="draft.items.length > 3" class="text-[11px] text-gray-500 px-1 py-0.5">
						{{ __("+{0} more", [draft.items.length - 3]) }}
					</span>
				</div>

				<div class="flex items-center justify-between gap-2 pt-2 border-t border-gray-100">
					<span class="text-[11px] font-mono text-gray-400 truncate">{{ draft.draft_id }}</span>
					<div class="flex items-center gap-1 shrink-0">
						<button
							v-if="props.allowPrintDraftInvoices"
							@click.stop="handlePrintDraft(draft)"
							class="w-8 h-8 rounded-lg flex items-center justify-center text-gray-500 hover:bg-gray-100 hover:text-blue-600"
							:title="__('Print draft')"
							:aria-label="__('Print draft')"
						>
							<FeatherIcon name="printer" class="w-4 h-4" />
						</button>
						<button
							@click.stop="handleDeleteDraft(draft.draft_id)"
							class="w-8 h-8 rounded-lg flex items-center justify-center text-gray-500 hover:bg-red-50 hover:text-red-600"
							:title="__('Delete draft')"
							:aria-label="__('Delete draft')"
						>
							<FeatherIcon name="trash-2" class="w-4 h-4" />
						</button>
						<span class="ms-1 inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 h-8 text-xs font-semibold text-white">
							{{ __("Continue") }}
							<FeatherIcon name="arrow-right" class="w-3.5 h-3.5" />
						</span>
					</div>
				</div>
			</div>
		</div>

		<template #footer>
			<Button
				v-if="drafts.length > 0"
				variant="subtle"
				theme="red"
				@click="showClearAllDialog = true"
			>
				{{ __("Clear All") }}
			</Button>
		</template>
	</PosDialogShell>

	<!-- Delete Single Draft Confirmation -->
	<Dialog v-model="showDeleteDialog" :options="{ title: __('Delete Draft?'), size: 'xs' }">
		<template #body-content>
			<div class="py-3">
				<p class="text-sm text-gray-600">
					{{ __("Permanently delete this draft invoice?") }}
				</p>
			</div>
		</template>
		<template #actions>
			<div class="flex gap-2 w-full">
				<Button class="flex-1" variant="subtle" @click="showDeleteDialog = false">
					{{ __("Cancel") }}
				</Button>
				<Button class="flex-1" variant="solid" theme="red" @click="confirmDeleteDraft">
					{{ __("Delete") }}
				</Button>
			</div>
		</template>
	</Dialog>

	<!-- Clear All Drafts Confirmation -->
	<Dialog v-model="showClearAllDialog" :options="{ title: __('Clear All Drafts?'), size: 'xs' }">
		<template #body-content>
			<div class="py-3">
				<p class="text-sm text-gray-600">
					{{ __("Permanently delete all {0} draft invoices?", [drafts.length]) }}
				</p>
			</div>
		</template>
		<template #actions>
			<div class="flex gap-2 w-full">
				<Button class="flex-1" variant="subtle" @click="showClearAllDialog = false">
					{{ __("Cancel") }}
				</Button>
				<Button class="flex-1" variant="solid" theme="red" @click="confirmClearAll">
					{{ __("Clear All") }}
				</Button>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import {
	DEFAULT_CURRENCY,
	DEFAULT_LOCALE,
	formatCurrency as formatCurrencyUtil,
	roundCurrency,
} from "@/utils/currency";
import { getActiveUserId } from "@/data/user";
import { clearAllDrafts, deleteDraft, getAllDrafts } from "@/utils/draftManager";
import { printInvoiceCustom } from "@/utils/printInvoice";
import { useToast } from "@/composables/useToast";
import { usePOSShiftStore } from "@/stores/posShift";
import PosDialogShell from "@/components/common/PosDialogShell.vue";
import { Button, Dialog, FeatherIcon } from "frappe-ui";
import { onMounted, ref, watch } from "vue";

const { showSuccess, showError } = useToast();
const shiftStore = usePOSShiftStore();

const props = defineProps({
	modelValue: Boolean,
	currency: {
		type: String,
		default: DEFAULT_CURRENCY,
	},
	allowPrintDraftInvoices: {
		type: Boolean,
		default: false,
	},
});

const emit = defineEmits(["update:modelValue", "load-draft", "drafts-updated"]);

const show = ref(props.modelValue);
const drafts = ref([]);
const showDeleteDialog = ref(false);
const showClearAllDialog = ref(false);
const draftToDelete = ref(null);

watch(
	() => props.modelValue,
	(val) => {
		show.value = val;
		if (val) {
			loadDrafts();
		}
	}
);

watch(show, (val) => {
	emit("update:modelValue", val);
});

onMounted(() => {
	loadDrafts();
});

async function loadDrafts() {
	try {
		drafts.value = await getAllDrafts(getActiveUserId());
	} catch (error) {
		console.error("Error loading drafts:", error);
		showError(__("Failed to load draft invoices"));
	}
}

function handlePrintDraft(draft) {
	if (!props.allowPrintDraftInvoices) {
		return;
	}

	try {
		const invoiceData = {
			name: draft.draft_id,
			company: shiftStore.profileCompany,
			items: draft.items,
			payments: [],
			grand_total: calculateTotal(draft.items),
			posting_date: draft.created_at,
			customer_name: draft.customer?.customer_name || draft.customer?.name || draft.customer,
			status: "Draft",
			header: __("Draft"),
			footer: __("الفاتورة لم يتم تسجيلها في حسابات الجهة، وبالتالي لا يُعتد بها، ولا تتحمل الجهة أي مسؤولية عن أي أضرار قد تنتج عنها."),
		};
		printInvoiceCustom(invoiceData);
	} catch (error) {
		console.error("Error printing draft:", error);
		showError(__("Failed to print draft"));
	}
}

function handleDeleteDraft(draftId) {
	draftToDelete.value = draftId;
	showDeleteDialog.value = true;
}

async function confirmDeleteDraft() {
	try {
		await deleteDraft(draftToDelete.value);
		await loadDrafts();
		showDeleteDialog.value = false;
		draftToDelete.value = null;

		// Notify parent to update count
		emit("drafts-updated");

		showSuccess(__("Draft invoice deleted"));
	} catch (error) {
		console.error("Error deleting draft:", error);
		showError(__("Failed to delete draft"));
	}
}

async function confirmClearAll() {
	try {
		await clearAllDrafts(getActiveUserId());
		await loadDrafts();
		showClearAllDialog.value = false;

		// Notify parent to update count
		emit("drafts-updated");

		showSuccess(__("All draft invoices deleted"));
	} catch (error) {
		console.error("Error clearing drafts:", error);
		showError(__("Failed to clear drafts"));
	}
}

function draftCustomer(draft) {
	return draft.customer?.customer_name || draft.customer?.name || draft.customer || "";
}

function formatDateTime(dateStr) {
	const date = new Date(dateStr);
	return date.toLocaleString(DEFAULT_LOCALE, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	});
}

function formatCurrency(amount) {
	return formatCurrencyUtil(Number.parseFloat(amount || 0), props.currency);
}

function calculateTotal(items) {
	if (!items || items.length === 0) return 0;
	return roundCurrency(
		items.reduce((sum, item) => {
			const qty = item.quantity || item.qty || 1;
			const rate = item.rate || 0;
			return sum + roundCurrency(qty * roundCurrency(rate));
		}, 0)
	);
}
</script>
