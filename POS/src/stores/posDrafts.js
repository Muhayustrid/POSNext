import {
	deleteDraft,
	getDraftsCount,
	saveDraft,
	getAllDrafts,
	updateDraft,
} from "@/utils/draftManager";
import { useToast } from "@/composables/useToast";
import { defineStore } from "pinia";
import { ref } from "vue";

export const usePOSDraftsStore = defineStore("posDrafts", () => {
	// Use custom toast
	const { showSuccess, showError, showWarning } = useToast();

	// State
	const draftsCount = ref(0);
	const drafts = ref([]);

	// Actions
	async function updateDraftsCount() {
		try {
			draftsCount.value = await getDraftsCount();
		} catch (error) {
			console.error("Error getting drafts count:", error);
		}
	}

	async function loadDrafts() {
		try {
			drafts.value = await getAllDrafts();
			draftsCount.value = drafts.value.length;
		} catch (error) {
			console.error("Error loading drafts:", error);
		}
	}

	async function saveDraftInvoice(
		invoiceItems,
		customer,
		posProfile,
		appliedOffers = [],
		draftId = null,
		buyerName = null
	) {
		if (invoiceItems.length === 0) {
			showWarning(__("Cannot save an empty cart as draft"));
			return null;
		}

		try {
			const draftData = {
				pos_profile: posProfile,
				customer: customer,
				items: invoiceItems,
				applied_offers: appliedOffers, // Save applied offers
				// Buyer name survives the hold/resume round trip (queue-buyer-
				// identity spec). The queue NUMBER is deliberately not persisted:
				// the server allocates it at submit from the shift counter, so a
				// draft must not carry a stale real number — on resume the chip
				// re-derives the next estimate from the live shift instead.
				buyer_name: buyerName?.trim() || null,
			};

			let savedDraft;
			if (draftId) {
				savedDraft = await updateDraft(draftId, draftData);
			} else {
				savedDraft = await saveDraft(draftData);
			}

			await loadDrafts(); // Refresh drafts list and count

			showSuccess(__("Invoice saved as draft successfully"));

			return savedDraft;
		} catch (error) {
			console.error("Error saving draft:", error);
			showError(__("Failed to save draft"));
			return null;
		}
	}

	async function loadDraft(draft) {
		try {
			showSuccess(__("Draft invoice loaded successfully"));

			return {
				items: draft.items || [],
				customer: draft.customer,
				applied_offers: draft.applied_offers || [], // Restore applied offers
				// Restored into the cart's buyer-name state on resume (2.10).
				buyer_name: draft.buyer_name || null,
			};
		} catch (error) {
			console.error("Error loading draft:", error);
			showError(__("Failed to load draft"));
			throw error;
		}
	}

	async function deleteDraftById(draftId) {
		try {
			await deleteDraft(draftId);
			await loadDrafts(); // Refresh drafts list and count
			showSuccess(__("Draft deleted successfully"));
		} catch (error) {
			console.error("Error deleting draft:", error);
			showError(__("Failed to delete draft"));
		}
	}

	return {
		// State
		draftsCount,
		drafts,

		// Actions
		updateDraftsCount,
		loadDrafts,
		saveDraftInvoice,
		loadDraft,
		deleteDraft: deleteDraftById,
	};
});
