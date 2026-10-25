<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: dialogTitle, size: '4xl' }"
	>
		<template #body-content>
			<!-- 1. LIST VIEW -->
			<div v-if="view === 'list'" class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<div class="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5" role="group" :aria-label="__('Status')">
					<button
						v-for="tab in statusTabs"
						:key="tab.value"
						type="button"
						class="shrink-0 inline-flex items-center min-h-[44px] md:min-h-[40px] rounded-full border px-3.5 py-1 text-xs font-medium transition-colors touch-manipulation"
						:class="
							statusFilter === tab.value
								? 'border-gray-900 bg-gray-900 text-white'
								: 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:text-gray-900'
						"
						:aria-pressed="statusFilter === tab.value"
						@click="setStatusFilter(tab.value)"
					>
						{{ tab.label }}
					</button>
				</div>

				<div class="flex flex-wrap items-center justify-between gap-3">
					<p class="text-sm text-gray-500">
						{{ __("Report unsold or damaged stock for Head Office review") }}
					</p>
					<div class="ms-auto flex items-center gap-2">
						<RefreshButton :loading="loading" @click="loadList()" />
						<Button variant="solid" class="h-11 md:h-10 touch-manipulation" @click="openNew">
							<template #prefix>
								<FeatherIcon name="plus" class="h-4 w-4" />
							</template>
							{{ __("New Request") }}
						</Button>
					</div>
				</div>

				<div class="overflow-hidden rounded-xl border border-gray-200 bg-white">
					<!-- desktop column header -->
					<div
						class="hidden border-b border-gray-200 bg-gray-50 px-4 py-2.5 text-xs font-medium text-gray-500 md:grid md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_7rem_8rem_11rem] md:gap-x-3"
					>
						<span>{{ __("Request") }}</span>
						<span>{{ __("Purpose") }}</span>
						<span>{{ __("Incident Date") }}</span>
						<span class="text-end">{{ __("Amount") }}</span>
						<span>{{ __("Status") }}</span>
					</div>

					<div v-if="loading && !requests.length" class="divide-y divide-gray-100">
						<span class="sr-only">{{ __("Loading...") }}</span>
						<div v-for="n in 3" :key="n" class="flex animate-pulse gap-4 px-4 py-3">
							<div class="h-4 w-28 rounded bg-gray-100"></div>
							<div class="h-4 flex-1 rounded bg-gray-100"></div>
							<div class="h-4 w-20 rounded bg-gray-100"></div>
						</div>
					</div>
					<div v-else-if="!requests.length" class="px-4 py-8 text-center">
						<p class="text-sm font-medium text-gray-900">{{ __("No reject requests") }}</p>
						<p class="mt-1 text-xs text-gray-500">
							{{ statusFilter ? __("Try a different status") : __("Create one with New Request") }}
						</p>
					</div>
					<div v-else class="divide-y divide-gray-100">
						<button
							v-for="r in requests"
							:key="r.name"
							type="button"
							class="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-4 py-3 text-start transition-colors hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-none md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_7rem_8rem_11rem] md:gap-y-0"
							@click="openDetail(r.name)"
						>
							<span class="flex min-w-0 items-center gap-1.5">
								<span class="truncate text-sm font-semibold text-gray-900">{{ r.name }}</span>
								<span
									v-if="r.is_late"
									class="shrink-0 rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700 ring-1 ring-inset ring-red-600/20"
								>
									{{ __("Late") }}
								</span>
							</span>
							<span class="justify-self-end md:order-last md:justify-self-start">
								<span :class="statusPill(r.workflow_state)">{{ __(r.workflow_state) }}</span>
							</span>
							<span class="truncate text-sm text-gray-700">{{ __(r.purpose) }}</span>
							<span class="text-xs text-gray-500 md:text-sm md:text-gray-700">
								{{ formatDate(r.incident_date) }}
							</span>
							<span class="col-span-2 text-sm tabular-nums text-gray-900 md:col-span-1 md:text-end">
								{{ formatCurrency(r.total_amount) }}
							</span>
						</button>
					</div>
				</div>
				<div v-if="hasMore" class="flex justify-center">
					<Button variant="subtle" size="sm" :loading="loading" @click="loadList(false)">
						{{ __("Load More") }}
					</Button>
				</div>
			</div>

			<!-- 2. NEW REQUEST FORM -->
			<div v-else-if="view === 'new'" class="mx-auto flex w-full max-w-6xl flex-col gap-4">
				<div class="flex items-center gap-2">
					<button
						type="button"
						class="-ms-1 flex h-11 w-11 md:h-10 md:w-10 shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-900 touch-manipulation"
						:aria-label="__('Back')"
						:title="__('Back')"
						:disabled="submitting"
						@click="view = 'list'"
					>
						<FeatherIcon name="arrow-left" class="h-5 w-5" />
					</button>
					<h3 class="truncate text-base font-semibold text-gray-900">{{ __("New Reject Request") }}</h3>
					<Button
						class="ms-auto h-11 md:h-10 touch-manipulation"
						variant="solid"
						:loading="submitting"
						:disabled="!canSubmit"
						@click="submitNew"
					>
						{{ __("Submit") }}
					</Button>
				</div>

				<!-- Step Indicator -->
				<div data-testid="step-indicator" class="overflow-x-auto rounded-xl border border-gray-200 bg-white p-3 sm:p-4">
					<div class="flex items-center justify-between min-w-[480px]">
						<template v-for="(step, idx) in WORKFLOW_STEPS" :key="step">
							<div
								class="flex items-center gap-2 shrink-0"
								:data-step="step"
								:data-current="idx === 0 ? 'true' : undefined"
								:class="{
									'font-semibold text-blue-600': idx === 0,
									'text-gray-400': idx > 0,
								}"
							>
								<span
									class="flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium"
									:class="{
										'bg-blue-600 text-white': idx === 0,
										'bg-gray-100 text-gray-500': idx > 0,
									}"
								>
									{{ idx + 1 }}
								</span>
								<span class="text-xs sm:text-sm whitespace-nowrap">{{ __(step) }}</span>
							</div>
							<div
								v-if="idx < WORKFLOW_STEPS.length - 1"
								class="mx-2 h-0.5 flex-1 min-w-[1rem] bg-gray-200"
							></div>
						</template>
					</div>
				</div>

				<!-- Request Information -->
				<section class="rounded-xl border border-gray-200 bg-white p-4">
					<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Request Information") }}</h4>
					<div class="grid gap-3.5 md:grid-cols-2">
						<div>
							<label for="rr-purpose" class="mb-1 block text-xs font-medium text-gray-600">
								{{ __("Purpose") }} <span class="text-red-500">*</span>
							</label>
							<select
								id="rr-purpose"
								v-model="form.purpose"
								data-testid="purpose-select"
								class="w-full rounded-md border border-gray-300 bg-gray-50 px-3 py-2 text-sm text-gray-800 focus:bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
							>
								<option v-for="p in purposes" :key="p" :value="p">{{ __(p) }}</option>
							</select>
						</div>
						<div>
							<label for="rr-incident-date" class="mb-1 block text-xs font-medium text-gray-600">
								{{ __("Incident Date") }} <span class="text-red-500">*</span>
							</label>
							<input
								id="rr-incident-date"
								v-model="form.incident_date"
								type="date"
								class="w-full rounded-md border border-gray-300 bg-gray-50 px-3 py-2 text-sm text-gray-800 focus:bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
							/>
						</div>
						<div v-if="posProfile" class="col-span-1 md:col-span-2">
							<span class="mb-1 block text-xs font-medium text-gray-600">{{ __("Outlet") }}</span>
							<div class="w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm font-medium text-gray-700">
								{{ posProfile }}
							</div>
						</div>
					</div>
				</section>

				<!-- Items child table (ERPNext / Frappe grid feel) -->
				<section class="rounded-xl border border-gray-200 bg-white">
					<div class="flex items-center justify-between border-b border-gray-100 px-4 py-3">
						<h4 class="text-sm font-semibold text-gray-900">
							{{ __("Items") }} <span class="text-red-500">*</span>
						</h4>
					</div>

					<div>
						<div>
							<!-- Desktop & Tablet Table Header -->
							<div
								class="hidden border-b border-gray-200 bg-gray-50 px-3 py-2 text-xs font-medium text-gray-500 md:grid md:grid-cols-[2rem_1.75rem_minmax(0,1.5fr)_5.5rem_4.5rem_8rem_minmax(0,1fr)_2.25rem] md:items-center md:gap-x-2"
							>
								<div class="flex items-center justify-center">
									<input
										type="checkbox"
										data-testid="select-all-rows"
										:checked="isAllSelected"
										:indeterminate="isPartiallySelected"
										:aria-label="__('Select All')"
										class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
										@change="toggleSelectAll($event.target.checked)"
									/>
								</div>
								<span class="text-center">{{ __("No.") }}</span>
								<span>{{ __("Item") }}</span>
								<span>{{ __("UOM") }}</span>
								<span class="text-end">{{ __("Qty") }}</span>
								<span>{{ __("Production Date") }}</span>
								<span>{{ __("Reason") }}</span>
								<span></span>
							</div>

							<p
								v-if="!form.items.length"
								class="border-b border-gray-200 bg-gray-50/30 px-4 py-8 text-center text-sm text-gray-400"
							>
								{{ __("No Data") }}
							</p>

							<!-- Item Rows (Desktop & Tablet grid / Mobile stacked cards) -->
							<div data-testid="grid-rows" class="p-2 md:p-0">
								<div
									v-for="(row, idx) in form.items"
									:key="row.key"
									class="rounded-lg border border-gray-200 bg-white p-3.5 shadow-xs space-y-2.5 mb-3 last:mb-0 md:mb-0 md:space-y-0 md:border-0 md:border-b md:border-gray-100 md:p-0 md:rounded-none md:grid md:grid-cols-[2rem_1.75rem_minmax(0,1.5fr)_5.5rem_4.5rem_8rem_minmax(0,1fr)_2.25rem] md:items-center md:gap-x-2 md:px-3 md:py-1.5 md:hover:bg-gray-50/50"
								>
									<!-- Checkbox + Row # (Mobile card header / Desktop & Tablet cols 1 & 2) -->
									<div class="flex items-center justify-between pb-2 border-b border-gray-100 md:border-b-0 md:pb-0 md:contents">
										<div class="flex items-center gap-2 md:justify-center">
											<input
												type="checkbox"
												v-model="row.selected"
												data-testid="row-checkbox"
												:aria-label="`Select row ${idx + 1}`"
												class="h-5 w-5 md:h-4 md:w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
											/>
											<span class="text-xs font-semibold text-gray-700 md:hidden">{{ __("Row #{0}", [idx + 1]) }}</span>
										</div>
										<span class="hidden text-xs tabular-nums text-center text-gray-400 md:block">{{ idx + 1 }}</span>
									</div>

									<!-- Item column -->
									<div class="relative min-w-0">
										<span class="mb-1 block text-xs font-medium text-gray-500 md:hidden">{{ __("Item") }}</span>
										<template v-if="row.item_code">
											<div
												class="group flex min-h-[40px] md:min-h-0 items-center justify-between rounded border border-gray-200 bg-gray-50/70 px-2.5 py-1.5 md:py-1 hover:border-gray-300 hover:bg-gray-100/70 cursor-pointer transition-colors"
												:title="__('Click to change item')"
												@click="editRowItem(row, idx)"
											>
												<div class="min-w-0 pr-1">
													<p class="truncate text-sm font-medium text-gray-900">{{ row.item_name || row.item_code }}</p>
													<p v-if="row.item_code !== row.item_name" class="truncate text-xs text-gray-400">
														{{ row.item_code }}
													</p>
												</div>
												<FeatherIcon
													name="edit-2"
													class="h-3.5 w-3.5 shrink-0 text-gray-400 opacity-60 md:opacity-0 md:group-hover:opacity-100 transition-opacity"
												/>
											</div>
										</template>
										<template v-else>
											<input
												v-model="row.search"
												type="text"
												data-testid="row-item-input"
												:data-cell="`${idx}-item`"
												:placeholder="__('Search item...')"
												:aria-label="__('Item')"
												class="h-10 min-h-[40px] md:h-8 md:min-h-0 w-full rounded-md border border-dashed border-gray-300 bg-gray-50/50 px-2.5 py-1.5 text-sm focus:border-solid focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30"
												@input="searchRow(row)"
												@keyup.enter="onItemEnter(row, idx)"
												@keyup.escape="closeResults(row)"
												@blur="blurRow(row)"
											/>
											<div
												v-if="row.results.length"
												class="absolute inset-x-0 top-full z-20 mt-1 max-h-56 overflow-y-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
											>
												<button
													v-for="i in row.results"
													:key="i.item_code"
													type="button"
													data-testid="row-item-result"
													class="flex min-h-[40px] w-full items-center justify-between gap-3 px-3 py-2 text-start text-sm hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-none"
													@click="pickItem(row, i); focusCell(idx, 'uom')"
												>
													<span class="truncate font-medium text-gray-900">{{ i.item_name || i.item_code }}</span>
													<span v-if="i.item_name && i.item_name !== i.item_code" class="shrink-0 text-xs text-gray-400">
														{{ i.item_code }}
													</span>
												</button>
											</div>
										</template>
										<p v-if="rowDuplicateError(row)" class="mt-0.5 text-xs text-red-600">
											{{ rowDuplicateError(row) }}
										</p>
									</div>

									<!-- Mobile 2-column group for UOM + Qty -->
									<div class="grid grid-cols-2 gap-2 md:contents">
										<!-- UOM -->
										<div class="min-w-0">
											<span class="mb-1 block text-xs font-medium text-gray-500 md:hidden">{{ __("UOM") }}</span>
											<select
												v-if="row.item_code"
												v-model="row.uom"
												data-testid="uom-select"
												:data-cell="`${idx}-uom`"
												:aria-label="`${row.item_name || row.item_code} — ${__('UOM')}`"
												class="h-10 min-h-[40px] md:h-8 md:min-h-0 w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-sm focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30"
												@keydown.enter.prevent="focusCell(idx, 'qty')"
											>
												<option v-for="u in uomOptionsOf(row)" :key="u" :value="u">{{ u }}</option>
											</select>
											<span v-else class="text-xs text-gray-400 md:block">—</span>
										</div>

										<!-- Qty -->
										<div class="min-w-0">
											<span class="mb-1 block text-xs font-medium text-gray-500 md:hidden">{{ __("Qty") }}</span>
											<input
												v-if="row.item_code"
												v-model.number="row.qty"
												type="number"
												min="0"
												step="any"
												inputmode="decimal"
												data-testid="qty-input"
												:data-cell="`${idx}-qty`"
												:aria-label="`${row.item_name || row.item_code} — ${__('Qty')}`"
												class="h-10 min-h-[40px] md:h-8 md:min-h-0 w-full rounded-md border px-2 py-1 text-end text-sm tabular-nums focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30"
												:class="row.qty > 0 ? 'border-gray-300 bg-gray-50' : 'border-red-400 bg-red-50'"
												@keydown.enter.prevent="focusCell(idx, 'prod-date')"
											/>
											<span v-else class="text-xs text-gray-400 md:block md:text-end">—</span>
										</div>
									</div>

									<!-- Production Date -->
									<div class="min-w-0">
										<span class="mb-1 block text-xs font-medium text-gray-500 md:hidden">{{ __("Production Date") }}</span>
										<input
											v-if="row.item_code"
											v-model="row.production_date"
											type="date"
											:max="form.incident_date"
											data-testid="prod-date-input"
											:data-cell="`${idx}-prod-date`"
											:aria-label="`${row.item_name || row.item_code} — ${__('Production Date')}`"
											class="h-10 min-h-[40px] md:h-8 md:min-h-0 w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-xs sm:text-sm focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30"
											@keydown.enter.prevent="focusCell(idx, 'reason')"
										/>
										<span v-else class="text-xs text-gray-400 md:block">—</span>
									</div>

									<!-- Reason -->
									<div class="min-w-0">
										<span class="mb-1 block text-xs font-medium text-gray-500 md:hidden">{{ __("Reason") }}</span>
										<input
											v-if="row.item_code"
											v-model="row.reject_reason"
											type="text"
											data-testid="item-reason-input"
											:data-cell="`${idx}-reason`"
											:placeholder="__('Reason (optional)')"
											:aria-label="`${row.item_name || row.item_code} — ${__('Reason')}`"
											class="h-10 min-h-[40px] md:h-8 md:min-h-0 w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-xs sm:text-sm focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30"
											@keydown.enter.prevent="onReasonEnter(idx)"
										/>
										<span v-else class="text-xs text-gray-400 md:block">—</span>
									</div>

									<!-- Remove button (full width tap target on mobile, compact icon on desktop/tablet) -->
									<div class="flex md:justify-center">
										<button
											type="button"
											data-testid="remove-row"
											class="flex h-10 w-full min-h-[40px] items-center justify-center gap-1.5 rounded-md border border-gray-200 bg-gray-50 text-xs font-medium text-red-600 hover:bg-red-50 hover:border-red-200 md:h-8 md:w-8 md:min-h-0 md:border-0 md:bg-transparent md:text-gray-400 md:hover:bg-red-50 md:hover:text-red-600"
											:aria-label="__('Remove item')"
											:title="__('Remove item')"
											@click="form.items.splice(idx, 1)"
										>
											<FeatherIcon name="trash-2" class="h-4 w-4" />
											<span class="md:hidden">{{ __("Remove Item") }}</span>
										</button>
									</div>
								</div>
							</div>
						</div>
					</div>

					<!-- Add Row & Delete Selected footer -->
					<div class="flex flex-wrap items-center justify-between gap-3 rounded-b-xl border-t border-gray-200 bg-gray-50/40 px-4 py-2.5">
						<div class="flex items-center gap-2">
							<Button variant="subtle" size="sm" data-testid="add-row" @click="addRow">
								<template #prefix>
									<FeatherIcon name="plus" class="h-4 w-4" />
								</template>
								{{ __("Add Row") }}
							</Button>
							<Button
								v-if="selectedCount > 0"
								variant="subtle"
								theme="red"
								size="sm"
								data-testid="delete-selected-rows"
								@click="deleteSelectedRows"
							>
								<template #prefix>
									<FeatherIcon name="trash-2" class="h-4 w-4" />
								</template>
								{{ __("Delete") }} ({{ selectedCount }})
							</Button>
						</div>
						<p v-if="!form.items.length" class="text-xs text-gray-500">
							{{ __("Click Add Row to add items for reject.") }}
						</p>
					</div>
				</section>

				<div class="grid gap-4 md:grid-cols-[minmax(0,1fr)_18rem]">
					<section class="order-2 rounded-xl border border-gray-200 bg-white p-4 md:order-1">
						<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Reason & Evidence") }}</h4>
						<label for="rr-reason" class="mb-1 block text-xs font-medium text-gray-700">
							{{ __("Reason") }} <span class="text-red-500">*</span>
						</label>
						<textarea
							id="rr-reason"
							v-model="form.reason"
							rows="3"
							class="w-full resize-y rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
						/>
						<div class="mt-3">
							<PurchaseAttachments
								editable
								:pending="form.files"
								:label="__('Evidence Photos (1-5)')"
								@add="addEvidence(form.files, $event)"
								@remove="(row) => form.files.splice(form.files.indexOf(row), 1)"
							/>
						</div>
					</section>

					<!-- Checklist summary -->
					<section class="order-1 rounded-xl border border-gray-200 bg-gray-50 p-4 md:order-2 md:self-start" data-testid="new-summary">
						<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Before you submit") }}</h4>
						<ul class="space-y-2 text-sm">
							<li v-for="c in newChecklist" :key="c.label" class="flex items-center justify-between gap-3">
								<span class="flex items-center gap-2" :class="c.ok ? 'text-gray-900' : 'text-gray-500'">
									<FeatherIcon
										:name="c.ok ? 'check-circle' : 'circle'"
										class="h-4 w-4 shrink-0"
										:class="c.ok ? 'text-green-600' : 'text-gray-300'"
									/>
									{{ c.label }}
								</span>
								<span class="tabular-nums text-xs text-gray-500">{{ c.value }}</span>
							</li>
						</ul>
					</section>
				</div>
			</div>

			<!-- 3. DETAIL VIEW -->
			<div v-else class="mx-auto flex w-full max-w-6xl flex-col gap-4">
				<div v-if="loadingDetail" class="py-10 text-center text-sm text-gray-500">
					{{ __("Loading...") }}
				</div>
				<template v-else-if="detail">
					<!-- Header -->
					<div class="flex flex-wrap items-center gap-2">
						<button
							type="button"
							class="-ms-1 flex h-11 w-11 md:h-10 md:w-10 shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-900 touch-manipulation"
							:aria-label="__('Back')"
							:title="__('Back')"
							@click="backToList"
						>
							<FeatherIcon name="arrow-left" class="h-5 w-5" />
						</button>
						<h3 class="truncate text-base font-semibold text-gray-900">{{ detail.name }}</h3>
						<span :class="statusPill(detail.workflow_state)">{{ __(detail.workflow_state) }}</span>
						<span
							v-if="detail.is_late"
							class="rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700 ring-1 ring-inset ring-red-600/20"
						>
							{{ __("Late") }}
						</span>
						<div class="ms-auto flex items-center gap-2">
							<!-- Draft: can submit/resubmit -->
							<Button
								v-if="detail.workflow_state === 'Draft'"
								variant="solid"
								class="h-11 md:h-10 touch-manipulation"
								:loading="submitting"
								:disabled="!canSubmitDetail"
								@click="submitDetail"
							>
								{{ __("Submit") }}
							</Button>

							<!-- Pending Approval: Withdraw button -->
							<Button
								v-else-if="detail.workflow_state === 'Pending Approval' && detail.actions?.includes('Withdraw')"
								variant="subtle"
								theme="red"
								class="h-11 md:h-10 touch-manipulation"
								data-testid="withdraw-button"
								:loading="acting"
								@click="doWithdraw"
							>
								{{ __("Withdraw") }}
							</Button>

							<!-- Awaiting Destruction: Send Proof button -->
							<Button
								v-else-if="detail.workflow_state === 'Awaiting Destruction' && detail.actions?.includes('Send Proof')"
								variant="solid"
								class="h-11 md:h-10 touch-manipulation"
								data-testid="send-proof"
								:loading="acting"
								:disabled="!canSendProof"
								@click="doSendProof"
							>
								{{ __("Send Proof") }}
							</Button>
						</div>
					</div>

					<!-- Step Indicator -->
					<div data-testid="step-indicator" class="overflow-x-auto rounded-xl border border-gray-200 bg-white p-3 sm:p-4">
						<div class="flex items-center justify-between min-w-[500px]">
							<template v-for="(step, idx) in WORKFLOW_STEPS" :key="step">
								<div
									class="flex items-center gap-2 shrink-0"
									:data-step="step"
									:data-current="detail.workflow_state === step ? 'true' : undefined"
									:class="{
										'font-semibold text-blue-600': detail.workflow_state === step,
										'font-medium text-gray-900': isStepPassed(step),
										'text-gray-400': !isStepPassed(step) && detail.workflow_state !== step,
									}"
								>
									<span
										class="flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium"
										:class="{
											'bg-blue-600 text-white': detail.workflow_state === step,
											'bg-green-100 text-green-700': isStepPassed(step),
											'bg-gray-100 text-gray-500': !isStepPassed(step) && detail.workflow_state !== step,
										}"
									>
										<FeatherIcon v-if="isStepPassed(step)" name="check" class="h-3.5 w-3.5" />
										<span v-else>{{ idx + 1 }}</span>
									</span>
									<span class="text-xs sm:text-sm whitespace-nowrap">{{ __(step) }}</span>
								</div>
								<div
									v-if="idx < WORKFLOW_STEPS.length - 1"
									class="mx-2 h-0.5 flex-1 min-w-[1rem] bg-gray-200"
									:class="{
										'bg-green-500': isStepPassed(WORKFLOW_STEPS[idx + 1]) || detail.workflow_state === WORKFLOW_STEPS[idx + 1],
									}"
								></div>
							</template>
						</div>
					</div>

					<!-- Return Reason Banner (Draft returned by HO) -->
					<div
						v-if="detail.workflow_state === 'Draft' && detail.return_reason"
						data-testid="return-reason-banner"
						class="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800"
					>
						<div class="flex items-start gap-2">
							<FeatherIcon name="alert-triangle" class="h-5 w-5 shrink-0 text-amber-600 mt-0.5" />
							<div>
								<h4 class="font-semibold text-amber-900">{{ __("Returned by Head Office") }}</h4>
								<p class="mt-1 text-amber-800">{{ detail.return_reason }}</p>
							</div>
						</div>
					</div>

					<!-- Awaiting Destruction Notice + Berita Acara -->
					<div
						v-if="detail.workflow_state === 'Awaiting Destruction'"
						class="rounded-xl border border-blue-200 bg-blue-50 p-4"
					>
						<div class="flex flex-wrap items-center justify-between gap-3">
							<div>
								<h4 class="font-semibold text-blue-900">{{ __("Ready for Destruction") }}</h4>
								<p class="mt-0.5 text-xs text-blue-700">
									{{ __("Download Berita Acara, perform destruction, and upload video/photos proof.") }}
								</p>
							</div>
							<a
								:href="beritaAcaraUrl"
								target="_blank"
								rel="noopener"
								data-testid="download-berita-acara"
								class="inline-flex items-center gap-1.5 rounded-lg border border-blue-300 bg-white px-3 py-2 text-sm font-medium text-blue-700 shadow-sm hover:bg-blue-50"
							>
								<FeatherIcon name="download" class="h-4 w-4" />
								{{ __("Download Berita Acara") }}
							</a>
						</div>
					</div>

					<!-- Main content grid -->
					<div class="grid gap-4 md:grid-cols-[minmax(0,1fr)_18rem]">
						<div class="flex min-w-0 flex-col gap-4">
							<!-- Draft: editable Request Information (purpose & incident date) -->
							<section v-if="detail.workflow_state === 'Draft' && !detail.is_system_generated" class="rounded-xl border border-gray-200 bg-white p-4">
								<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Request Information") }}</h4>
								<div class="grid gap-3.5 md:grid-cols-2">
									<div>
										<label for="rr-detail-purpose" class="mb-1 block text-xs font-medium text-gray-600">
											{{ __("Purpose") }} <span class="text-red-500">*</span>
										</label>
										<select
											id="rr-detail-purpose"
											v-model="detail.purpose"
											data-testid="detail-purpose-select"
											class="w-full rounded-md border border-gray-300 bg-gray-50 px-3 py-2 text-sm text-gray-800 focus:bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
										>
											<option v-for="p in purposes" :key="p" :value="p">{{ __(p) }}</option>
										</select>
									</div>
									<div>
										<label for="rr-detail-incident-date" class="mb-1 block text-xs font-medium text-gray-600">
											{{ __("Incident Date") }} <span class="text-red-500">*</span>
										</label>
										<input
											id="rr-detail-incident-date"
											v-model="detail.incident_date"
											type="date"
											data-testid="detail-incident-date-input"
											class="w-full rounded-md border border-gray-300 bg-gray-50 px-3 py-2 text-sm text-gray-800 focus:bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
										/>
									</div>
									<div v-if="detail.pos_profile || posProfile" class="col-span-1 md:col-span-2">
										<span class="mb-1 block text-xs font-medium text-gray-600">{{ __("Outlet") }}</span>
										<div class="w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm font-medium text-gray-700">
											{{ detail.pos_profile || posProfile }}
										</div>
									</div>
								</div>
							</section>

							<!-- Draft: editable Items child table -->
							<section v-if="detail.workflow_state === 'Draft' && !detail.is_system_generated" class="rounded-xl border border-gray-200 bg-white">
								<div class="flex items-center justify-between border-b border-gray-100 px-4 py-3">
									<h4 class="text-sm font-semibold text-gray-900">
										{{ __("Items") }} <span class="text-red-500">*</span>
									</h4>
								</div>

								<div>
									<div>
										<!-- Desktop & Tablet Table Header -->
										<div
											class="hidden border-b border-gray-200 bg-gray-50 px-3 py-2 text-xs font-medium text-gray-500 md:grid md:grid-cols-[2rem_1.75rem_minmax(0,1.5fr)_5.5rem_4.5rem_8rem_minmax(0,1fr)_2.25rem] md:items-center md:gap-x-2"
										>
											<div class="flex items-center justify-center">
												<input
													type="checkbox"
													data-testid="select-all-rows"
													:checked="isAllSelected"
													:indeterminate="isPartiallySelected"
													:aria-label="__('Select All')"
													class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
													@change="toggleSelectAll($event.target.checked)"
												/>
											</div>
											<span class="text-center">{{ __("No.") }}</span>
											<span>{{ __("Item") }}</span>
											<span>{{ __("UOM") }}</span>
											<span class="text-end">{{ __("Qty") }}</span>
											<span>{{ __("Production Date") }}</span>
											<span>{{ __("Reason") }}</span>
											<span></span>
										</div>

										<p
											v-if="!form.items.length"
											class="border-b border-gray-200 bg-gray-50/30 px-4 py-8 text-center text-sm text-gray-400"
										>
											{{ __("No Data") }}
										</p>

										<!-- Item Rows (Desktop & Tablet grid / Mobile stacked cards) -->
										<div data-testid="grid-rows" class="p-2 md:p-0">
											<div
												v-for="(row, idx) in form.items"
												:key="row.key"
												class="rounded-lg border border-gray-200 bg-white p-3.5 shadow-xs space-y-2.5 mb-3 last:mb-0 md:mb-0 md:space-y-0 md:border-0 md:border-b md:border-gray-100 md:p-0 md:rounded-none md:grid md:grid-cols-[2rem_1.75rem_minmax(0,1.5fr)_5.5rem_4.5rem_8rem_minmax(0,1fr)_2.25rem] md:items-center md:gap-x-2 md:px-3 md:py-1.5 md:hover:bg-gray-50/50"
											>
												<!-- Checkbox + Row # (Mobile card header / Desktop & Tablet cols 1 & 2) -->
												<div class="flex items-center justify-between pb-2 border-b border-gray-100 md:border-b-0 md:pb-0 md:contents">
													<div class="flex items-center gap-2 md:justify-center">
														<input
															type="checkbox"
															v-model="row.selected"
															data-testid="row-checkbox"
															:aria-label="`Select row ${idx + 1}`"
															class="h-5 w-5 md:h-4 md:w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
														/>
														<span class="text-xs font-semibold text-gray-700 md:hidden">{{ __("Row #{0}", [idx + 1]) }}</span>
													</div>
													<span class="hidden text-xs tabular-nums text-center text-gray-400 md:block">{{ idx + 1 }}</span>
												</div>

												<!-- Item column -->
												<div class="relative min-w-0">
													<span class="mb-1 block text-xs font-medium text-gray-500 md:hidden">{{ __("Item") }}</span>
													<template v-if="row.item_code">
														<div
															class="group flex min-h-[40px] md:min-h-0 items-center justify-between rounded border border-gray-200 bg-gray-50/70 px-2.5 py-1.5 md:py-1 hover:border-gray-300 hover:bg-gray-100/70 cursor-pointer transition-colors"
															:title="__('Click to change item')"
															@click="editRowItem(row, idx)"
														>
															<div class="min-w-0 pr-1">
																<p class="truncate text-sm font-medium text-gray-900">{{ row.item_name || row.item_code }}</p>
																<p v-if="row.item_code !== row.item_name" class="truncate text-xs text-gray-400">
																	{{ row.item_code }}
																</p>
															</div>
															<FeatherIcon
																name="edit-2"
																class="h-3.5 w-3.5 shrink-0 text-gray-400 opacity-60 md:opacity-0 md:group-hover:opacity-100 transition-opacity"
															/>
														</div>
													</template>
													<template v-else>
														<input
															v-model="row.search"
															type="text"
															data-testid="row-item-input"
															:data-cell="`${idx}-item`"
															:placeholder="__('Search item...')"
															:aria-label="__('Item')"
															class="h-10 min-h-[40px] md:h-8 md:min-h-0 w-full rounded-md border border-dashed border-gray-300 bg-gray-50/50 px-2.5 py-1.5 text-sm focus:border-solid focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30"
															@input="searchRow(row)"
															@keyup.enter="onItemEnter(row, idx)"
															@keyup.escape="closeResults(row)"
															@blur="blurRow(row)"
														/>
														<div
															v-if="row.results.length"
															class="absolute inset-x-0 top-full z-20 mt-1 max-h-56 overflow-y-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
														>
															<button
																v-for="i in row.results"
																:key="i.item_code"
																type="button"
																data-testid="row-item-result"
																class="flex min-h-[40px] w-full items-center justify-between gap-3 px-3 py-2 text-start text-sm hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-none"
																@click="pickItem(row, i); focusCell(idx, 'uom')"
															>
																<span class="truncate font-medium text-gray-900">{{ i.item_name || i.item_code }}</span>
																<span v-if="i.item_name && i.item_name !== i.item_code" class="shrink-0 text-xs text-gray-400">
																	{{ i.item_code }}
																</span>
															</button>
														</div>
													</template>
													<p v-if="rowDuplicateError(row)" class="mt-0.5 text-xs text-red-600">
														{{ rowDuplicateError(row) }}
													</p>
												</div>

												<!-- Mobile 2-column group for UOM + Qty -->
												<div class="grid grid-cols-2 gap-2 md:contents">
													<!-- UOM -->
													<div class="min-w-0">
														<span class="mb-1 block text-xs font-medium text-gray-500 md:hidden">{{ __("UOM") }}</span>
														<select
															v-if="row.item_code"
															v-model="row.uom"
															data-testid="uom-select"
															:data-cell="`${idx}-uom`"
															:aria-label="`${row.item_name || row.item_code} — ${__('UOM')}`"
															class="h-10 min-h-[40px] md:h-8 md:min-h-0 w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-sm focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30"
															@keydown.enter.prevent="focusCell(idx, 'qty')"
														>
															<option v-for="u in uomOptionsOf(row)" :key="u" :value="u">{{ u }}</option>
														</select>
														<span v-else class="text-xs text-gray-400 md:block">—</span>
													</div>

													<!-- Qty -->
													<div class="min-w-0">
														<span class="mb-1 block text-xs font-medium text-gray-500 md:hidden">{{ __("Qty") }}</span>
														<input
															v-if="row.item_code"
															v-model.number="row.qty"
															type="number"
															min="0"
															step="any"
															inputmode="decimal"
															data-testid="qty-input"
															:data-cell="`${idx}-qty`"
															:aria-label="`${row.item_name || row.item_code} — ${__('Qty')}`"
															class="h-10 min-h-[40px] md:h-8 md:min-h-0 w-full rounded-md border px-2 py-1 text-end text-sm tabular-nums focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30"
															:class="row.qty > 0 ? 'border-gray-300 bg-gray-50' : 'border-red-400 bg-red-50'"
															@keydown.enter.prevent="focusCell(idx, 'prod-date')"
														/>
														<span v-else class="text-xs text-gray-400 md:block md:text-end">—</span>
													</div>
												</div>

												<!-- Production Date -->
												<div class="min-w-0">
													<span class="mb-1 block text-xs font-medium text-gray-500 md:hidden">{{ __("Production Date") }}</span>
													<input
														v-if="row.item_code"
														v-model="row.production_date"
														type="date"
														:max="detail.incident_date || form.incident_date"
														data-testid="prod-date-input"
														:data-cell="`${idx}-prod-date`"
														:aria-label="`${row.item_name || row.item_code} — ${__('Production Date')}`"
														class="h-10 min-h-[40px] md:h-8 md:min-h-0 w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-xs sm:text-sm focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30"
														@keydown.enter.prevent="focusCell(idx, 'reason')"
													/>
													<span v-else class="text-xs text-gray-400 md:block">—</span>
												</div>

												<!-- Reason -->
												<div class="min-w-0">
													<span class="mb-1 block text-xs font-medium text-gray-500 md:hidden">{{ __("Reason") }}</span>
													<input
														v-if="row.item_code"
														v-model="row.reject_reason"
														type="text"
														data-testid="item-reason-input"
														:data-cell="`${idx}-reason`"
														:placeholder="__('Reason (optional)')"
														:aria-label="`${row.item_name || row.item_code} — ${__('Reason')}`"
														class="h-10 min-h-[40px] md:h-8 md:min-h-0 w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-xs sm:text-sm focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30"
														@keydown.enter.prevent="onReasonEnter(idx)"
													/>
													<span v-else class="text-xs text-gray-400 md:block">—</span>
												</div>

												<!-- Remove button -->
												<div class="flex md:justify-center">
													<button
														type="button"
														data-testid="remove-row"
														class="flex h-10 w-full min-h-[40px] items-center justify-center gap-1.5 rounded-md border border-gray-200 bg-gray-50 text-xs font-medium text-red-600 hover:bg-red-50 hover:border-red-200 md:h-8 md:w-8 md:min-h-0 md:border-0 md:bg-transparent md:text-gray-400 md:hover:bg-red-50 md:hover:text-red-600"
														:aria-label="__('Remove item')"
														:title="__('Remove item')"
														@click="form.items.splice(idx, 1)"
													>
														<FeatherIcon name="trash-2" class="h-4 w-4" />
														<span class="md:hidden">{{ __("Remove Item") }}</span>
													</button>
												</div>
											</div>
										</div>
									</div>
								</div>

								<!-- Add Row & Delete Selected footer -->
								<div class="flex flex-wrap items-center justify-between gap-3 rounded-b-xl border-t border-gray-200 bg-gray-50/40 px-4 py-2.5">
									<div class="flex items-center gap-2">
										<Button variant="subtle" size="sm" data-testid="add-row" @click="addRow">
											<template #prefix>
												<FeatherIcon name="plus" class="h-4 w-4" />
											</template>
											{{ __("Add Row") }}
										</Button>
										<Button
											v-if="selectedCount > 0"
											variant="subtle"
											theme="red"
											size="sm"
											data-testid="delete-selected-rows"
											@click="deleteSelectedRows"
										>
											<template #prefix>
												<FeatherIcon name="trash-2" class="h-4 w-4" />
											</template>
											{{ __("Delete") }} ({{ selectedCount }})
										</Button>
									</div>
									<p v-if="!form.items.length" class="text-xs text-gray-500">
										{{ __("Click Add Row to add items for reject.") }}
									</p>
								</div>
							</section>

							<!-- Non-Draft or system-generated: read-only Items Table -->
							<section v-else class="overflow-hidden rounded-xl border border-gray-200 bg-white">
								<h4 class="px-4 pb-2 pt-4 text-sm font-semibold text-gray-900">{{ __("Items") }}</h4>
								<div
									class="hidden border-y border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500 md:grid md:grid-cols-[minmax(0,1.5fr)_4.5rem_4rem_6.5rem_minmax(0,1fr)_6rem] md:gap-3"
								>
									<span>{{ __("Item") }}</span>
									<span>{{ __("UOM") }}</span>
									<span class="text-end">{{ __("Qty") }}</span>
									<span>{{ __("Production Date") }}</span>
									<span>{{ __("Reason") }}</span>
									<span class="text-end">{{ __("Amount") }}</span>
								</div>
								<div
									v-for="(row, idx) in detail.items"
									:key="row.name || idx"
									class="border-b border-gray-100 p-3 last:border-b-0 md:grid md:grid-cols-[minmax(0,1.5fr)_4.5rem_4rem_6.5rem_minmax(0,1fr)_6rem] md:items-center md:gap-3 md:px-4 md:py-2 md:text-sm"
								>
									<div class="min-w-0">
										<p class="truncate font-medium text-gray-900">{{ row.item_name || row.item_code }}</p>
										<p v-if="row.item_name && row.item_name !== row.item_code" class="truncate text-xs text-gray-400">
											{{ row.item_code }}
										</p>
									</div>
									<div class="mt-1 flex items-center justify-between text-xs text-gray-600 md:mt-0 md:text-sm">
										<span class="text-gray-500 md:hidden">{{ __("UOM") }}:</span>
										<span>{{ row.uom || row.stock_uom || "—" }}</span>
									</div>
									<div class="mt-1 flex items-center justify-between md:mt-0 md:justify-end">
										<span class="text-xs text-gray-500 md:hidden">{{ __("Qty") }}:</span>
										<span class="tabular-nums font-medium text-gray-900" data-testid="item-qty">{{ row.qty }}</span>
									</div>
									<div class="mt-1 flex items-center justify-between text-xs text-gray-600 md:mt-0 md:text-sm">
										<span class="text-gray-500 md:hidden">{{ __("Production Date") }}:</span>
										<span>{{ row.production_date ? formatDate(row.production_date) : "—" }}</span>
									</div>
									<div class="mt-1 text-xs text-gray-600 md:mt-0 md:text-sm">
										<span class="text-gray-500 md:hidden">{{ __("Reason") }}: </span>
										<span class="italic text-gray-700">{{ row.reject_reason || "—" }}</span>
									</div>
									<div class="mt-1 flex items-center justify-between md:mt-0 md:justify-end">
										<span class="text-xs text-gray-500 md:hidden">{{ __("Amount") }}:</span>
										<span class="tabular-nums text-gray-700">{{ formatCurrency(row.amount) }}</span>
									</div>
								</div>
								<p v-if="!detail.items?.length" class="px-4 py-4 text-center text-sm text-gray-500">
									{{ __("No items") }}
								</p>
							</section>

							<!-- Draft: crew-editable reason + video link + photos -->
							<section v-if="detail.workflow_state === 'Draft'" class="rounded-xl border border-gray-200 bg-white p-4">
								<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Reason & Evidence") }}</h4>
								<label for="rr-detail-reason" class="mb-1 block text-xs font-medium text-gray-700">
									{{ __("Reason") }} <span class="text-red-500">*</span>
								</label>
								<textarea
									id="rr-detail-reason"
									v-model="detail.reason"
									rows="3"
									class="w-full resize-y rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
								/>
								<div class="mt-3 flex flex-col gap-2">
									<PurchaseAttachments :attached="detail.evidence" :label="__('Evidence Photos (1-5)')" />
									<PurchaseAttachments
										editable
										:pending="newEvidence"
										:label="__('Add Evidence Photos')"
										@add="addEvidence(newEvidence, $event)"
										@remove="(row) => newEvidence.splice(newEvidence.indexOf(row), 1)"
									/>
								</div>
							</section>

							<!-- Non-Draft: read-only reason, video link, and evidence photos -->
							<section v-else class="rounded-xl border border-gray-200 bg-white p-4">
								<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Reason & Evidence") }}</h4>
								<dl class="space-y-2 text-sm">
									<div>
										<dt class="text-xs text-gray-500">{{ __("Reason") }}</dt>
										<dd class="whitespace-pre-line text-gray-900">{{ detail.reason || "—" }}</dd>
									</div>
									<div v-if="detail.video_link && videoLinkOk">
										<dt class="text-xs text-gray-500">{{ __("Video Link") }}</dt>
										<dd>
											<a
												:href="detail.video_link"
												target="_blank"
												rel="noopener"
												class="break-all text-blue-600 hover:underline"
											>
												{{ detail.video_link }}
											</a>
										</dd>
									</div>
								</dl>
								<div class="mt-3">
									<PurchaseAttachments :attached="detail.evidence" :label="__('Evidence Photos')" />
								</div>
							</section>

							<!-- Awaiting Destruction: Video link input + destruction photos upload -->
							<section
								v-if="detail.workflow_state === 'Awaiting Destruction'"
								class="rounded-xl border border-gray-200 bg-white p-4"
							>
								<h4 class="mb-1 text-sm font-semibold text-gray-900">{{ __("Destruction Proof") }}</h4>
								<p class="mb-3 text-xs text-gray-500">
									{{ __("Upload photos or provide a video link showing stock destruction, then click Send Proof.") }}
								</p>

								<label for="rr-awaiting-video" class="mb-1 block text-xs font-medium text-gray-700">
									{{ __("Destruction Video Link") }}
								</label>
								<input
									id="rr-awaiting-video"
									v-model="detail.video_link"
									type="text"
									inputmode="url"
									data-testid="video-link"
									class="mb-3 w-full rounded-lg border px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
									:class="detail.video_link && !videoLinkOk ? 'border-red-400' : 'border-gray-300'"
									placeholder="https://..."
								/>
								<p v-if="detail.video_link && !videoLinkOk" class="mb-3 mt-1 text-xs text-red-600">
									{{ __("Video link must start with http:// or https://") }}
								</p>

								<div class="flex flex-col gap-2">
									<PurchaseAttachments :attached="detail.destruction" :label="__('Destruction Photos')" />
									<PurchaseAttachments
										editable
										:pending="newDestruction"
										:label="__('Add Destruction Photos')"
										@add="addDestruction($event)"
										@remove="(row) => newDestruction.splice(newDestruction.indexOf(row), 1)"
									/>
								</div>
							</section>

							<!-- Destruction Submitted / Completed: read-only destruction proof -->
							<section
								v-else-if="detail.workflow_state === 'Destruction Submitted' || detail.workflow_state === 'Completed'"
								class="rounded-xl border border-gray-200 bg-white p-4"
							>
								<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Destruction Proof") }}</h4>
								<dl v-if="detail.video_link && videoLinkOk" class="mb-3 space-y-1 text-sm">
									<dt class="text-xs text-gray-500">{{ __("Destruction Video Link") }}</dt>
									<dd>
										<a
											:href="detail.video_link"
											target="_blank"
											rel="noopener"
											class="break-all text-blue-600 hover:underline"
										>
											{{ detail.video_link }}
										</a>
									</dd>
								</dl>
								<PurchaseAttachments :attached="detail.destruction" :label="__('Destruction Photos')" />
							</section>
						</div>

						<!-- Right column: summary + liability -->
						<div class="flex flex-col gap-4">
							<!-- Approval Code Card (shown whenever approval_code exists) -->
							<section v-if="detail.approval_code" class="rounded-xl border border-gray-200 bg-white p-4">
								<div class="flex items-start justify-between gap-3">
									<div>
										<p class="text-xs font-medium text-gray-500 uppercase tracking-wider">{{ __("Approval Code") }}</p>
										<p class="mt-1 font-mono text-xl font-bold tracking-widest text-gray-900" data-testid="approval-code">
											{{ detail.approval_code }}
										</p>
										<p v-if="detail.decided_by" class="mt-1 text-xs text-gray-500">
											{{ __("Approved by {0} on {1}", [detail.decided_by, formatDate(detail.decided_on)]) }}
										</p>
									</div>
									<a
										v-if="detail.workflow_state !== 'Awaiting Destruction'"
										:href="beritaAcaraUrl"
										target="_blank"
										rel="noopener"
										data-testid="download-berita-acara"
										class="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-2.5 py-1.5 text-xs font-medium text-blue-700 shadow-xs hover:bg-blue-100 shrink-0"
									>
										<FeatherIcon name="download" class="h-3.5 w-3.5" />
										{{ __("Download Berita Acara") }}
									</a>
								</div>
							</section>

							<!-- Summary -->
							<section class="rounded-xl border border-gray-200 bg-gray-50 p-4">
								<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Summary") }}</h4>
								<dl class="space-y-2 text-sm">
									<div class="flex justify-between gap-3">
										<dt class="text-gray-500">{{ __("Purpose") }}</dt>
										<dd class="text-end font-medium text-gray-900">{{ __(detail.purpose) }}</dd>
									</div>
									<div class="flex justify-between gap-3">
										<dt class="text-gray-500">{{ __("Incident Date") }}</dt>
										<dd class="font-medium text-gray-900">{{ formatDate(detail.incident_date) }}</dd>
									</div>
									<div v-if="detail.deadline_date" class="flex justify-between gap-3">
										<dt class="text-gray-500">{{ __("Deadline") }}</dt>
										<dd class="font-medium text-gray-900">{{ formatDate(detail.deadline_date) }}</dd>
									</div>
									<div class="flex justify-between gap-3">
										<dt class="text-gray-500">{{ __("Evidence Photos") }}</dt>
										<dd class="font-medium tabular-nums text-gray-900">{{ detail.evidence?.length || 0 }}</dd>
									</div>
									<div v-if="detail.issue_entry" class="flex justify-between gap-3">
										<dt class="text-gray-500">{{ __("Stock Entry") }}</dt>
										<dd class="font-medium text-gray-900">{{ detail.issue_entry }}</dd>
									</div>
									<div class="flex justify-between gap-3 border-t border-gray-200 pt-2">
										<dt class="text-gray-500">{{ __("Total Amount") }}</dt>
										<dd class="font-semibold tabular-nums text-gray-900">{{ formatCurrency(detail.total_amount) }}</dd>
									</div>
								</dl>
							</section>

							<!-- Liability info -->
							<section
								v-if="detail.borne_by"
								class="rounded-xl border p-4"
								:class="detail.borne_by === 'Crew' ? 'border-amber-200 bg-amber-50/40' : 'border-green-200 bg-green-50/40'"
							>
								<h4 class="text-sm font-semibold text-gray-900">
									{{ detail.borne_by === "Company" ? __("Borne by Company") : __("Borne by Crew") }}
								</h4>
								<p class="mt-0.5 text-xs text-gray-600">
									{{
										detail.borne_by === "Company"
											? __("Head Office approved this loss to be borne by company.")
											: __("Head Office assigned this loss to crew liabilities.")
									}}
								</p>
								<ul
									v-if="detail.borne_by === 'Crew' && detail.liabilities?.length"
									class="mt-3 divide-y divide-amber-100 border-t border-amber-100 text-sm"
								>
									<li v-for="l in detail.liabilities" :key="l.user" class="flex justify-between gap-3 py-1.5">
										<span class="min-w-0 truncate text-gray-700">{{ l.user }}</span>
										<span class="shrink-0 font-medium tabular-nums text-gray-900">{{ formatCurrency(l.amount) }}</span>
									</li>
								</ul>
							</section>
						</div>
					</div>
				</template>
			</div>
		</template>
	</DialogHost>
</template>

<script setup>
import { Button, FeatherIcon, call } from "frappe-ui"
import { computed, ref, watch } from "vue"
import DialogHost from "@/components/common/DialogHost.js"
import RefreshButton from "@/components/common/RefreshButton.vue"
import PurchaseAttachments from "@/components/purchase/PurchaseAttachments.vue"
import { useFormatters } from "@/composables/useFormatters"
import { useToast } from "@/composables/useToast"
import { fileToBase64, sanitizePickedFiles } from "@/utils/attachments"

const props = defineProps({
	modelValue: Boolean,
	posProfile: String,
	embedded: { type: Boolean, default: false },
	initialRequest: { type: String, default: null },
})

const emit = defineEmits(["update:modelValue"])

const { showSuccess, showError } = useToast()
const { formatCurrency, formatDate } = useFormatters()

const show = ref(props.modelValue)

const MAX_EVIDENCE = 5
const PAGE = 30

const WORKFLOW_STEPS = [
	"Draft",
	"Pending Approval",
	"Awaiting Destruction",
	"Destruction Submitted",
	"Completed",
]

// view: "list" | "new" | "detail"
const view = ref("list")
const statusFilter = ref("")
const requests = ref([])
const hasMore = ref(false)
const loading = ref(false)
const loadingDetail = ref(false)

const statusTabs = [
	{ value: "", label: __("All") },
	{ value: "Draft", label: __("Draft") },
	{ value: "Pending Approval", label: __("Pending Approval") },
	{ value: "Awaiting Destruction", label: __("Awaiting Destruction") },
	{ value: "Destruction Submitted", label: __("Destruction Submitted") },
	{ value: "Completed", label: __("Completed") },
	{ value: "Cancelled", label: __("Cancelled") },
]

const acting = ref(false)
const submitting = ref(false)
const detail = ref(null)
// newly picked files waiting to be uploaded with the save
const newEvidence = ref([])
const newDestruction = ref([])

// draft tracking
const draftName = ref(null)
const uploadedEvidence = ref(0)
const uploadedDestruction = ref(0)

const form = ref({
	incident_date: "",
	purpose: "",
	reason: "",
	items: [],
	files: [],
})

const purposes = ref([])

async function loadPurposes() {
	try {
		purposes.value =
			(await call("pos_next.api.reject_request.get_reject_purposes")) || []
		if (!form.value.purpose && purposes.value.length)
			form.value.purpose = purposes.value[0]
	} catch (err) {
		showError(errMsg(err, "Failed to load reject purposes"))
	}
}

const STATUS_TONES = {
	Draft: "bg-orange-50 text-orange-700 ring-orange-600/20",
	"Pending Approval": "bg-blue-50 text-blue-700 ring-blue-600/20",
	"Awaiting Destruction": "bg-amber-50 text-amber-700 ring-amber-600/20",
	"Destruction Submitted": "bg-amber-50 text-amber-700 ring-amber-600/20",
	Completed: "bg-green-50 text-green-700 ring-green-600/20",
	Cancelled: "bg-gray-50 text-gray-600 ring-gray-500/20",
}

function statusPill(status) {
	return [
		"inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
		STATUS_TONES[status] || "bg-gray-50 text-gray-600 ring-gray-500/20",
	]
}

const dialogTitle = computed(() =>
	view.value === "new" ? __("New Reject Request") : __("Reject Requests"),
)

function isStepPassed(step) {
	if (
		!detail.value?.workflow_state ||
		detail.value.workflow_state === "Cancelled"
	)
		return false
	const currentIdx = WORKFLOW_STEPS.indexOf(detail.value.workflow_state)
	const stepIdx = WORKFLOW_STEPS.indexOf(step)
	return currentIdx > -1 && stepIdx < currentIdx
}

const beritaAcaraUrl = computed(() => {
	if (!detail.value?.name) return "#"
	const name = encodeURIComponent(detail.value.name)
	const format = encodeURIComponent("Berita Acara Reject")
	const letterhead = encodeURIComponent(detail.value.letter_head || "")
	return `/api/method/frappe.utils.print_format.download_pdf?doctype=POS%20Reject%20Request&name=${name}&format=${format}&letterhead=${letterhead}`
})

const videoLinkOk = computed(() => {
	const link = detail.value?.video_link || ""
	if (!link) return true
	try {
		return ["http:", "https:"].includes(new URL(link).protocol)
	} catch {
		return false
	}
})

const destructionCount = computed(
	() =>
		(detail.value?.destruction?.length || 0) +
		newDestruction.value.length +
		uploadedDestruction.value,
)

const hasProof = computed(() => {
	const hasValidVideo = !!detail.value?.video_link?.trim() && videoLinkOk.value
	return hasValidVideo || destructionCount.value >= 1
})

const canSendProof = computed(() => {
	return (
		detail.value?.workflow_state === "Awaiting Destruction" &&
		hasProof.value &&
		videoLinkOk.value
	)
})

function rowDuplicateError(row) {
	if (!row.item_code) return ""
	const duplicates = form.value.items.filter(
		(r) =>
			r !== row &&
			r.item_code === row.item_code &&
			(r.production_date || "") === (row.production_date || ""),
	)
	if (duplicates.length > 0) {
		return __("Duplicate item with same production date")
	}
	return ""
}

const canSubmit = computed(() => {
	const filledItems = form.value.items.filter((i) => !!i.item_code)
	const hasDuplicate = form.value.items.some(
		(r) => r.item_code && !!rowDuplicateError(r),
	)
	const validDates = filledItems.every(
		(r) =>
			!r.production_date ||
			!form.value.incident_date ||
			r.production_date <= form.value.incident_date,
	)
	return (
		!!props.posProfile &&
		!!form.value.purpose &&
		!!form.value.reason.trim() &&
		filledItems.length > 0 &&
		filledItems.every((i) => i.qty > 0) &&
		!hasDuplicate &&
		validDates &&
		form.value.files.length + uploadedEvidence.value >= 1 &&
		form.value.files.length + uploadedEvidence.value <= MAX_EVIDENCE
	)
})

const newChecklist = computed(() => {
	const photos = form.value.files.length + uploadedEvidence.value
	const items = form.value.items.filter((i) => !!i.item_code)
	return [
		{
			label: __("Items"),
			value: items.length,
			ok:
				items.length > 0 &&
				items.every((i) => i.qty > 0) &&
				!form.value.items.some((r) => r.item_code && !!rowDuplicateError(r)),
		},
		{ label: __("Reason"), value: "", ok: !!form.value.reason.trim() },
		{
			label: __("Evidence Photos"),
			value: `${photos}/${MAX_EVIDENCE}`,
			ok: photos >= 1 && photos <= MAX_EVIDENCE,
		},
	]
})

const canSubmitDetail = computed(() => {
	if (!detail.value) return false
	const hasReason = !!detail.value.reason?.trim()
	const photos =
		(detail.value.evidence?.length || 0) +
		newEvidence.value.length +
		uploadedEvidence.value
	const hasPhotos = photos >= 1 && photos <= MAX_EVIDENCE
	if (!hasReason || !hasPhotos) return false
	if (!detail.value.is_system_generated) {
		if (!detail.value.purpose) return false
		const items = form.value.items.filter((i) => !!i.item_code)
		if (!items.length) return false
		if (!items.every((i) => i.qty > 0)) return false
		if (form.value.items.some((r) => r.item_code && !!rowDuplicateError(r)))
			return false
		if (
			!items.every(
				(r) =>
					!r.production_date ||
					!detail.value.incident_date ||
					r.production_date <= detail.value.incident_date,
			)
		)
			return false
	}
	return true
})

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

async function loadList(reset = true) {
	if (!props.posProfile) return
	if (!reset && loading.value) return
	const offset = reset ? 0 : requests.value.length
	loading.value = true
	try {
		const data = await call("pos_next.api.reject_request.get_reject_requests", {
			pos_profile: props.posProfile,
			status: statusFilter.value || undefined,
			limit: PAGE,
			offset,
		})
		requests.value = reset
			? data.requests || []
			: requests.value.concat(data.requests || [])
		hasMore.value = !!data.has_more
	} catch (err) {
		showError(errMsg(err, "Failed to load reject requests"))
	} finally {
		loading.value = false
	}
}

function setStatusFilter(value) {
	if (statusFilter.value === value) return
	statusFilter.value = value
	loadList()
}

// ---------------------------------------------------------------------------
// New request
// ---------------------------------------------------------------------------

let rowSeq = 0
function blankRow(fields = {}) {
	return {
		key: ++rowSeq,
		selected: false,
		item_code: "",
		item_name: "",
		stock_uom: "Nos",
		uom: "Nos",
		uoms: [],
		qty: 1,
		production_date: "",
		reject_reason: "",
		search: "",
		results: [],
		...fields,
	}
}

const isAllSelected = computed(() => {
	const items = form.value.items
	return items.length > 0 && items.every((r) => r.selected)
})

const isPartiallySelected = computed(() => {
	const items = form.value.items
	const sel = items.filter((r) => r.selected).length
	return sel > 0 && sel < items.length
})

const selectedCount = computed(() => {
	return form.value.items.filter((r) => r.selected).length
})

function toggleSelectAll(checked) {
	for (const row of form.value.items) {
		row.selected = checked
	}
}

function deleteSelectedRows() {
	form.value.items = form.value.items.filter((r) => !r.selected)
}

function focusCell(idx, cellName) {
	requestAnimationFrame(() => {
		const el = document.querySelector(`[data-cell="${idx}-${cellName}"]`)
		if (el) {
			el.focus()
			if (typeof el.select === "function") el.select()
		}
	})
}

function editRowItem(row, idx) {
	row.search = row.item_code
	row.item_code = ""
	row.results = []
	focusCell(idx, "item")
}

async function onItemEnter(row, idx) {
	if (row.results?.length > 0) {
		pickItem(row, row.results[0])
		focusCell(idx, "uom")
		return
	}
	await addExactMatch(row)
	if (row.item_code) {
		focusCell(idx, "uom")
	}
}

function addRow() {
	const nextIdx = form.value.items.length
	form.value.items.push(blankRow())
	focusCell(nextIdx, "item")
}

function onReasonEnter(idx) {
	if (idx === form.value.items.length - 1) {
		addRow()
	} else {
		focusCell(idx + 1, "item")
	}
}

function uomOptionsOf(row) {
	return [
		...new Set([row.stock_uom, row.uom, ...(row.uoms || [])].filter(Boolean)),
	]
}

function localDate() {
	const d = new Date()
	const m = String(d.getMonth() + 1).padStart(2, "0")
	const day = String(d.getDate()).padStart(2, "0")
	return `${d.getFullYear()}-${m}-${day}`
}

function openNew() {
	form.value = {
		incident_date: localDate(),
		purpose: purposes.value[0] || "",
		reason: "",
		items: [],
		files: [],
	}
	draftName.value = null
	uploadedEvidence.value = 0
	if (!purposes.value.length) loadPurposes()
	view.value = "new"
}

let searchTimer = null
let searchSeq = 0

function searchRow(row) {
	clearTimeout(searchTimer)
	for (const r of form.value.items) if (r !== row) r.results = []
	row.results = []
	const term = row.search.trim()
	if (!term) {
		searchSeq++
		return
	}
	searchTimer = setTimeout(() => doSearchRow(row, term), 350)
}

async function doSearchRow(row, term) {
	const seq = ++searchSeq
	try {
		const rows = await call("pos_next.api.items.get_items", {
			pos_profile: props.posProfile,
			search_term: term,
			start: 0,
			limit: 10,
		})
		if (seq !== searchSeq) return null
		row.results = rows || []
		return row.results
	} catch (err) {
		if (seq === searchSeq) showError(errMsg(err, "Failed to search items"))
		return null
	}
}

function closeResults(row) {
	clearTimeout(searchTimer)
	row.results = []
}

function blurRow(row) {
	setTimeout(() => {
		row.results = []
	}, 200)
}

async function addExactMatch(row) {
	const term = row.search.trim()
	if (!term) return
	let exact = row.results.find((i) => i.item_code === term)
	if (!exact) {
		clearTimeout(searchTimer)
		const rows = await doSearchRow(row, term)
		exact = rows?.find((i) => i.item_code === term)
	}
	if (exact) pickItem(row, exact)
}

function pickItem(row, item) {
	clearTimeout(searchTimer)
	searchSeq++
	row.search = ""
	row.results = []
	row.item_code = item.item_code
	row.item_name = item.item_name || item.item_code
	row.stock_uom = item.stock_uom || item.uom || "Nos"
	row.uom = item.uom || item.stock_uom || "Nos"
	row.uoms = item.uoms ? item.uoms.map((u) => u.uom || u) : [row.uom]
	if (!row.qty) row.qty = 1
}

function addEvidence(list, files) {
	const { ok, tooBig } = sanitizePickedFiles(files)
	if (tooBig.length) showError(__("Some files exceed the 10 MB limit"))
	const attached =
		view.value === "detail"
			? (detail.value?.evidence?.length || 0) + uploadedEvidence.value
			: 0
	const room = Math.max(0, MAX_EVIDENCE - attached - list.length)
	const accepted = ok.slice(0, room)
	if (ok.length > room) {
		showError(__("At most {0} evidence photos are allowed", [MAX_EVIDENCE]))
	}
	list.push(...accepted)
}

function addDestruction(files) {
	const { ok, tooBig } = sanitizePickedFiles(files)
	if (tooBig.length) showError(__("Some files exceed the 10 MB limit"))
	newDestruction.value.push(...ok)
}

async function toPayloads(files) {
	return Promise.all(
		files.map(async (f) => ({
			file_name: f.name,
			filedata: await fileToBase64(f),
		})),
	)
}

async function attachFiles(name, kind, files) {
	if (!files.length) return 0
	await call("pos_next.api.reject_request.attach_reject_files", {
		name,
		kind,
		files: JSON.stringify(await toPayloads(files)),
	})
	return files.length
}

async function submitRequest(buildData, draftNameRef, getPending) {
	if (!draftNameRef.value) {
		const draft = await call(
			"pos_next.api.reject_request.save_reject_request",
			{
				data: JSON.stringify(buildData()),
				submit: 0,
			},
		)
		draftNameRef.value = draft.name
	}
	const name = draftNameRef.value
	const pending = getPending()
	uploadedEvidence.value += await attachFiles(name, "evidence", pending)
	getPending(true)
	return call("pos_next.api.reject_request.save_reject_request", {
		data: JSON.stringify({ ...buildData(), name }),
		submit: 1,
	})
}

async function submitNew() {
	if (!canSubmit.value || submitting.value) return
	submitting.value = true
	try {
		const buildData = () => ({
			pos_profile: props.posProfile,
			incident_date: form.value.incident_date,
			purpose: form.value.purpose,
			reason: form.value.reason.trim(),
			items: form.value.items
				.filter((i) => !!i.item_code)
				.map((i) => ({
					item_code: i.item_code,
					qty: i.qty,
					uom: i.uom,
					production_date: i.production_date || null,
					reject_reason: (i.reject_reason || "").trim() || null,
				})),
		})
		await submitRequest(buildData, draftName, (clear) => {
			const files = form.value.files
			if (clear) form.value.files = []
			return files
		})
		showSuccess(__("Reject request submitted"))
		view.value = "list"
		loadList()
	} catch (err) {
		showError(errMsg(err, "Failed to submit reject request"))
	} finally {
		submitting.value = false
	}
}

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

async function openDetail(name) {
	view.value = "detail"
	loadingDetail.value = true
	draftName.value = null
	uploadedEvidence.value = 0
	uploadedDestruction.value = 0
	newEvidence.value = []
	newDestruction.value = []
	try {
		detail.value = await call(
			"pos_next.api.reject_request.get_reject_request",
			{ name },
		)
		if (
			detail.value?.workflow_state === "Draft" &&
			!detail.value.is_system_generated
		) {
			form.value.purpose = detail.value.purpose
			form.value.incident_date = detail.value.incident_date
			form.value.items = (detail.value.items || []).map((r) =>
				blankRow({
					item_code: r.item_code,
					item_name: r.item_name || r.item_code,
					stock_uom: r.stock_uom || r.uom || "Nos",
					uom: r.uom || r.stock_uom || "Nos",
					uoms: r.uoms || [r.uom || "Nos"],
					qty: r.qty,
					production_date: r.production_date || "",
					reject_reason: r.reject_reason || "",
				}),
			)
			if (!purposes.value.length) loadPurposes()
		}
	} catch (err) {
		detail.value = null
		showError(errMsg(err, "Failed to load reject request"))
		backToList()
	} finally {
		loadingDetail.value = false
	}
}

function backToList() {
	detail.value = null
	form.value.items = []
	view.value = "list"
	loadList()
}

async function submitDetail() {
	if (!canSubmitDetail.value || submitting.value || !detail.value) return
	submitting.value = true
	try {
		const buildData = () => {
			const data = {
				name: detail.value.name,
				reason: detail.value.reason.trim(),
			}
			if (!detail.value.is_system_generated) {
				data.purpose = detail.value.purpose
				data.incident_date = detail.value.incident_date
				data.items = form.value.items
					.filter((i) => !!i.item_code)
					.map((i) => ({
						item_code: i.item_code,
						qty: i.qty,
						uom: i.uom,
						production_date: i.production_date || null,
						reject_reason: (i.reject_reason || "").trim() || null,
					}))
			}
			return data
		}
		draftName.value = detail.value.name
		await submitRequest(buildData, draftName, (clear) => {
			const files = newEvidence.value
			if (clear) newEvidence.value = []
			return files
		})
		showSuccess(__("Reject request submitted"))
		backToList()
	} catch (err) {
		showError(errMsg(err, "Failed to submit reject request"))
	} finally {
		submitting.value = false
	}
}

async function doWithdraw() {
	if (acting.value || !detail.value) return
	if (!window.confirm(__("Withdraw this reject request back to Draft?"))) return
	acting.value = true
	try {
		detail.value = await call(
			"pos_next.api.reject_request.apply_reject_action",
			{
				name: detail.value.name,
				action: "Withdraw",
			},
		)
		showSuccess(__("Reject request withdrawn to Draft"))
	} catch (err) {
		showError(errMsg(err, "Failed to withdraw reject request"))
	} finally {
		acting.value = false
	}
}

async function doSendProof() {
	if (acting.value || !canSendProof.value || !detail.value) return
	acting.value = true
	try {
		if (newDestruction.value.length) {
			uploadedDestruction.value += await attachFiles(
				detail.value.name,
				"destruction",
				newDestruction.value,
			)
			newDestruction.value = []
		}
		if (detail.value.video_link?.trim()) {
			await call("pos_next.api.reject_request.save_reject_video", {
				name: detail.value.name,
				video_link: detail.value.video_link.trim(),
			})
		}
		detail.value = await call(
			"pos_next.api.reject_request.apply_reject_action",
			{
				name: detail.value.name,
				action: "Send Proof",
			},
		)
		showSuccess(__("Destruction proof sent for review"))
	} catch (err) {
		showError(errMsg(err, "Failed to send destruction proof"))
	} finally {
		acting.value = false
	}
}

watch(
	() => props.modelValue,
	(v) => {
		show.value = v
		if (v) {
			if (props.initialRequest) {
				openDetail(props.initialRequest)
			} else {
				view.value = "list"
				loadList()
			}
		}
	},
	{ immediate: true },
)
watch(show, (v) => emit("update:modelValue", v))

function errMsg(err, fallback) {
	if (typeof err === "string") return err
	if (err?.messages?.length) return err.messages[0]
	if (err?.message) return err.message
	return fallback
}
</script>
