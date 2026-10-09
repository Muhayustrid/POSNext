<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: __('Production'), size: '4xl' }"
	>
		<template #body-content>
			<!-- LIST LEVEL: Recipes (one-shot + start) | In Production (two-phase) -->
			<template v-if="view !== 'detail'">
				<div class="flex items-end gap-2 mb-4 border-b border-gray-200">
					<nav class="flex gap-5 overflow-x-auto" role="tablist">
						<button
							v-for="tab in [
								{ id: 'recipes', label: __('Recipes') },
								{ id: 'active', label: __('In Production') },
								{ id: 'history', label: __('History') },
								{ id: 'overview', label: __('Overview') },
							]"
							:key="tab.id"
							type="button"
							role="tab"
							:aria-selected="view === tab.id"
							class="-mb-px pb-2.5 pt-1 border-b-2 text-sm font-medium whitespace-nowrap transition-colors"
							:class="
								view === tab.id
									? 'border-gray-900 text-gray-900'
									: 'border-transparent text-gray-500 hover:text-gray-800'
							"
							@click="switchView(tab.id)"
						>
							{{ tab.label }}
							<span
								v-if="tab.id === 'active' && productions.length"
								class="ms-1 inline-flex min-w-[1.25rem] justify-center rounded-full bg-blue-600 px-1.5 text-xs font-semibold text-white tabular-nums"
								>{{ productions.length }}</span
							>
						</button>
					</nav>
					<RefreshButton
						class="ms-auto mb-1.5"
						:loading="
							view === 'recipes'
								? loadingRecipes
								: view === 'active'
									? loadingActive
									: view === 'history'
										? loadingHistory
										: loadingDashboard
						"
						@click="
							view === 'recipes'
								? loadRecipes()
								: view === 'active'
									? loadActive()
									: view === 'history'
										? loadHistory()
										: loadDashboard()
						"
					/>
				</div>

				<!-- RECIPES: pick a recipe -->
				<template v-if="view === 'recipes'">
					<input
						v-model="search"
						type="text"
						:placeholder="__('Search recipe...')"
						class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
					/>
					<div v-if="loadingRecipes" class="py-10 text-center text-sm text-gray-500">
						{{ __("Loading recipes...") }}
					</div>
					<div
						v-else-if="!filteredRecipes.length"
						class="py-10 text-center text-sm text-gray-500"
					>
						{{ __("No recipes available for this outlet") }}
					</div>
					<div
						v-else
						class="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-[60vh] overflow-y-auto mt-3"
					>
						<button
							v-for="r in filteredRecipes"
							:key="r.name"
							class="w-full text-start px-4 py-3 border border-gray-200 rounded-lg hover:border-gray-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 transition-colors"
							@click="selectRecipe(r)"
						>
							<div class="font-semibold text-gray-900 truncate">{{ r.recipe_name }}</div>
							<div class="text-xs text-gray-500 mt-0.5 truncate">
								{{ __("makes {0} × {1}", [r.output_qty, r.production_item_name]) }}
							</div>
							<div
								class="mt-2 inline-flex items-center gap-1.5 text-xs font-medium"
								:class="canMake(r) ? 'text-green-700' : 'text-red-600'"
							>
								<span
									class="w-2 h-2 rounded-full"
									:class="canMake(r) ? 'bg-green-500' : 'bg-red-500'"
									aria-hidden="true"
								/>
								{{
									canMake(r)
										? __("Materials available")
										: __("Materials insufficient")
								}}
							</div>
						</button>
					</div>
				</template>

				<!-- IN PRODUCTION: live Work Orders of this outlet -->
				<template v-else-if="view === 'active'">
					<div v-if="loadingActive" class="py-10 text-center text-sm text-gray-500">
						{{ __("Loading active productions...") }}
					</div>
					<div
						v-else-if="!productions.length"
						class="py-10 text-center text-sm text-gray-500"
					>
						{{ __("No active productions") }}
					</div>
					<div
						v-else
						class="flex flex-col gap-2 max-h-[60vh] overflow-y-auto"
					>
						<div
							v-for="p in productions"
							:key="p.work_order"
							class="border border-gray-200 rounded-lg px-4 py-3"
						>
							<div class="flex items-start justify-between gap-2">
								<div class="min-w-0">
									<div class="font-medium text-gray-900 truncate">
										{{ p.production_item_name }}
									</div>
									<div class="text-xs text-gray-500">{{ p.recipe_name }}</div>
								</div>
								<span
									class="shrink-0 px-2 py-0.5 rounded-full text-xs font-medium"
									:class="statusClass(p.pos_status)"
								>
									{{ posLabel(p) }}
								</span>
							</div>
							<div
								class="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-sm text-gray-700"
							>
								<span>
									{{ __("Planned") }}:
									<span class="font-medium">{{ p.qty }}</span>
								</span>
								<span>
									{{ __("Produced") }}:
									<span class="font-medium">{{ p.produced_qty }}</span>
								</span>
								<span
									class="ms-auto inline-flex items-center gap-1 text-xs text-gray-500"
								>
									<FeatherIcon name="clock" class="w-3.5 h-3.5" />
									{{ elapsedLabel(p) }}
								</span>
							</div>

							<!-- Finish form: good (required) + loss (auto-suggest) + notes -->
							<div
								v-if="finishingWo === p.work_order"
								class="mt-3 border-t border-gray-100 pt-3 flex flex-col gap-2"
							>
								<div>
									<label class="block text-sm font-medium text-gray-700 mb-1">
										{{ __("Good Qty") }}
									</label>
									<input
										:value="goodText"
										type="text"
										inputmode="decimal"
										class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
										@input="onGoodInput($event)"
									/>
								</div>
								<div>
									<label class="block text-sm font-medium text-gray-700 mb-1">
										{{ __("Loss Qty") }}
									</label>
									<input
										:value="lossText"
										type="text"
										inputmode="decimal"
										class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
										@input="onLossInput($event)"
									/>
									<p class="text-xs text-gray-500 mt-0.5">
										{{ __("Auto: planned - good") }}
									</p>
								</div>
								<!-- Batch pickers: only for batched materials. "Auto" sends
								     no pick — the server FEFO-picks at submit time. -->
								<div v-if="finishBatchRows.length" class="overflow-x-auto -mx-1 px-1">
									<table class="w-full text-sm">
										<thead>
											<tr class="text-start text-xs text-gray-500 uppercase">
												<th class="py-1 text-start">{{ __("Material") }}</th>
												<th class="py-1 text-start">{{ __("Qty") }}</th>
												<th class="py-1 text-start">{{ __("Batch") }}</th>
											</tr>
										</thead>
										<tbody>
											<tr v-for="m in finishBatchRows" :key="m.item_code">
												<td class="py-1.5 pe-2">{{ m.item_name }}</td>
												<td class="py-1.5 pe-2 tabular-nums">
													{{ finishNeeded(m) }}
												</td>
												<td class="py-1.5 w-48">
													<select
														v-model="batchPicks[m.item_code]"
														class="w-full px-2 py-1.5 text-sm border border-gray-300 rounded-lg bg-white focus:ring-2 focus:ring-blue-500"
													>
														<option value="">{{ __("Auto") }}</option>
														<option
															v-for="b in m.batches"
															:key="b.batch_no"
															:value="b.batch_no"
														>
															{{ b.batch_no }} ({{ b.qty }})
														</option>
													</select>
												</td>
											</tr>
										</tbody>
									</table>
								</div>
								<p v-else-if="finishContextError" class="text-xs text-gray-500">
									{{ __("Batch list unavailable - the system picks automatically.") }}
								</p>
								<div>
									<label class="block text-sm font-medium text-gray-700 mb-1">
										{{ __("Notes") }}
									</label>
									<input
										v-model="finishNotes"
										type="text"
										class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
									/>
								</div>
								<div class="flex justify-end gap-2 pt-1">
									<Button variant="subtle" :disabled="acting" @click="cancelFinish">
										{{ __("Cancel") }}
									</Button>
									<Button
										variant="solid"
										:loading="acting"
										:disabled="!canFinish"
										@click="doFinish(p)"
									>
										{{ __("Finish") }}
									</Button>
								</div>
							</div>

							<!-- Row actions: Close only once something was produced —
							     before that Cancel is the clean exit (no stock moved). -->
							<div v-else class="flex flex-wrap gap-2 mt-3">
								<Button size="sm" :disabled="acting" @click="openFinish(p)">
									{{ __("Finish") }}
								</Button>
								<Button
									v-if="p.produced_qty > 0"
									variant="subtle"
									size="sm"
									:disabled="acting"
									@click="askClose(p)"
								>
									{{ __("Close") }}
								</Button>
								<Button
									variant="subtle"
									theme="red"
									size="sm"
									:disabled="acting"
									@click="askCancel(p)"
								>
									{{ __("Cancel") }}
								</Button>
							</div>
						</div>
					</div>
				</template>

				<!-- HISTORY: finished Work Orders of this outlet, newest first -->
				<template v-else-if="view === 'history'">
					<div class="flex items-center justify-end gap-2 mb-3">
						<select
							v-model="period"
							:aria-label="__('Export period')"
							class="py-1.5 ps-3 pe-8 text-sm border border-gray-300 rounded-lg bg-white"
						>
							<option v-for="o in periodOptions" :key="o.value" :value="o.value">
								{{ o.label }}
							</option>
						</select>
						<Button variant="subtle" :loading="exporting" @click="exportHistory">
							{{ __("Export to Excel") }}
						</Button>
					</div>
					<div v-if="loadingHistory" class="py-10 text-center text-sm text-gray-500">
						{{ __("Loading production history...") }}
					</div>
					<div
						v-else-if="!historyProductions.length"
						class="py-10 text-center text-sm text-gray-500"
					>
						{{ __("No production history yet") }}
					</div>
					<template v-else>
						<div class="flex flex-col max-h-[60vh] overflow-y-auto divide-y divide-gray-100 border-y border-gray-100">
							<div
								v-for="p in historyProductions"
								:key="p.work_order"
								class="px-1 py-3"
							>
								<div class="flex items-start justify-between gap-2">
									<div class="min-w-0">
										<div class="font-medium text-gray-900 truncate">
											{{ p.production_item_name }}
										</div>
										<div class="text-xs text-gray-500">{{ p.recipe_name }}</div>
									</div>
									<span
										class="shrink-0 px-2 py-0.5 rounded-full text-xs font-medium"
										:class="statusClass(p.pos_status)"
									>
										{{ posLabel(p) }}
									</span>
								</div>
								<div
									class="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-sm text-gray-700"
								>
									<span>
										{{ __("Planned") }}:
										<span class="font-medium tabular-nums">{{ p.qty }}</span>
									</span>
									<span>
										{{ __("Produced") }}:
										<span class="font-medium tabular-nums">{{ p.produced_qty }}</span>
									</span>
									<span v-if="p.process_loss_qty > 0">
										{{ __("Loss") }}:
										<span class="font-medium text-red-600 tabular-nums">{{
											p.process_loss_qty
										}}</span>
									</span>
									<span class="ms-auto text-xs text-gray-500 tabular-nums">
										{{ historyStamp(p) }}<template v-if="p.operator">, {{ p.operator }}</template>
									</span>
								</div>
							</div>
						</div>
						<div v-if="historyHasMore" class="mt-3 flex justify-center">
							<Button
								variant="subtle"
								size="sm"
								:loading="loadingHistory"
								@click="loadHistory(false)"
							>
								{{ __("Load More") }}
							</Button>
						</div>
					</template>
				</template>

			<!-- OVERVIEW: output / loss for a period, top items, daily trend -->
				<template v-else>
					<div class="flex flex-wrap items-center gap-2 mb-4">
						<div class="flex rounded-lg border border-gray-300 overflow-hidden" role="group">
							<button
								v-for="o in periodOptions"
								:key="o.value"
								type="button"
								class="px-3 py-1.5 text-sm border-e border-gray-300 last:border-e-0 transition-colors"
								:class="
									period === o.value
										? 'bg-gray-900 text-white'
										: 'bg-white text-gray-700 hover:bg-gray-50'
								"
								:aria-pressed="period === o.value"
								@click="period = o.value"
							>
								{{ o.label }}
							</button>
						</div>
						<Button class="ms-auto" variant="subtle" :loading="exporting" @click="exportHistory">
							{{ __("Export to Excel") }}
						</Button>
					</div>
					<div
						v-if="loadingDashboard && !dashboard"
						class="py-10 text-center text-sm text-gray-500"
					>
						{{ __("Loading...") }}
					</div>
					<div v-else-if="dashboard" class="flex flex-col gap-6">
						<!-- Figures: produced is the one number that matters, the rest support it -->
						<div class="grid grid-cols-2 lg:grid-cols-4 gap-y-4 border-y border-gray-200 py-4">
							<div class="col-span-2 lg:col-span-1 lg:border-e lg:border-gray-200 lg:pe-4">
								<div class="text-sm text-gray-500">{{ __("Produced") }}</div>
								<div class="text-4xl font-bold text-gray-900 tabular-nums leading-tight">
									{{ fmtQty(dashboard.produced) }}
								</div>
								<div class="text-xs text-gray-500">
									{{ __("{0} runs", [dashboard.runs]) }}
								</div>
							</div>
							<div class="lg:border-e lg:border-gray-200 lg:px-4">
								<div class="text-sm text-gray-500">{{ __("Loss rate") }}</div>
								<div
									class="text-2xl font-semibold tabular-nums"
									:class="dashboard.loss_pct >= 10 ? 'text-red-600' : 'text-gray-900'"
								>
									{{ dashboard.loss_pct }}%
								</div>
								<div class="text-xs text-gray-500">
									{{ __("{0} units lost", [fmtQty(dashboard.loss)]) }}
								</div>
							</div>
							<div class="lg:border-e lg:border-gray-200 lg:px-4">
								<div class="text-sm text-gray-500">{{ __("Running now") }}</div>
								<div class="text-2xl font-semibold text-gray-900 tabular-nums">
									{{ dashboard.active }}
								</div>
								<button
									v-if="dashboard.active"
									type="button"
									class="text-xs text-blue-600 hover:underline"
									@click="switchView('active')"
								>
									{{ __("Open list") }}
								</button>
							</div>
							<div class="lg:px-4">
								<div class="text-sm text-gray-500">{{ __("Average per run") }}</div>
								<div class="text-2xl font-semibold text-gray-900 tabular-nums">
									{{ dashboard.runs ? fmtQty(dashboard.produced / dashboard.runs) : "0" }}
								</div>
							</div>
						</div>

						<p v-if="!dashboard.runs" class="text-sm text-gray-500 text-center py-6">
							{{ __("Nothing was produced in this period.") }}
						</p>
						<div v-else class="grid grid-cols-1 lg:grid-cols-5 gap-6">
							<!-- Daily trend: produced (blue) with loss stacked on top (red) -->
							<section class="lg:col-span-3">
								<h3 class="text-sm font-semibold text-gray-900 mb-3">
									{{ __("Output per day") }}
								</h3>
								<div class="flex items-end gap-1 h-40" role="img" :aria-label="__('Output per day')">
									<div
										v-for="d in dashboard.daily"
										:key="d.day"
										class="flex-1 min-w-[4px] flex flex-col justify-end h-full"
										:title="`${d.day}: ${fmtQty(d.produced)} / ${__('Loss')} ${fmtQty(d.loss)}`"
									>
										<div
											class="bg-red-400 rounded-t-sm"
											:style="{ height: barPct(d.loss) }"
										/>
										<div
											class="bg-blue-600"
											:class="{ 'rounded-t-sm': !d.loss }"
											:style="{ height: barPct(d.produced) }"
										/>
									</div>
								</div>
								<div class="flex justify-between mt-1 text-xs text-gray-500 tabular-nums">
									<span>{{ dashboard.daily[0]?.day }}</span>
									<span v-if="dashboard.daily.length > 1">{{
										dashboard.daily[dashboard.daily.length - 1].day
									}}</span>
								</div>
								<div class="flex gap-4 mt-2 text-xs text-gray-600">
									<span class="inline-flex items-center gap-1.5"
										><span class="w-2.5 h-2.5 rounded-sm bg-blue-600" />{{ __("Produced") }}</span
									>
									<span class="inline-flex items-center gap-1.5"
										><span class="w-2.5 h-2.5 rounded-sm bg-red-400" />{{ __("Loss") }}</span
									>
								</div>
							</section>
							<!-- Top items -->
							<section class="lg:col-span-2">
								<h3 class="text-sm font-semibold text-gray-900 mb-3">
									{{ __("Most produced") }}
								</h3>
								<ol class="flex flex-col gap-3">
									<li v-for="t in dashboard.top_items" :key="t.item_code">
										<div class="flex items-baseline justify-between gap-2 text-sm">
											<span class="truncate text-gray-900">{{ t.item_name }}</span>
											<span class="shrink-0 font-semibold tabular-nums">{{
												fmtQty(t.produced)
											}}</span>
										</div>
										<div class="mt-1 h-1.5 rounded-full bg-gray-100 overflow-hidden">
											<div
												class="h-full bg-blue-600 rounded-full"
												:style="{ width: topPct(t.produced) }"
											/>
										</div>
									</li>
								</ol>
							</section>
						</div>
					</div>
				</template>
			</template>

			<!-- STEP 2: produce (locked except quantity) -->
			<template v-else>
				<div class="mb-3">
					<div class="font-medium text-gray-900">{{ selectedRecipe.recipe_name }}</div>
					<div class="text-xs text-gray-500">
						{{
							__("Output per run: {0} × {1}", [
								selectedRecipe.output_qty,
								selectedRecipe.production_item_name,
							])
						}}
					</div>
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

				<div class="overflow-x-auto -mx-1 px-1">
					<table class="w-full text-sm">
						<thead>
							<tr class="text-start text-xs text-gray-500 uppercase">
								<th class="py-1 text-start">{{ __("Material") }}</th>
								<th class="py-1 text-start">{{ __("Qty") }}</th>
								<th class="py-1 text-start">{{ __("Stock") }}</th>
								<th class="py-1 text-start" v-if="hasAnyBatch">{{ __("Batch") }}</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="row in materialRows"
								:key="row.item_code"
								class="border-t border-gray-100"
							>
								<td class="py-1.5 pe-2">
									<div>{{ row.item_name }}</div>
									<div
										v-if="rowHasInsufficientStock(row)"
										class="text-xs text-red-500"
									>
										{{ __("insufficient") }}
									</div>
								</td>
								<td class="py-1.5 pe-2 w-24 whitespace-nowrap">
									{{ row.qty }} {{ row.stock_uom }}
								</td>
								<td class="py-1.5 pe-2 text-gray-500 whitespace-nowrap">
									{{ row.available_qty }} {{ row.stock_uom }}
								</td>
								<td v-if="hasAnyBatch" class="py-1.5 pe-2 w-48">
									<template v-if="row.has_batch_no">
										<span v-if="row.batch_no"
											>{{ row.batch_no }} ({{ batchQty(row) }})</span
										>
										<span v-else class="text-red-500">{{
											__("No batch in stock")
										}}</span>
									</template>
								</td>
							</tr>
						</tbody>
					</table>
				</div>

				<div class="mt-4 flex justify-end gap-2">
					<Button variant="subtle" @click="view = 'recipes'">{{ __("Back") }}</Button>
					<Button
						variant="solid"
						:loading="starting"
						:disabled="!canSubmit"
						@click="startProduction"
					>
						{{ __("Start Production") }}
					</Button>
				</div>
			</template>

			<div v-if="errorMessage" class="mt-3 text-sm text-red-600">{{ errorMessage }}</div>
		</template>
	</DialogHost>

	<!-- Confirm overlay for Close / Cancel production (above the app shell) -->
	<Transition name="fade">
		<div
			v-if="confirmAction"
			class="fixed inset-0 bg-black bg-opacity-50 z-[500] flex items-center justify-center"
			@click.self="confirmAction = null"
		>
			<div class="bg-white rounded-lg shadow-2xl max-w-md w-full mx-4 p-6">
				<div class="flex items-start gap-4">
					<div class="flex-shrink-0">
						<div
							class="w-12 h-12 rounded-full bg-red-100 flex items-center justify-center"
						>
							<FeatherIcon name="alert-triangle" class="w-6 h-6 text-red-600" />
						</div>
					</div>
					<div class="flex-1">
						<h3 class="text-lg font-semibold text-gray-900 mb-2">
							{{
								confirmAction.type === "close"
									? __("Close Production")
									: __("Cancel Production")
							}}
						</h3>
						<p class="text-sm text-gray-600 mb-1">
							{{
								confirmAction.type === "close"
									? __("Stop production {0}? Produced quantities are kept.", [
											confirmAction.p.work_order,
										])
									: confirmAction.p.produced_qty > 0
										? __(
												"Cancel production {0}? All {1} produced units will be reversed.",
												[confirmAction.p.work_order, confirmAction.p.produced_qty],
											)
										: __(
												"Cancel production {0}? All stock movements will be reversed.",
												[confirmAction.p.work_order],
											)
							}}
						</p>
						<p class="text-sm text-gray-500">
							{{ __("This action cannot be undone.") }}
						</p>
					</div>
				</div>
				<div class="flex justify-end gap-3 mt-6">
					<Button variant="ghost" @click="confirmAction = null">
						{{ __("Cancel") }}
					</Button>
					<Button variant="solid" theme="red" :loading="acting" @click="runConfirm">
						{{ confirmAction.type === "close" ? __("Close") : __("Cancel Production") }}
					</Button>
				</div>
			</div>
		</div>
	</Transition>
</template>

<script setup>
import { Button, call, createResource, FeatherIcon } from "frappe-ui"
import { formatAmountInput, parseAmountInput } from "@/utils/amountInput"
import { useToast } from "@/composables/useToast"
import { computed, onUnmounted, ref, watch } from "vue"
import DialogHost from "@/components/common/DialogHost.js"
import RefreshButton from "@/components/common/RefreshButton.vue"
import { downloadXlsx } from "@/utils/downloadXlsx"
import { periodRange } from "@/utils/salesRecap"

const props = defineProps({
	modelValue: Boolean,
	posProfile: String,
	company: String,
	currency: { type: String, default: "" },
	embedded: { type: Boolean, default: false },
})

const emit = defineEmits(["update:modelValue", "production-created"])

const { showSuccess, showError } = useToast()

const show = ref(props.modelValue)
// Non-immediate: embedded mode mounts with modelValue already true, but the
// initial load must run after every ref below is declared — an immediate
// watcher here hit loadRecipes() mid-setup (TDZ) and crashed the whole app.
watch(
	() => props.modelValue,
	(v) => {
		show.value = v
		if (v) {
			view.value = "recipes"
			loadRecipes()
		}
	},
)
watch(show, (v) => emit("update:modelValue", v))

// view: "recipes" (list) | "detail" (locked recipe form) | "active" (running WOs) | "history" (finished WOs)
const view = ref("recipes")
const loadingRecipes = ref(false)
const recipes = ref([])
const search = ref("")
const selectedRecipe = ref(null)
const outputQty = ref(1)
const materialRows = ref([])
const errorMessage = ref("")

// Two-phase state
const productions = ref([])
const loadingActive = ref(false)
const starting = ref(false)
const acting = ref(false)
const finishingWo = ref(null)
const goodText = ref("")
const lossText = ref("")
const lossTouched = ref(false)
const finishNotes = ref("")
// Finish batch pickers: "" = Auto (server FEFO-picks at submit — always freshest)
const finishContext = ref(null)
const finishContextError = ref(false)
const batchPicks = ref({})
const confirmAction = ref(null)
const now = ref(Date.now())
let elapsedTimer = null

// History state (D5: the Work Order is the history)
const historyProductions = ref([])
const loadingHistory = ref(false)
const historyHasMore = ref(false)
const HISTORY_PAGE = 30

// Overview + export share one period
const period = ref("last7")
const periodOptions = [
	{ value: "today", label: __("Today") },
	{ value: "last7", label: __("Last 7 Days") },
	{ value: "month", label: __("This Month") },
	{ value: "year", label: __("This Year") },
]
const dashboard = ref(null)
const loadingDashboard = ref(false)
const exporting = ref(false)

async function loadDashboard() {
	const range = periodRange(period.value)
	if (!props.posProfile || !range) return
	loadingDashboard.value = true
	try {
		dashboard.value = await call("pos_next.api.production.get_production_dashboard", {
			pos_profile: props.posProfile,
			from_date: range.from,
			to_date: range.to,
		})
	} catch (err) {
		showError(errMsg(err, "Failed to load production overview"))
	} finally {
		loadingDashboard.value = false
	}
}
watch(period, () => view.value === "overview" && loadDashboard())

async function exportHistory() {
	const range = periodRange(period.value)
	if (exporting.value || !range) return
	exporting.value = true
	try {
		await downloadXlsx("pos_next.api.production.export_production_history", {
			pos_profile: props.posProfile,
			from_date: range.from,
			to_date: range.to,
		})
	} catch (err) {
		showError(err?.message || __("Export failed"))
	} finally {
		exporting.value = false
	}
}

const dailyMax = computed(() =>
	Math.max(1, ...(dashboard.value?.daily || []).map((d) => d.produced + d.loss)),
)
const barPct = (v) => `${(v / dailyMax.value) * 100}%`
const topPct = (v) =>
	`${(v / Math.max(1, dashboard.value?.top_items?.[0]?.produced || 0)) * 100}%`
const fmtQty = (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })

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

const finishingProduction = computed(
	() =>
		productions.value.find((p) => p.work_order === finishingWo.value) || null,
)

const canFinish = computed(() => parseAmountInput(goodText.value) > 0)

const finishBatchRows = computed(() => {
	const ctx = finishContext.value
	if (!ctx) return []
	// a batched material with no live batches has nothing to pick — Auto will
	// fail server-side with the standard shortage message either way
	return (ctx.materials || []).filter((m) => m.has_batch_no && m.batches.length)
})

function finishNeeded(m) {
	const woQty = finishContext.value?.wo_qty
	const gross =
		parseAmountInput(goodText.value) + parseAmountInput(lossText.value)
	if (!woQty) return m.required_qty
	return +((m.required_qty * gross) / woQty).toFixed(3)
}

function canMake(recipe) {
	return recipe.items.every((i) =>
		i.has_batch_no
			? i.batches.some((b) => b.qty >= i.qty)
			: i.available_qty >= i.qty,
	)
}

function switchView(target) {
	if (view.value === target) return
	view.value = target
	if (target === "active") loadActive()
	if (target === "history") loadHistory()
	if (target === "overview") loadDashboard()
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
			err?.messages?.join("\n") || err || __("Failed to load recipes")
		loadingRecipes.value = false
	},
})

function loadRecipes() {
	if (!props.posProfile) return
	// Availability is a snapshot: producing from this dialog doesn't reload it
	// (the refresh button does) — the server re-checks real stock before
	// posting, so a stale number can never move stock.
	loadingRecipes.value = true
	errorMessage.value = ""
	selectedRecipe.value = null
	recipesResource.submit({ pos_profile: props.posProfile })
}

function selectRecipe(recipe) {
	selectedRecipe.value = recipe
	outputQty.value = recipe.output_qty
	scaleRows()
	view.value = "detail"
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

async function startProduction() {
	const r = selectedRecipe.value
	if (!r || starting.value) return
	errorMessage.value = ""
	starting.value = true
	try {
		const result = await call("pos_next.api.production.start_production", {
			recipe: r.name,
			qty: outputQty.value,
			pos_profile: props.posProfile,
		})
		starting.value = false
		showSuccess(
			__(
				"Production started: {0} × {1}. Finish it from the list below once output is ready.",
				[
					result?.production_item || r.production_item_name,
					result?.qty ?? outputQty.value,
				],
			),
		)
		view.value = "active"
		loadActive()
	} catch (err) {
		starting.value = false
		showError(errMsg(err, "Failed to start production"))
	}
}

// ---------------------------------------------------------------------------
// In Production list + Finish / Close / Cancel
// ---------------------------------------------------------------------------

const activeResource = createResource({
	url: "pos_next.api.production.get_active_productions",
	auto: false,
	onSuccess(data) {
		productions.value = data.productions || []
		loadingActive.value = false
	},
	onError(err) {
		loadingActive.value = false
		showError(errMsg(err, "Failed to load active productions"))
	},
})

function loadActive() {
	if (!props.posProfile) return
	loadingActive.value = true
	finishingWo.value = null
	activeResource.submit({ pos_profile: props.posProfile })
}

async function loadHistory(reset = true) {
	if (!props.posProfile || loadingHistory.value) return
	const offset = reset ? 0 : historyProductions.value.length
	loadingHistory.value = true
	try {
		const data = await call("pos_next.api.production.get_production_history", {
			pos_profile: props.posProfile,
			limit: HISTORY_PAGE,
			offset,
		})
		historyProductions.value = reset
			? data.productions || []
			: historyProductions.value.concat(data.productions || [])
		historyHasMore.value = !!data.has_more
	} catch (err) {
		showError(errMsg(err, "Failed to load production history"))
	} finally {
		loadingHistory.value = false
	}
}

// "Not Started" is a shared translation key (coupons/promotions reuse it), so
// the production context gets its own wording through a dedicated key.
function posLabel(p) {
	return p.pos_status === "Not Started"
		? __("Ready to Produce")
		: __(p.pos_status)
}

function statusClass(posStatus) {
	if (posStatus === "In Progress") return "bg-blue-100 text-blue-700"
	if (posStatus === "Failed") return "bg-red-100 text-red-700"
	return "bg-gray-100 text-gray-600"
}

// "2026-09-27 19:23:42.123456" → "2026-09-27 19:23": calm and locale-free.
function historyStamp(p) {
	return String(p.created_at || "").slice(0, 16)
}

// "2026-09-27 10:00:00" is not ISO — Safari yields Invalid Date, so the
// space becomes "T" before parsing (device-local, matching the outlet tz).
function parseServerDate(value) {
	if (!value) return null
	const d = new Date(String(value).replace(" ", "T"))
	return Number.isNaN(d.getTime()) ? null : d
}

function elapsedLabel(p) {
	// epoch (detik) = waktu absolut dari server; string naive adalah fallback
	// untuk bundle lama yang masih memanggil API versi sebelumnya.
	const start =
		typeof p.started_at === "number"
			? new Date(p.started_at * 1000)
			: parseServerDate(p.started_at)
	if (!start) return "—"
	const mins = Math.max(0, Math.floor((now.value - start.getTime()) / 60000))
	const h = Math.floor(mins / 60)
	const m = mins % 60
	return h ? __("{0}h {1}m", [h, m]) : __("{0}m", [m])
}

function startElapsedTimer() {
	stopElapsedTimer()
	now.value = Date.now()
	elapsedTimer = setInterval(() => {
		now.value = Date.now()
	}, 30000)
}

function stopElapsedTimer() {
	if (elapsedTimer) {
		clearInterval(elapsedTimer)
		elapsedTimer = null
	}
}

watch(view, (v) => (v === "active" ? startElapsedTimer() : stopElapsedTimer()))
onUnmounted(stopElapsedTimer)

function openFinish(p) {
	finishingWo.value = p.work_order
	lossTouched.value = false
	finishNotes.value = ""
	// Prefill with what is left of the plan; the cashier edits from there.
	goodText.value = formatAmountInput(
		Math.max(0, (p.qty || 0) - (p.produced_qty || 0)),
	)
	suggestLoss()
	loadFinishContext(p.work_order)
}

async function loadFinishContext(wo) {
	finishContext.value = null
	finishContextError.value = false
	batchPicks.value = {}
	if (!wo) return
	try {
		finishContext.value = await call(
			"pos_next.api.production.get_finish_context",
			{ work_order: wo },
		)
	} catch {
		// finishing must never depend on this — Auto needs no context at all
		finishContextError.value = true
	}
}

function suggestLoss() {
	const p = finishingProduction.value
	if (!p) return
	const good = parseAmountInput(goodText.value)
	lossText.value = formatAmountInput(Math.max(0, (p.qty || 0) - good))
}

function onGoodInput(e) {
	goodText.value = formatAmountInput(e.target.value)
	if (!lossTouched.value) suggestLoss()
}

function onLossInput(e) {
	lossText.value = formatAmountInput(e.target.value)
	lossTouched.value = true
}

function cancelFinish() {
	finishingWo.value = null
	goodText.value = ""
	lossText.value = ""
	finishNotes.value = ""
	lossTouched.value = false
	finishContext.value = null
	finishContextError.value = false
	batchPicks.value = {}
}

async function doFinish(p) {
	const good = parseAmountInput(goodText.value)
	if (good <= 0 || acting.value) return
	acting.value = true
	try {
		const payload = {
			work_order: p.work_order,
			good_qty: good,
			loss_qty: parseAmountInput(lossText.value),
		}
		const notes = finishNotes.value.trim()
		if (notes) payload.notes = notes
		const picks = {}
		for (const [code, batch] of Object.entries(batchPicks.value)) {
			if (batch) picks[code] = batch
		}
		if (Object.keys(picks).length) payload.batch_picks = picks
		const result = await call(
			"pos_next.api.production.finish_production",
			payload,
		)
		// stock just moved — let the parent refresh the item catalog
		emit("production-created", result)
		showSuccess(
			__("Production complete: {0} × {1}", [
				p.production_item,
				result?.good_qty ?? good,
			]),
		)
		cancelFinish()
		loadActive()
	} catch (err) {
		showError(errMsg(err, "Failed to finish production"))
	} finally {
		acting.value = false
	}
}

function askClose(p) {
	confirmAction.value = { type: "close", p }
}

function askCancel(p) {
	confirmAction.value = { type: "cancel", p }
}

async function runConfirm() {
	const action = confirmAction.value
	if (!action || acting.value) return
	acting.value = true
	try {
		if (action.type === "close") {
			const result = await call("pos_next.api.production.close_production", {
				work_order: action.p.work_order,
			})
			if (action.p.produced_qty > 0) emit("production-created", result)
			showSuccess(__("Production closed: {0}", [action.p.work_order]))
		} else {
			const result = await call("pos_next.api.production.cancel_production", {
				work_order: action.p.work_order,
			})
			// cancelling reverses earlier stock movements — refresh the catalog
			emit("production-created", result)
			showSuccess(__("Production cancelled: {0}", [action.p.work_order]))
		}
		confirmAction.value = null
		loadActive()
	} catch (err) {
		showError(
			errMsg(
				err,
				action.type === "close"
					? "Failed to close production"
					: "Failed to cancel production",
			),
		)
	} finally {
		acting.value = false
	}
}

function errMsg(err, fallback) {
	const text =
		err?.messages?.join("\n") ||
		(typeof err === "string" ? err : err?.message) ||
		""
	return text || __(fallback)
}

function batchQty(row) {
	return row.batches.find((b) => b.batch_no === row.batch_no)?.qty ?? ""
}

// Initial load for embedded mode (mounted with modelValue already true) —
// kept at the end of setup so every ref loadRecipes touches exists.
if (props.modelValue) {
	view.value = "recipes"
	loadRecipes()
}
</script>

<style scoped>
.fade-enter-active,
.fade-leave-active {
	transition: opacity 0.3s ease
}

.fade-enter-from,
.fade-leave-to {
	opacity: 0
}
</style>
