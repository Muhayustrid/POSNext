<template>
	<Dialog
		:model-value="modelValue"
		:options="{ title, size: 'xs' }"
		@update:model-value="(value) => emit('update:modelValue', value)"
		@close="emit('cancel')"
	>
		<template #body-content>
			<p class="text-sm text-gray-700">{{ message }}</p>
		</template>
		<template #actions>
			<div class="flex w-full justify-between gap-2">
				<Button variant="subtle" @click="onCancel">{{ __("Cancel") }}</Button>
				<Button
					variant="solid"
					:theme="destructive ? 'red' : undefined"
					@click="onConfirm"
				>
					{{ confirmLabel || __("Confirm") }}
				</Button>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { Button, Dialog } from "frappe-ui"

const props = defineProps({
	modelValue: { type: Boolean, default: false },
	title: { type: String, default: "" },
	message: { type: String, default: "" },
	confirmLabel: { type: String, default: "" },
	destructive: { type: Boolean, default: false },
})

const emit = defineEmits(["update:modelValue", "confirm", "cancel"])

// Both buttons close the dialog themselves so the host only has to handle the
// semantic event, never the v-model.
function onConfirm() {
	emit("confirm")
	emit("update:modelValue", false)
}

function onCancel() {
	emit("cancel")
	emit("update:modelValue", false)
}
</script>
