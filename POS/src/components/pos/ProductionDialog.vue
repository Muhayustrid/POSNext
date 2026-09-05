<template>
	<Dialog v-model="show" :options="{ title: __('Production'), size: '4xl' }">
		<template #body-content>
			<!-- STEP 1: pick recipe -->
			<template v-if="!selectedRecipe">
				<input
					v-model="search"
					type="text"
					:placeholder="__('Search recipe...')"
					class="w-full mb-3 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
				/>
				<div v-if="loadingRecipes" class="py-10 text-center text-sm text-gray-500">
					{{ __("Loading recipes...") }}
				</div>
				<div v-else-if="!filteredRecipes.length" class="py-10 text-center text-sm text-gray-500">
					{{ __("No recipes available for this outlet") }}
				</div>
				<div v-else class="flex flex-col gap-2 max-h-[60vh] overflow-y-auto">
					<button
						v-for="r in filteredRecipes"
						:key="r.name"
						class="w-full text-start px-4 py-3 border border-gray-200 rounded-lg hover:border-blue-400 hover:bg-blue-50 transition-colors"
						@click="selectRecipe(r)"
					>
						<div class="flex items-center justify-between">
							<span class="font-medium text-gray-900">{{ r.recipe_name }}</span>
							<span class="text-xs text-gray-500">
								{{ __("makes {0} × {1}", [r.output_qty, r.production_item_name]) }}
							</span>
						</div>
						<div class="text-xs mt-1" :class="canMake(r) ? 'text-green-600' : 'text-red-500'">
							{{ canMake(r) ? __("Materials available") : __("Materials insufficient") }}
						</div>
					</button>
				</div>
			</template>

			<!-- STEP 2: produce -->
			<template v-else>
				<div class="flex items-center justify-between mb-3">
					<div>
						<div class="font-medium text-gray-900">{{ selectedRecipe.recipe_name }}</div>
						<div class="text-xs text-gray-500">
							{{ __("Output per run: {0} × {1}", [selectedRecipe.output_qty, selectedRecipe.production_item_name]) }}
						</div>
					</div>
					<button class="text-sm text-gray-500 hover:text-gray-800" @click="backToRecipes">
						{{ __("Change recipe") }}
					</button>
				</div>

				<label class="block text-sm font-medium text-gray-700 mb-1">
					{{ __("Quantity to produce ({0})", [selectedRecipe.production_item_name]) }}
				</label>
				<input
					v-model.number="outputQty"
					type="number"
					min="0"
					step="any"
					class="w-full px-3 py-2 mb-3 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
				/>

				<table class="w-full text-sm">
					<thead>
						<tr class="text-start text-xs text-gray-500 uppercase">
							<th class="py-1 text-start">{{ __("Material") }}</th>
							<th class="py-1 text-start">{{ __("Qty") }}</th>
							<th class="py-1 text-start">{{ __("Stock") }}</th>
							<th class="py-1 text-start" v-if="hasAnyBatch">{{ __("Batch") }}</th>
							<th></th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="(row, idx) in materialRows" :key="row.item_code" class="border-t border-gray-100">
							<td class="py-1.5 pe-2">
								<div>{{ row.item_name }}</div>
								<div v-if="rowHasInsufficientStock(row)" class="text-xs text-red-500">
									{{ __("insufficient") }}
								</div>
							</td>
							<td class="py-1.5 pe-2 w-24">
								<input
									v-model.number="row.qty"
									type="number"
									min="0"
									step="any"
									class="w-full px-2 py-1 border border-gray-300 rounded"
								/>
							</td>
							<td class="py-1.5 pe-2 text-gray-500 whitespace-nowrap">
								{{ row.available_qty }} {{ row.stock_uom }}
							</td>
							<td v-if="hasAnyBatch" class="py-1.5 pe-2 w-48">
								<select
									v-if="row.has_batch_no"
									v-model="row.batch_no"
									class="w-full px-2 py-1 border border-gray-300 rounded"
								>
									<option v-if="!row.batches.length" value="" disabled>
										{{ __("No batch in stock") }}
									</option>
									<option v-for="b in row.batches" :key="b.batch_no" :value="b.batch_no">
										{{ b.batch_no }} ({{ b.qty }})
									</option>
								</select>
							</td>
							<td class="py-1.5 text-end">
								<button
									class="text-red-500 hover:text-red-700 text-xs"
									@click="materialRows.splice(idx, 1)"
								>
									{{ __("Remove") }}
								</button>
							</td>
						</tr>
					</tbody>
				</table>

				<!-- add material -->
				<div class="mt-2 flex gap-2">
					<input
						v-model="newItemSearch"
						type="text"
						:placeholder="__('Add material by name / code...')"
						class="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg"
						@input="debouncedSearchItems"
					/>
				</div>
				<div v-if="newItemResults.length" class="mt-1 border border-gray-200 rounded-lg max-h-40 overflow-y-auto">
					<button
						v-for="it in newItemResults"
						:key="it.item_code"
						class="w-full text-start px-3 py-2 text-sm hover:bg-gray-50"
						@click="addMaterial(it)"
					>
						{{ it.item_name || it.item_code }} <span class="text-xs text-gray-400">{{ it.item_code }}</span>
					</button>
				</div>

				<div v-if="errorMessage" class="mt-3 text-sm text-red-600">{{ errorMessage }}</div>

				<div class="mt-4 flex justify-end gap-2">
					<Button variant="subtle" @click="show = false">{{ __("Cancel") }}</Button>
					<Button variant="solid" :loading="submitting" :disabled="!canSubmit" @click="submit">
						{{ __("Process Production") }}
					</Button>
				</div>
			</template>
		</template>
	</Dialog>
</template>

<script setup>
import { Button, Dialog, createResource } from "frappe-ui"
import { computed, ref, watch } from "vue"

const props = defineProps({
	modelValue: Boolean,
	posProfile: String,
	company: String,
	currency: { type: String, default: "" },
})

const emit = defineEmits(["update:modelValue", "production-created"])

const show = ref(props.modelValue)
watch(
	() => props.modelValue,
	(v) => {
		show.value = v
		if (v) loadRecipes()
	},
)
watch(show, (v) => emit("update:modelValue", v))

const loadingRecipes = ref(false)
const recipes = ref([])
const search = ref("")
const selectedRecipe = ref(null)
const outputQty = ref(1)
const materialRows = ref([])
const submitting = ref(false)
const errorMessage = ref("")
const newItemSearch = ref("")
const newItemResults = ref([])

const filteredRecipes = computed(() => {
	if (!search.value) return recipes.value
	const term = search.value.toLowerCase()
	return recipes.value.filter(
		(r) =>
			r.recipe_name.toLowerCase().includes(term) ||
			r.production_item_name.toLowerCase().includes(term),
	)
})

const hasAnyBatch = computed(() =>
	materialRows.value.some((r) => r.has_batch_no),
)

function canMake(recipe) {
	return recipe.items.every((i) =>
		i.has_batch_no
			? i.batches.some((b) => b.qty >= i.qty)
			: i.available_qty >= i.qty,
	)
}

const recipesResource = createResource({
	url: "pos_next.api.production.get_production_recipes",
	auto: false,
	onSuccess(data) {
		recipes.value = data.recipes || []
		loadingRecipes.value = false
	},
	onError(err) {
		errorMessage.value =
			err?.messages?.join("\n") || err || "Failed to load recipes"
		loadingRecipes.value = false
	},
})

function loadRecipes() {
	if (!props.posProfile) return
	loadingRecipes.value = true
	errorMessage.value = ""
	selectedRecipe.value = null
	recipesResource.submit({ pos_profile: props.posProfile })
}

function selectRecipe(recipe) {
	selectedRecipe.value = recipe
	outputQty.value = recipe.output_qty
	scaleRows()
}

function scaleRows() {
	const r = selectedRecipe.value
	if (!r) return
	const factor = r.output_qty ? outputQty.value / r.output_qty : 0
	materialRows.value = r.items.map((i) => ({
		item_code: i.item_code,
		item_name: i.item_name,
		stock_uom: i.stock_uom,
		has_batch_no: i.has_batch_no,
		available_qty: i.available_qty,
		batches: i.batches || [],
		qty: +(i.qty * factor).toFixed(4),
		batch_no: i.batches.length ? bestBatch(i) : "",
	}))
}

function bestBatch(row) {
	// FIFO: batches already sorted by expiry date from the API
	const fit = row.batches.find((b) => b.qty >= row.qty)
	return (fit || row.batches[0])?.batch_no || ""
}

watch(outputQty, scaleRows)

function rowHasInsufficientStock(row) {
	return row.has_batch_no
		? !row.batches.some((b) => b.batch_no === row.batch_no && b.qty >= row.qty)
		: row.available_qty < row.qty
}

const canSubmit = computed(
	() =>
		!!selectedRecipe.value &&
		outputQty.value > 0 &&
		materialRows.value.length > 0 &&
		materialRows.value.every(
			(r) => r.qty > 0 && (!r.has_batch_no || !!r.batch_no),
		),
)

const createResource$ = createResource({
	url: "pos_next.api.production.create_production",
	auto: false,
	onSuccess(result) {
		submitting.value = false
		emit("production-created", result)
		show.value = false
	},
	onError(err) {
		submitting.value = false
		errorMessage.value = err?.messages?.join("\n") || err || "Production failed"
	},
})

function submit() {
	errorMessage.value = ""
	submitting.value = true
	const batches = {}
	for (const r of materialRows.value) {
		if (r.has_batch_no) batches[r.item_code] = r.batch_no
	}
	createResource$.submit({
		recipe: selectedRecipe.value.name,
		qty: outputQty.value,
		items: materialRows.value.map((r) => ({
			item_code: r.item_code,
			qty: r.qty,
		})),
		pos_profile: props.posProfile,
		batches,
	})
}

const itemsResource = createResource({
	url: "pos_next.api.items.get_items",
	auto: false,
	onSuccess(data) {
		newItemResults.value = (data || []).slice(0, 8).map((d) => ({
			item_code: d.item_code || d.name,
			item_name: d.item_name,
		}))
	},
})

let searchTimer
function debouncedSearchItems() {
	clearTimeout(searchTimer)
	searchTimer = setTimeout(() => {
		if (!newItemSearch.value || !props.posProfile) {
			newItemResults.value = []
			return
		}
		itemsResource.submit({
			pos_profile: props.posProfile,
			search_term: newItemSearch.value,
			limit: 8,
		})
	}, 300)
}

function addMaterial(it) {
	if (materialRows.value.some((r) => r.item_code === it.item_code)) return
	materialRows.value.push({
		item_code: it.item_code,
		item_name: it.item_name || it.item_code,
		stock_uom: "",
		has_batch_no: false, // plain add; batch UI only for recipe-known batch items
		available_qty: 0,
		batches: [],
		qty: 1,
		batch_no: "",
	})
	newItemSearch.value = ""
	newItemResults.value = []
}

function backToRecipes() {
	selectedRecipe.value = null
	errorMessage.value = ""
}
</script>
