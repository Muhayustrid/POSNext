<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: __('Production'), size: '3xl' }"
	>
		<template #body-content>
			<!-- Embedded mode fills the menu pane: cap the measure here too -->
			<div class="mx-auto w-full max-w-3xl">
			<!-- LIST LEVEL: Recipes (one-shot + start) | In Production (two-phase) -->
			<template v-if="view !== 'detail' && view !== 'history-detail'">
				<div class="flex items-end gap-2 mb-4 border-b border-gray-200">
					<nav class="flex gap-1 overflow-x-auto" role="tablist">
						<button
							v-for="tab in [
								{ id: 'recipes', label: __('Recipes') },
								{ id: 'active', label: __('In Production') },
								{ id: 'history', label: __('History') },
								{ id: 'overview', label: __('Overview') },
							]"
							:key="tab.id"
							:id="`production-tab-${tab.id}`"
							type="button"
							role="tab"
							aria-controls="production-panel"
							:aria-selected="view === tab.id"
							class="-mb-px min-h-11 px-3 border-b-2 text-sm font-semibold whitespace-nowrap transition-colors duration-150 touch-manipulation"
							:class="
								view === tab.id
									? 'border-blue-600 text-blue-700'
									: 'border-transparent text-gray-700 hover:text-gray-800'
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

				<div
					id="production-panel"
					role="tabpanel"
					:aria-labelledby="`production-tab-${view}`"
				>
				<!-- RECIPES: pick a recipe -->
				<template v-if="view === 'recipes'">
					<div class="relative">
						<FeatherIcon
							name="search"
							class="pointer-events-none absolute start-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
						/>
						<input
							v-model="search"
							type="text"
							:placeholder="__('Search recipe...')"
							:aria-label="__('Search recipe...')"
							class="w-full h-11 ps-10 pe-3 text-base border border-gray-300 rounded-xl bg-white placeholder-gray-600 focus:border-blue-500 focus:ring-4 focus:ring-blue-500/15"
						/>
					</div>
					<div v-if="loadingRecipes" class="py-10 text-center text-sm text-gray-700">
						{{ __("Loading recipes...") }}
					</div>
					<div
						v-else-if="!filteredRecipes.length"
						class="py-10 text-center text-sm text-gray-700"
					>
						{{ __("No recipes available for this outlet") }}
					</div>
					<div
						v-else
						class="grid grid-cols-1 sm:grid-cols-2 gap-2.5 max-h-[60vh] overflow-y-auto mt-3 p-0.5"
					>
						<button
							v-for="r in filteredRecipes"
							:key="r.name"
							class="w-full text-start px-4 py-3.5 border rounded-xl bg-white touch-manipulation transition-colors duration-150 hover:border-blue-300 hover:bg-blue-50/40 active:bg-blue-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
							:class="maxUnits(r) ? 'border-gray-200' : 'border-red-200 bg-red-50/40'"
							@click="selectRecipe(r)"
						>
							<div class="flex items-start justify-between gap-3">
								<div class="min-w-0">
									<div class="font-semibold text-gray-900 truncate">
										{{ r.recipe_name }}
									</div>
									<div class="text-xs text-gray-700 mt-0.5 truncate">
										{{ r.production_item_name }}
									</div>
								</div>
								<!-- Capacity: how much today's material stock can still make -->
								<div class="shrink-0 text-end">
									<div
										class="text-[28px] font-bold leading-none tabular-nums"
										:class="maxUnits(r) ? 'text-gray-900' : 'text-red-600'"
									>
										{{ fmtQty(maxUnits(r)) }}
									</div>
									<div class="text-xs text-gray-700 mt-1">
										{{ outputUom(r) }}
									</div>
								</div>
							</div>
							<div
								class="mt-3 flex items-center gap-1.5 border-t border-gray-100 pt-2.5 text-xs font-medium"
								:class="maxUnits(r) ? 'text-green-700' : 'text-red-600'"
							>
								<span
									class="w-2 h-2 rounded-full"
									:class="maxUnits(r) ? 'bg-green-500' : 'bg-red-500'"
									aria-hidden="true"
								/>
								<template v-if="maxUnits(r)">
									{{ capacityLabel(r) }}
								</template>
								<template v-else>
									{{ __("Not enough {0}", [limitingMaterial(r)?.item_name || ""]) }}
								</template>
							</div>
						</button>
					</div>
				</template>

				<!-- IN PRODUCTION: live Work Orders of this outlet -->
				<template v-else-if="view === 'active'">
					<div v-if="loadingActive" class="py-10 text-center text-sm text-gray-700">
						{{ __("Loading active productions...") }}
					</div>
					<div
						v-else-if="!productions.length"
						class="mx-auto flex max-w-xl flex-col items-center gap-3 py-10 text-center text-sm text-gray-700"
					>
						{{ __("No active productions") }}
						<Button variant="subtle" class="min-h-11 px-4 touch-manipulation" @click="switchView('recipes')">
							{{ __("Choose a recipe") }}
						</Button>
					</div>
					<div
						v-else
						class="flex flex-col gap-2.5 max-h-[60vh] overflow-y-auto"
					>
						<div
							v-for="p in productions"
							:key="p.work_order"
							class="border rounded-xl px-4 py-3.5 transition-colors"
							:class="finishingWo === p.work_order ? 'border-blue-500 ring-1 ring-inset ring-blue-500' : 'border-gray-200'"
						>
							<div class="flex items-start justify-between gap-2">
								<div class="min-w-0">
									<div class="font-medium text-gray-900 truncate">
										{{ p.production_item_name }}
									</div>
									<div class="text-xs text-gray-700">{{ p.recipe_name }}</div>
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
									<span class="font-semibold text-gray-900 tabular-nums">{{ p.qty }}</span>
								</span>
								<span>
									{{ __("Produced") }}:
									<span class="font-semibold text-gray-900 tabular-nums">{{ p.produced_qty }}</span>
								</span>
								<span
									class="ms-auto inline-flex items-center gap-1 text-xs text-gray-700 tabular-nums"
								>
									<FeatherIcon name="clock" class="w-3.5 h-3.5" />
									{{ elapsedLabel(p) }}
								</span>
							</div>

							<!-- Finish form: good (required) + loss (auto-suggest) + notes -->
							<div
								v-if="finishingWo === p.work_order"
								class="mt-3 border-t border-gray-100 pt-3 flex flex-col gap-3"
							>
								<div>
									<label for="production-good-qty" class="block text-sm font-medium text-gray-700 mb-1">
										{{ __("Good Qty") }}
									</label>
									<input
										id="production-good-qty"
										:value="goodText"
										type="text"
										inputmode="decimal"
										class="w-full h-12 px-4 text-xl font-semibold text-end tabular-nums border border-gray-300 rounded-xl caret-blue-600 focus:border-blue-500 focus:ring-4 focus:ring-blue-500/15"
										@input="onGoodInput($event)"
									/>
									<!-- Loss is no longer typed: whatever is short of the plan is loss -->
									<p v-if="finishLoss > 0" class="flex items-center gap-1.5 text-sm font-medium text-red-700 mt-1.5" role="status">
										<FeatherIcon name="alert-circle" class="h-4 w-4 shrink-0" />
										{{ __("{0} short of plan, recorded as loss", [fmtQty(finishLoss)]) }}
									</p>
								</div>
								<!-- Batch pickers: only for batched materials. "Auto" sends
								     no pick — the server FEFO-picks at submit time. -->
								<div v-if="finishBatchRows.length" class="overflow-x-auto -mx-1 px-1">
									<table class="w-full text-sm">
										<thead>
											<tr class="text-start text-xs text-gray-700 uppercase">
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
														class="w-full h-10 px-2 text-sm border border-gray-300 rounded-lg bg-white focus:border-blue-500 focus:ring-4 focus:ring-blue-500/15"
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
								<p v-else-if="finishContextError" class="text-xs text-gray-700">
									{{ __("Batch list unavailable - the system picks automatically.") }}
								</p>
								<div>
									<label for="production-finish-notes" class="block text-sm font-medium text-gray-700 mb-1">
										{{ __("Notes") }}
									</label>
									<input
										id="production-finish-notes"
										v-model="finishNotes"
										type="text"
										class="w-full h-11 px-3 text-base border border-gray-300 rounded-xl focus:border-blue-500 focus:ring-4 focus:ring-blue-500/15"
									/>
								</div>
								<div class="flex justify-end gap-2 pt-1">
									<Button variant="subtle" class="min-h-11 px-4 touch-manipulation" :disabled="acting" @click="cancelFinish">
										{{ __("Cancel") }}
									</Button>
									<Button
										variant="solid"
										theme="blue"
										class="min-h-11 px-5 touch-manipulation"
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
								<Button variant="solid" theme="blue" class="min-h-11 px-5 touch-manipulation" :disabled="acting" @click="openFinish(p)">
									{{ __("Finish") }}
								</Button>
								<Button
									v-if="p.produced_qty > 0"
									variant="subtle"
									class="min-h-11 px-4 touch-manipulation"
									:disabled="acting"
									@click="askClose(p)"
								>
									{{ __("Close") }}
								</Button>
								<Button
									variant="subtle"
									theme="red"
									class="min-h-11 px-4 touch-manipulation ms-auto"
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
					<div class="flex flex-wrap items-end gap-2 mb-3">
						<label class="flex flex-col gap-1 text-xs font-medium text-gray-700">
							{{ __("Period") }}
							<select
								v-model="period"
								class="h-11 ps-3 pe-8 text-sm text-gray-900 border border-gray-300 rounded-lg bg-white focus:border-blue-500 focus:ring-4 focus:ring-blue-500/15"
							>
								<option v-for="o in periodOptions" :key="o.value" :value="o.value">
									{{ o.label }}
								</option>
							</select>
						</label>
						<Button class="ms-auto min-h-11 px-4 touch-manipulation" variant="subtle" :loading="exporting" @click="exportHistory">
							{{ __("Export to Excel") }}
						</Button>
					</div>
					<div v-if="loadingHistory && !historyProductions.length" class="py-10 text-center text-sm text-gray-700">
						{{ __("Loading production history...") }}
					</div>
					<div
						v-else-if="!historyProductions.length"
						class="mx-auto max-w-xl py-10 text-center text-sm text-gray-700"
					>
						{{ __("No production history yet") }}
					</div>
					<template v-else>
						<div
							ref="historyScroller"
							class="flex flex-col max-h-[60vh] overflow-y-auto divide-y divide-gray-100 border-y border-gray-100"
						>
							<div v-for="p in historyProductions" :key="p.work_order">
								<!-- Whole row opens the detail page; the report action sits outside it -->
								<button
									type="button"
									class="flex min-h-11 w-full items-center gap-3 px-2 py-3 text-start transition-colors duration-150 hover:bg-gray-50 active:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 touch-manipulation"
									@click="openHistoryDetail(p)"
								>
									<div class="min-w-0 flex-1">
										<div class="flex items-start justify-between gap-2">
											<div class="min-w-0">
												<div class="font-medium text-gray-900 truncate">
													{{ p.production_item_name }}
												</div>
												<div class="text-xs text-gray-700 truncate">
													{{ p.recipe_name }} · {{ p.work_order }}
												</div>
											</div>
											<span
												class="shrink-0 px-2 py-0.5 rounded-full text-xs font-medium"
												:class="statusClass(p.pos_status)"
											>
												{{ posLabel(p) }}
											</span>
										</div>
										<div class="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-sm text-gray-700">
											<span>
												{{ __("Planned") }}:
												<span class="font-medium text-gray-900 tabular-nums">{{ fmtQty(p.qty) }}</span>
											</span>
											<span>
												{{ __("Produced") }}:
												<span class="font-medium text-gray-900 tabular-nums">{{ fmtQty(p.produced_qty) }}</span>
											</span>
											<span v-if="p.process_loss_qty > 0">
												{{ __("Loss") }}:
												<span class="font-medium text-red-700 tabular-nums">{{ fmtQty(p.process_loss_qty) }}</span>
											</span>
											<span class="ms-auto text-xs text-gray-700 tabular-nums">
												{{ historyStamp(p) }}<template v-if="p.operator">, {{ p.operator }}</template>
											</span>
										</div>
									</div>
									<FeatherIcon name="chevron-right" class="h-4 w-4 shrink-0 text-gray-700" aria-hidden="true" />
								</button>
								<!-- Production Loss draft tracking: badge only when a
								     system reject request exists for this Work Order -->
								<div v-if="p.reject_request" class="flex items-center gap-2 px-2 pb-2 -mt-1">
									<span
										class="inline-flex px-2 py-0.5 rounded-full text-xs font-medium"
										:class="rejectStatusClass(p.reject_status)"
									>
										{{ rejectLabel(p.reject_status) }}
									</span>
									<button
										v-if="p.reject_status === 'Draft'"
										type="button"
										class="min-h-11 px-2 text-sm font-medium text-blue-700 hover:underline touch-manipulation"
										@click="emit('open-reject-request', p.reject_request)"
									>
										{{ __("Submit report") }}
									</button>
								</div>
							</div>
						</div>
						<div v-if="historyHasMore" class="mt-3 flex justify-center">
							<Button
								variant="subtle"
								class="min-h-11 px-4 touch-manipulation"
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
						<div class="flex w-full sm:w-auto rounded-lg border border-gray-300 overflow-hidden" role="group" :aria-label="__('Period')">
							<button
								v-for="o in periodOptions"
								:key="o.value"
								type="button"
								class="flex-1 sm:flex-none min-h-11 px-2 sm:px-3 text-sm font-medium whitespace-nowrap border-e border-gray-300 last:border-e-0 transition-colors touch-manipulation"
								:class="
									period === o.value
										? 'bg-blue-600 text-white'
										: 'bg-white text-gray-700 hover:bg-gray-50'
								"
								:aria-pressed="period === o.value"
								@click="period = o.value"
							>
								{{ o.label }}
							</button>
						</div>
						<Button class="ms-auto min-h-11 px-4 touch-manipulation" variant="subtle" :loading="exporting" @click="exportHistory">
							{{ __("Export to Excel") }}
						</Button>
					</div>
					<div
						v-if="loadingDashboard && !dashboard"
						class="py-10 text-center text-sm text-gray-700"
					>
						{{ __("Loading...") }}
					</div>
					<div v-else-if="dashboard" class="flex flex-col gap-6">
						<!-- Figures: four equal cells, produced leads by weight not size -->
						<dl class="grid grid-cols-2 sm:grid-cols-4 gap-px overflow-hidden rounded-xl border border-gray-200 bg-gray-200 [&>div]:bg-white [&>div]:px-4 [&>div]:py-3">
							<div>
								<dt class="text-sm text-gray-700">{{ __("Produced") }}</dt>
								<dd class="text-2xl font-bold text-gray-900 tabular-nums">
									{{ fmtQty(dashboard.produced) }}
								</dd>
								<dd class="text-xs text-gray-700">
									{{ __("{0} runs", [dashboard.runs]) }}
								</dd>
							</div>
							<div>
								<dt class="text-sm text-gray-700">{{ __("Loss rate") }}</dt>
								<dd
									class="text-2xl font-semibold tabular-nums"
									:class="dashboard.loss_pct >= 10 ? 'text-red-700' : 'text-gray-900'"
								>
									{{ dashboard.loss_pct }}%
								</dd>
								<dd class="text-xs text-gray-700">
									{{ __("{0} units lost", [fmtQty(dashboard.loss)]) }}
								</dd>
							</div>
							<div>
								<dt class="text-sm text-gray-700">{{ __("Running now") }}</dt>
								<dd class="text-2xl font-semibold text-gray-900 tabular-nums">
									{{ dashboard.active }}
								</dd>
								<dd v-if="dashboard.active">
									<button
										type="button"
										class="-my-2 min-h-11 text-sm font-medium text-blue-700 hover:underline touch-manipulation"
										@click="switchView('active')"
									>
										{{ __("Open list") }}
									</button>
								</dd>
							</div>
							<div>
								<dt class="text-sm text-gray-700">{{ __("Average per run") }}</dt>
								<dd class="text-2xl font-semibold text-gray-900 tabular-nums">
									{{ dashboard.runs ? fmtQty(dashboard.produced / dashboard.runs) : "0" }}
								</dd>
							</div>
						</dl>

						<p v-if="!dashboard.runs" class="text-sm text-gray-700 text-center py-6">
							{{ __("Nothing was produced in this period.") }}
						</p>
						<template v-else>
							<!-- Daily trend: produced (blue) with loss stacked on top (red) -->
							<section>
								<h3 class="text-sm font-semibold text-gray-900 mb-3">
									{{ __("Output per day") }}
								</h3>
								<div
									class="flex items-end justify-center h-40"
									:class="dailyLabelled ? 'gap-2' : 'gap-1'"
									role="img"
									:aria-label="__('Output per day')"
								>
									<div
										v-for="d in dashboard.daily"
										:key="d.day"
										class="flex-1 min-w-[4px] max-w-12 flex flex-col justify-end h-full"
										:title="`${d.day}: ${fmtQty(d.produced)} / ${__('Loss')} ${fmtQty(d.loss)}`"
									>
										<div
											v-if="dailyLabelled"
											class="mb-1 text-center text-xs font-medium text-gray-900 tabular-nums"
										>
											{{ fmtQty(d.produced) }}
										</div>
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
								<!-- Short ranges label every bar; long ones only the ends -->
								<div
									v-if="dailyLabelled"
									class="flex justify-center gap-2 mt-1 text-xs text-gray-700 tabular-nums"
								>
									<span v-for="d in dashboard.daily" :key="d.day" class="flex-1 max-w-12 text-center">
										{{ dayLabel(d.day) }}
									</span>
								</div>
								<div v-else class="flex justify-between mt-1 text-xs text-gray-700 tabular-nums">
									<span>{{ dashboard.daily[0]?.day }}</span>
									<span v-if="dashboard.daily.length > 1">{{
										dashboard.daily[dashboard.daily.length - 1].day
									}}</span>
								</div>
								<div class="flex justify-center gap-4 mt-3 text-xs text-gray-700">
									<span class="inline-flex items-center gap-1.5"
										><span class="w-2.5 h-2.5 rounded-sm bg-blue-600" />{{ __("Produced") }}</span
									>
									<span class="inline-flex items-center gap-1.5"
										><span class="w-2.5 h-2.5 rounded-sm bg-red-400" />{{ __("Loss") }}</span
									>
								</div>
							</section>
							<!-- Top items -->
							<section>
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
						</template>
					</div>
				</template>
				</div>
			</template>

			<!-- STEP 2: produce (locked except quantity) -->
			<template v-else-if="view === 'detail'">
				<div class="mb-4 flex items-center gap-3 border-b border-gray-200 pb-4">
					<button
						type="button"
						class="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-gray-200 text-gray-700 hover:bg-gray-50 touch-manipulation"
						:aria-label="__('Back')"
						@click="view = 'recipes'"
					>
						<FeatherIcon name="arrow-left" class="h-5 w-5" />
					</button>
					<div class="min-w-0">
						<div class="truncate text-lg font-semibold text-gray-900">
							{{ selectedRecipe.recipe_name }}
						</div>
						<div class="truncate text-sm text-gray-700">{{ selectedRecipe.production_item_name }}</div>
					</div>
				</div>

				<!-- Capacity banner: what the outlet's material stock can make right now -->
				<div
					class="mb-5 flex items-start gap-3 rounded-xl px-4 py-3"
					:class="selectedMax ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-700'"
				>
					<FeatherIcon
						:name="selectedMax ? 'check-circle' : 'alert-circle'"
						class="w-5 h-5 shrink-0 mt-0.5"
					/>
					<div class="text-sm">
						<template v-if="selectedMax">
							<div class="font-semibold">{{ capacityLabel(selectedRecipe) }}</div>
							<div class="text-xs opacity-80">
								{{
									__("Limited by {0}", [limitingMaterial(selectedRecipe)?.item_name || ""])
								}}
							</div>
						</template>
						<div v-else class="font-semibold">
							{{ __("Not enough {0}", [limitingMaterial(selectedRecipe)?.item_name || ""]) }}
						</div>
					</div>
				</div>

				<!-- Quantity: counted in output units; materials scale pro rata from the recipe -->
				<label for="production-qty" class="block text-sm font-semibold text-gray-900 mb-2">
					{{ __("Quantity to produce") }}
				</label>
				<div class="flex flex-wrap items-center gap-3 mb-5">
					<div class="inline-flex items-center rounded-xl border border-gray-300 bg-white overflow-hidden focus-within:border-blue-500 focus-within:ring-4 focus-within:ring-blue-500/15">
						<button
							type="button"
							class="w-14 h-14 flex items-center justify-center text-gray-700 hover:bg-gray-50 active:bg-gray-100 disabled:opacity-40 touch-manipulation"
							:aria-label="__('Decrease')"
							:disabled="produceQty <= minQty"
							@click="stepQty(-1)"
						>
							<FeatherIcon name="minus" class="w-4 h-4" />
						</button>
						<input
							id="production-qty"
							v-model.number="produceQty"
							type="number"
							min="1"
							:step="wholeOutput ? 1 : 'any'"
							:inputmode="wholeOutput ? 'numeric' : 'decimal'"
							class="w-24 h-14 border-0 border-x border-gray-200 text-center text-2xl font-semibold tabular-nums caret-blue-600 focus:ring-0 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
						/>
						<button
							type="button"
							class="w-14 h-14 flex items-center justify-center text-gray-700 hover:bg-gray-50 active:bg-gray-100 touch-manipulation"
							:aria-label="__('Increase')"
							@click="stepQty(1)"
						>
							<FeatherIcon name="plus" class="w-4 h-4" />
						</button>
					</div>
					<div class="text-sm text-gray-700">{{ outputUom(selectedRecipe) }}</div>
				</div>

				<div class="overflow-x-auto rounded-xl border border-gray-200">
					<table class="w-full text-sm">
						<thead class="bg-gray-50">
							<tr class="text-start text-xs font-medium text-gray-700">
								<th class="py-2.5 px-3 text-start">{{ __("Material") }}</th>
								<th class="py-2.5 px-3 text-end">{{ __("Needed") }}</th>
								<th class="py-2.5 px-3 text-end">{{ __("Stock") }}</th>
								<th class="py-2.5 px-3 text-start" v-if="hasAnyBatch">{{ __("Batch") }}</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="row in materialRows"
								:key="row.item_code"
								class="border-t border-gray-100"
								:class="{ 'bg-red-50': rowHasInsufficientStock(row) }"
							>
								<td class="py-2.5 px-3">
									<div class="text-gray-900">{{ row.item_name }}</div>
									<div
										v-if="rowHasInsufficientStock(row)"
										class="text-xs text-red-600"
									>
										{{ __("insufficient") }}
									</div>
								</td>
								<td class="py-2.5 px-3 text-end font-semibold text-gray-900 tabular-nums whitespace-nowrap">
									{{ fmtQty(row.qty) }} {{ row.stock_uom }}
								</td>
								<td
									class="py-2.5 px-3 text-end tabular-nums whitespace-nowrap"
									:class="rowHasInsufficientStock(row) ? 'text-red-600' : 'text-gray-700'"
								>
									{{ fmtQty(row.available_qty) }} {{ row.stock_uom }}
								</td>
								<td v-if="hasAnyBatch" class="py-2.5 px-3 w-48 tabular-nums">
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

				<div class="mt-5 flex justify-end gap-2">
					<Button variant="subtle" class="min-h-11 px-4 touch-manipulation" @click="view = 'recipes'">{{ __("Back") }}</Button>
					<Button
						variant="solid"
						theme="blue"
						class="min-h-11 px-5 touch-manipulation"
						:loading="starting"
						:disabled="!canSubmit"
						@click="startProduction"
					>
						{{ __("Start Production") }}
					</Button>
				</div>
			</template>

			<!-- HISTORY DETAIL: what one finished Work Order actually did -->
			<template v-else-if="view === 'history-detail'">
				<div class="mb-4 flex items-center gap-3 border-b border-gray-200 pb-4">
					<button
						type="button"
						class="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-gray-200 text-gray-700 hover:bg-gray-50 touch-manipulation"
						:aria-label="__('Back')"
						@click="closeHistoryDetail"
					>
						<FeatherIcon name="arrow-left" class="h-5 w-5" />
					</button>
					<div class="min-w-0 flex-1">
						<h3 class="text-lg font-semibold leading-snug text-gray-900 break-words">
							{{ historyRow.production_item_name }}
						</h3>
						<div class="text-sm text-gray-700 break-all">{{ historyRow.work_order }}</div>
					</div>
					<span
						class="shrink-0 px-2 py-0.5 rounded-full text-xs font-medium"
						:class="statusClass(historyRow.pos_status)"
					>
						{{ posLabel(historyRow) }}
					</span>
				</div>

				<div v-if="loadingDetail" class="py-10 text-center text-sm text-gray-700">
					{{ __("Loading...") }}
				</div>
				<div v-else-if="detailError" class="mx-auto flex max-w-xl flex-col items-center gap-3 py-10 text-center">
					<p class="text-sm text-red-700">{{ detailError }}</p>
					<Button variant="subtle" class="min-h-11 px-4 touch-manipulation" @click="loadHistoryDetail">
						{{ __("Retry") }}
					</Button>
				</div>
				<div v-else-if="historyDetail" class="flex flex-col gap-6">
					<!-- Outcome: what was planned against what came out -->
					<dl class="grid grid-cols-3 gap-px overflow-hidden rounded-xl border border-gray-200 bg-gray-200 [&>div]:bg-white [&>div]:px-4 [&>div]:py-3">
						<div>
							<dt class="text-sm text-gray-700">{{ __("Planned") }}</dt>
							<dd class="text-2xl font-semibold text-gray-900 tabular-nums">{{ fmtQty(historyDetail.qty) }}</dd>
							<dd class="text-xs text-gray-700">{{ historyDetail.stock_uom }}</dd>
						</div>
						<div>
							<dt class="text-sm text-gray-700">{{ __("Good") }}</dt>
							<dd class="text-2xl font-bold text-gray-900 tabular-nums">{{ fmtQty(historyDetail.produced_qty) }}</dd>
							<dd class="text-xs text-gray-700">{{ historyDetail.stock_uom }}</dd>
						</div>
						<div>
							<dt class="text-sm text-gray-700">{{ __("Loss") }}</dt>
							<dd
								class="text-2xl font-semibold tabular-nums"
								:class="historyDetail.process_loss_qty > 0 ? 'text-red-700' : 'text-gray-900'"
							>
								{{ fmtQty(historyDetail.process_loss_qty) }}
							</dd>
							<dd class="text-xs text-gray-700">{{ historyDetail.stock_uom }}</dd>
						</div>
					</dl>

					<dl class="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
						<div>
							<dt class="text-gray-700">{{ __("Recipe") }}</dt>
							<dd class="font-medium text-gray-900">{{ historyDetail.recipe_name || "—" }}</dd>
						</div>
						<div>
							<dt class="text-gray-700">{{ __("Operator") }}</dt>
							<dd class="font-medium text-gray-900 break-all">{{ historyDetail.operator || "—" }}</dd>
						</div>
						<div>
							<dt class="text-gray-700">{{ __("Created") }}</dt>
							<dd class="font-medium text-gray-900 tabular-nums">{{ historyStamp(historyDetail) }}</dd>
						</div>
						<div>
							<dt class="text-gray-700">{{ __("Started") }}</dt>
							<dd class="font-medium text-gray-900 tabular-nums">{{ startedStamp(historyDetail) }}</dd>
						</div>
					</dl>

					<section>
						<h4 class="mb-2 text-sm font-semibold text-gray-900">{{ __("Materials used") }}</h4>
						<p v-if="!historyDetail.materials.length" class="text-sm text-gray-700">
							{{ __("No materials were consumed.") }}
						</p>
						<div v-else class="overflow-x-auto rounded-xl border border-gray-200">
							<table class="w-full text-sm">
								<thead class="bg-gray-50">
									<tr class="text-xs font-medium text-gray-700">
										<th class="py-2.5 px-3 text-start">{{ __("Material") }}</th>
										<th class="py-2.5 px-3 text-start">{{ __("Batch") }}</th>
										<th class="py-2.5 px-3 text-end">{{ __("Qty") }}</th>
									</tr>
								</thead>
								<tbody>
									<tr
										v-for="m in historyDetail.materials"
										:key="`${m.item_code}|${m.batch_no}|${m.written_off}`"
										class="border-t border-gray-100"
									>
										<td class="py-2.5 px-3">
											<div class="text-gray-900">{{ m.item_name }}</div>
											<div v-if="m.written_off" class="text-xs font-medium text-red-700">
												{{ __("Written off") }}
											</div>
										</td>
										<td class="py-2.5 px-3 text-gray-700 tabular-nums">{{ m.batch_no || "—" }}</td>
										<td class="py-2.5 px-3 text-end font-semibold text-gray-900 tabular-nums whitespace-nowrap">
											{{ fmtQty(m.qty) }} {{ m.stock_uom }}
										</td>
									</tr>
								</tbody>
							</table>
						</div>
					</section>

					<section v-if="historyDetail.stock_entries.length">
						<h4 class="mb-2 text-sm font-semibold text-gray-900">{{ __("Stock entries") }}</h4>
						<ul class="divide-y divide-gray-100 border-y border-gray-100 text-sm">
							<li
								v-for="e in historyDetail.stock_entries"
								:key="e.name"
								class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2.5"
							>
								<span class="font-medium text-gray-900" :class="{ 'line-through': e.cancelled }">{{ e.name }}</span>
								<span class="text-gray-700">{{ __(e.purpose) }}</span>
								<span
									v-if="e.cancelled"
									class="px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-700"
								>
									{{ __("Cancelled") }}
								</span>
								<span class="ms-auto text-xs text-gray-700 tabular-nums">{{ e.posted_at }}</span>
							</li>
						</ul>
					</section>

					<section v-if="historyDetail.notes.length">
						<h4 class="mb-2 text-sm font-semibold text-gray-900">{{ __("Notes") }}</h4>
						<!-- Comment content is stored as HTML-escaped text; render as text, never v-html -->
						<p
							v-for="(n, i) in historyDetail.notes"
							:key="i"
							class="whitespace-pre-line text-sm text-gray-900"
						>
							{{ plainNote(n) }}
						</p>
					</section>

					<div v-if="historyDetail.reject_request" class="flex flex-wrap items-center gap-3 rounded-xl border border-gray-200 px-4 py-3">
						<span class="text-sm text-gray-700">{{ __("Loss report") }}</span>
						<span
							class="inline-flex px-2 py-0.5 rounded-full text-xs font-medium"
							:class="rejectStatusClass(historyDetail.reject_status)"
						>
							{{ rejectLabel(historyDetail.reject_status) }}
						</span>
						<Button
							v-if="historyDetail.reject_status === 'Draft'"
							variant="solid"
							theme="blue"
							class="ms-auto min-h-11 px-4 touch-manipulation"
							@click="emit('open-reject-request', historyDetail.reject_request)"
						>
							{{ __("Submit report") }}
						</Button>
					</div>
				</div>
			</template>

			<div v-if="errorMessage" class="mt-3 text-sm text-red-600">{{ errorMessage }}</div>
			</div>
		</template>
	</DialogHost>

	<!-- Confirm overlay for Close / Cancel production (above the app shell) -->
	<Transition name="fade">
		<div
			v-if="confirmAction"
			class="fixed inset-0 bg-black bg-opacity-50 z-[500] flex items-center justify-center"
			@click.self="confirmAction = null"
		>
			<div
				class="bg-white rounded-lg shadow-2xl max-w-md w-full mx-4 p-6"
				role="alertdialog"
				aria-modal="true"
				aria-labelledby="production-confirm-title"
			>
				<div class="flex items-start gap-4">
					<div class="flex-shrink-0">
						<div
							class="w-12 h-12 rounded-full bg-red-100 flex items-center justify-center"
						>
							<FeatherIcon name="alert-triangle" class="w-6 h-6 text-red-600" />
						</div>
					</div>
					<div class="flex-1">
						<h3 id="production-confirm-title" class="text-lg font-semibold text-gray-900 mb-2">
							{{
								confirmAction.type === "close"
									? __("Close Production")
									: __("Cancel Production")
							}}
						</h3>
						<p class="text-sm text-gray-700 mb-1">
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
						<p class="text-sm text-gray-700">
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
import { computed, nextTick, onUnmounted, ref, watch } from "vue"
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

const emit = defineEmits(["update:modelValue", "production-created", "open-reject-request"])

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

// view: "recipes" (list) | "detail" (locked recipe form) | "active" (running WOs)
// | "history" (finished WOs) | "history-detail" (one finished WO) | "overview"
const view = ref("recipes")
const loadingRecipes = ref(false)
const recipes = ref([])
const search = ref("")
const selectedRecipe = ref(null)
// Quantity to produce, in output units (not recipe batches)
const produceQty = ref(1)
const materialRows = ref([])
const errorMessage = ref("")

// Two-phase state
const productions = ref([])
const loadingActive = ref(false)
const starting = ref(false)
const acting = ref(false)
const finishingWo = ref(null)
const goodText = ref("")
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
const historyScroller = ref(null)
// History detail page: the clicked row shows at once, the posted docs follow
const historyRow = ref(null)
const historyDetail = ref(null)
const loadingDetail = ref(false)
const detailError = ref("")
let historyScrollTop = 0

// Overview, history list and export share one period
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
watch(period, () => {
	if (view.value === "overview") loadDashboard()
	if (view.value === "history") loadHistory()
})

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
// Up to two weeks each bar gets its value and day; longer ranges get too thin
const dailyLabelled = computed(() => (dashboard.value?.daily?.length || 0) <= 14)
// "2026-10-08" → "08/10": short, locale-free
const dayLabel = (day) => `${String(day).slice(8, 10)}/${String(day).slice(5, 7)}`
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

// Whatever is short of the remaining plan is loss — never typed by hand.
const finishLoss = computed(() => {
	const p = finishingProduction.value
	if (!p) return 0
	const remaining = Math.max(0, (p.qty || 0) - (p.produced_qty || 0))
	return Math.max(0, +(remaining - parseAmountInput(goodText.value)).toFixed(3))
})

const finishBatchRows = computed(() => {
	const ctx = finishContext.value
	if (!ctx) return []
	// a batched material with no live batches has nothing to pick — Auto will
	// fail server-side with the standard shortage message either way
	return (ctx.materials || []).filter((m) => m.has_batch_no && m.batches.length)
})

function finishNeeded(m) {
	const woQty = finishContext.value?.wo_qty
	const gross = parseAmountInput(goodText.value) + finishLoss.value
	if (!woQty) return m.required_qty
	return +((m.required_qty * gross) / woQty).toFixed(3)
}

// Output (in the item's stock UOM) the material stock covers; the material
// with the least is the limit. Whole-number UOMs (Pcs) round down to units,
// fractional ones (Kg, Litre) to 3 decimals. No materials = never limited.
function unitsOf(item, recipe) {
	if (!(item.qty > 0)) return Number.POSITIVE_INFINITY
	const raw = ((item.available_qty || 0) * (recipe.output_qty || 1)) / item.qty
	return recipe.output_whole === false
		? Math.floor(raw * 1000 + 1e-6) / 1000
		: Math.floor(raw + 1e-9)
}

function maxUnits(recipe) {
	const n = Math.min(...recipe.items.map((i) => unitsOf(i, recipe)))
	return Number.isFinite(n) ? Math.max(0, n) : 0
}

function limitingMaterial(recipe) {
	if (!recipe?.items.length) return null
	return recipe.items.reduce((min, i) =>
		unitsOf(i, recipe) < unitsOf(min, recipe) ? i : min,
	)
}

function outputUom(recipe) {
	return recipe?.output_uom || recipe?.production_item_name || ""
}

function capacityLabel(recipe) {
	return __("Stock is enough for {0}", [`${fmtQty(maxUnits(recipe))} ${outputUom(recipe)}`])
}

const selectedMax = computed(() =>
	selectedRecipe.value ? maxUnits(selectedRecipe.value) : 0,
)
const outputQty = computed(() => +(Number(produceQty.value) || 0).toFixed(4))
// Older payloads lack output_whole: treat as whole, the previous behaviour
const wholeOutput = computed(() => selectedRecipe.value?.output_whole !== false)
const minQty = computed(() => (wholeOutput.value ? 1 : 0))

function stepQty(delta) {
	const cur = Number(produceQty.value) || 0
	produceQty.value = wholeOutput.value
		? Math.max(1, Math.floor(cur) + delta)
		: Math.max(0, +(cur + delta).toFixed(3))
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
	produceQty.value = 1
	scaleRows()
	view.value = "detail"
}

function scaleRows() {
	const r = selectedRecipe.value
	if (!r) return
	const factor = (Number(produceQty.value) || 0) / (r.output_qty || 1)
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

watch(produceQty, scaleRows)

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
	const range = periodRange(period.value)
	loadingHistory.value = true
	try {
		const data = await call("pos_next.api.production.get_production_history", {
			pos_profile: props.posProfile,
			limit: HISTORY_PAGE,
			offset,
			from_date: range?.from,
			to_date: range?.to,
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

async function openHistoryDetail(p) {
	historyScrollTop = historyScroller.value?.scrollTop || 0
	historyRow.value = p
	view.value = "history-detail"
	await loadHistoryDetail()
}

async function loadHistoryDetail() {
	const wo = historyRow.value?.work_order
	if (!wo) return
	historyDetail.value = null
	detailError.value = ""
	loadingDetail.value = true
	try {
		const data = await call("pos_next.api.production.get_production_detail", { work_order: wo })
		// a fast Back-and-open of another row must not show the older answer
		if (historyRow.value?.work_order === wo) historyDetail.value = data
	} catch (err) {
		if (historyRow.value?.work_order === wo)
			detailError.value = errMsg(err, "Failed to load production detail")
	} finally {
		loadingDetail.value = false
	}
}

async function closeHistoryDetail() {
	view.value = "history"
	historyRow.value = null
	historyDetail.value = null
	await nextTick()
	if (historyScroller.value) historyScroller.value.scrollTop = historyScrollTop
}

// Comment content can carry markup from Desk; show its text only
function plainNote(html) {
	return new DOMParser().parseFromString(String(html || ""), "text/html").body.textContent.trim()
}

function startedStamp(p) {
	if (typeof p.started_at !== "number") return "—"
	const d = new Date(p.started_at * 1000)
	const pad = (n) => String(n).padStart(2, "0")
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
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
	if (posStatus === "Completed") return "bg-green-100 text-green-800"
	if (posStatus === "Partially Completed") return "bg-amber-100 text-amber-800"
	return "bg-gray-100 text-gray-700"
}

// Same palette as RejectRequestDialog's status chips, so the same reject
// status reads identically across the two dialogs.
function rejectStatusClass(status) {
	return (
		{
			Draft: "bg-orange-50 text-orange-700",
			"Pending Approval": "bg-blue-50 text-blue-700",
			"Awaiting Destruction": "bg-amber-50 text-amber-700",
			"Destruction Submitted": "bg-amber-50 text-amber-700",
			Completed: "bg-green-50 text-green-700",
		}[status] || "bg-gray-100 text-gray-700"
	)
}

// Workflow states (POS Reject Request.workflow_state)
function rejectLabel(status) {
	if (!status || status === "Draft") return __("Not submitted")
	if (status === "Pending Approval") return __("Waiting for HO")
	return __(status)
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
	finishNotes.value = ""
	// Prefill with what is left of the plan; the cashier edits from there.
	goodText.value = formatAmountInput(
		Math.max(0, (p.qty || 0) - (p.produced_qty || 0)),
	)
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

function onGoodInput(e) {
	goodText.value = formatAmountInput(e.target.value)
}

function cancelFinish() {
	finishingWo.value = null
	goodText.value = ""
	finishNotes.value = ""
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
			loss_qty: finishLoss.value,
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
