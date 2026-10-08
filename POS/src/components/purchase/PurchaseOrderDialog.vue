<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: dialogTitle, size: view === 'receive' ? 'lg' : '4xl' }"
	>
		<template #body-content>
			<!-- LIST VIEW -->
			<div v-if="view === 'list'" class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<div class="flex flex-wrap items-center justify-between gap-3">
					<p class="text-sm text-gray-500">
						{{ __("Manage supplier orders and purchasing requests") }}
					</p>
					<Button variant="solid" data-test="new-button" @click="openNew">
						<template #prefix>
							<FeatherIcon name="plus" class="w-4 h-4" />
						</template>
						{{ __("New Purchase Order") }}
					</Button>
				</div>

				<div class="flex gap-2">
					<div class="relative flex-1">
						<FeatherIcon
							name="search"
							class="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
						/>
						<input
							v-model="searchTerm"
							type="text"
							data-test="list-search"
							:placeholder="__('Search PO number or supplier...')"
							class="w-full rounded-lg border border-gray-300 py-2 pe-3 ps-9 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
							@input="onListSearch"
						/>
					</div>
					<RefreshButton :loading="loadingOrders" @click="loadOrders" />
				</div>

				<div class="-mx-1 flex gap-1 overflow-x-auto px-1 pb-0.5" role="group" :aria-label="__('Status')">
					<button
						v-for="chip in STATUS_CHIPS"
						:key="chip.value"
						type="button"
						class="shrink-0 rounded-full border px-3 py-1 text-xs font-medium transition-colors"
						:class="
							statusFilter === chip.value
								? 'border-gray-900 bg-gray-900 text-white'
								: 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:text-gray-900'
						"
						:aria-pressed="statusFilter === chip.value"
						@click="setStatus(chip.value)"
					>
						{{ chip.label }}
					</button>
				</div>

				<div class="rounded-xl border border-gray-200 bg-white">
					<!-- desktop column header; rows below share the same grid -->
					<div
						class="hidden border-b border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium uppercase tracking-wide text-gray-500 md:grid md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.6fr)_8.5rem_7rem_10rem_minmax(0,1.2fr)] md:gap-x-3"
					>
						<span>{{ __("PO Number") }}</span>
						<span>{{ __("Supplier") }}</span>
						<span>{{ __("Transaction Date") }}</span>
						<span>{{ __("Required By") }}</span>
						<span>{{ __("Status") }}</span>
						<span class="text-end">{{ __("Actions") }}</span>
					</div>

					<div v-if="loadingOrders" class="divide-y divide-gray-100" data-test="list-loading">
						<div v-for="n in 3" :key="n" class="flex animate-pulse gap-4 px-4 py-3">
							<div class="h-4 w-28 rounded bg-gray-100"></div>
							<div class="h-4 flex-1 rounded bg-gray-100"></div>
							<div class="h-4 w-20 rounded bg-gray-100"></div>
						</div>
					</div>
					<div v-else-if="orders.length === 0" class="px-4 py-6 text-center">
						<p class="text-sm font-medium text-gray-900">{{ __("No purchase orders found") }}</p>
						<p class="mt-1 text-xs text-gray-500">
							{{
								searchTerm || statusFilter
									? __("Try a different search or status")
									: __("Create one with New Purchase Order")
							}}
						</p>
					</div>
					<div v-else class="divide-y divide-gray-100">
						<div
							v-for="order in orders"
							:key="order.name"
							class="cursor-pointer px-4 py-3 transition-colors hover:bg-gray-50"
							:class="{ 'bg-gray-50': expandedOrder === order.name }"
							:data-test="`po-${order.name}`"
							@click="toggleOrder(order)"
						>
							<div class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 md:gap-y-0 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.6fr)_8.5rem_7rem_10rem_minmax(0,1.2fr)] md:gap-x-3">
								<div class="flex min-w-0 items-center gap-1.5 md:order-1">
									<FeatherIcon
										name="chevron-right"
										class="h-3.5 w-3.5 shrink-0 text-gray-400 transition-transform"
										:class="{ 'rotate-90': expandedOrder === order.name }"
									/>
									<span class="truncate text-sm font-semibold text-gray-900">{{ order.name }}</span>
									<span
										v-if="order.attachment_count"
										class="inline-flex shrink-0 items-center gap-0.5 text-xs text-gray-500"
										:title="__('Attachments')"
									>
										<FeatherIcon name="paperclip" class="h-3 w-3" />
										{{ order.attachment_count }}
									</span>
								</div>
								<!-- status sits top-right on cards, in its own column on desktop -->
								<div class="flex items-center gap-1 justify-self-end md:order-5 md:justify-self-start">
									<span :class="statusPill(order.status)">{{ __(order.status) }}</span>
									<span
										v-if="isFactoryLinked(order)"
										class="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600"
									>
										{{ __("Factory SO") }}
									</span>
								</div>
								<p class="col-span-2 truncate text-sm text-gray-700 md:order-2 md:col-span-1">
									{{ order.supplier_name || order.supplier }}
								</p>
								<p class="col-span-2 text-xs text-gray-500 md:order-3 md:col-span-1 md:text-sm md:text-gray-700">
									<span class="md:hidden">{{ __("Ordered") }}&nbsp;</span>{{ formatDate(order.transaction_date) }}
									<span class="md:hidden"> · {{ __("Required By") }} {{ formatDate(order.schedule_date) }}</span>
								</p>
								<p class="hidden text-sm text-gray-700 md:order-4 md:block">
									{{ formatDate(order.schedule_date) }}
								</p>
								<div
									class="col-span-2 -mx-2 flex flex-wrap justify-start gap-0.5 md:order-6 md:col-span-1 md:mx-0 md:justify-end"
									@click.stop
								>
									<button
										v-if="order.docstatus === 0"
										type="button"
										class="rounded px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50"
										@click="openEdit(order)"
									>
										{{ __("Edit") }}
									</button>
									<button
										v-if="order.docstatus === 0 && canSubmitPO"
										type="button"
										class="rounded px-2 py-1 text-xs font-medium text-green-700 hover:bg-green-50"
										@click="submitOrder(order)"
									>
										{{ __("Submit") }}
									</button>
									<button
										v-if="
											order.docstatus === 1 &&
											order.per_received < 100 &&
											(!poDefaults?.receive_requires_delivery_note || order.delivery_ready) &&
											canReceivePR
										"
										type="button"
										data-test="receive-button"
										class="rounded px-2 py-1 text-xs font-medium text-purple-600 hover:bg-purple-50"
										@click="openReceive(order)"
									>
										{{ __("Receive") }}
									</button>
									<button
										v-if="order.docstatus === 1 && canCancelPO && !isFactoryLinked(order)"
										type="button"
										class="rounded px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
										@click="cancelOrder(order)"
									>
										{{ __("Cancel") }}
									</button>
									<button
										type="button"
										class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
										:title="__('Open in ERPNext')"
										:aria-label="__('Open in ERPNext')"
										@click="openInErpnext(order)"
									>
										<FeatherIcon name="external-link" class="h-3.5 w-3.5" />
									</button>
								</div>
							</div>

							<!-- item peek: lazy-loaded once per order, cached for the session -->
							<div
								v-if="expandedOrder === order.name"
								class="mt-2 rounded-lg border border-gray-200 bg-white px-3 py-1 md:ms-5"
								@click.stop
							>
								<div
									v-if="loadingDetail && !orderDetails[order.name]"
									class="py-2 text-center text-xs text-gray-400"
								>
									{{ __("Loading...") }}
								</div>
								<div
									v-else-if="orderDetails[order.name]?.items?.length"
									class="divide-y divide-gray-100"
								>
									<div
										v-for="row in orderDetails[order.name].items"
										:key="row.name"
										class="flex items-center justify-between gap-3 py-1.5 text-xs"
									>
										<span class="min-w-0 truncate text-gray-700">
											{{ row.item_name || row.item_code }}
										</span>
										<span class="shrink-0 font-medium tabular-nums text-gray-900">
											{{ formatQty(row.qty) }} {{ row.uom }}
										</span>
									</div>
								</div>
								<p v-else class="py-2 text-center text-xs text-gray-400">
									{{ __("No items") }}
								</p>
								<div v-if="orderDetails[order.name]?.attachments?.length" class="pb-2">
									<PurchaseAttachments
										:attached="orderDetails[order.name].attachments"
										:label="__('Attachments')"
									/>
								</div>
							</div>
						</div>
					</div>
				</div>
			</div>

			<!-- RECEIVE VIEW -->
			<div v-else-if="view === 'receive'" class="flex flex-col gap-3">
				<div class="flex items-end justify-between gap-3">
					<div class="min-w-0">
						<p class="text-sm font-semibold text-gray-900 truncate">
							{{ receive.supplier_name }}
						</p>
						<p class="text-xs text-gray-500 mt-0.5 truncate">{{ receive.supplier }}</p>
						<p
							v-if="receive.inter_company_reference"
							class="text-xs text-gray-400 mt-0.5 truncate"
						>
							{{ __("From Delivery Note: {0}", [receive.inter_company_reference]) }}
						</p>
					</div>
					<div>
						<label for="pr-posting-date" class="block text-xs font-medium text-gray-600 mb-1">
							{{ __("Posting Date") }}
						</label>
						<input
							id="pr-posting-date"
							v-model="receive.posting_date"
							type="date"
							data-test="receive-posting-date"
							class="px-3 py-2 text-sm border border-gray-300 rounded-lg"
						/>
					</div>
				</div>

				<div v-if="receive.items.length" class="overflow-x-auto">
					<table class="w-full text-sm" data-test="receive-table">
						<thead>
							<tr class="text-xs text-gray-500 uppercase">
								<th class="py-1 text-start">{{ __("Item") }}</th>
								<th class="py-1 text-start w-16">{{ __("Ordered") }}</th>
								<th class="py-1 text-start w-16">{{ __("Received") }}</th>
								<th class="py-1 text-start w-16">{{ __("Remaining") }}</th>
								<th class="py-1 text-start w-20">{{ __("Qty") }}</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="row in receive.items"
								:key="row.purchase_order_item"
								class="border-t border-gray-100"
							>
								<td class="py-1.5 pe-2">
									<div class="truncate max-w-[180px]">{{ row.item_name }}</div>
									<div class="text-xs text-gray-400">{{ row.uom }}</div>
								</td>
								<td class="py-1.5 pe-2">{{ row.ordered_qty }}</td>
								<td class="py-1.5 pe-2">{{ row.received_qty }}</td>
								<td class="py-1.5 pe-2">{{ row.pending_qty }}</td>
								<td class="py-1.5 pe-2">
									<input
										v-model.number="row.qty"
										data-test="receive-qty"
										type="number"
										min="0"
										step="any"
										:aria-label="`${row.item_name} — ${__('Qty')}`"
										class="w-full px-2 py-1 text-sm border border-gray-300 rounded"
									/>
								</td>
							</tr>
							</tbody>
						</table>
					</div>

					<PurchaseAttachments
						editable
						:pending="receivePending"
						:label="__('Delivery evidence (surat jalan / delivery note)')"
						@add="onAttachAdd($event, receivePending)"
						@remove="(row) => removeAttachment(receivePending, row)"
					/>
				</div>

			<!-- FORM VIEW -->
			<div v-else class="mx-auto flex w-full max-w-6xl flex-col gap-4">
				<div class="flex items-center gap-2">
					<button
						type="button"
						class="-ms-1 rounded p-1 text-gray-500 hover:text-gray-900"
						:aria-label="__('Back')"
						:title="__('Back')"
						@click="view = 'list'"
					>
						<FeatherIcon name="arrow-left" class="h-4 w-4" />
					</button>
					<h3 class="truncate text-base font-semibold text-gray-900">
						{{ form.name || __("New Purchase Order") }}
					</h3>
					<span v-if="form.name" :class="statusPill('Draft')">{{ __("Draft") }}</span>
				</div>

				<!-- A — order information -->
				<section class="rounded-xl border border-gray-200 bg-white p-4">
					<h4 class="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
						{{ __("Order Information") }}
					</h4>
					<div class="grid gap-3 sm:grid-cols-2">
						<div class="sm:col-span-2">
							<label class="mb-1 block text-xs font-medium text-gray-700">
								{{ __("Supplier") }} <span class="text-red-500">*</span>
							</label>
							<div :class="{ 'rounded-lg ring-2 ring-red-300': errors.supplier }">
								<AutocompleteSelect
									:model-value="form.supplier"
									:options="supplierOptions"
									:placeholder="__('Search supplier...')"
									:loading="loadingSuppliers"
									data-test="supplier-select"
									@update:model-value="onSupplierSelect"
									@search="onSupplierSearch"
								/>
							</div>
							<p v-if="errors.supplier" class="mt-1 text-xs text-red-600">{{ errors.supplier }}</p>
						</div>
						<div>
							<label for="po-transaction-date" class="mb-1 block text-xs font-medium text-gray-700">
								{{ __("Transaction Date") }}
							</label>
							<input
								id="po-transaction-date"
								v-model="form.transaction_date"
								type="date"
								class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
							/>
						</div>
						<div>
							<label for="po-schedule-date" class="mb-1 block text-xs font-medium text-gray-700">
								{{ __("Required By") }}
							</label>
							<input
								id="po-schedule-date"
								v-model="form.schedule_date"
								type="date"
								:min="form.transaction_date"
								class="w-full rounded-lg border px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
								:class="errors.schedule_date ? 'border-red-400' : 'border-gray-300'"
							/>
							<p v-if="errors.schedule_date" class="mt-1 text-xs text-red-600">
								{{ errors.schedule_date }}
							</p>
						</div>
					</div>
					<!-- read-only context the PO is created under -->
					<div
						v-if="company || form.set_warehouse || form.currency"
						class="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-gray-100 pt-3 text-xs text-gray-500"
					>
						<span v-if="company">{{ __("Company") }}: <b class="font-medium text-gray-700">{{ company }}</b></span>
						<span v-if="form.set_warehouse">{{ __("Warehouse") }}: <b class="font-medium text-gray-700">{{ form.set_warehouse }}</b></span>
						<span v-if="form.currency">{{ __("Currency") }}: <b class="font-medium text-gray-700">{{ form.currency }}</b></span>
					</div>
				</section>

				<!-- B — items: ERPNext-style grid, rows are added below the table -->
				<section class="rounded-xl border border-gray-200 bg-white">
					<div class="flex items-center justify-between px-4 pb-2 pt-4">
						<h4 class="text-xs font-semibold uppercase tracking-wide text-gray-500">
							{{ __("Items") }} <span class="text-red-500">*</span>
						</h4>
					</div>
					<div ref="itemsRef" data-test="items-table">
						<div
							class="hidden border-y border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium uppercase tracking-wide text-gray-500 md:grid md:grid-cols-[2rem_minmax(0,1fr)_7rem_9rem_2rem] md:gap-3"
						>
							<span>#</span>
							<span>{{ __("Item") }}</span>
							<span>{{ __("Qty") }}</span>
							<span>{{ __("UOM") }}</span>
							<span></span>
						</div>
						<div
							v-for="(row, idx) in form.items"
							:key="row.key"
							class="grid grid-cols-2 gap-2 border-b border-gray-100 px-4 py-2.5 transition-colors hover:bg-gray-50 md:grid-cols-[2rem_minmax(0,1fr)_7rem_9rem_2rem] md:items-center md:gap-3 md:py-1.5"
							:data-blank-row="row.item_code ? null : ''"
						>
							<span class="hidden text-xs tabular-nums text-gray-400 md:block">{{ idx + 1 }}</span>
							<div class="col-span-2 flex min-w-0 items-center gap-2 md:col-span-1">
								<div v-if="row.item_code" class="min-w-0 flex-1">
									<p class="truncate text-sm font-medium text-gray-900">{{ row.item_name }}</p>
									<p v-if="row.item_name !== row.item_code" class="truncate text-xs text-gray-400">
										{{ row.item_code }}
									</p>
								</div>
								<div v-else class="min-w-0 flex-1">
									<AutocompleteSelect
										model-value=""
										:options="itemOptions"
										:placeholder="__('Search item...')"
										:loading="loadingItems || row.loading"
										data-test="item-select"
										@update:model-value="(code) => onItemPick(code, row)"
										@search="onItemSearch"
									/>
								</div>
								<button
									type="button"
									class="shrink-0 rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600 md:hidden"
									:aria-label="__('Remove')"
									@click="removeRow(idx)"
								>
									<FeatherIcon name="x" class="h-4 w-4" />
								</button>
							</div>
							<template v-if="row.item_code">
								<label class="block">
									<span class="mb-0.5 block text-xs text-gray-500 md:hidden">{{ __("Qty") }}</span>
									<input
										v-model.number="row.qty"
										data-test="item-qty"
										type="number"
										min="0"
										step="any"
										inputmode="decimal"
										:aria-label="`${row.item_name} — ${__('Qty')}`"
										class="w-full rounded-md border px-2 py-1.5 text-sm tabular-nums focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
										:class="triedSave && !(Number(row.qty) > 0) ? 'border-red-400 bg-red-50' : 'border-gray-300'"
										@keydown.enter.prevent="addItemRow"
									/>
								</label>
								<label class="block">
									<span class="mb-0.5 block text-xs text-gray-500 md:hidden">{{ __("UOM") }}</span>
									<select
										v-model="row.uom"
										data-test="item-uom"
										:aria-label="`${row.item_name} — ${__('UOM')}`"
										class="w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
										@change="onUomChange(row)"
									>
										<option v-for="u in uomOptionsOf(row)" :key="u" :value="u">
											{{ u }}
										</option>
									</select>
								</label>
							</template>
							<template v-else>
								<span class="hidden text-sm text-gray-300 md:block">—</span>
								<span class="hidden text-sm text-gray-300 md:block">—</span>
							</template>
							<button
								type="button"
								class="hidden h-7 w-7 items-center justify-center rounded text-gray-400 hover:bg-red-50 hover:text-red-600 md:flex"
								:aria-label="__('Remove')"
								:title="__('Remove')"
								@click="removeRow(idx)"
							>
								<FeatherIcon name="x" class="h-4 w-4" />
							</button>
						</div>
						<div v-if="!form.items.length" class="px-4 py-4 text-center">
							<p class="text-sm text-gray-600">{{ __("No items added yet.") }}</p>
							<p class="text-xs text-gray-400">{{ __("Click Add Item to start.") }}</p>
						</div>
					</div>
					<div class="flex items-center justify-between gap-2 px-4 py-2.5">
						<button
							type="button"
							data-test="add-item"
							class="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
							@click="addItemRow"
						>
							<FeatherIcon name="plus" class="h-3.5 w-3.5" />
							{{ __("Add Item") }}
						</button>
						<p v-if="errors.items" class="text-xs text-red-600">{{ errors.items }}</p>
					</div>
				</section>

				<div class="grid gap-4 md:grid-cols-[minmax(0,1fr)_18rem]">
					<!-- D — notes & attachments (secondary) -->
					<section class="order-2 rounded-xl border border-gray-200 bg-white p-4 md:order-1">
						<h4 class="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
							{{ __("Notes & Attachments") }}
						</h4>
						<label for="po-remarks" class="sr-only">{{ __("Remarks") }}</label>
						<textarea
							id="po-remarks"
							v-model="form.remarks"
							rows="2"
							:placeholder="__('Add remarks about this purchase order...')"
							class="mb-3 w-full resize-y rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
						></textarea>
						<PurchaseAttachments
							editable
							:pending="formPending"
							@add="onAttachAdd($event, formPending)"
							@remove="(row) => removeAttachment(formPending, row)"
						/>
					</section>

					<!-- C — order summary (quantities only: pricing stays in Desk) -->
					<section class="order-1 rounded-xl border border-gray-200 bg-gray-50 p-4 md:order-2" data-test="order-summary">
						<h4 class="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
							{{ __("Order Summary") }}
						</h4>
						<dl class="space-y-2 text-sm">
							<div class="flex justify-between gap-3">
								<dt class="text-gray-500">{{ __("Total Items") }}</dt>
								<dd class="font-semibold tabular-nums text-gray-900">{{ filledItems.length }}</dd>
							</div>
							<div class="flex justify-between gap-3">
								<dt class="text-gray-500">{{ __("Total Qty") }}</dt>
								<dd class="text-end font-semibold tabular-nums text-gray-900">
									<div v-for="q in qtyByUom" :key="q.uom">{{ formatQty(q.qty) }} {{ q.uom }}</div>
									<span v-if="!qtyByUom.length">0</span>
								</dd>
							</div>
							<div class="flex justify-between gap-3">
								<dt class="text-gray-500">{{ __("Required By") }}</dt>
								<dd class="font-medium text-gray-900">{{ formatDate(form.schedule_date) || "—" }}</dd>
							</div>
						</dl>
						<div
							v-if="form.taxes_and_charges"
							class="mt-3 flex items-center justify-between gap-2 border-t border-gray-200 pt-3"
						>
							<span class="min-w-0 truncate text-xs text-gray-600">
								{{ __("Tax Template: {0}", [form.taxes_and_charges]) }}
							</span>
							<button
								type="button"
								class="shrink-0 text-xs text-red-600 hover:text-red-700"
								@click="form.taxes_and_charges = ''"
							>
								{{ __("Remove") }}
							</button>
						</div>
					</section>
				</div>
			</div>
		</template>

		<template #actions>
			<div
				v-if="view === 'form'"
				class="mx-auto flex w-full max-w-6xl flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between"
			>
				<Button variant="subtle" class="w-full sm:w-auto" @click="view = 'list'">{{ __("Back") }}</Button>
				<div class="flex gap-2">
					<Button variant="subtle" class="flex-1 sm:flex-none" :loading="saving" @click="save(false)">
						{{ __("Save Draft") }}
					</Button>
					<Button v-if="canSubmitPO" variant="solid" class="flex-1 sm:flex-none" :loading="saving" @click="save(true)">
						{{ __("Save & Submit") }}
					</Button>
				</div>
			</div>
			<div v-else-if="view === 'receive'" class="flex justify-between items-center w-full">
				<Button variant="subtle" @click="view = 'list'">{{ __("Back") }}</Button>
				<div class="flex gap-2">
					<Button variant="subtle" :loading="saving" @click="saveReceipt(false)">
						{{ __("Save Draft") }}
					</Button>
					<Button v-if="canSubmitPR" variant="solid" :loading="saving" @click="saveReceipt(true)">
						{{ __("Submit Receipt") }}
					</Button>
				</div>
			</div>
			<div v-else-if="!embedded" class="flex justify-end w-full">
				<Button variant="subtle" @click="show = false">{{ __("Close") }}</Button>
			</div>
		</template>
	</DialogHost>
</template>

<script setup>
import AutocompleteSelect from "@/components/common/AutocompleteSelect.vue"
import PurchaseAttachments from "@/components/purchase/PurchaseAttachments.vue"
import { useToast } from "@/composables/useToast"
import { useFormatters } from "@/composables/useFormatters"
import { usePermissions } from "@/composables/usePermissions"
import {
	sanitizePickedFiles,
	uploadPurchaseFiles,
} from "@/utils/attachments"
import { call, serverErrorMessage } from "@/utils/apiWrapper"
import { parseError } from "@/utils/errorHandler"
import { Button, FeatherIcon } from "frappe-ui"
import { computed, nextTick, ref, watch } from "vue"
import DialogHost from "@/components/common/DialogHost.js"
import RefreshButton from "@/components/common/RefreshButton.vue"

const props = defineProps({
	modelValue: Boolean,
	posProfile: { type: String, default: null },
	company: { type: String, default: null },
	warehouse: { type: String, default: null },
	currency: { type: String, default: "" },
	embedded: { type: Boolean, default: false },
})

const emit = defineEmits(["update:modelValue"])


const { showSuccess, showError, showInfo } = useToast()
const { formatDate } = useFormatters()
const { usePermissionCheck } = usePermissions()
const { hasPermission: canSubmitPO } = usePermissionCheck(
	"Purchase Order",
	"submit",
)
const { hasPermission: canCancelPO } = usePermissionCheck(
	"Purchase Order",
	"cancel",
)
const { hasPermission: canReceivePR } = usePermissionCheck(
	"Purchase Receipt",
	"create",
)
const { hasPermission: canSubmitPR } = usePermissionCheck(
	"Purchase Receipt",
	"submit",
)

const API = "pos_next.api.purchase_orders"
const PR_API = "pos_next.api.purchase_receipts"

const view = ref("list")

// AutocompleteSelect only fires @search while typing; without a preload the
// dropdowns open empty and look broken. Prime the top options on form entry.
watch(view, (v) => {
	if (v === "form") {
		onSupplierSearch("")
		onItemSearch("")
	}
})
const show = ref(props.modelValue)
watch(show, (val) => emit("update:modelValue", val))

// ---------- list view ----------
const STATUS_CHIPS = [
	{ value: "", label: __("All") },
	{ value: "Draft", label: __("Draft") },
	{ value: "To Receive and Bill", label: __("To Receive and Bill") },
	{ value: "To Receive", label: __("To Receive") },
	{ value: "To Bill", label: __("To Bill") },
	{ value: "Completed", label: __("Completed") },
	{ value: "Cancelled", label: __("Cancelled") },
]

const STATUS_TONES = {
	Draft: "bg-orange-50 text-orange-700 ring-orange-200",
	"To Receive and Bill": "bg-blue-50 text-blue-700 ring-blue-200",
	"To Receive": "bg-blue-50 text-blue-700 ring-blue-200",
	"To Bill": "bg-purple-50 text-purple-700 ring-purple-200",
	Completed: "bg-green-50 text-green-700 ring-green-200",
	Cancelled: "bg-red-50 text-red-700 ring-red-200",
}

const orders = ref([])
const loadingOrders = ref(false)
// expand-to-peek: item rows are fetched once per order and cached
const expandedOrder = ref(null)
const orderDetails = ref({})
const loadingDetail = ref(false)
const searchTerm = ref("")
const statusFilter = ref("")
let listTimer = null

// POS Settings defaults (per profile) — one fetch per dialog session; also
// drives the Receive gate (receive_requires_delivery_note). Declared above
// the open watcher: the watcher fires immediately during setup.
const poDefaults = ref(null)

async function loadPoDefaults() {
	if (poDefaults.value) return poDefaults.value
	try {
		poDefaults.value = await call(`${API}.get_po_defaults`, { pos_profile: props.posProfile })
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
		poDefaults.value = {}
	}
	return poDefaults.value
}

// immediate: also covers mounting with the dialog already open
watch(
	() => props.modelValue,
	(val) => {
		show.value = val
		if (val) {
			view.value = "list"
			loadOrders()
			// the Receive gate reads the POS Settings flag — have it ready by
			// the time the list renders
			loadPoDefaults()
		} else {
			clearTimeout(listTimer)
		}
	},
	{ immediate: true },
)

const dialogTitle = computed(() => {
	if (view.value === "form")
		return form.value.name
			? __("Edit Purchase Order")
			: __("New Purchase Order")
	if (view.value === "receive") return __("Receive Goods")
	return __("Purchase Order")
})

function statusPill(status) {
	return [
		"inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
		STATUS_TONES[status] || "bg-gray-50 text-gray-700 ring-gray-200",
	]
}

// Same condition as the "Factory SO" badge: the live PO↔SO link resolves on
// the selling-company SO side, and a factory-linked PO must not be cancelled
// from the POS (the factory plans against it).
function isFactoryLinked(order) {
	return Boolean(order.is_internal_supplier && order.inter_company_order_reference)
}

async function loadOrders() {
	loadingOrders.value = true
	try {
		const res = await call(`${API}.get_purchase_orders`, {
			pos_profile: props.posProfile,
			status: statusFilter.value || null,
			search_term: searchTerm.value || null,
			limit: 50,
		})
		orders.value = res?.orders || []
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		loadingOrders.value = false
	}
}

function onListSearch() {
	clearTimeout(listTimer)
	listTimer = setTimeout(loadOrders, 300)
}

function setStatus(value) {
	if (statusFilter.value === value) return
	statusFilter.value = value
	loadOrders()
}

function formatQty(value) {
	return Number(value || 0).toLocaleString("id-ID")
}

async function toggleOrder(order) {
	if (expandedOrder.value === order.name) {
		expandedOrder.value = null
		return
	}
	expandedOrder.value = order.name
	if (orderDetails.value[order.name]) return
	loadingDetail.value = true
	try {
		const res = await call(`${API}.get_purchase_order`, { name: order.name })
		// full summary — items feed the peek, attachments ride along
		orderDetails.value[order.name] = res || {}
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
		expandedOrder.value = null
	} finally {
		loadingDetail.value = false
	}
}

async function submitOrder(order) {
	try {
		const res = await call(`${API}.submit_purchase_order`, { name: order.name })
		showSuccess(__("Purchase Order {0} submitted", [res?.name || order.name]))
		await loadOrders()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

async function cancelOrder(order) {
	try {
		const res = await call(`${API}.cancel_purchase_order`, { name: order.name })
		showSuccess(__("Purchase Order {0} cancelled", [res?.name || order.name]))
		await loadOrders()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

function openInErpnext(order) {
	window.open(`/app/purchase-order/${order.name}`, "_blank")
}

// ---------- attachments (evidence files held client-side until save) ----------
const formPending = ref([])
const receivePending = ref([])

function onAttachAdd(files, target) {
	const { ok, tooBig } = sanitizePickedFiles(files)
	if (tooBig.length) showError(__("File {0} exceeds the {1} MB limit", [tooBig[0].name, 10]))
	target.push(...ok)
}

function removeAttachment(target, row) {
	const i = target.indexOf(row)
	if (i !== -1) target.splice(i, 1)
}

// the doc is created first, then the held files upload to its real name — a
// failed upload keeps the document and reports the miss instead of blocking it
async function uploadAfterSave(doctype, name, files) {
	if (!name || !files.length) return
	try {
		const uploaded = await uploadPurchaseFiles(doctype, name, files)
		showSuccess(__("{0} attachment(s) uploaded", [uploaded.length]))
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

// ---------- receive view ----------
const receive = ref({
	po_name: null,
	supplier: "",
	supplier_name: "",
	posting_date: "",
	company: "",
	currency: "",
	set_warehouse: "",
	// internal POs are received from the factory's Delivery Note — the DN
	// reference rides the payload so the PR keeps the DN <-> PR cross links
	inter_company_reference: null,
	items: [],
})

async function openReceive(order) {
	try {
		// internal supplier POs draft from the factory DN, everyone else
		// straight from the PO
		const method = order.is_internal_supplier
			? `${PR_API}.get_intercompany_receipt_draft`
			: `${PR_API}.get_purchase_receipt_draft`
		const d = await call(method, {
			po_name: order.name,
		})
		receive.value = {
			po_name: order.name,
			supplier: d?.supplier || "",
			supplier_name: d?.supplier_name || order.supplier_name,
			posting_date: d?.posting_date || localDate(),
			company: d?.company || props.company,
			currency: d?.currency || props.currency || "",
			set_warehouse: d?.set_warehouse || "",
			inter_company_reference: d?.inter_company_reference || null,
			// default each row to what is still owed; the mapper only returns
			// rows with a pending qty
			items: (d?.items || []).map((row) => ({ ...row, qty: row.pending_qty })),
		}
		view.value = "receive"
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

async function saveReceipt(submitAfter) {
	// rows left at 0 are "not receiving this now", not "receive nothing" —
	// drop them; the mapper links (purchase_order / purchase_order_item /
	// delivery_note_item) ride along on every row that is sent
	const items = receive.value.items
		.filter((row) => Number(row.qty) > 0)
		.map((row) => ({
			item_code: row.item_code,
			qty: Number(row.qty),
			rate: row.rate,
			uom: row.uom,
			warehouse: row.warehouse,
			purchase_order: row.purchase_order,
			purchase_order_item: row.purchase_order_item,
			delivery_note_item: row.delivery_note_item,
		}))
	if (!items.length) {
		showError(__("At least one item is required"))
		return
	}
	saving.value = true
	try {
		const payload = {
			supplier: receive.value.supplier,
			posting_date: receive.value.posting_date,
			company: receive.value.company,
			set_warehouse: receive.value.set_warehouse || null,
			currency: receive.value.currency || null,
			items,
		}
		if (receive.value.inter_company_reference)
			payload.inter_company_reference = receive.value.inter_company_reference
		const res = await call(`${PR_API}.save_purchase_receipt`, {
			data: JSON.stringify(payload),
			submit: submitAfter ? 1 : 0,
		})
		showSuccess(
			submitAfter
				? __("Purchase Receipt {0} submitted", [res?.name])
				: __("Purchase Receipt {0} saved", [res?.name]),
		)
		await uploadAfterSave("Purchase Receipt", res?.name, receivePending.value)
		receivePending.value = []
		delete orderDetails.value[res?.name]
		view.value = "list"
		await loadOrders()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		saving.value = false
	}
}

// ---------- form view ----------
function localDate(offsetDays = 0) {
	const d = new Date(Date.now() + offsetDays * 86400000)
	const m = String(d.getMonth() + 1).padStart(2, "0")
	const day = String(d.getDate()).padStart(2, "0")
	return `${d.getFullYear()}-${m}-${day}`
}

function blankForm() {
	return {
		name: null,
		supplier: "",
		transaction_date: localDate(),
		schedule_date: localDate(1),
		currency: props.currency || "",
		set_warehouse: "",
		taxes_and_charges: "",
		remarks: "",
		items: [],
	}
}

const form = ref(blankForm())
// stable v-for keys: rows are inserted/removed mid-list
let rowSeq = 0
function itemRow(fields = {}) {
	return { key: ++rowSeq, item_code: "", item_name: "", qty: 1, uom: "", uoms: [], rate: 0, ...fields }
}
const itemsRef = ref(null)
// field errors appear only after the first save attempt
const triedSave = ref(false)
const filledItems = computed(() => form.value.items.filter((row) => row.item_code))
// mixed UOMs never sum into one number — one line per UOM
const qtyByUom = computed(() => {
	const totals = new Map()
	for (const row of filledItems.value)
		totals.set(row.uom, (totals.get(row.uom) || 0) + (Number(row.qty) || 0))
	return [...totals].map(([uom, qty]) => ({ uom, qty }))
})
const errors = computed(() => {
	if (!triedSave.value) return {}
	const out = {}
	if (!form.value.supplier) out.supplier = __("Supplier is required")
	if (!filledItems.value.length) out.items = __("At least one item is required")
	else if (filledItems.value.some((row) => !(Number(row.qty) > 0)))
		out.items = __("Quantity must be greater than 0")
	if (
		form.value.schedule_date &&
		form.value.transaction_date &&
		form.value.schedule_date < form.value.transaction_date
	)
		out.schedule_date = __("Required By cannot be before Transaction Date")
	return out
})
const supplierOptions = ref([])
const itemOptions = ref([])
const loadingSuppliers = ref(false)
const loadingItems = ref(false)
const saving = ref(false)
let supplierTimer = null
let itemTimer = null

// AutocompleteSelect only shows a label for options it has; make the
// prefilled supplier selectable/displayable even before a search runs
function seedSupplierOption(name, label) {
	if (name && !supplierOptions.value.some((o) => o.value === name)) {
		supplierOptions.value.unshift({ value: name, label: label || name })
	}
}

async function openNew() {
	form.value = blankForm()
	// ERPNext-style: a fresh PO opens with one empty row ready for an item
	form.value.items.push(itemRow())
	formPending.value = []
	triedSave.value = false
	view.value = "form"
	const d = await loadPoDefaults()
	if (form.value.name) return // user switched away mid-await
	form.value.supplier = d?.supplier || ""
	form.value.set_warehouse = d?.warehouse || ""
	seedSupplierOption(d?.supplier, d?.supplier_name)
	if (d?.supplier) await onSupplierSelect(d.supplier)
}

async function openEdit(order) {
	formPending.value = []
	triedSave.value = false
	try {
		const d = await call(`${API}.get_purchase_order`, { name: order.name })
		form.value = {
			name: d?.name || null,
			supplier: d?.supplier || "",
			transaction_date: d?.transaction_date || localDate(),
			schedule_date: d?.schedule_date || localDate(1),
			currency: d?.currency || props.currency || "",
			set_warehouse: d?.set_warehouse || "",
			// prefill the loaded template — an empty value here is the explicit
			// remove-tax signal, not "leave untouched"
			taxes_and_charges: d?.taxes_and_charges || "",
			remarks: d?.remarks || "",
			items: (d?.items || []).map((i) =>
				itemRow({
					item_code: i.item_code,
					item_name: i.item_name,
					qty: i.qty,
					uom: i.uom,
					uoms: [i.uom],
					rate: i.rate,
				}),
			),
		}
		seedSupplierOption(d?.supplier, d?.supplier_name)
		view.value = "form"
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

function onSupplierSearch(term) {
	clearTimeout(supplierTimer)
	supplierTimer = setTimeout(async () => {
		loadingSuppliers.value = true
		try {
			const res = await call(`${API}.search_suppliers`, {
				search_term: term || null,
				limit: 20,
			})
			supplierOptions.value = (res?.suppliers || []).map((s) => ({
				value: s.name,
				label: s.supplier_name || s.name,
				subtitle: s.supplier_group,
			}))
		} catch (error) {
			showError(parseError(error)?.message || serverErrorMessage(error))
		} finally {
			loadingSuppliers.value = false
		}
	}, 300)
}

async function onSupplierSelect(name) {
	form.value.supplier = name || ""
	if (!name) {
		// clear selection must not leave the previous supplier's defaults behind
		form.value.currency = props.currency || ""
		form.value.taxes_and_charges = ""
		return
	}
	try {
		const d = await call(`${API}.get_supplier_details`, {
			supplier: name,
			pos_profile: props.posProfile,
		})
		form.value.currency = d?.currency || props.currency || ""
		form.value.taxes_and_charges = d?.taxes_and_charges || ""
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

function onItemSearch(term) {
	clearTimeout(itemTimer)
	itemTimer = setTimeout(async () => {
		loadingItems.value = true
		try {
			const res = await call(`${API}.search_purchase_items`, {
				search_term: term || null,
				limit: 20,
			})
			itemOptions.value = (res?.items || []).map((i) => ({
				value: i.item_code,
				label: i.item_name,
				subtitle: i.stock_uom,
			}))
		} catch (error) {
			showError(parseError(error)?.message || serverErrorMessage(error))
		} finally {
			loadingItems.value = false
		}
	}, 300)
}

// the cashier's last-chosen UOM per item, remembered in this browser — wins
// over the backend's default (native Default Purchase UOM, else the item's
// custom inventory UOM, else stock UOM)
const PO_UOM_STORAGE_KEY = "posNext.poUom"

function readLastUoms() {
	try {
		return JSON.parse(localStorage.getItem(PO_UOM_STORAGE_KEY)) || {}
	} catch {
		return {}
	}
}

function lastUsedUom(itemCode) {
	return readLastUoms()[itemCode] || null
}

function rememberUom(itemCode, uom) {
	const all = readLastUoms()
	all[itemCode] = uom
	localStorage.setItem(PO_UOM_STORAGE_KEY, JSON.stringify(all))
}

function uomOptionsOf(row) {
	// rows edited from an existing PO start with their saved UOM only — the
	// full list fills in after the first change re-fetches details
	return row.uoms?.length ? row.uoms : [row.uom]
}

async function fetchItemDetails(code, uom, qty) {
	const defaults = await loadPoDefaults()
	return call(`${API}.get_purchase_item_details`, {
		item_code: code,
		supplier: form.value.supplier || null,
		pos_profile: props.posProfile,
		qty: qty || 1,
		transaction_date: form.value.transaction_date || null,
		warehouse: props.warehouse || null,
		uom: uom || null,
		price_list: defaults?.price_list || null,
	})
}

// Add Item appends an empty row (ERPNext grid style) and focuses its search;
// an existing empty row is reused instead of stacking blanks
async function addItemRow() {
	if (!form.value.items.some((row) => !row.item_code)) form.value.items.push(itemRow())
	await nextTick()
	itemsRef.value?.querySelector("[data-blank-row] input")?.focus()
}

function removeRow(idx) {
	form.value.items.splice(idx, 1)
}

async function onItemPick(code, row) {
	if (!code || !row) return
	// picking an item already on the order bumps that row instead of adding
	// a duplicate line
	const existing = form.value.items.find((r) => r !== row && r.item_code === code)
	if (existing) {
		existing.qty = (Number(existing.qty) || 0) + 1
		showInfo(__("{0} is already on this order — qty increased", [existing.item_name]))
		return
	}
	row.loading = true
	try {
		// the remembered pick (if any) prices the row in that UOM from the start
		const d = await fetchItemDetails(code, lastUsedUom(code), 1)
		Object.assign(row, {
			item_code: d?.item_code || code,
			item_name: d?.item_name || code,
			qty: 1,
			uom: d?.uom || d?.default_uom || d?.stock_uom || "",
			uoms: (d?.uoms || []).map((u) => u.uom),
			// kept for the payload but never shown — pricing lives in Desk
			rate: d?.price_list_rate || d?.rate || 0,
		})
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		row.loading = false
	}
}

async function onUomChange(row) {
	rememberUom(row.item_code, row.uom)
	try {
		const d = await fetchItemDetails(row.item_code, row.uom, row.qty)
		row.rate = d?.price_list_rate || d?.rate || row.rate
		if (d?.uoms?.length) row.uoms = d.uoms.map((u) => u.uom)
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

async function save(submitAfter) {
	triedSave.value = true
	const firstError = Object.values(errors.value)[0]
	if (firstError) {
		showError(firstError)
		return
	}
	saving.value = true
	try {
		const defaults = await loadPoDefaults()
		const payload = {
			supplier: form.value.supplier,
			transaction_date: form.value.transaction_date,
			schedule_date: form.value.schedule_date,
			company: props.company,
			currency: form.value.currency,
			set_warehouse: form.value.set_warehouse || null,
			buying_price_list: defaults?.price_list || null,
			taxes_and_charges: form.value.taxes_and_charges,
			remarks: form.value.remarks || "",
			items: filledItems.value.map((row) => ({
				item_code: row.item_code,
				qty: Number(row.qty) || 0,
				rate: Number(row.rate) || 0,
				uom: row.uom,
			})),
		}
		if (form.value.name) payload.name = form.value.name
		const res = await call(`${API}.save_purchase_order`, {
			data: JSON.stringify(payload),
			pos_profile: props.posProfile,
			submit: submitAfter ? 1 : 0,
		})
		showSuccess(
			submitAfter
				? __("Purchase Order {0} submitted", [res?.name])
				: __("Purchase Order {0} saved", [res?.name]),
		)
		await uploadAfterSave("Purchase Order", res?.name, formPending.value)
		formPending.value = []
		delete orderDetails.value[res?.name]
		view.value = "list"
		await loadOrders()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		saving.value = false
	}
}
</script>
