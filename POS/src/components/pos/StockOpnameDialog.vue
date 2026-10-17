<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: dialogTitle, size: '4xl' }"
	>
		<template #body-content>
			<!-- LIST -->
			<div v-if="view === 'list'" class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<div class="flex flex-wrap items-center justify-between gap-3">
					<h3 class="text-base font-semibold text-gray-900">{{ __("Stock Opname") }}</h3>
					<div class="flex items-center gap-2">
						<RefreshButton :loading="loading" @click="loadList()" />
						<Button v-if="openOpname" variant="solid" @click="openDoc(openOpname.name)">
							<template #prefix>
								<FeatherIcon name="edit-2" class="h-4 w-4" />
							</template>
							{{ __("Continue") }}
						</Button>
						<Button v-else-if="!hasOpenOpname" variant="solid" @click="openNew">
							<template #prefix>
								<FeatherIcon name="plus" class="h-4 w-4" />
							</template>
							{{ __("New Count") }}
						</Button>
					</div>
				</div>

				<div class="overflow-hidden rounded-xl border border-gray-200 bg-white">
					<div v-if="loading && !opnames.length" class="divide-y divide-gray-100">
						<span class="sr-only">{{ __("Loading...") }}</span>
						<div v-for="n in 3" :key="n" class="flex animate-pulse gap-4 px-4 py-3">
							<div class="h-4 w-28 rounded bg-gray-100"></div>
							<div class="h-4 flex-1 rounded bg-gray-100"></div>
							<div class="h-4 w-20 rounded bg-gray-100"></div>
						</div>
					</div>
					<div v-else-if="!opnames.length" class="px-4 py-8 text-center">
						<p class="text-sm font-medium text-gray-900">{{ __("No stock opname yet") }}</p>
						<p class="mt-1 text-xs text-gray-500">{{ __("Start one with New Count") }}</p>
					</div>
					<table v-else class="w-full text-sm">
						<thead>
							<tr
								data-testid="list-header"
								class="border-b border-gray-200 bg-gray-50 text-xs font-medium text-gray-500"
							>
								<th class="px-4 py-2 text-start font-medium">{{ __("ID") }}</th>
								<th class="px-4 py-2 text-start font-medium">{{ __("Status") }}</th>
								<th class="px-4 py-2 text-start font-medium">{{ __("Posting Date") }}</th>
								<th class="px-4 py-2 text-end font-medium">{{ __("Items") }}</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-gray-100">
							<tr
								v-for="o in opnames"
								:key="o.name"
								tabindex="0"
								data-testid="opname-row"
								class="cursor-pointer transition-colors hover:bg-gray-50 focus:outline-none focus-visible:bg-gray-50"
								@click="openDoc(o.name)"
								@keydown.enter="openDoc(o.name)"
							>
								<td class="px-4 py-3 font-semibold text-gray-900">
									{{ o.name }}
									<p v-if="o.returned && o.reject_reason" class="text-xs font-normal text-red-600">
										{{ o.reject_reason }}
									</p>
								</td>
								<td class="px-4 py-3">
									<span class="inline-flex items-center gap-1.5">
										<span
											data-testid="status-dot"
											:class="['h-2 w-2 shrink-0 rounded-full', toneClass(o)]"
										></span>
										<span class="text-gray-700">{{ o.workflow_state }}</span>
									</span>
								</td>
								<td class="px-4 py-3 text-gray-700">{{ formatDate(o.posting_date) }}</td>
								<td class="px-4 py-3 text-end tabular-nums text-gray-900">{{ o.item_count }}</td>
							</tr>
						</tbody>
					</table>
				</div>
			</div>

			<!-- DOC PAGE -->
			<div v-else class="mx-auto flex w-full max-w-6xl flex-col gap-4">
				<div v-if="loadingDetail" class="py-10 text-center text-sm text-gray-500">
					{{ __("Loading...") }}
				</div>
				<template v-else-if="editor">
					<!-- header: back, breadcrumb, indicator, actions -->
					<div data-testid="doc-header" class="flex flex-wrap items-center gap-2">
						<button
							type="button"
							data-testid="doc-back"
							class="-ms-1 rounded p-1 text-gray-500 hover:text-gray-900"
							:aria-label="__('Back')"
							:title="__('Back')"
							@click="backToList"
						>
							<FeatherIcon name="arrow-left" class="h-4 w-4" />
						</button>
						<nav class="flex min-w-0 items-center gap-1.5 text-sm">
							<span class="text-gray-500">{{ __("Stock Opname") }}</span>
							<span class="text-gray-300">/</span>
							<span class="truncate font-semibold text-gray-900">
								{{ editor.name || __("New Count") }}
							</span>
						</nav>
						<span v-if="editor.workflow_state" class="inline-flex items-center gap-1.5">
							<span
								data-testid="status-dot"
								:class="['h-2 w-2 shrink-0 rounded-full', toneClass(editor)]"
							></span>
							<span class="text-sm text-gray-700">{{ editor.workflow_state }}</span>
						</span>

						<div class="ms-auto flex items-center gap-2">
							<Button
								v-if="primaryMode === 'save'"
								variant="solid"
								data-testid="primary-action"
								:loading="saving"
								@click="save"
							>
								{{ __("Save") }}
							</Button>
							<Button
								v-else-if="primaryMode === 'submit'"
								variant="solid"
								data-testid="primary-action"
								:loading="submitting"
								@click="submitAction(actions[0])"
							>
								{{ actions[0] }}
							</Button>
							<div v-else-if="primaryMode === 'actions'" class="relative">
								<Button
									variant="solid"
									data-testid="primary-action"
									@click="actionsMenuOpen = !actionsMenuOpen"
								>
									{{ __("Actions") }}
									<template #suffix>
										<FeatherIcon name="chevron-down" class="h-4 w-4" />
									</template>
								</Button>
								<div
									v-if="actionsMenuOpen"
									data-testid="actions-menu"
									class="absolute end-0 z-20 mt-1 w-40 rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
								>
									<button
										v-for="a in actions"
										:key="a"
										type="button"
										class="block w-full px-3 py-2 text-start text-sm text-gray-700 hover:bg-gray-50"
										@click="submitAction(a)"
									>
										{{ a }}
									</button>
								</div>
							</div>

							<div v-if="editable && editor.name" class="relative">
								<button
									type="button"
									data-testid="doc-menu-button"
									class="flex h-8 w-8 items-center justify-center rounded text-gray-500 hover:bg-gray-100 hover:text-gray-900"
									:aria-label="__('More actions')"
									:title="__('More actions')"
									@click="docMenuOpen = !docMenuOpen"
								>
									<FeatherIcon name="more-horizontal" class="h-4 w-4" />
								</button>
								<div
									v-if="docMenuOpen"
									data-testid="doc-menu"
									class="absolute end-0 z-20 mt-1 w-40 rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
								>
									<button
										type="button"
										data-testid="doc-menu-delete"
										class="block w-full px-3 py-2 text-start text-sm text-red-600 hover:bg-red-50"
										@click="remove"
									>
										{{ __("Delete") }}
									</button>
								</div>
							</div>
						</div>
					</div>

					<p
						v-if="editor.returned && editor.reject_reason"
						class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
					>
						{{ __("Rejected by Head Office") }}: {{ editor.reject_reason }}
					</p>

					<!-- Details -->
					<section data-testid="opname-detail" class="rounded-xl border border-gray-200 bg-white">
						<h4 class="px-4 pb-2 pt-4 text-sm font-semibold text-gray-900">{{ __("Details") }}</h4>
						<div
							class="grid grid-cols-2 gap-x-4 gap-y-3 border-t border-gray-200 px-4 py-3 md:grid-cols-3"
						>
							<div class="min-w-0">
								<p class="text-xs text-gray-500">{{ __("ID") }}</p>
								<p class="truncate text-sm font-semibold text-gray-900">
									{{ editor.name || __("New Count") }}
								</p>
							</div>
							<div class="min-w-0">
								<p class="text-xs text-gray-500">{{ __("Status") }}</p>
								<span v-if="editor.workflow_state" class="inline-flex items-center gap-1.5">
									<span :class="['h-2 w-2 shrink-0 rounded-full', toneClass(editor)]"></span>
									<span class="text-sm text-gray-700">{{ editor.workflow_state }}</span>
								</span>
							</div>
							<div class="min-w-0">
								<p class="text-xs text-gray-500">{{ __("Warehouse") }}</p>
								<p class="truncate text-sm text-gray-900">{{ editor.set_warehouse }}</p>
							</div>
							<div class="col-span-2 min-w-0">
								<p class="text-xs text-gray-500">{{ __("Posting Date") }}</p>
								<template v-if="editable">
									<label class="mb-1 flex items-center gap-1.5 text-xs text-gray-700">
										<input
											v-model="editor.set_posting_time"
											type="checkbox"
											data-testid="posting-toggle"
										/>
										{{ __("Edit Posting Date and Time") }}
									</label>
									<div class="flex flex-wrap gap-2">
										<input
											v-model="editor.posting_date"
											type="date"
											data-testid="posting-date-input"
											:disabled="!editor.set_posting_time"
											class="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 disabled:bg-gray-50 disabled:text-gray-400"
										/>
										<input
											v-model="editor.posting_time"
											type="time"
											data-testid="posting-time-input"
											:disabled="!editor.set_posting_time"
											class="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 disabled:bg-gray-50 disabled:text-gray-400"
										/>
									</div>
								</template>
								<p v-else class="truncate text-sm text-gray-900">
									{{ formatDate(editor.posting_date) }} · {{ formatTime(editor.posting_time) }}
								</p>
							</div>
							<div class="min-w-0">
								<p class="text-xs text-gray-500">{{ __("Created By") }}</p>
								<p class="truncate text-sm text-gray-900">{{ editor.owner_fullname }}</p>
								<p class="truncate text-xs text-gray-400">{{ formatDateTime(editor.creation) }}</p>
							</div>
							<div class="min-w-0">
								<p class="text-xs text-gray-500">{{ __("Last Modified") }}</p>
								<p class="truncate text-sm text-gray-900">{{ editor.modified_by_fullname }}</p>
								<p class="truncate text-xs text-gray-400">{{ formatDateTime(editor.modified) }}</p>
							</div>
						</div>
					</section>

					<!-- Items -->
					<section class="rounded-xl border border-gray-200 bg-white">
						<h4 class="px-4 pb-2 pt-4 text-sm font-semibold text-gray-900">{{ __("Items") }}</h4>

						<!-- read-only -->
						<template v-if="!editable">
							<div
								class="hidden border-y border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500 md:grid md:grid-cols-[minmax(0,1fr)_6rem_5rem] md:gap-x-3"
							>
								<span>{{ __("Item") }}</span>
								<span>{{ __("UOM") }}</span>
								<span class="text-end">{{ __("Qty") }}</span>
							</div>
							<div
								v-for="(row, idx) in editor.items"
								:key="idx"
								class="grid grid-cols-2 gap-x-3 gap-y-1 border-b border-gray-100 px-4 py-2 text-sm last:border-b-0 md:grid-cols-[minmax(0,1fr)_6rem_5rem] md:gap-y-0 md:py-1.5"
							>
								<div class="col-span-2 min-w-0 md:col-span-1">
									<p class="truncate text-gray-900">{{ row.item_name || row.item_code }}</p>
									<p v-if="row.batch_no" class="truncate text-xs text-gray-400">
										{{ __("Batch") }}: {{ row.batch_no }}
									</p>
									<p
										v-else-if="row.is_serial && row.serial_nos.length"
										class="truncate text-xs text-gray-400"
									>
										{{ row.serial_nos.join(", ") }}
									</p>
								</div>
								<div class="min-w-0">
									<span class="mb-0.5 block text-xs text-gray-500 md:hidden">{{ __("UOM") }}</span>
									<span class="truncate text-gray-500">{{ row.uom }}</span>
								</div>
								<div class="min-w-0">
									<span class="mb-0.5 block text-xs text-gray-500 md:hidden">{{ __("Qty") }}</span>
									<span class="block text-end tabular-nums">{{ row.qty }}</span>
								</div>
							</div>
							<p v-if="!editor.items.length" class="px-4 py-4 text-center text-sm text-gray-500">
								{{ __("No items") }}
							</p>
						</template>

						<!-- editable -->
						<template v-else>
							<div
								class="hidden border-y border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium uppercase tracking-wide text-gray-500 md:grid md:grid-cols-[2rem_minmax(0,1fr)_8rem_6rem_6rem_2rem] md:gap-x-3"
							>
								<span>#</span>
								<span>{{ __("Item") }}</span>
								<span>{{ __("Batch") }}</span>
								<span>{{ __("UOM") }}</span>
								<span>{{ __("Qty") }}</span>
								<span></span>
							</div>

							<div
								v-for="(row, idx) in editor.items"
								:key="row.key"
								class="grid grid-cols-2 items-center gap-x-3 gap-y-1 border-b border-gray-100 px-4 py-2 last:border-b-0 md:grid-cols-[2rem_minmax(0,1fr)_8rem_6rem_6rem_2rem] md:gap-y-0 md:py-1.5"
							>
								<span class="hidden text-xs tabular-nums text-gray-400 md:block">{{ idx + 1 }}</span>

								<div class="col-span-2 min-w-0 md:col-span-1">
									<p class="truncate text-sm font-medium text-gray-900">{{ row.item_name || row.item_code }}</p>
									<p v-if="row.item_code !== row.item_name" class="truncate text-xs text-gray-400">
										{{ row.item_code }}
									</p>
								</div>

								<div class="min-w-0">
									<span class="mb-0.5 block text-xs text-gray-500 md:hidden">{{ __("Batch") }}</span>
									<span class="truncate text-xs text-gray-500">{{ row.batch_no }}</span>
								</div>

								<div class="min-w-0">
									<span class="mb-0.5 block text-xs text-gray-500 md:hidden">{{ __("UOM") }}</span>
									<!-- serials are counted by ticking, never in a converted UOM -->
									<span
										v-if="row.is_serial"
										data-testid="uom-readonly"
										class="block truncate text-sm text-gray-500"
									>
										{{ row.stock_uom }}
									</span>
									<select
										v-else
										v-model="row.uom"
										data-testid="uom-select"
										:aria-label="`${row.item_name || row.item_code} — ${__('UOM')}`"
										class="w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
									>
										<option v-for="u in uomOptionsOf(row)" :key="u" :value="u">{{ u }}</option>
									</select>
								</div>

								<div class="min-w-0">
									<span class="mb-0.5 block text-xs text-gray-500 md:hidden">{{ __("Qty") }}</span>
									<!-- serial qty is the number of ticked serials, never typed -->
									<span
										v-if="row.is_serial"
										data-testid="item-qty"
										class="block text-end text-sm tabular-nums text-gray-900"
									>
										{{ row.serial_nos.length }}
									</span>
									<input
										v-else
										v-model.number="row.qty"
										type="number"
										min="0"
										step="any"
										inputmode="decimal"
										data-testid="qty-input"
										:aria-label="`${row.item_name || row.item_code} — ${__('Qty')}`"
										class="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm tabular-nums focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
									/>
								</div>

								<button
									type="button"
									class="flex h-7 w-7 shrink-0 items-center justify-center justify-self-end rounded text-gray-400 hover:bg-red-50 hover:text-red-600"
									:aria-label="__('Remove item')"
									:title="__('Remove item')"
									@click="editor.items.splice(idx, 1)"
								>
									<FeatherIcon name="x" class="h-4 w-4" />
								</button>

								<!-- serial tick list: tick the serials physically present -->
								<div v-if="row.is_serial" class="col-span-2 mt-2 md:col-span-6">
									<ul class="flex flex-wrap gap-x-4 gap-y-1">
										<li v-for="s in row.serial_options" :key="s">
											<label class="flex items-center gap-1.5 text-sm text-gray-700">
												<input
													type="checkbox"
													data-testid="serial-option"
													:checked="row.serial_nos.includes(s)"
													@change="toggleSerial(row, s)"
												/>
												{{ s }}
											</label>
										</li>
									</ul>
									<input
										type="text"
										:placeholder="__('Scan serial...')"
										:aria-label="__('Scan serial')"
										class="mt-1 w-full max-w-xs rounded-md border border-dashed border-gray-300 px-2 py-1.5 text-sm focus:border-solid focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
										@keyup.enter="scanSerial(row, $event)"
									/>
								</div>
							</div>

							<!-- batch picker: one row per picked batch, no free-text batch -->
							<div
								v-if="batchPicker"
								data-testid="batch-picker"
								class="border-t border-gray-200 bg-gray-50 px-4 py-3"
							>
								<p class="mb-2 text-sm font-medium text-gray-900">
									{{ __("Pick batches for {0}", [batchPicker.item_name]) }}
								</p>
								<div class="flex flex-col gap-1.5">
									<label
										v-for="b in batchPicker.batches"
										:key="b.batch_no"
										class="flex items-center gap-2 text-sm text-gray-700"
									>
										<input
											type="checkbox"
											data-testid="batch-option"
											:value="b.batch_no"
											v-model="batchPicker.selected"
										/>
										<span>{{ b.batch_no }}<span v-if="b.expiry_date"> — {{ formatDate(b.expiry_date) }}</span></span>
									</label>
									<p v-if="!batchPicker.batches.length" class="text-xs text-gray-500">
										{{ __("No enabled batch for this item") }}
									</p>
								</div>
								<div class="mt-3 flex justify-end gap-2">
									<Button variant="subtle" size="sm" @click="batchPicker = null">{{ __("Cancel") }}</Button>
									<Button
										variant="solid"
										size="sm"
										:disabled="!batchPicker.selected.length"
										@click="addBatchRows"
									>
										{{ __("Add") }}
									</Button>
								</div>
							</div>

							<div class="relative px-4 py-3">
								<div class="relative">
									<FeatherIcon
										name="search"
										class="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
									/>
									<input
										v-model="itemSearch"
										type="text"
										:placeholder="__('Search item...')"
										:aria-label="__('Add item')"
										class="w-full rounded-lg border border-dashed border-gray-300 py-2 pe-3 ps-9 text-sm focus:border-solid focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
										@input="searchItems"
										@keyup.enter="addExactMatch"
									/>
								</div>
								<div
									v-if="itemResults.length"
									class="absolute inset-x-4 top-[3.25rem] z-10 max-h-56 overflow-y-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
								>
									<button
										v-for="i in itemResults"
										:key="i.item_code"
										type="button"
										class="flex w-full items-center justify-between gap-3 px-3 py-2 text-start text-sm hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-none"
										@click="addItem(i)"
									>
										<span class="truncate text-gray-900">{{ i.item_name || i.item_code }}</span>
										<span v-if="i.item_name && i.item_name !== i.item_code" class="shrink-0 text-xs text-gray-400">
											{{ i.item_code }}
										</span>
									</button>
								</div>
								<p v-if="!editor.items.length" class="mt-2 text-xs text-gray-400">
									{{ __("Search and add only the items you physically counted.") }}
								</p>
							</div>

							<!-- Excel count sheet -->
							<div class="border-t border-gray-200 px-4 py-3">
								<h5 class="mb-1 text-sm font-semibold text-gray-900">{{ __("Excel Template") }}</h5>
								<p class="mb-3 text-xs text-gray-500">
									{{ __("Download the count sheet, fill Counted Qty, then upload it back.") }}
								</p>
								<div class="flex flex-wrap items-end gap-3">
									<div>
										<label for="so-item-group" class="mb-1 block text-xs font-medium text-gray-700">
											{{ __("Item Group") }}
										</label>
										<select
											id="so-item-group"
											v-model="itemGroup"
											class="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
										>
											<option value="">{{ __("All item groups") }}</option>
											<option v-for="g in itemGroups" :key="g.item_group" :value="g.item_group">
												{{ g.item_group }}
											</option>
										</select>
									</div>
									<Button variant="subtle" @click="downloadTemplate">
										<template #prefix>
											<FeatherIcon name="download" class="h-4 w-4" />
										</template>
										{{ __("Download Template") }}
									</Button>
									<label
										class="cursor-pointer rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
									>
										<input type="file" accept=".xlsx" class="hidden" :disabled="importing" @change="onFilePicked" />
										{{ importing ? __("Uploading...") : __("Upload") }}
									</label>
								</div>
								<ul v-if="importResult?.errors?.length" data-testid="import-errors" class="mt-3 space-y-1">
									<li
										v-for="e in importResult.errors"
										:key="`${e.row}-${e.item_code}`"
										class="text-xs text-red-600"
									>
										{{ __("Row {0}: {1} — {2}", [e.row, e.item_code, e.message]) }}
									</li>
								</ul>
							</div>
						</template>
					</section>

					<!-- Attachments -->
					<section class="rounded-xl border border-gray-200 bg-white p-4" data-testid="opname-attachments">
						<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Attachments") }}</h4>
						<PurchaseAttachments
							v-if="editable"
							editable
							:pending="editor.attachments"
							:label="__('Attach File')"
							:accept="OPNAME_ATTACHMENT_ACCEPT"
							:hint="__('Image or document · max 10 MB')"
							@add="attachFiles"
							@remove="removeAttachment"
						/>
						<PurchaseAttachments v-else :attached="editor.attachments" />
						<template v-if="editable">
							<div class="mt-3 flex gap-2">
								<input
									v-model="linkInput"
									type="text"
									inputmode="url"
									data-testid="attach-link"
									:placeholder="__('Paste a link (https://...)')"
									:aria-label="__('Link')"
									class="min-w-0 flex-1 rounded-lg border px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
									:class="linkInput && !linkOk ? 'border-red-400' : 'border-gray-300'"
									@keyup.enter="attachLink"
								/>
								<Button variant="subtle" :loading="attaching" :disabled="!linkOk" @click="attachLink">
									<template #prefix>
										<FeatherIcon name="link" class="h-4 w-4" />
									</template>
									{{ __("Add Link") }}
								</Button>
							</div>
							<p v-if="linkInput && !linkOk" class="mt-1 text-xs text-red-600">
								{{ __("Link must start with http:// or https://") }}
							</p>
						</template>
					</section>

					<!-- Activity -->
					<section
						v-if="editor.activity?.length"
						data-testid="opname-activity"
						class="rounded-xl border border-gray-200 bg-white p-4"
					>
						<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Activity") }}</h4>
						<ul class="space-y-2">
							<li
								v-for="(entry, i) in editor.activity"
								:key="i"
								data-testid="activity-entry"
								class="flex flex-wrap items-baseline gap-x-2 text-sm"
							>
								<span class="font-medium text-gray-900">{{ entry.user_fullname || entry.user }}</span>
								<span class="text-gray-700">{{ activityLabel(entry) }}</span>
								<span class="text-xs text-gray-400" :title="entry.at">{{ relativeTime(entry.at) }}</span>
							</li>
						</ul>
					</section>
				</template>
			</div>
		</template>
	</DialogHost>

	<ConfirmDialog
		v-model="confirm.open"
		:title="confirm.title"
		:message="confirm.message"
		:confirm-label="confirm.confirmLabel"
		:destructive="confirm.destructive"
		@confirm="resolveConfirm(true)"
		@cancel="resolveConfirm(false)"
	/>
</template>

<script setup>
import { Button, FeatherIcon, call } from "frappe-ui"
import { computed, reactive, ref, watch } from "vue"
import DialogHost from "@/components/common/DialogHost.js"
import ConfirmDialog from "../common/ConfirmDialog.vue"
import RefreshButton from "@/components/common/RefreshButton.vue"
import PurchaseAttachments from "@/components/purchase/PurchaseAttachments.vue"
import { useFormatters } from "@/composables/useFormatters"
import { useToast } from "@/composables/useToast"
import { ATTACHMENT_ACCEPT, fileToBase64, sanitizePickedFiles } from "@/utils/attachments"
import { downloadXlsx } from "@/utils/downloadXlsx"

const props = defineProps({
	modelValue: Boolean,
	posProfile: String,
	embedded: { type: Boolean, default: false },
})

const emit = defineEmits(["update:modelValue"])

const { showSuccess, showError } = useToast()
const { formatDate, formatTime, formatDateTime } = useFormatters()

const show = ref(props.modelValue)
watch(
	() => props.modelValue,
	(v) => {
		show.value = v
		if (v) {
			view.value = "list"
			loadList()
		}
	},
)
watch(show, (v) => emit("update:modelValue", v))

const API = "pos_next.api.stock_opname"

// view: "list" | "doc"
const view = ref("list")
const opnames = ref([])
const loading = ref(false)
const loadingDetail = ref(false)
const editor = ref(null)
// JSON snapshot of the editable fields at load / last save; drives `dirty`
const snapshot = ref(null)

const itemSearch = ref("")
const itemResults = ref([])
const batchPicker = ref(null)
const itemGroups = ref([])
const itemGroup = ref("")

const saving = ref(false)
const submitting = ref(false)
const acting = ref(false)
const importing = ref(false)
const importResult = ref(null)
const attaching = ref(false)
const actionsMenuOpen = ref(false)
const docMenuOpen = ref(false)
// opname evidence also comes as office documents (count sheets, BA)
const OPNAME_ATTACHMENT_ACCEPT = `${ATTACHMENT_ACCEPT},.doc,.docx,.xls,.xlsx,.csv,.txt,.ppt,.pptx,.odt,.ods`
const linkInput = ref("")
const linkOk = computed(() => /^https?:\/\/\S+$/i.test(linkInput.value.trim()))

const dialogTitle = computed(() => __("Stock Opname"))

// ---------------------------------------------------------------------------
// Confirmation dialog (promise helper over the shared ConfirmDialog)
// ---------------------------------------------------------------------------

const confirm = reactive({
	open: false,
	title: "",
	message: "",
	confirmLabel: "",
	destructive: false,
})
let confirmResolve = null

// cancel may fire twice on overlay/Esc: resolving once keeps the promise honest
function confirmAsk({ title, message = "", confirmLabel = "", destructive = false }) {
	confirm.title = title
	confirm.message = message
	confirm.confirmLabel = confirmLabel
	confirm.destructive = destructive
	confirm.open = true
	return new Promise((resolve) => {
		confirmResolve = resolve
	})
}

function resolveConfirm(value) {
	confirm.open = false
	const resolve = confirmResolve
	confirmResolve = null
	if (resolve) resolve(value)
}

// ---------------------------------------------------------------------------
// Status indicator (tones derived from payload flags, never from state names)
// ---------------------------------------------------------------------------

// initial/returned red, other open states orange, background submit blue,
// submitted green, cancelled grey
function toneClass(row) {
	if (!row) return "bg-gray-400"
	if (row.docstatus === 1) return "bg-green-500"
	if (row.docstatus === 2) return "bg-gray-400"
	if (row.processing) return "bg-blue-500"
	if (row.returned || row.editable) return "bg-red-500"
	return "bg-orange-400"
}

const editable = computed(() => !!editor.value?.editable)
const actions = computed(() => editor.value?.actions || [])

// One primary action like Desk: Save while there is something to save, else
// the single workflow action, else an Actions dropdown.
const primaryMode = computed(() => {
	if (!editor.value || !editable.value) return null
	if (!editor.value.name || dirty.value) return "save"
	if (actions.value.length === 1) return "submit"
	if (actions.value.length > 1) return "actions"
	return null
})

// Continue opens only an editable count; a pending opname opens read-only.
const openOpname = computed(() =>
	opnames.value.find((o) => !o.docstatus && o.editable),
)

// One open count per warehouse: any docstatus-0 opname blocks starting a new one (D5).
const hasOpenOpname = computed(() => opnames.value.some((o) => !o.docstatus))

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

async function loadList() {
	if (!props.posProfile) return
	loading.value = true
	try {
		opnames.value =
			(await call(`${API}.list_opnames`, { pos_profile: props.posProfile })) ||
			[]
	} catch (err) {
		showError(errMsg(err, "Failed to load stock opname"))
	} finally {
		loading.value = false
	}
}

// ---------------------------------------------------------------------------
// Doc page
// ---------------------------------------------------------------------------

// Stable per-row key: index keys shift state when a middle row is removed.
let rowSeq = 0
function blankRow(fields = {}) {
	return {
		key: ++rowSeq,
		item_code: "",
		item_name: "",
		stock_uom: "",
		uom: "",
		uoms: [],
		qty: 0,
		batch_no: null,
		has_batch_no: 0,
		has_serial_no: 0,
		serial_nos: [],
		serial_options: [],
		is_serial: false,
		...fields,
	}
}

// has_serial_no is the item flag (not stock data): a serial row with zero
// ticked serials is still a serial row and must save serial_nos: [].
function normalizeItem(item) {
	const serials = Array.isArray(item.serial_nos) ? item.serial_nos : []
	const is_serial = !!item.has_serial_no || serials.length > 0
	return blankRow({
		item_code: item.item_code,
		item_name: item.item_name || item.item_code,
		stock_uom: item.stock_uom,
		// Count UOM roundtrip: a saved count restores its UOM and counted qty.
		uom: item.custom_uom || item.stock_uom,
		qty: item.custom_qty_after ?? item.qty,
		batch_no: item.batch_no || null,
		has_batch_no: item.has_batch_no,
		has_serial_no: item.has_serial_no,
		serial_nos: [...serials],
		serial_options: [...serials],
		is_serial,
	})
}

function localDate() {
	const d = new Date()
	const m = String(d.getMonth() + 1).padStart(2, "0")
	const day = String(d.getDate()).padStart(2, "0")
	return `${d.getFullYear()}-${m}-${day}`
}

function localTime() {
	const d = new Date()
	return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
}

function snapshotOf() {
	const e = editor.value
	if (!e) return null
	return JSON.stringify({
		items: buildItems(),
		set_posting_time: e.set_posting_time ? 1 : 0,
		posting_date: e.posting_date || "",
		posting_time: e.posting_time || "",
	})
}

const dirty = computed(() => {
	if (!editor.value || snapshot.value === null) return false
	return snapshotOf() !== snapshot.value
})

function takeSnapshot() {
	snapshot.value = snapshotOf()
}

function applyEditor(payload) {
	editor.value = {
		name: payload.name,
		workflow_state: payload.workflow_state,
		docstatus: payload.docstatus,
		modified: payload.modified,
		reject_reason: payload.reject_reason,
		set_warehouse: payload.set_warehouse,
		posting_date: payload.posting_date || localDate(),
		posting_time: (payload.posting_time || localTime()).slice(0, 5),
		// display-only prefill: only a ticked box sends posting fields (N5)
		set_posting_time: !!payload.set_posting_time,
		owner: payload.owner,
		owner_fullname: payload.owner_fullname,
		creation: payload.creation,
		modified_by_fullname: payload.modified_by_fullname,
		editable: !!payload.editable,
		returned: !!payload.returned,
		processing: !!payload.processing,
		actions: payload.actions || [],
		activity: payload.activity || [],
		items: (payload.items || []).map(normalizeItem),
		attachments: payload.attachments || [],
	}
	actionsMenuOpen.value = false
	docMenuOpen.value = false
	takeSnapshot()
}

async function openDoc(name) {
	view.value = "doc"
	loadingDetail.value = true
	importResult.value = null
	batchPicker.value = null
	linkInput.value = ""
	clearSearch()
	try {
		applyEditor(
			await call(`${API}.get_opname`, { pos_profile: props.posProfile, name }),
		)
		if (!itemGroups.value.length) loadGroups()
		ensureSerialOptions()
	} catch (err) {
		showError(errMsg(err, "Failed to load stock opname"))
		goList()
	} finally {
		loadingDetail.value = false
	}
}

function openNew() {
	editor.value = {
		name: null,
		workflow_state: "",
		docstatus: 0,
		modified: null,
		reject_reason: null,
		set_warehouse: "",
		posting_date: localDate(),
		posting_time: localTime(),
		set_posting_time: false,
		owner_fullname: "",
		creation: null,
		modified_by_fullname: "",
		editable: true,
		returned: false,
		processing: false,
		actions: [],
		activity: [],
		items: [],
		attachments: [],
	}
	linkInput.value = ""
	view.value = "doc"
	importResult.value = null
	batchPicker.value = null
	actionsMenuOpen.value = false
	docMenuOpen.value = false
	clearSearch()
	if (!itemGroups.value.length) loadGroups()
	takeSnapshot()
}

// Guarded: leaving with unsaved edits would lose them silently (N2).
async function backToList() {
	if (editable.value && dirty.value) {
		const ok = await confirmAsk({
			title: __("Discard unsaved changes?"),
			message: __("Your edits will be lost."),
			confirmLabel: __("Discard"),
			destructive: true,
		})
		if (!ok) return
	}
	goList()
}

function goList() {
	editor.value = null
	snapshot.value = null
	importResult.value = null
	batchPicker.value = null
	linkInput.value = ""
	clearSearch()
	actionsMenuOpen.value = false
	docMenuOpen.value = false
	view.value = "list"
	loadList()
}

// A loaded serial row only knows its ticked serials; fetch the full list so a
// fresh physical serial can still be ticked or scanned.
async function ensureSerialOptions() {
	for (const row of editor.value?.items || []) {
		if (!row.is_serial) continue
		try {
			const all = await call(`${API}.get_item_serials`, {
				pos_profile: props.posProfile,
				item_code: row.item_code,
			})
			row.serial_options = Array.from(
				new Set([...(all || []), ...row.serial_nos]),
			)
		} catch {
			// options stay as the loaded ticked serials
		}
	}
}

async function loadGroups() {
	try {
		itemGroups.value =
			(await call("pos_next.api.items.get_item_groups", {
				pos_profile: props.posProfile,
			})) || []
	} catch {
		itemGroups.value = [] // the filter is optional; the sheet still works
	}
}

// ---------------------------------------------------------------------------
// Activity timeline
// ---------------------------------------------------------------------------

function relativeTime(value) {
	if (!value) return ""
	// "YYYY-MM-DD HH:MM:SS" parses as Invalid Date on Safari
	const then = new Date(String(value).replace(" ", "T")).getTime()
	if (Number.isNaN(then)) return ""
	const minutes = Math.floor((Date.now() - then) / 60000)
	if (minutes < 1) return __("just now")
	if (minutes < 60) return __("{0} minutes ago", [minutes])
	const hours = Math.floor(minutes / 60)
	if (hours < 24) return __("{0} hours ago", [hours])
	return __("{0} days ago", [Math.floor(hours / 24)])
}

function activityLabel(entry) {
	if (entry.type === "created") return __("Created")
	if (entry.type === "workflow") return entry.state || __("Status updated")
	if (entry.type === "attachment") return __("Attachment added")
	return entry.content || ""
}

// ---------------------------------------------------------------------------
// Item search / add
// ---------------------------------------------------------------------------

let searchTimer = null
let searchSeq = 0

function searchItems() {
	clearTimeout(searchTimer)
	const term = itemSearch.value.trim()
	if (!term) {
		searchSeq++
		itemResults.value = []
		return
	}
	searchTimer = setTimeout(() => doSearchItems(term), 350)
}

async function doSearchItems(term) {
	const seq = ++searchSeq
	try {
		const rows = await call(`${API}.search_opname_items`, {
			pos_profile: props.posProfile,
			search_term: term,
			start: 0,
			limit: 10,
		})
		if (seq === searchSeq) itemResults.value = rows || []
	} catch (err) {
		if (seq === searchSeq) showError(errMsg(err, "Failed to search items"))
	}
}

// Barcode scan: Enter picks the exact item_code from the current results.
function addExactMatch() {
	const term = itemSearch.value.trim()
	const exact = itemResults.value.find((i) => i.item_code === term)
	if (exact) addItem(exact)
}

function clearSearch() {
	searchSeq++
	clearTimeout(searchTimer)
	itemSearch.value = ""
	itemResults.value = []
}

// The search endpoint already excludes the stock UOM; offer it first.
function uomOptionsOf(row) {
	return [...new Set([row.stock_uom, ...(row.uoms || [])].filter(Boolean))]
}

function altUoms(item) {
	return (item.uoms || []).map((u) => u.uom)
}

async function addItem(item) {
	clearSearch()
	if (item.has_serial_no) {
		if (editor.value.items.some((r) => r.item_code === item.item_code)) return
		const serials =
			(await call(`${API}.get_item_serials`, {
				pos_profile: props.posProfile,
				item_code: item.item_code,
			})) || []
		editor.value.items.push(
			blankRow({
				item_code: item.item_code,
				item_name: item.item_name || item.item_code,
				stock_uom: item.stock_uom,
				has_batch_no: item.has_batch_no,
				has_serial_no: item.has_serial_no,
				serial_options: serials,
				is_serial: true,
			}),
		)
	} else if (item.has_batch_no) {
		const batches =
			(await call(`${API}.get_item_batches`, {
				pos_profile: props.posProfile,
				item_code: item.item_code,
			})) || []
		batchPicker.value = {
			item_code: item.item_code,
			item_name: item.item_name || item.item_code,
			stock_uom: item.stock_uom,
			uoms: altUoms(item),
			batches,
			selected: [],
		}
	} else {
		if (editor.value.items.some((r) => r.item_code === item.item_code)) return
		editor.value.items.push(
			blankRow({
				item_code: item.item_code,
				item_name: item.item_name || item.item_code,
				stock_uom: item.stock_uom,
				uom: item.stock_uom,
				uoms: altUoms(item),
				qty: 1,
				has_batch_no: item.has_batch_no,
				has_serial_no: item.has_serial_no,
			}),
		)
	}
}

function addBatchRows() {
	const picker = batchPicker.value
	if (!picker) return
	for (const batch_no of picker.selected) {
		if (
			editor.value.items.some(
				(r) => r.item_code === picker.item_code && r.batch_no === batch_no,
			)
		)
			continue
		editor.value.items.push(
			blankRow({
				item_code: picker.item_code,
				item_name: picker.item_name,
				stock_uom: picker.stock_uom,
				uom: picker.stock_uom,
				uoms: picker.uoms || [],
				batch_no,
				has_batch_no: 1,
			}),
		)
	}
	batchPicker.value = null
}

function toggleSerial(row, serial) {
	const i = row.serial_nos.indexOf(serial)
	if (i === -1) row.serial_nos.push(serial)
	else row.serial_nos.splice(i, 1)
}

function scanSerial(row, event) {
	const value = (event.target.value || "").trim()
	event.target.value = ""
	if (!value) return
	if (!row.serial_options.includes(value)) {
		showError(__("Unknown serial"))
		return
	}
	if (!row.serial_nos.includes(value)) row.serial_nos.push(value)
}

function buildItems() {
	return editor.value.items.map((r) => {
		const out = {
			item_code: r.item_code,
			qty: r.is_serial ? r.serial_nos.length : Number(r.qty) || 0,
		}
		if (r.batch_no) out.batch_no = r.batch_no
		if (r.is_serial) {
			// serial rows never carry a count UOM (serials are ticked)
			out.serial_nos = [...r.serial_nos]
		} else {
			out.custom_uom = r.uom || r.stock_uom
			out.custom_qty_after = Number(r.qty) || 0
		}
		return out
	})
}

// ---------------------------------------------------------------------------
// Save / submit / delete
// ---------------------------------------------------------------------------

async function save() {
	if (saving.value) return false
	saving.value = true
	try {
		const data = {
			name: editor.value.name || undefined,
			modified: editor.value.modified || undefined,
			items: buildItems(),
		}
		// posting fields only when the user opted in (N5)
		if (editor.value.set_posting_time) {
			data.set_posting_time = 1
			data.posting_date = editor.value.posting_date || undefined
			data.posting_time = editor.value.posting_time || undefined
		}
		const payload = await call(`${API}.save_opname`, {
			pos_profile: props.posProfile,
			data,
		})
		applyEditor(payload)
		// a save response only carries the ticked serials; refetch so the
		// remaining serials of a serial item stay tickable
		await ensureSerialOptions()
		showSuccess(__("Stock opname saved"))
		return true
	} catch (err) {
		// a stale save comes back as "modified, reload" — surface the server text
		showError(errMsg(err, "Failed to save stock opname"))
		return false
	} finally {
		saving.value = false
	}
}

async function submitAction(action) {
	if (submitting.value || !action) return
	actionsMenuOpen.value = false
	const ok = await confirmAsk({
		title: __("Submit Stock Opname"),
		message: __("After submitting you cannot edit until HQ decides"),
		confirmLabel: action,
	})
	if (!ok) return
	submitting.value = true
	try {
		// a link typed but not added yet would be lost once the doc locks
		if (linkInput.value.trim()) {
			if (!linkOk.value) {
				showError(__("Link must start with http:// or https://"))
				return
			}
			if (!(await sendAttachments({ link: linkInput.value.trim() }))) return
			linkInput.value = ""
		}
		await call(`${API}.submit_opname`, {
			pos_profile: props.posProfile,
			name: editor.value.name,
			action,
		})
		showSuccess(__("Stock opname submitted"))
		goList()
	} catch (err) {
		showError(errMsg(err, "Failed to submit stock opname"))
	} finally {
		submitting.value = false
	}
}

async function remove() {
	if (acting.value || !editor.value?.name) return
	docMenuOpen.value = false
	const ok = await confirmAsk({
		title: __("Delete Stock Opname"),
		message: __("Delete this stock opname?"),
		confirmLabel: __("Delete"),
		destructive: true,
	})
	if (!ok) return
	acting.value = true
	try {
		await call(`${API}.delete_opname`, {
			pos_profile: props.posProfile,
			name: editor.value.name,
		})
		showSuccess(__("Stock opname deleted"))
		goList()
	} catch (err) {
		showError(errMsg(err, "Failed to delete stock opname"))
	} finally {
		acting.value = false
	}
}

// ---------------------------------------------------------------------------
// Excel template
// ---------------------------------------------------------------------------

async function downloadTemplate() {
	try {
		await downloadXlsx(`${API}.download_opname_template`, {
			pos_profile: props.posProfile,
			item_group: itemGroup.value || undefined,
		})
	} catch (err) {
		showError(err?.message || __("Export failed"))
	}
}

async function uploadFile(file) {
	const form = new FormData()
	form.append("file", file)
	form.append("is_private", "1")
	const response = await fetch("/api/method/upload_file", {
		method: "POST",
		headers: { "X-Frappe-CSRF-Token": window.csrf_token || "" },
		body: form,
	})
	if (!response.ok) throw new Error(__("Upload failed"))
	const body = await response.json()
	return body.message.file_url
}

async function onFilePicked(event) {
	const file = event.target.files?.[0]
	event.target.value = ""
	if (!file) return
	// An unsaved new count would be replaced wholesale by the upload: confirm.
	if (!editor.value?.name && editor.value?.items?.length) {
		const ok = await confirmAsk({
			title: __("Uploading replaces the unsaved rows. Upload anyway?"),
			confirmLabel: __("Upload"),
			destructive: true,
		})
		if (!ok) return
	}
	importing.value = true
	importResult.value = null
	try {
		const fileUrl = await uploadFile(file)
		const result = await call(`${API}.import_opname_template`, {
			pos_profile: props.posProfile,
			file_url: fileUrl,
			name: editor.value.name || undefined,
		})
		importResult.value = result
		// merge lands in the open draft: reload its rows, keep the row errors
		if (result?.name) {
			applyEditor(
				await call(`${API}.get_opname`, {
					pos_profile: props.posProfile,
					name: result.name,
				}),
			)
			ensureSerialOptions()
		}
		showSuccess(__("Template imported"))
	} catch (err) {
		showError(errMsg(err, "Failed to import template"))
	} finally {
		importing.value = false
	}
}

// ---------------------------------------------------------------------------
// Attachments (files + links, stored as File rows on the opname)
// ---------------------------------------------------------------------------

// Files attach to a real doc name: a new count is saved first.
async function ensureSaved() {
	if (editor.value.name) return true
	if (!editor.value.items.length) {
		showError(__("Add at least one counted item before attaching"))
		return false
	}
	return (await save()) && !!editor.value.name
}

async function sendAttachments(args) {
	if (attaching.value || !(await ensureSaved())) return false
	attaching.value = true
	try {
		editor.value.attachments =
			(await call(`${API}.attach_opname_files`, {
				pos_profile: props.posProfile,
				name: editor.value.name,
				...args,
			})) || []
		return true
	} catch (err) {
		showError(errMsg(err, "Failed to attach"))
		return false
	} finally {
		attaching.value = false
	}
}

async function attachFiles(files) {
	const { ok, tooBig } = sanitizePickedFiles(files)
	if (tooBig.length) showError(__("Some files exceed the 10 MB limit"))
	if (!ok.length) return
	const payloads = await Promise.all(
		ok.map(async (file) => ({ file_name: file.name, filedata: await fileToBase64(file) })),
	)
	if (await sendAttachments({ files: JSON.stringify(payloads) }))
		showSuccess(__("{0} attachment(s) uploaded", [ok.length]))
}

async function attachLink() {
	if (!linkOk.value) return
	if (await sendAttachments({ link: linkInput.value.trim() })) linkInput.value = ""
}

async function removeAttachment(row) {
	const ok = await confirmAsk({
		title: __("Remove this attachment?"),
		confirmLabel: __("Remove"),
		destructive: true,
	})
	if (!ok) return
	try {
		editor.value.attachments =
			(await call(`${API}.remove_opname_attachment`, {
				pos_profile: props.posProfile,
				name: editor.value.name,
				file: row.name,
			})) || []
	} catch (err) {
		showError(errMsg(err, "Failed to remove attachment"))
	}
}

function errMsg(err, fallback) {
	const text =
		err?.messages?.join("\n") ||
		(typeof err === "string" ? err : err?.message) ||
		""
	return text || __(fallback)
}

if (props.modelValue) {
	view.value = "list"
	loadList()
}
</script>
