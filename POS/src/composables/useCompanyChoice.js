import { computed, ref } from "vue"

const COMPANIES_API = "pos_next.api.purchase_orders.get_purchase_companies"

/**
 * The company a Purchase Order, Purchase Receipt or stock opname acts for.
 *
 * A cashier has exactly one: their open shift's POS Profile company. A manager
 * with access to several companies picks one (CompanyPicker). Every option
 * carries the POS Profile that represents its company, so the existing
 * profile-based calls keep working: send `profile.value` as `pos_profile`.
 * The server re-checks the company on every write.
 *
 * @param {Function} call - the dialog's API caller
 * @param {Function} getPosProfile - returns the shift's POS Profile (or null)
 */
export function useCompanyChoice(call, getPosProfile) {
	const options = ref([])
	const company = ref(null)
	const loaded = ref(false)

	const current = computed(
		() => options.value.find((o) => o.company === company.value) || null,
	)
	const profile = computed(() => current.value?.pos_profile || getPosProfile() || null)
	const warehouse = computed(() => current.value?.warehouse || null)
	const hasChoice = computed(() => options.value.length > 1)

	async function load() {
		try {
			const res = await call(COMPANIES_API, { pos_profile: getPosProfile() || null })
			options.value = res?.companies || []
			if (!options.value.some((o) => o.company === company.value)) {
				// the shift's company when allowed, else the first option: the
				// picker stays visible, so the choice is always explicit on screen
				company.value = res?.default || options.value[0]?.company || null
			}
		} catch {
			options.value = []
		} finally {
			loaded.value = true
		}
	}

	return { options, company, profile, warehouse, hasChoice, loaded, load }
}
