<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: dialogTitle, size: '4xl' }"
	>
		<template #body-content>
			<!-- LIST -->
			<div v-if="view === 'list'" class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<div
					v-if="statusTabs.length > 1"
					class="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5"
					role="group"
					:aria-label="__('Status')"
				>
					<button
						v-for="tab in statusTabs"
						:key="tab.value"
						type="button"
						data-testid="status-chip"
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
					<div class="relative min-w-0 flex-1 sm:w-72 sm:flex-none">
						<FeatherIcon
							name="search"
							class="pointer-events-none absolute start-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
						/>
						<input
							v-model="searchTerm"
							type="search"
							data-testid="list-search"
							class="h-[44px] sm:h-10 w-full rounded-lg border border-gray-200 bg-white ps-8 pe-2 text-sm focus:border-gray-400 focus:outline-none"
							:placeholder="__('Search ID or item...')"
							:aria-label="__('Search ID or item...')"
							@input="onSearchInput"
						/>
					</div>
					<div class="ms-auto flex items-center gap-2">
						<RefreshButton :loading="loading" @click="loadList()" />
						<Button
							v-if="page === 0 && !filtered && !hasOpenOpname"
							variant="solid"
							class="h-[44px] sm:h-10 touch-manipulation"
							@click="openNew"
						>
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
						<p class="mt-1 text-xs text-gray-500">
							{{ filtered ? __("Try a different search or status") : __("Start one with New Count") }}
						</p>
					</div>
					<table v-else class="w-full text-sm">
						<thead>
							<tr
								data-testid="list-header"
								class="hidden border-b border-gray-200 bg-gray-50 text-xs font-medium text-gray-500 md:table-row"
							>
								<th class="px-4 py-2 text-start font-medium">{{ __("ID") }}</th>
								<th class="px-4 py-2 text-start font-medium">{{ __("Posting Date") }}</th>
								<th class="px-4 py-2 text-end font-medium">{{ __("Items") }}</th>
								<th class="w-44 px-4 py-2 text-start font-medium">{{ __("Status") }}</th>
								<th class="w-px px-4 py-2"><span class="sr-only">{{ __("Actions") }}</span></th>
							</tr>
						</thead>
						<tbody class="divide-y divide-gray-100">
							<tr
								v-for="o in opnames"
								:key="o.name"
								tabindex="0"
								data-testid="opname-row"
								class="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-4 py-3 transition-colors hover:bg-gray-50 focus:outline-none focus-visible:bg-gray-50 md:table-row md:p-0"
								@click="openDoc(o.name)"
								@keydown.enter="openDoc(o.name)"
							>
								<td class="order-1 col-span-2 min-w-0 font-semibold text-gray-900 md:px-4 md:py-3">
									{{ o.name }}
									<p
										v-if="o.returned && o.reject_reason"
										class="line-clamp-2 text-xs font-normal text-red-600"
										:title="o.reject_reason"
									>
										{{ o.reject_reason }}
									</p>
								</td>
								<td class="order-3 text-gray-700 md:px-4 md:py-3">{{ formatDate(o.posting_date) }}</td>
								<td class="order-4 text-end text-xs tabular-nums text-gray-500 md:px-4 md:py-3 md:text-sm md:text-gray-900">
									<span class="md:hidden">{{ __("{0} items", [o.item_count]) }}</span>
									<span class="hidden md:inline">{{ o.item_count }}</span>
								</td>
								<!-- status gets its own card line (workflow states can be long), before Actions on desktop -->
								<td class="order-2 col-span-2 min-w-0 md:px-4 md:py-3">
									<span data-testid="status-pill" :class="statusPill(o)">{{ o.workflow_state }}</span>
								</td>
								<td class="order-5 col-span-2 text-end empty:hidden md:px-4 md:py-3 md:empty:table-cell">
									<Button
										v-if="!o.docstatus && o.editable"
										data-testid="row-continue"
										variant="solid"
										class="h-[44px] sm:h-10 touch-manipulation"
										@click.stop="openDoc(o.name)"
									>
										<template #prefix>
											<FeatherIcon name="edit-2" class="h-3.5 w-3.5" />
										</template>
										{{ __("Continue") }}
									</Button>
								</td>
							</tr>
						</tbody>
					</table>
				</div>
				<div
					v-if="page > 0 || hasMore"
					data-testid="list-pager"
					class="flex items-center justify-end gap-2 text-sm"
				>
					<Button :disabled="loading || page === 0" @click="goPage(page - 1)">
						{{ __("Previous") }}
					</Button>
					<span class="px-1 tabular-nums text-gray-500">{{ __("Page {0}", [page + 1]) }}</span>
					<Button :disabled="loading || !hasMore" @click="goPage(page + 1)">
						{{ __("Next") }}
					</Button>
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
							class="-ms-1 flex h-[44px] w-[44px] sm:h-10 sm:w-10 shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-900 touch-manipulation"
							:aria-label="__('Back')"
							:title="__('Back')"
							@click="backToList"
						>
							<FeatherIcon name="arrow-left" class="h-5 w-5" />
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
								class="h-[44px] sm:h-10 touch-manipulation"
								data-testid="primary-action"
								:loading="saving"
								@click="save"
							>
								{{ __("Save") }}
							</Button>
							<Button
								v-else-if="primaryMode === 'submit'"
								variant="solid"
								class="h-[44px] sm:h-10 touch-manipulation"
								data-testid="primary-action"
								:loading="submitting"
								@click="submitAction(actions[0])"
							>
								{{ actions[0] }}
							</Button>
							<div v-else-if="primaryMode === 'actions'" class="relative">
								<Button
									variant="solid"
									class="h-[44px] sm:h-10 touch-manipulation"
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
									class="pos-touch-target flex items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-900 touch-manipulation"
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
							<div v-if="editor.set_warehouse" class="min-w-0">
								<p class="text-xs text-gray-500">{{ __("Warehouse") }}</p>
								<p class="truncate text-sm text-gray-900">{{ editor.set_warehouse }}</p>
							</div>
							<div class="col-span-2 min-w-0 md:col-span-3">
								<p class="mb-1 text-xs text-gray-500">{{ __("Posting Date") }}</p>
								<template v-if="editable">
									<div class="flex flex-wrap items-center gap-2">
										<input
											v-model="editor.posting_date"
											type="date"
											data-testid="posting-date-input"
											:aria-label="__('Posting Date')"
											:disabled="!editor.set_posting_time"
											class="h-[44px] sm:h-10 rounded-md border border-gray-300 px-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 disabled:bg-gray-50 disabled:text-gray-500"
										/>
										<input
											v-model="editor.posting_time"
											type="time"
											data-testid="posting-time-input"
											:aria-label="__('Posting Time')"
											:disabled="!editor.set_posting_time"
											class="h-[44px] sm:h-10 rounded-md border border-gray-300 px-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 disabled:bg-gray-50 disabled:text-gray-500"
										/>
										<label class="ms-1 flex min-h-[44px] sm:min-h-[40px] items-center gap-2 text-sm text-gray-700 touch-manipulation">
											<input
												v-model="editor.set_posting_time"
												type="checkbox"
												data-testid="posting-toggle"
											/>
											{{ __("Edit Posting Date and Time") }}
										</label>
									</div>
								</template>
								<p v-else class="truncate text-sm text-gray-900">
									{{ formatDate(editor.posting_date) }} · {{ formatTime(editor.posting_time) }}
								</p>
							</div>
							<div v-if="editor.name" class="min-w-0">
								<p class="text-xs text-gray-500">{{ __("Created By") }}</p>
								<p class="truncate text-sm text-gray-900">{{ editor.owner_fullname }}</p>
								<p class="truncate text-xs text-gray-400">{{ formatDateTime(editor.creation) }}</p>
							</div>
							<div v-if="editor.name" class="min-w-0">
								<p class="text-xs text-gray-500">{{ __("Last Modified") }}</p>
								<p class="truncate text-sm text-gray-900">{{ editor.modified_by_fullname }}</p>
								<p class="truncate text-xs text-gray-400">{{ formatDateTime(editor.modified) }}</p>
							</div>
						</div>
					</section>

					<!-- Items -->
					<section class="rounded-xl border border-gray-200 bg-white">
						<div class="flex flex-wrap items-center justify-between gap-2 px-4 pb-3 pt-4">
							<h4 class="text-sm font-semibold text-gray-900">{{ __("Items") }}</h4>
							<!-- Excel tools share the section header (N4) -->
							<div v-if="editable" data-testid="grid-toolbar" class="flex w-full flex-wrap items-center gap-2 sm:w-auto">
								<select
									id="so-item-group"
									v-model="itemGroup"
									:aria-label="__('Item Group')"
									:title="__('Item Group')"
									class="h-[44px] sm:h-10 min-w-0 flex-1 sm:flex-none rounded-md border border-gray-300 bg-white py-0 pe-8 ps-2 text-sm text-gray-700 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
								>
									<option value="">{{ __("Items with transactions") }}</option>
									<option v-for="g in itemGroups" :key="g.item_group" :value="g.item_group">
										{{ g.item_group }}
									</option>
								</select>
								<Button variant="subtle" class="h-[44px] sm:h-10 touch-manipulation" data-testid="fetch-items" :loading="fetching" @click="fetchItems">
									<template #prefix>
										<FeatherIcon name="list" class="h-4 w-4" />
									</template>
									{{ __("Fetch Items") }}
								</Button>
								<Button variant="subtle" class="h-[44px] sm:h-10 touch-manipulation" @click="downloadTemplate">
									<template #prefix>
										<FeatherIcon name="download" class="h-4 w-4" />
									</template>
									{{ __("Download Template") }}
								</Button>
								<label
									class="inline-flex h-[44px] sm:h-10 cursor-pointer items-center gap-1.5 rounded bg-gray-100 px-2 text-base touch-manipulation text-gray-800 hover:bg-gray-200"
									:class="{ 'pointer-events-none opacity-60': importing }"
								>
									<input type="file" accept=".xlsx" class="hidden" :disabled="importing" @change="onFilePicked" />
									<FeatherIcon name="upload" class="h-4 w-4" />
									{{ importing ? __("Uploading...") : __("Upload") }}
								</label>
							</div>
						</div>

						<!-- read-only -->
						<template v-if="!editable">
							<div
								class="hidden border-y border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500 md:grid md:grid-cols-[2rem_minmax(0,3fr)_minmax(0,1fr)_minmax(0,1fr)_5rem] md:gap-x-3"
							>
								<span>{{ __("No.") }}</span>
								<span>{{ __("Item") }}</span>
								<span>{{ __("Qty") }}</span>
								<span>{{ __("UOM") }}</span>
								<span>{{ __("Photo") }}</span>
							</div>
							<div
								v-for="(row, idx) in editor.items"
								:key="idx"
								class="grid grid-cols-2 items-center gap-x-3 gap-y-1 border-b border-gray-100 px-4 py-2 text-sm last:border-b-0 md:grid-cols-[2rem_minmax(0,3fr)_minmax(0,1fr)_minmax(0,1fr)_5rem] md:gap-y-0 md:py-2"
							>
								<span class="hidden text-xs tabular-nums text-gray-400 md:block">{{ idx + 1 }}</span>
								<div class="col-span-2 min-w-0 md:col-span-1">
									<p class="truncate font-medium text-gray-900">{{ row.item_name || row.item_code }}</p>
									<p v-if="row.item_code !== row.item_name" class="truncate text-xs text-gray-400">
										{{ row.item_code }}
									</p>
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
									<span class="mb-0.5 block text-xs text-gray-500 md:hidden">{{ __("Qty") }}</span>
									<span class="block tabular-nums">{{ row.qty }}</span>
								</div>
								<div class="min-w-0">
									<span class="mb-0.5 block text-xs text-gray-500 md:hidden">{{ __("UOM") }}</span>
									<span class="truncate text-gray-700">{{ row.uom }}</span>
								</div>
								<div class="col-span-2 flex items-center md:col-span-1">
									<a
										v-if="row.pos_opname_photo"
										:href="row.pos_opname_photo"
										target="_blank"
										rel="noopener noreferrer"
										data-testid="readonly-photo"
										class="block shrink-0 overflow-hidden rounded border border-gray-200 hover:opacity-80"
										:title="__('View proof photo')"
									>
										<img :src="row.pos_opname_photo" class="h-8 w-8 object-cover" :alt="__('Proof photo')" />
									</a>
									<span v-else class="hidden text-xs text-gray-300 md:inline">-</span>
								</div>
							</div>
							<p v-if="!editor.items.length" class="px-4 py-6 text-center text-sm text-gray-400">
								{{ __("No items") }}
							</p>
						</template>

						<!-- editable -->
						<template v-else>
							<div
								class="hidden border-y border-gray-200 bg-gray-50 px-3 py-2 text-xs font-medium text-gray-500 xl:grid xl:grid-cols-[2rem_1.75rem_minmax(0,2.5fr)_minmax(0,1fr)_7.5rem_8rem_8rem_2.5rem] xl:items-center xl:gap-x-3"
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
								<span>{{ __("No.") }}</span>
								<span>{{ __("Item") }}</span>
								<span>{{ __("Batch") }}</span>
								<span>{{ __("Qty") }}</span>
								<span>{{ __("UOM") }}</span>
								<span>{{ __("Photo") }}</span>
								<span></span>
							</div>

							<p
								v-if="!editor.items.length"
								class="border-b border-gray-100 px-4 py-6 text-center text-sm text-gray-400"
							>
								{{ __("No Data") }}
							</p>

							<div data-testid="grid-rows" class="p-2 xl:p-0">
								<div
									v-for="(row, idx) in editor.items"
									:key="row.key"
									class="mb-3 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2.5 rounded-lg border border-gray-200 bg-white p-3.5 shadow-xs last:mb-0 xl:mb-0 xl:rounded-none xl:border-0 xl:border-b xl:border-gray-100 xl:px-3 xl:shadow-none xl:last:border-b-0 xl:grid-cols-[2rem_1.75rem_minmax(0,2.5fr)_minmax(0,1fr)_7.5rem_8rem_8rem_2.5rem] xl:gap-y-0 xl:py-1.5 xl:hover:bg-gray-50/50"
									:class="{ 'rounded-md ring-2 ring-red-500 bg-red-50/20': row.photoRequired }"
								>
									<!-- phone: checkbox + row label head the card; xl: checkbox column -->
									<div class="col-span-2 flex items-center gap-2 xl:col-span-1 xl:justify-center">
										<input
											v-model="row.selected"
											type="checkbox"
											data-testid="row-checkbox"
											:aria-label="__('Select row {0}', [idx + 1])"
											class="h-5 w-5 rounded border-gray-300 text-blue-600 focus:ring-blue-500 xl:h-4 xl:w-4"
										/>
										<span class="text-xs font-semibold text-gray-700 xl:hidden">{{ __("Row #{0}", [idx + 1]) }}</span>
									</div>
									<span class="hidden text-xs tabular-nums text-gray-400 xl:block">{{ idx + 1 }}</span>

									<!-- Item cell: a link field until an item is picked -->
									<div class="relative col-span-3 min-w-0 xl:col-span-1">
										<button
											v-if="row.item_code"
											type="button"
											data-testid="row-item-edit"
											class="group flex min-h-[40px] w-full items-center justify-between rounded border border-gray-200 bg-gray-50/70 px-2.5 py-1.5 text-start transition-colors hover:border-gray-300 hover:bg-gray-100/70 xl:min-h-0 xl:py-1"
											:title="__('Click to change item')"
											@click="editRowItem(row, idx)"
										>
											<span class="min-w-0 pr-1">
												<span class="block truncate text-sm font-medium text-gray-900">{{ row.item_name || row.item_code }}</span>
												<span v-if="row.item_code !== row.item_name" class="block truncate text-xs text-gray-400">
													{{ row.item_code }}
												</span>
											</span>
											<FeatherIcon name="edit-2" class="h-3.5 w-3.5 shrink-0 text-gray-400 opacity-60 transition-opacity xl:opacity-0 xl:group-hover:opacity-100 xl:group-focus-visible:opacity-100" />
										</button>
										<template v-else>
											<input
												v-model="row.search"
												type="text"
												data-testid="row-item-input"
												:data-cell="`${idx}-item`"
												:placeholder="__('Search item...')"
												:aria-label="__('Item')"
												class="h-10 min-h-[40px] w-full rounded-md border border-dashed border-gray-300 bg-gray-50/50 px-2.5 py-1.5 text-sm focus:border-solid focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30 xl:h-8 xl:min-h-0"
												@input="searchRow(row)"
												@keyup.enter="addExactMatch(row)"
												@keyup.escape="closeResults(row)"
												@blur="blurRow(row)"
											/>
											<div
												v-if="row.results.length"
												class="absolute inset-x-0 top-full z-10 mt-1 max-h-56 overflow-y-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
											>
												<button
													v-for="i in row.results"
													:key="i.item_code"
													type="button"
													data-testid="row-item-result"
													class="flex w-full items-center justify-between gap-3 px-3 py-2 text-start text-sm hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-none"
													@click="pickItem(row, i)"
												>
													<span class="truncate text-gray-900">{{ i.item_name || i.item_code }}</span>
													<span v-if="i.item_name && i.item_name !== i.item_code" class="shrink-0 text-xs text-gray-400">
														{{ i.item_code }}
													</span>
												</button>
											</div>
										</template>
									</div>

									<!-- Batch cell: a select once the picked item needs a batch -->
									<div class="col-span-3 min-w-0 xl:col-span-1" :class="{ 'hidden xl:block': !row.has_batch_no && !row.batch_no }">
										<span class="mb-0.5 block text-xs text-gray-500 xl:hidden">{{ __("Batch") }}</span>
										<select
											v-if="row.has_batch_no"
											v-model="row.batch_no"
											data-testid="row-batch-select"
											:aria-label="`${row.item_name || row.item_code} — ${__('Batch')}`"
											class="h-10 min-h-[40px] w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-sm focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30 xl:h-8 xl:min-h-0"
											@change="checkBatchDuplicate(row)"
										>
											<option :value="null">{{ __("Select batch") }}</option>
											<option v-for="b in row.batches" :key="b.batch_no" :value="b.batch_no">
												{{ b.batch_no }}
											</option>
										</select>
										<span v-else class="truncate text-xs text-gray-500">{{ row.batch_no }}</span>
									</div>

									<div class="min-w-0">
										<span class="mb-0.5 block text-xs text-gray-500 xl:hidden">{{ __("Qty") }}</span>
										<!-- serial qty is the number of ticked serials, never typed -->
										<span
											v-if="row.is_serial"
											data-testid="item-qty"
											class="block text-sm tabular-nums text-gray-900"
										>
											{{ row.serial_nos.length }}
										</span>
										<input
											v-else-if="row.item_code"
											v-model.number="row.qty"
											:placeholder="__('Not counted')"
											type="number"
											min="0"
											step="any"
											inputmode="decimal"
											data-testid="qty-input"
											:aria-label="`${row.item_name || row.item_code} — ${__('Qty')}`"
											class="h-10 min-h-[40px] w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-sm tabular-nums focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30 xl:h-8 xl:min-h-0"
										/>
									</div>

									<div class="min-w-0">
										<span class="mb-0.5 block text-xs text-gray-500 xl:hidden">{{ __("UOM") }}</span>
										<!-- serials are counted by ticking, never in a converted UOM -->
										<span
											v-if="row.is_serial"
											data-testid="uom-readonly"
											class="block truncate text-sm text-gray-500"
										>
											{{ row.stock_uom }}
										</span>
										<select
											v-else-if="row.item_code"
											v-model="row.uom"
											data-testid="uom-select"
											:aria-label="`${row.item_name || row.item_code} — ${__('UOM')}`"
											class="h-10 min-h-[40px] w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-sm focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30 xl:h-8 xl:min-h-0"
										>
											<option v-for="u in uomOptionsOf(row)" :key="u" :value="u">{{ u }}</option>
										</select>
									</div>

									<!-- Photo cell -->
									<div class="flex min-w-0 flex-col items-start justify-center self-end xl:self-auto">
										<span v-if="row.photoRequired" data-testid="photo-required" class="mb-0.5 block text-xs font-medium text-red-600">
											{{ __("Photo required") }}
										</span>
										<div v-if="row.uploading_photo" class="flex h-11 w-11 items-center justify-center">
											<FeatherIcon name="loader" class="h-5 w-5 animate-spin text-blue-600" />
										</div>
										<div v-else-if="row.pos_opname_photo" class="flex items-center gap-1.5" data-testid="photo-thumbnail-container">
											<a
												:href="row.pos_opname_photo"
												target="_blank"
												rel="noopener noreferrer"
												data-testid="photo-thumbnail-link"
												class="block shrink-0 overflow-hidden rounded border border-gray-200 hover:opacity-80"
												:title="__('View proof photo')"
											>
												<img :src="row.pos_opname_photo" class="h-10 w-10 object-cover" :alt="__('Proof photo')" />
											</a>
											<div class="flex items-center">
												<label
													data-testid="replace-photo-btn"
													role="button"
													tabindex="0"
													:title="__('Replace photo')"
													:aria-label="__('Replace photo')"
													class="pos-touch-target flex cursor-pointer items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-600 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-blue-500"
													@keydown.enter.space.prevent="$event.target.querySelector('input')?.click()"
												>
													<input
														type="file"
														accept="image/*"
														class="hidden"
														data-testid="replace-photo-input"
														@change="handlePhotoUpload(row, $event)"
													/>
													<FeatherIcon name="refresh-cw" class="h-4 w-4" />
												</label>
												<button
													type="button"
													data-testid="remove-photo-btn"
													:title="__('Remove photo')"
													:aria-label="__('Remove photo')"
													class="pos-touch-target flex items-center justify-center rounded-lg text-gray-400 hover:bg-red-50 hover:text-red-600"
													@click="removeRowPhoto(row)"
												>
													<FeatherIcon name="trash-2" class="h-4 w-4" />
												</button>
											</div>
										</div>
										<label
											v-else
											data-testid="add-photo-btn"
											role="button"
											tabindex="0"
											:aria-label="__('Add proof photo')"
											:title="__('Add proof photo')"
											class="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-dashed border-gray-300 text-gray-400 hover:border-gray-400 hover:bg-gray-50 hover:text-gray-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
											@keydown.enter.space.prevent="$event.target.querySelector('input')?.click()"
										>
											<input
												type="file"
												accept="image/*"
												class="hidden"
												data-testid="row-photo-input"
												@change="handlePhotoUpload(row, $event)"
											/>
											<FeatherIcon name="camera" class="h-5 w-5" />
										</label>
									</div>

									<button
										type="button"
										class="pos-touch-target col-start-3 row-start-1 flex shrink-0 items-center justify-center justify-self-end rounded text-gray-400 hover:bg-red-50 hover:text-red-600 xl:col-start-auto xl:row-start-auto"
										:aria-label="__('Remove item')"
										:title="__('Remove item')"
										@click="editor.items.splice(idx, 1)"
									>
										<FeatherIcon name="trash-2" class="h-4 w-4" />
									</button>

									<!-- serial tick list: tick the serials physically present -->
									<div v-if="row.is_serial" class="col-span-3 mt-2 xl:col-span-8">
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
							</div>

							<!-- Add Row footer -->
							<div class="flex flex-wrap items-center gap-3 px-4 py-3">
								<Button variant="subtle" class="h-[44px] sm:h-10 touch-manipulation" data-testid="add-row" @click="addRow">
									<template #prefix>
										<FeatherIcon name="plus" class="h-4 w-4" />
									</template>
									{{ __("Add Row") }}
								</Button>
								<Button
									v-if="selectedCount > 0"
									variant="subtle"
									theme="red"
									class="h-[44px] sm:h-10 touch-manipulation"
									data-testid="delete-selected-rows"
									@click="deleteSelectedRows"
								>
									<template #prefix>
										<FeatherIcon name="trash-2" class="h-4 w-4" />
									</template>
									{{ __("Delete") }} ({{ selectedCount }})
								</Button>
								<p v-if="!editor.items.length" class="text-sm text-gray-500">
									{{ __("Search and add only the items you physically counted.") }}
								</p>
							</div>

							<ul
								v-if="importResult?.errors?.length"
								data-testid="import-errors"
								class="space-y-1 border-t border-gray-200 px-4 py-3"
							>
								<li
									v-for="e in importResult.errors"
									:key="`${e.row}-${e.item_code}`"
									class="text-xs text-red-600"
								>
									{{ __("Row {0}: {1} — {2}", [e.row, e.item_code, e.message]) }}
								</li>
							</ul>
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
						<PurchaseAttachments v-else-if="editor.attachments?.length" :attached="editor.attachments" />
						<p v-else class="text-sm text-gray-400">{{ __("No attachments") }}</p>
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
								<Button v-if="linkInput" variant="subtle" :loading="attaching" :disabled="!linkOk" @click="attachLink">
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
						<h4 class="mb-4 text-sm font-semibold text-gray-900">{{ __("Activity") }}</h4>
						<ol class="relative">
							<li
								v-for="(entry, i) in editor.activity"
								:key="i"
								data-testid="activity-entry"
								class="relative flex gap-3 pb-4 last:pb-0"
							>
								<!-- connector line between markers -->
								<span
									v-if="i < editor.activity.length - 1"
									class="absolute start-3.5 top-7 -bottom-0 w-px bg-gray-200"
									aria-hidden="true"
								></span>
								<span
									:class="[
										'relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full ring-4 ring-white',
										activityTone(entry),
									]"
									aria-hidden="true"
								>
									<FeatherIcon :name="activityIcon(entry)" class="h-3.5 w-3.5" />
								</span>
								<div class="min-w-0 flex-1 pt-1">
									<div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-sm">
										<p class="min-w-0 text-gray-600">
											<span class="font-medium text-gray-900">{{ entry.user_fullname || entry.user }}</span>
											<template v-if="entry.type !== 'comment'">
												&middot; <span>{{ activityLabel(entry) }}</span>
											</template>
										</p>
										<time class="shrink-0 text-xs text-gray-400" :title="formatDateTime(entry.at)">
											{{ relativeTime(entry.at) }}
										</time>
									</div>
									<p
										v-if="entry.type === 'comment'"
										class="mt-1.5 whitespace-pre-line break-words rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700"
									>
										{{ activityLabel(entry) }}
									</p>
								</div>
							</li>
						</ol>
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
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue"
import DialogHost from "@/components/common/DialogHost.js"
import ConfirmDialog from "../common/ConfirmDialog.vue"
import RefreshButton from "@/components/common/RefreshButton.vue"
import PurchaseAttachments from "@/components/purchase/PurchaseAttachments.vue"
import { useFormatters } from "@/composables/useFormatters"
import { useToast } from "@/composables/useToast"
import { ATTACHMENT_ACCEPT, fileToBase64, sanitizePickedFiles } from "@/utils/attachments"
import { compressImage } from "@/utils/compressImage"
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
			page.value = 0
			loadStates()
			loadList()
		}
	},
)
watch(show, (v) => emit("update:modelValue", v))

const API = "pos_next.api.stock_opname"

// view: "list" | "doc"
const view = ref("list")
const opnames = ref([])
const openCount = ref(null)
const hasServerOpenCount = ref(false)
const PAGE_SIZE = 20
const page = ref(0)
const hasMore = ref(false)
const loading = ref(false)
const loadingDetail = ref(false)
const editor = ref(null)
// JSON snapshot of the editable fields at load / last save; drives `dirty`
const snapshot = ref(null)

const itemGroups = ref([])
const fetching = ref(false)
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

const PILL_TONES = {
	"bg-green-500": "bg-green-50 text-green-700 ring-green-600/20",
	"bg-gray-400": "bg-gray-50 text-gray-600 ring-gray-500/20",
	"bg-blue-500": "bg-blue-50 text-blue-700 ring-blue-600/20",
	"bg-red-500": "bg-red-50 text-red-700 ring-red-600/20",
	"bg-orange-400": "bg-orange-50 text-orange-700 ring-orange-600/20",
}

// list pill, same shape as the other SPA lists (docs/UI_GUIDELINES.md)
function statusPill(row) {
	return [
		"inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
		PILL_TONES[toneClass(row)],
	]
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

// A Draft or Rejected count blocks starting a new one; approval stages do not (server mirrors this).
const hasOpenOpname = computed(() => {
	if (hasServerOpenCount.value) {
		return Boolean(openCount.value)
	}
	return opnames.value.some((o) => !o.docstatus && (o.editable || o.returned))
})

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

async function loadList() {
	if (!props.posProfile) return
	loading.value = true
	try {
		// one extra row tells whether a next page exists
		const res =
			(await call(`${API}.list_opnames`, {
				pos_profile: props.posProfile,
				start: page.value * PAGE_SIZE,
				page_length: PAGE_SIZE + 1,
				status: statusFilter.value || undefined,
				search: searchTerm.value.trim() || undefined,
			})) || []
		// shortcut: bare-list fallback only serves old test mocks; the server always sends the object
		const rows = Array.isArray(res) ? res : res.opnames || []
		hasServerOpenCount.value = !Array.isArray(res) && "open_count" in res
		openCount.value = Array.isArray(res) ? null : res.open_count ?? null
		hasMore.value = rows.length > PAGE_SIZE
		opnames.value = rows.slice(0, PAGE_SIZE)
	} catch (err) {
		showError(errMsg(err, "Failed to load stock opname"))
	} finally {
		loading.value = false
	}
}

function goPage(n) {
	page.value = Math.max(n, 0)
	loadList()
}

// Status chips follow the active workflow's states, so an admin flow works too.
const states = ref([])
const statusFilter = ref("")
const searchTerm = ref("")
const filtered = computed(
	() => !!(statusFilter.value || searchTerm.value.trim()),
)
const statusTabs = computed(() => [
	{ value: "", label: __("All") },
	...states.value.map((s) => ({ value: s, label: __(s) })),
])

async function loadStates() {
	if (!props.posProfile || states.value.length) return
	try {
		states.value =
			(await call(`${API}.get_opname_states`, {
				pos_profile: props.posProfile,
			})) || []
	} catch {
		// chips are optional: the list still works unfiltered
	}
}

function setStatusFilter(value) {
	if (statusFilter.value === value) return
	statusFilter.value = value
	goPage(0)
}

let listSearchTimer = null
onBeforeUnmount(() => clearTimeout(listSearchTimer))
function onSearchInput() {
	clearTimeout(listSearchTimer)
	listSearchTimer = setTimeout(() => goPage(0), 350)
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
		pos_opname_photo: null,
		photoRequired: false,
		uploading_photo: false,
		selected: false,
		// per-row link-field search state (search term + dropdown results)
		search: "",
		results: [],
		batches: [],
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
		uoms: altUoms(item),
		qty: item.custom_qty_after ?? item.qty,
		batch_no: item.batch_no || null,
		has_batch_no: item.has_batch_no,
		has_serial_no: item.has_serial_no,
		serial_nos: [...serials],
		serial_options: [...serials],
		is_serial,
		pos_opname_photo: item.pos_opname_photo || null,
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
	linkInput.value = ""
	clearSearch()
	try {
		applyEditor(
			await call(`${API}.get_opname`, { pos_profile: props.posProfile, name }),
		)
		if (!itemGroups.value.length) loadGroups()
		ensureSerialOptions()
		ensureBatchOptions()
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

// A loaded batch row only knows its own batch; fetch the full list so another
// batch of the same item stays selectable.
async function ensureBatchOptions() {
	for (const row of editor.value?.items || []) {
		if (!row.has_batch_no) continue
		try {
			const batches =
				(await call(`${API}.get_item_batches`, {
					pos_profile: props.posProfile,
					item_code: row.item_code,
				})) || []
			// keep a saved batch selectable even if the endpoint omits it (disabled)
			if (row.batch_no && !batches.some((b) => b.batch_no === row.batch_no))
				batches.unshift({ batch_no: row.batch_no })
			row.batches = batches
		} catch {
			// options stay as whatever was loaded
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

function activityIcon(entry) {
	if (entry.type === "created") return "plus"
	if (entry.type === "workflow") return "git-commit"
	if (entry.type === "attachment") return "paperclip"
	return "message-square"
}

function activityTone(entry) {
	if (entry.type === "created") return "bg-blue-50 text-blue-600"
	if (entry.type === "comment") return "bg-red-50 text-red-600"
	return "bg-gray-100 text-gray-600"
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

onBeforeUnmount(() => clearTimeout(searchTimer))

// One shared debounce timer and sequence guard across rows: only the last
// keystroke's search lands, and its results land on the row that started it.
function searchRow(row) {
	clearTimeout(searchTimer)
	// only one dropdown open at a time
	for (const r of editor.value.items) if (r !== row) r.results = []
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
		const rows = await call(`${API}.search_opname_items`, {
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
	restoreItem(row)
}

// Leaving an edited item cell without picking keeps the old item; an empty
// item_code would silently drop the row (and its count) from the save.
function restoreItem(row) {
	if (!row.item_code && row.prevItemCode) {
		row.item_code = row.prevItemCode
		row.search = ""
	}
}

// blur fires before a click on a result: closing is delayed so the click lands
function blurRow(row) {
	setTimeout(() => {
		row.results = []
		restoreItem(row)
	}, 200)
}

// Barcode scan: Enter picks the exact item_code. A scanner types + Enter faster
// than the debounce, so look the code up now when the results are still empty.
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

function clearSearch() {
	searchSeq++
	clearTimeout(searchTimer)
	for (const row of editor.value?.items || []) {
		row.search = ""
		row.results = []
	}
}

function addRow() {
	editor.value.items.push(blankRow())
	focusCell(editor.value.items.length - 1, "item")
}

// Like Reject Request / ERPNext grids: clicking a picked item reopens the
// search so the row can be repointed without deleting it.
function editRowItem(row, idx) {
	row.prevItemCode = row.item_code
	row.search = row.item_code
	row.item_code = ""
	row.results = []
	focusCell(idx, "item")
}

function focusCell(idx, cellName) {
	requestAnimationFrame(() => {
		document.querySelector(`[data-cell="${idx}-${cellName}"]`)?.focus()
	})
}

const selectedCount = computed(
	() => editor.value?.items.filter((r) => r.selected).length || 0,
)
const isAllSelected = computed(
	() => !!editor.value?.items.length && selectedCount.value === editor.value.items.length,
)
const isPartiallySelected = computed(
	() => selectedCount.value > 0 && !isAllSelected.value,
)

function toggleSelectAll(checked) {
	for (const row of editor.value.items) row.selected = checked
}

function deleteSelectedRows() {
	editor.value.items = editor.value.items.filter((r) => !r.selected)
}

// The search endpoint already excludes the stock UOM; offer it first.
function uomOptionsOf(row) {
	return [...new Set([row.stock_uom, ...(row.uoms || [])].filter(Boolean))]
}

function altUoms(item) {
	return (item.uoms || []).map((u) => u.uom)
}

function setRow(row, fields) {
	Object.assign(row, blankRow({ ...fields, key: row.key }))
}

function clearRow(row) {
	Object.assign(row, blankRow({ key: row.key }))
}

// Fills the row from a picked search result; a batch row keeps batch_no empty
// until the user picks a batch in the row's select.
async function pickItem(row, item) {
	clearTimeout(searchTimer)
	searchSeq++
	row.search = ""
	row.results = []
	const label = item.item_name || item.item_code
	if (
		!item.has_batch_no &&
		editor.value.items.some((r) => r !== row && r.item_code === item.item_code)
	) {
		showError(__("{0} is already in the list", [label]))
		if (row.prevItemCode) restoreItem(row)
		else clearRow(row)
		return
	}
	const base = {
		item_code: item.item_code,
		item_name: label,
		stock_uom: item.stock_uom,
		uom: item.stock_uom,
		uoms: altUoms(item),
		has_batch_no: item.has_batch_no,
		has_serial_no: item.has_serial_no,
	}
	if (item.has_serial_no) {
		const serials =
			(await call(`${API}.get_item_serials`, {
				pos_profile: props.posProfile,
				item_code: item.item_code,
			})) || []
		setRow(row, { ...base, serial_options: serials, is_serial: true })
	} else if (item.has_batch_no) {
		const batches =
			(await call(`${API}.get_item_batches`, {
				pos_profile: props.posProfile,
				item_code: item.item_code,
			})) || []
		// one batch per row: qty starts at 0 until a batch is counted
		setRow(row, { ...base, qty: 0, batches })
	} else {
		setRow(row, { ...base, qty: 1 })
	}
}

// Same item_code + batch_no twice is the batch duplicate guard.
function checkBatchDuplicate(row) {
	if (!row.batch_no) return
	const dup = editor.value.items.some(
		(r) => r !== row && r.item_code === row.item_code && r.batch_no === row.batch_no,
	)
	if (dup) {
		showError(
			__("{0} batch {1} is already in the list", [row.item_name || row.item_code, row.batch_no]),
		)
		clearRow(row)
	}
}

function toggleSerial(row, serial) {
	row.uncounted = false
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
	row.uncounted = false
	if (!row.serial_nos.includes(value)) row.serial_nos.push(value)
}

function buildItems() {
	// an Add Row row without a picked item is not a count, nor is a fetched
	// row left blank: saving it as 0 would wipe that item's stock
	return editor.value.items
		.filter((r) => r.item_code && !isUncounted(r))
		.map((r) => {
			const out = {
				item_code: r.item_code,
				qty: r.is_serial ? r.serial_nos.length : Number(r.qty) || 0,
			}
			if (r.batch_no) out.batch_no = r.batch_no
			if (r.pos_opname_photo) out.pos_opname_photo = r.pos_opname_photo
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

function isUncounted(row) {
	return row.is_serial ? !!row.uncounted : row.qty === "" || row.qty == null
}

// Like ERPNext's "Fetch Items from Warehouse", minus quantities (blind count):
// every item of the warehouse lands as a blank row the outlet fills in.
async function fetchItems() {
	if (fetching.value) return
	fetching.value = true
	try {
		const rows =
			(await call(`${API}.fetch_opname_items`, {
				pos_profile: props.posProfile,
				item_group: itemGroup.value || undefined,
			})) || []
		const items = editor.value.items
		let added = 0
		for (const item of rows) {
			const exists = items.some(
				(r) =>
					r.item_code === item.item_code &&
					(!item.has_batch_no || r.batch_no === item.batch_no),
			)
			if (exists) continue
			const serial = !!item.has_serial_no
			items.push(
				blankRow({
					item_code: item.item_code,
					item_name: item.item_name || item.item_code,
					stock_uom: item.stock_uom,
					uom: item.stock_uom,
					uoms: altUoms(item),
					qty: "",
					batch_no: item.batch_no || null,
					batches: item.batch_no ? [{ batch_no: item.batch_no }] : [],
					has_batch_no: item.has_batch_no,
					has_serial_no: item.has_serial_no,
					serial_options: serial ? [...(item.serial_nos || [])] : [],
					is_serial: serial,
					uncounted: serial,
				}),
			)
			added++
		}
		if (added) showSuccess(__("{0} items added", [added]))
		else showSuccess(__("No new items to add"))
	} catch (err) {
		showError(errMsg(err, "Failed to fetch items"))
	} finally {
		fetching.value = false
	}
}

// ---------------------------------------------------------------------------
// Save / submit / delete
// ---------------------------------------------------------------------------

async function save() {
	if (saving.value) return false
	// a batch row without a batch is an invalid count: block the save
	const missingBatch = editor.value.items.find((r) => r.item_code && r.has_batch_no && !r.batch_no)
	if (missingBatch) {
		showError(__("Choose a batch for {0}", [missingBatch.item_name || missingBatch.item_code]))
		return false
	}
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
		const uncounted = editor.value.items.filter(
			(r) => r.item_code && isUncounted(r),
		)
		applyEditor(payload)
		editor.value.items.push(...uncounted)
		// a save response only carries the ticked serials; refetch so the
		// remaining serials of a serial item stay tickable
		await ensureSerialOptions()
		await ensureBatchOptions()
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
	// a returned count's action sends it back to draft for editing
	if (editor.value.returned) return reviseAction(action)

	const counted = editor.value.items.filter((r) => r.item_code && !isUncounted(r))
	const missing = counted.filter((r) => !r.pos_opname_photo)
	if (missing.length) {
		for (const r of editor.value.items) {
			r.photoRequired = missing.includes(r)
		}
		showError(__("{0} rows need a proof photo", [missing.length]))
		return
	}
	for (const r of editor.value.items) {
		r.photoRequired = false
	}

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
		if (dirty.value || !editor.value.name) {
			if (!(await save())) return
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

async function reviseAction(action) {
	submitting.value = true
	try {
		applyEditor(
			await call(`${API}.submit_opname`, {
				pos_profile: props.posProfile,
				name: editor.value.name,
				action,
			}),
		)
		showSuccess(__("Stock opname is back in draft"))
	} catch (err) {
		showError(errMsg(err, "Failed to revise stock opname"))
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
	// The upload replaces the rows wholesale: confirm when it would drop unsaved edits.
	if ((!editor.value?.name && editor.value?.items?.length) || dirty.value) {
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
			ensureBatchOptions()
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

async function handlePhotoUpload(row, event) {
	const file = event.target?.files?.[0]
	if (event.target) event.target.value = ""
	if (!file) return
	row.uploading_photo = true
	try {
		const compressed = await compressImage(file, { maxEdge: 1600, quality: 0.8 })
		const b64 = await fileToBase64(compressed)
		if (!(await ensureSaved())) return
		// a first save rebuilds the rows: re-find this one by item + batch
		const target = editor.value.items.includes(row)
			? row
			: editor.value.items.find(
					(r) => r.item_code === row.item_code && (r.batch_no || "") === (row.batch_no || ""),
				) || row
		const res = await call(`${API}.attach_opname_row_photo`, {
			pos_profile: props.posProfile,
			name: editor.value.name,
			file_name: compressed.name || file.name,
			filedata: b64,
		})
		if (res?.file_url) {
			target.pos_opname_photo = res.file_url
			target.photoRequired = false
		}
	} catch (err) {
		showError(errMsg(err, "Failed to upload photo"))
	} finally {
		row.uploading_photo = false
	}
}

function removeRowPhoto(row) {
	row.pos_opname_photo = null
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
	loadStates()
	loadList()
}
</script>
