<template>
	<div class="h-full flex flex-col items-center px-4 py-10 sm:px-6 text-center bg-white">
		<div class="w-full max-w-md flex flex-col gap-4">
			<p class="text-sm text-gray-700">
				{{
					__(
						"Generate a refund code for a cashier at {0}. The code is valid today only and only for this company. Generating a new code disables the previous one.",
						[company || "-"],
					)
				}}
			</p>
			<Button
				variant="solid"
				theme="blue"
				class="min-h-11"
				:loading="loading"
				:disabled="!company"
				data-test="refund-code-generate"
				@click="generate"
			>
				{{ __("Generate") }}
			</Button>

			<div
				v-if="result"
				class="rounded-lg border border-gray-200 bg-gray-50 px-4 py-6"
				data-test="refund-code-result"
			>
				<p class="text-sm text-gray-700">{{ __("Refund Code") }}</p>
				<p
					class="mt-2 font-mono text-3xl font-semibold tracking-widest text-gray-900 select-all"
					aria-live="polite"
				>
					{{ result.code }}
				</p>
				<p class="mt-2 text-sm text-gray-700">
					{{ __("Valid until {0}", [result.valid_upto]) }} · {{ result.company }}
				</p>
				<Button variant="subtle" class="mt-4 min-h-11" @click="copy">
					<template #prefix><FeatherIcon name="copy" class="w-4 h-4" /></template>
					{{ __("Copy") }}
				</Button>
			</div>

			<section class="text-left" data-test="refund-code-history">
				<h3 class="text-sm font-semibold text-gray-900">{{ __("My Refund Codes") }}</h3>
				<p v-if="!history.length" class="mt-2 text-sm text-gray-700">
					{{ historyLoading ? __("Loading...") : __("No codes generated yet") }}
				</p>
				<ul v-else class="mt-2 divide-y divide-gray-100 rounded-lg border border-gray-200">
					<li
						v-for="row in history"
						:key="row.code"
						class="flex items-center justify-between gap-3 px-3 py-2"
					>
						<div class="min-w-0">
							<p class="font-mono text-base font-semibold tracking-wider text-gray-900 select-all">
								{{ row.code }}
							</p>
							<p class="text-xs text-gray-700">
								{{ String(row.creation || "").slice(0, 16) }} · {{ __("Used {0}x", [row.used_count || 0]) }}
							</p>
						</div>
						<span
							class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium"
							:class="row.status === 'Active' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-700'"
						>
							{{ __(row.status) }}
						</span>
					</li>
				</ul>
			</section>
		</div>
	</div>
</template>

<script setup>
import { Button, FeatherIcon } from "frappe-ui"
import { call } from "@/utils/apiWrapper"
import { ref, watch } from "vue"
import { useToast } from "@/composables/useToast"

const props = defineProps({
	company: { type: String, default: null },
})

const { showError, showSuccess } = useToast()
const loading = ref(false)
const result = ref(null)
const history = ref([])
const historyLoading = ref(false)

async function loadHistory() {
	const company = props.company
	history.value = []
	if (!company) return
	historyLoading.value = true
	try {
		const rows = await call(
			"pos_next.pos_next.doctype.pos_refund_code.pos_refund_code.get_my_daily_codes",
			{ company },
		)
		// a slow answer for the previous company must not overwrite this one
		if (company === props.company) history.value = rows || []
	} catch (err) {
		showError(err?.messages?.[0] || err?.message || __("Failed to load codes"))
	} finally {
		historyLoading.value = false
	}
}

// a code shown for one company must not linger after switching to another
watch(
	() => props.company,
	() => {
		result.value = null
		loadHistory()
	},
	{ immediate: true },
)

async function generate() {
	if (loading.value || !props.company) return
	loading.value = true
	try {
		result.value = await call(
			"pos_next.pos_next.doctype.pos_refund_code.pos_refund_code.generate_daily_code",
			{ company: props.company },
		)
		loadHistory()
	} catch (err) {
		showError(err?.messages?.[0] || err?.message || __("Failed to generate code"))
	} finally {
		loading.value = false
	}
}

async function copy() {
	try {
		await navigator.clipboard.writeText(result.value.code)
		showSuccess(__("Copied"))
	} catch {
		showError(__("Copy failed, write the code down manually"))
	}
}
</script>
