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

// a code shown for one company must not linger after switching to another
watch(
	() => props.company,
	() => {
		result.value = null
	},
)

async function generate() {
	if (loading.value || !props.company) return
	loading.value = true
	try {
		result.value = await call(
			"pos_next.pos_next.doctype.pos_refund_code.pos_refund_code.generate_daily_code",
			{ company: props.company },
		)
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
