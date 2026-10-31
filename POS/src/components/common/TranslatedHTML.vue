<template>
	<component :is="tag" :="$attrs" v-html="sanitized"></component>
</template>

<script setup>
import { computed } from "vue";
import DOMPurify from "dompurify";

const props = defineProps({
	tag: {
		type: String,
		required: false,
		default: "span",
	},
	inner: {
		type: String,
		required: true,
	},
});

// computed, not onMounted: the text must follow prop changes (e.g. live filter counts)
const sanitized = computed(() => DOMPurify.sanitize(props.inner));
</script>
