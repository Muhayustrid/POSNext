import { Comment, defineComponent, h } from "vue"
import { Dialog } from "frappe-ui"

// Shared dialog host for management modules. Standalone: renders the
// frappe-ui Dialog as-is. Embedded: renders only the dialog's slot content
// inside the app shell's container — the shell owns the header and close.
export default defineComponent({
	props: {
		embedded: { type: Boolean, default: false },
		show: { type: Boolean, default: false },
		options: { type: Object, default: () => ({}) },
	},
	emits: ["update:show"],
	setup(hostProps, { slots, emit: hostEmit }) {
		return () => {
			if (!hostProps.embedded) {
				return h(
					Dialog,
					{
						modelValue: hostProps.show,
						"onUpdate:modelValue": (v) => hostEmit("update:show", v),
						options: hostProps.options,
					},
					{
						"body-title": slots["body-title"],
						"body-content": slots["body-content"],
						actions: slots.actions,
					},
				)
			}
			// Mirror the frappe Dialog body padding so content
			// written for the dialog renders identically and the
			// form/list scrolls on its own; actions stay pinned.
			// The footer is skipped when the actions slot renders
			// nothing (e.g. the list view has no footer when embedded).
			const actions = slots.actions?.() ?? []
			const hasActions = actions.some((vnode) => vnode?.type !== Comment)
			return h("div", { class: "h-full min-h-0 flex flex-col" }, [
				h(
					"div",
					{
						class: "flex-1 min-h-0 overflow-y-auto px-4 pt-4 pb-6 sm:px-6",
					},
					[slots["body-content"]?.()],
				),
				hasActions
					? h(
							"div",
							{
								class: "shrink-0 px-4 pb-4 pt-3 sm:px-6 border-t border-gray-200",
							},
							actions,
						)
					: null,
			])
		}
	},
})
