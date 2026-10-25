<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: dialogTitle, size: '4xl' }"
	>
		<template #body-content>
			<!-- LIST VIEW -->
			<div v-if="view === 'list'" class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<div class="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5" role="group" :aria-label="__('Status')">
					<button
						v-for="chip in STATUS_CHIPS"
						:key="chip.value"
						type="button"
						class="shrink-0 inline-flex items-center min-h-[44px] md:min-h-[40px] rounded-full border px-3.5 py-1 text-xs font-medium transition-colors touch-manipulation"
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

				<div class="flex flex-wrap items-center justify-between gap-3">
					<div class="relative flex-1 min-w-[200px] sm:min-w-[280px]">
						<FeatherIcon
							name="search"
							class="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
						/>
						<input
							v-model="searchTerm"
							type="text"
							data-test="list-search"
							:placeholder="__('Search PO number or supplier...')"
							class="h-11 md:h-10 w-full rounded-lg border border-gray-300 py-2 pe-3 ps-9 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
							@input="onListSearch"
						/>
					</div>
					<div class="ms-auto flex items-center gap-2">
						<RefreshButton :loading="loadingOrders" @click="loadOrders" />
						<Button
							variant="solid"
							class="h-11 md:h-10 touch-manipulation"
							data-test="new-button"
							@click="openNew"
						>
							<template #prefix>
								<FeatherIcon name="plus" class="w-4 h-4" />
							</template>
							{{ __("New Purchase Order") }}
						</Button>
					</div>
				</div>

				<div class="rounded-xl border border-gray-200 bg-white">
					<!-- desktop column header; rows below share the same grid -->
					<div
						class="hidden border-b border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500 xl:grid xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1.6fr)_8.5rem_7rem_10rem_minmax(0,1.2fr)] xl:gap-x-3"
					>
						<span>{{ __("PO Number") }}</span>
						<span>{{ __("Supplier") }}</span>
						<span>{{ __("Transaction Date") }}</span>
						<span>{{ __("Required By") }}</span>
						<span>{{ __("Status") }}</span>
						<span>{{ __("Shipping status") }}</span>
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
							tabindex="0"
							class="cursor-pointer px-4 py-3 transition-colors hover:bg-gray-50 focus:outline-none focus-visible:bg-gray-50"
							:data-test="`po-${order.name}`"
							@click="openDoc(order.name)"
							@keydown.enter="openDoc(order.name)"
						>
							<div class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 xl:gap-y-0 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1.6fr)_8.5rem_7rem_10rem_minmax(0,1.2fr)] xl:gap-x-3">
								<div class="flex min-w-0 items-center gap-1.5 xl:order-1">
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
								<div class="flex items-center gap-1 justify-self-end xl:order-5 xl:justify-self-start">
									<span :class="statusPill(order.status)">{{ __(order.status) }}</span>
								</div>
								<p class="col-span-2 truncate text-sm text-gray-700 xl:order-2 xl:col-span-1">
									{{ order.supplier_name || order.supplier }}
								</p>
								<p class="col-span-2 text-xs text-gray-500 xl:order-3 xl:col-span-1 xl:text-sm xl:text-gray-700">
									<span class="xl:hidden">{{ __("Ordered") }}&nbsp;</span>{{ formatDate(order.transaction_date) }}
									<span class="xl:hidden"> · {{ __("Required By") }} {{ formatDate(order.schedule_date) }}</span>
								</p>
								<p class="hidden text-sm text-gray-700 xl:order-4 xl:block">
									{{ formatDate(order.schedule_date) }}
								</p>
								<div class="col-span-2 flex items-center gap-1.5 text-xs text-gray-500 xl:order-6 xl:col-span-1 xl:text-sm">
									<span class="text-gray-500 xl:hidden">{{ __("Shipping status") }}:</span>
									<span
										v-if="shippingStatusInfo(order.shipping_status)"
										:class="shippingStatusPill(order.shipping_status)"
									>
										{{ shippingStatusText(order.shipping_status) }}
									</span>
									<span v-else class="text-gray-400">—</span>
								</div>
							</div>
						</div>
					</div>
				</div>
			</div>

			<!-- DOC VIEW -->
			<div v-else-if="view === 'doc'" class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<PurchaseDocView
					:title="__('Purchase Order')"
					kind="po"
					:doc="currentDoc"
					:loading="loadingDoc"
					:can-attach="canAttachPO"
					:pending-files="[]"
					@back="goList"
					@attach-add="onDocAttachAdd"
					@open-erpnext="openInErpnext(currentDoc)"
				>
					<template #actions>
						<template v-if="currentDoc?.docstatus === 0">
							<Button
								variant="subtle"
								data-testid="doc-edit"
								@click="openEdit(currentDoc)"
							>
								{{ __("Edit") }}
							</Button>
							<Button
								v-if="canSubmitPO"
								variant="solid"
								class="h-11 md:h-10 touch-manipulation"
								data-testid="primary-action"
								:loading="submitting"
								@click="submitDoc(currentDoc?.name)"
							>
								{{ __("Submit") }}
							</Button>
						</template>
						<Button
							v-else-if="
								currentDoc?.docstatus === 1 &&
								(currentDoc?.per_received < 100 || currentDoc?.per_received == null) &&
								(!poDefaults?.receive_requires_delivery_note || currentDoc?.delivery_ready) &&
								canReceivePR
							"
							variant="solid"
							class="h-11 md:h-10 touch-manipulation"
							data-testid="primary-action"
							data-test="receive-button"
							@click="openReceive(currentDoc)"
						>
							{{ __("Receive") }}
						</Button>

						<div v-if="currentDoc?.name" class="relative">
							<button
								type="button"
								data-testid="doc-menu-button"
								class="flex h-11 w-11 md:h-10 md:w-10 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-900 touch-manipulation"
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
									v-if="currentDoc?.docstatus === 1 && canCancelPO && !isFactoryLinked(currentDoc)"
									type="button"
									data-testid="doc-menu-cancel"
									class="block w-full px-3 py-2 text-start text-sm text-red-600 hover:bg-red-50"
									@click="cancelDoc(currentDoc); docMenuOpen = false"
								>
									{{ __("Cancel") }}
								</button>
								<button
									type="button"
									data-testid="doc-menu-open-erpnext"
									class="block w-full px-3 py-2 text-start text-sm text-gray-700 hover:bg-gray-50"
									@click="openInErpnext(currentDoc); docMenuOpen = false"
								>
									{{ __("Open in ERPNext") }}
								</button>
							</div>
						</div>
					</template>

					<template #after-items>
						<section
							v-if="currentDoc?.receipts?.length"
							data-testid="linked-receipts"
							class="rounded-xl border border-gray-200 bg-white p-4"
						>
							<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Purchase Receipts") }}</h4>
							<div class="divide-y divide-gray-100">
								<div
									v-for="pr in currentDoc.receipts"
									:key="pr.name"
									class="flex cursor-pointer items-center justify-between py-2.5 transition-colors hover:bg-gray-50"
									data-testid="linked-receipt-row"
									@click="openReceiptDoc(pr.name)"
								>
									<div class="flex items-center gap-2">
										<span class="text-sm font-medium text-gray-900">{{ pr.name }}</span>
										<span :class="statusPill(pr.workflow_state || pr.status)">
											{{ __(pr.workflow_state || pr.status) }}
										</span>
									</div>
									<div class="flex items-center gap-2 text-xs text-gray-500">
										<span>{{ formatDate(pr.posting_date) }}</span>
										<FeatherIcon name="chevron-right" class="h-4 w-4 text-gray-400" />
									</div>
								</div>
							</div>
						</section>
					</template>
				</PurchaseDocView>
			</div>

			<!-- RECEIPT DETAIL VIEW (from PO linked receipt) -->
			<div v-else-if="view === 'receipt'" class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<PurchaseDocView
					:title="__('Purchase Order / {0}', [currentDoc?.name || ''])"
					kind="pr"
					:doc="currentReceiptDoc"
					:loading="loadingReceiptDoc"
					:can-attach="currentReceiptDoc?.docstatus === 0"
					:pending-files="[]"
					@back="view = 'doc'"
					@attach-add="onReceiptAttachAdd"
					@open-erpnext="openInErpnext(currentReceiptDoc)"
				>
					<template #actions>
						<Button
							v-if="canSubmitPR && currentReceiptDoc?.editable && (currentReceiptDoc?.submit_actions?.length || 0) <= 1"
							variant="solid"
							data-testid="primary-action"
							:loading="submittingReceipt"
							@click="submitReceiptDoc(currentReceiptDoc?.submit_actions?.[0])"
						>
							{{ currentReceiptDoc?.submit_actions?.[0] || __("Submit") }}
						</Button>
						<div
							v-else-if="canSubmitPR && currentReceiptDoc?.editable && (currentReceiptDoc?.submit_actions?.length || 0) > 1"
							class="relative"
						>
							<Button
								variant="solid"
								data-testid="primary-action"
								@click="receiptActionsMenuOpen = !receiptActionsMenuOpen"
							>
								{{ __("Actions") }}
								<template #suffix>
									<FeatherIcon name="chevron-down" class="h-4 w-4" />
								</template>
							</Button>
							<div
								v-if="receiptActionsMenuOpen"
								data-testid="actions-menu"
								class="absolute end-0 z-20 mt-1 w-40 rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
							>
								<button
									v-for="a in currentReceiptDoc.submit_actions"
									:key="a"
									type="button"
									class="block w-full px-3 py-2 text-start text-sm text-gray-700 hover:bg-gray-50"
									@click="submitReceiptDoc(a)"
								>
									{{ a }}
								</button>
							</div>
						</div>

						<div v-if="currentReceiptDoc?.name" class="relative">
							<button
								type="button"
								data-testid="doc-menu-button"
								class="flex h-8 w-8 items-center justify-center rounded text-gray-500 hover:bg-gray-100 hover:text-gray-900"
								:aria-label="__('More actions')"
								:title="__('More actions')"
								@click="receiptMenuOpen = !receiptMenuOpen"
							>
								<FeatherIcon name="more-horizontal" class="h-4 w-4" />
							</button>
							<div
								v-if="receiptMenuOpen"
								data-testid="doc-menu"
								class="absolute end-0 z-20 mt-1 w-40 rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
							>
								<button
									type="button"
									data-testid="doc-menu-open-erpnext"
									class="block w-full px-3 py-2 text-start text-sm text-gray-700 hover:bg-gray-50"
									@click="openInErpnext(currentReceiptDoc); receiptMenuOpen = false"
								>
									{{ __("Open in ERPNext") }}
								</button>
							</div>
						</div>
					</template>
				</PurchaseDocView>
			</div>

			<!-- RECEIVE VIEW -->
			<div v-else-if="view === 'receive'" class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<!-- Header bar -->
				<div data-testid="doc-header" class="flex flex-wrap items-center gap-2">
					<button
						type="button"
						data-testid="receive-back"
						class="-ms-1 flex h-11 w-11 md:h-10 md:w-10 shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-900 touch-manipulation"
						:aria-label="__('Back')"
						:title="__('Back')"
						@click="backFromReceive"
					>
						<FeatherIcon name="arrow-left" class="h-5 w-5" />
					</button>
					<nav class="flex min-w-0 items-center gap-1.5 text-sm">
						<span class="text-gray-500">{{ __("Purchase Order") }}</span>
						<span class="text-gray-300">/</span>
						<span class="truncate font-semibold text-gray-900">
							{{ receive.po_name }}
						</span>
						<span class="text-gray-300">/</span>
						<span class="text-gray-500">{{ __("Receive") }}</span>
					</nav>

					<div class="ms-auto flex items-center gap-2">
						<Button
							variant="solid"
							class="h-11 md:h-10 touch-manipulation"
							data-testid="receive-save"
							:disabled="hasExcessQty || saving"
							:loading="saving"
							@click="saveReceipt"
						>
							{{ __("Save") }}
						</Button>
					</div>
				</div>

				<!-- Info Card -->
				<section class="rounded-xl border border-gray-200 bg-white p-4">
					<div class="grid grid-cols-2 gap-x-4 gap-y-3 md:grid-cols-4">
						<div class="min-w-0">
							<p class="text-xs text-gray-500">{{ __("Supplier") }}</p>
							<p
								class="truncate text-sm font-semibold text-gray-900"
								data-testid="receive-supplier"
							>
								{{ receive.supplier_name || receive.supplier }}
							</p>
						</div>
						<div class="min-w-0">
							<p class="text-xs text-gray-500">{{ __("Purchase Order") }}</p>
							<p class="truncate text-sm text-gray-900">{{ receive.po_name }}</p>
						</div>
						<div v-if="receive.inter_company_reference" class="min-w-0">
							<p class="text-xs text-gray-500">{{ __("Delivery Note") }}</p>
							<p class="truncate text-sm text-gray-900">
								{{ receive.inter_company_reference }}
							</p>
						</div>
						<div v-if="receive.set_warehouse" class="min-w-0">
							<p class="text-xs text-gray-500">{{ __("Warehouse") }}</p>
							<p class="truncate text-sm text-gray-900">{{ receive.set_warehouse }}</p>
						</div>
						<div class="col-span-2 min-w-0 md:col-span-1">
							<label
								for="pr-posting-date"
								class="block text-xs text-gray-500 mb-1"
							>
								{{ __("Posting Date") }}
							</label>
							<input
								id="pr-posting-date"
								v-model="receive.posting_date"
								type="date"
								data-test="receive-posting-date"
								class="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
							/>
						</div>
					</div>
				</section>

				<!-- Items Table Card -->
				<section class="rounded-xl border border-gray-200 bg-white p-4">
					<div class="mb-3 flex items-center justify-between gap-2">
						<h4 class="text-sm font-semibold text-gray-900">
							{{ __("Items to Receive") }}
						</h4>
						<div class="flex items-center gap-2">
							<Button
								variant="subtle"
								size="sm"
								data-testid="fill-remaining"
								@click="fillRemaining"
							>
								{{ __("Fill remaining") }}
							</Button>
							<Button
								variant="subtle"
								size="sm"
								data-testid="clear-qtys"
								@click="clearReceiveQtys"
							>
								{{ __("Clear") }}
							</Button>
						</div>
					</div>

					<div v-if="receive.items.length" class="overflow-x-auto">
						<table class="w-full text-sm" data-test="receive-table">
							<thead>
								<tr class="border-b border-gray-200 text-xs font-medium uppercase text-gray-500">
									<th class="py-2 text-start">{{ __("Item") }}</th>
									<th class="py-2 text-end">{{ __("Ordered") }}</th>
									<th class="py-2 text-end">{{ __("Received") }}</th>
									<th class="py-2 text-end">{{ __("Remaining") }}</th>
									<th class="py-2 text-end w-40">{{ __("Qty") }}</th>
								</tr>
							</thead>
							<tbody class="divide-y divide-gray-100">
								<tr
									v-for="row in receive.items"
									:key="row.purchase_order_item || row.item_code"
								>
									<td class="py-2.5 pe-3">
										<div class="font-medium text-gray-900">
											{{ row.item_name || row.item_code }}
										</div>
										<div v-if="row.item_name && row.item_code" class="text-xs text-gray-500">
											{{ row.item_code }}
										</div>
									</td>
									<td class="py-2.5 px-3 text-end tabular-nums text-gray-700">
										{{ row.ordered_qty }}
									</td>
									<td class="py-2.5 px-3 text-end tabular-nums text-gray-700">
										{{ row.received_qty }}
									</td>
									<td class="py-2.5 px-3 text-end tabular-nums text-gray-700">
										{{ row.pending_qty }}
									</td>
									<td class="py-2.5 ps-3 text-end">
										<div class="flex items-center justify-end gap-1.5">
											<input
												v-model.number="row.qty"
												data-test="receive-qty"
												data-testid="receive-qty"
												type="number"
												min="0"
												step="any"
												:aria-label="`${row.item_name} — ${__('Qty')}`"
												class="w-28 rounded-lg border border-gray-300 px-2 py-1 text-end text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
											/>
											<span class="text-xs text-gray-500">{{ row.uom }}</span>
										</div>
										<div
											v-if="Number(row.qty || 0) > Number(row.pending_qty || 0)"
											data-testid="excess-warning"
											class="mt-1 text-xs text-amber-600"
										>
											{{ __("More than remaining ({0})", [row.pending_qty]) }}
										</div>
									</td>
								</tr>
							</tbody>
						</table>
					</div>
				</section>

				<!-- Attachments -->
				<section class="rounded-xl border border-gray-200 bg-white p-4">
					<PurchaseAttachments
						editable
						:pending="receivePending"
						:label="__('Delivery evidence (surat jalan / delivery note)')"
						@add="onAttachAdd($event, receivePending)"
						@remove="(row) => removeAttachment(receivePending, row)"
					/>
				</section>
			</div>

			<!-- FORM VIEW -->
			<div v-else class="mx-auto flex w-full max-w-6xl flex-col gap-4">
				<div class="flex flex-wrap items-center gap-2">
					<button
						type="button"
						data-testid="doc-back"
						class="-ms-1 flex h-11 w-11 md:h-10 md:w-10 shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-900 touch-manipulation"
						:aria-label="__('Back')"
						:title="__('Back')"
						@click="backFromForm"
					>
						<FeatherIcon name="arrow-left" class="h-5 w-5" />
					</button>
					<nav class="flex min-w-0 items-center gap-1.5 text-sm">
						<span class="text-gray-500">{{ __("Purchase Order") }}</span>
						<span class="text-gray-300">/</span>
						<span class="truncate font-semibold text-gray-900">
							{{ form.name || __("New Purchase Order") }}
						</span>
					</nav>
					<span v-if="form.name" :class="statusPill('Draft')">{{ __("Draft") }}</span>

					<div class="ms-auto flex items-center gap-2">
						<Button
							v-if="formPrimaryMode === 'save'"
							variant="solid"
							class="h-11 md:h-10 touch-manipulation"
							data-testid="primary-action"
							:loading="saving"
							@click="save(false)"
						>
							{{ __("Save") }}
						</Button>
						<Button
							v-else-if="canSubmitPO"
							variant="solid"
							class="h-11 md:h-10 touch-manipulation"
							data-testid="primary-action"
							:loading="submitting"
							@click="submitDoc(form.name)"
						>
							{{ __("Submit") }}
						</Button>
					</div>
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
			<div v-if="view === 'list' && !embedded" class="flex justify-end w-full">
				<Button variant="subtle" @click="show = false">{{ __("Close") }}</Button>
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
import AutocompleteSelect from "@/components/common/AutocompleteSelect.vue"
import PurchaseAttachments from "@/components/purchase/PurchaseAttachments.vue"
import ConfirmDialog from "@/components/common/ConfirmDialog.vue"
import PurchaseDocView from "@/components/purchase/PurchaseDocView.vue"
import { __ } from "@/utils/translation"
import { useToast } from "@/composables/useToast"
import { useFormatters } from "@/composables/useFormatters"
import { usePermissions } from "@/composables/usePermissions"
import { sanitizePickedFiles, uploadPurchaseFiles } from "@/utils/attachments"
import { call, serverErrorMessage } from "@/utils/apiWrapper"
import { parseError } from "@/utils/errorHandler"
import { Button, FeatherIcon } from "frappe-ui"
import { computed, nextTick, reactive, ref, watch } from "vue"
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
const currentDoc = ref(null)
const loadingDoc = ref(false)
const submitting = ref(false)
const docMenuOpen = ref(false)

const currentReceiptDoc = ref(null)
const loadingReceiptDoc = ref(false)
const submittingReceipt = ref(false)
const receiptActionsMenuOpen = ref(false)
const receiptMenuOpen = ref(false)

const canAttachPO = computed(() => {
	if (!currentDoc.value) return false
	const d = currentDoc.value
	return (
		d.docstatus === 0 ||
		d.status === "To Receive" ||
		d.status === "To Receive and Bill"
	)
})

// Confirmation Dialog
const confirm = reactive({
	open: false,
	title: "",
	message: "",
	confirmLabel: "",
	destructive: false,
})
let confirmResolve = null

function confirmAsk({
	title,
	message = "",
	confirmLabel = "",
	destructive = false,
}) {
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
	if (confirmResolve) {
		confirmResolve(value)
		confirmResolve = null
	}
}

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
	Draft: "bg-orange-50 text-orange-700 ring-orange-600/20",
	"To Receive and Bill": "bg-blue-50 text-blue-700 ring-blue-600/20",
	"To Receive": "bg-blue-50 text-blue-700 ring-blue-600/20",
	"To Bill": "bg-amber-50 text-amber-700 ring-amber-600/20",
	Completed: "bg-green-50 text-green-700 ring-green-600/20",
	Cancelled: "bg-gray-50 text-gray-600 ring-gray-500/20",
}

const orders = ref([])
const loadingOrders = ref(false)
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
		poDefaults.value = await call(`${API}.get_po_defaults`, {
			pos_profile: props.posProfile,
		})
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
		"inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
		STATUS_TONES[status] || "bg-gray-50 text-gray-600 ring-gray-500/20",
	]
}

const SHIPPING_STATUS_MAP = {
	preparing: {
		label: "Goods being prepared",
		tone: "bg-amber-50 text-amber-700 ring-amber-200",
	},
	in_transit: {
		label: "In transit",
		tone: "bg-blue-50 text-blue-700 ring-blue-200",
	},
	received: {
		label: "Received",
		tone: "bg-green-50 text-green-700 ring-green-200",
	},
}

function shippingStatusInfo(status) {
	return SHIPPING_STATUS_MAP[status] || null
}

function shippingStatusText(status) {
	const info = shippingStatusInfo(status)
	return info ? __(info.label) : "—"
}

function shippingStatusPill(status) {
	const info = shippingStatusInfo(status)
	return [
		"inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
		info?.tone || "bg-gray-50 text-gray-700 ring-gray-200",
	]
}

// Same condition as the "Factory SO" badge: the live PO↔SO link resolves on
// the selling-company SO side, and a factory-linked PO must not be cancelled
// from the POS (the factory plans against it).
function isFactoryLinked(order) {
	return Boolean(
		order.is_internal_supplier && order.inter_company_order_reference,
	)
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

async function submitDoc(name) {
	if (submitting.value || !name) return
	const ok = await confirmAsk({
		title: __("Submit Purchase Order"),
		message: __(
			"Submit {0}? Once submitted, you can receive goods against it.",
			[name],
		),
		confirmLabel: __("Submit"),
	})
	if (!ok) return
	submitting.value = true
	try {
		const res = await call(`${API}.submit_purchase_order`, { name })
		showSuccess(__("Purchase Order {0} submitted", [res?.name || name]))
		await loadOrders()
		await openDoc(res?.name || name)
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		submitting.value = false
	}
}

async function cancelDoc(doc) {
	const name = typeof doc === "string" ? doc : doc?.name
	if (!name) return
	const ok = await confirmAsk({
		title: __("Cancel Purchase Order"),
		message: __("Cancel {0}? This cannot be undone.", [name]),
		confirmLabel: __("Cancel Purchase Order"),
		destructive: true,
	})
	if (!ok) return
	try {
		const res = await call(`${API}.cancel_purchase_order`, { name })
		showSuccess(__("Purchase Order {0} cancelled", [res?.name || name]))
		if (currentDoc.value?.name === name) {
			await reloadDoc(name)
		}
		await loadOrders()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

function openInErpnext(doc) {
	const name = typeof doc === "string" ? doc : doc?.name
	if (!name) return
	const doctype =
		view.value === "receipt" || doc?.doctype === "Purchase Receipt"
			? "purchase-receipt"
			: "purchase-order"
	window.open(`/app/${doctype}/${name}`, "_blank")
}

async function openDoc(name) {
	view.value = "doc"
	loadingDoc.value = true
	docMenuOpen.value = false
	currentDoc.value = { name }
	try {
		const res = await call(`${API}.get_purchase_order`, { name })
		currentDoc.value = res || { name }
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		loadingDoc.value = false
	}
}

async function reloadDoc(name = currentDoc.value?.name) {
	if (!name) return
	try {
		const res = await call(`${API}.get_purchase_order`, { name })
		currentDoc.value = res || currentDoc.value
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

function goList() {
	view.value = "list"
	currentDoc.value = null
	docMenuOpen.value = false
}

async function onDocAttachAdd(files) {
	if (!currentDoc.value?.name) return
	const { ok, tooBig } = sanitizePickedFiles(files)
	if (tooBig.length) {
		showError(__("File {0} exceeds the {1} MB limit", [tooBig[0].name, 10]))
	}
	if (!ok.length) return
	try {
		await uploadPurchaseFiles("Purchase Order", currentDoc.value.name, ok)
		showSuccess(__("{0} attachment(s) uploaded", [ok.length]))
		await reloadDoc(currentDoc.value.name)
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

async function openReceiptDoc(name) {
	view.value = "receipt"
	loadingReceiptDoc.value = true
	receiptActionsMenuOpen.value = false
	receiptMenuOpen.value = false
	currentReceiptDoc.value = { name }
	try {
		const res = await call(`${PR_API}.get_purchase_receipt`, { name })
		currentReceiptDoc.value = res || { name }
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		loadingReceiptDoc.value = false
	}
}

async function reloadReceiptDoc(name = currentReceiptDoc.value?.name) {
	if (!name) return
	try {
		const res = await call(`${PR_API}.get_purchase_receipt`, { name })
		currentReceiptDoc.value = res || currentReceiptDoc.value
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

async function submitReceiptDoc(action) {
	if (submittingReceipt.value || !currentReceiptDoc.value?.name) return
	receiptActionsMenuOpen.value = false
	const name = currentReceiptDoc.value.name
	const ok = await confirmAsk({
		title: __("Submit Purchase Receipt"),
		message: __(
			"Submit {0}? Stock will be updated and the receipt can no longer be edited.",
			[name],
		),
		confirmLabel: action || __("Submit"),
	})
	if (!ok) return
	submittingReceipt.value = true
	try {
		const res = await call(`${PR_API}.submit_purchase_receipt`, {
			name,
			action: action || undefined,
		})
		showSuccess(__("Purchase Receipt {0} submitted", [res?.name || name]))
		await reloadReceiptDoc(name)
		await reloadDoc()
		await loadOrders()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		submittingReceipt.value = false
	}
}

async function onReceiptAttachAdd(files) {
	if (!currentReceiptDoc.value?.name) return
	const { ok, tooBig } = sanitizePickedFiles(files)
	if (tooBig.length) {
		showError(__("File {0} exceeds the {1} MB limit", [tooBig[0].name, 10]))
	}
	if (!ok.length) return
	try {
		await uploadPurchaseFiles(
			"Purchase Receipt",
			currentReceiptDoc.value.name,
			ok,
		)
		showSuccess(__("{0} attachment(s) uploaded", [ok.length]))
		await reloadReceiptDoc(currentReceiptDoc.value.name)
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

// ---------- attachments (evidence files held client-side until save) ----------
const formPending = ref([])
const receivePending = ref([])

function onAttachAdd(files, target) {
	const { ok, tooBig } = sanitizePickedFiles(files)
	if (tooBig.length)
		showError(__("File {0} exceeds the {1} MB limit", [tooBig[0].name, 10]))
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

const hasExcessQty = computed(() => {
	return (receive.value?.items || []).some(
		(row) => Number(row.qty || 0) > Number(row.pending_qty || 0),
	)
})

function fillRemaining() {
	for (const row of receive.value.items) {
		row.qty = row.pending_qty
	}
}

function clearReceiveQtys() {
	for (const row of receive.value.items) {
		row.qty = 0
	}
}

function backFromReceive() {
	if (currentDoc.value?.name) {
		view.value = "doc"
	} else {
		view.value = "list"
	}
}

async function openReceive(order) {
	try {
		const d = await call(`${PR_API}.get_intercompany_receipt_draft`, {
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
			items: (d?.items || []).map((row) => ({ ...row, qty: row.pending_qty })),
		}
		view.value = "receive"
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

async function saveReceipt() {
	if (hasExcessQty.value) return
	const items = receive.value.items
		.filter((row) => Number(row.qty) > 0)
		.map((row) => ({
			item_code: row.item_code,
			qty: Number(row.qty),
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
			submit: 0,
		})
		showSuccess(__("Purchase Receipt {0} saved", [res?.name]))
		await uploadAfterSave("Purchase Receipt", res?.name, receivePending.value)
		receivePending.value = []
		await loadOrders()
		if (res?.name) {
			await openReceiptDoc(res.name)
		} else {
			view.value = "list"
		}
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
	return {
		key: ++rowSeq,
		item_code: "",
		item_name: "",
		qty: 1,
		uom: "",
		uoms: [],
		rate: 0,
		...fields,
	}
}
const itemsRef = ref(null)
// field errors appear only after the first save attempt
const triedSave = ref(false)
const filledItems = computed(() =>
	form.value.items.filter((row) => row.item_code),
)
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
function formSnapshotOf() {
	return JSON.stringify({
		supplier: form.value.supplier || "",
		transaction_date: form.value.transaction_date || "",
		schedule_date: form.value.schedule_date || "",
		remarks: form.value.remarks || "",
		taxes_and_charges: form.value.taxes_and_charges || "",
		set_warehouse: form.value.set_warehouse || "",
		items: (form.value.items || [])
			.filter((i) => i.item_code)
			.map((i) => ({
				item_code: i.item_code,
				qty: Number(i.qty) || 0,
				uom: i.uom || "",
			})),
	})
}

const formSnapshot = ref(null)

function takeFormSnapshot() {
	formSnapshot.value = formSnapshotOf()
}

const isFormDirty = computed(() => {
	if (formSnapshot.value === null) return false
	return formSnapshotOf() !== formSnapshot.value
})

const formPrimaryMode = computed(() => {
	if (!form.value.name || isFormDirty.value) return "save"
	return "submit"
})

async function backFromForm() {
	if (!form.value.name && isFormDirty.value) {
		const ok = await confirmAsk({
			title: __("Discard changes?"),
			message: __(
				"You have unsaved changes. Are you sure you want to discard them?",
			),
			confirmLabel: __("Discard"),
			destructive: true,
		})
		if (!ok) return
	}
	if (form.value.name) {
		view.value = "doc"
		await reloadDoc(form.value.name)
	} else {
		view.value = "list"
		currentDoc.value = null
	}
}

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
	takeFormSnapshot()
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
		takeFormSnapshot()
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
	if (!form.value.items.some((row) => !row.item_code))
		form.value.items.push(itemRow())
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
	const existing = form.value.items.find(
		(r) => r !== row && r.item_code === code,
	)
	if (existing) {
		existing.qty = (Number(existing.qty) || 0) + 1
		showInfo(
			__("{0} is already on this order — qty increased", [existing.item_name]),
		)
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
		const poName = res?.name || form.value.name
		showSuccess(
			submitAfter
				? __("Purchase Order {0} submitted", [poName])
				: __("Purchase Order {0} saved", [poName]),
		)
		await uploadAfterSave("Purchase Order", poName, formPending.value)
		formPending.value = []
		await loadOrders()
		if (poName) {
			await openDoc(poName)
		} else {
			view.value = "list"
		}
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		saving.value = false
	}
}
</script>
