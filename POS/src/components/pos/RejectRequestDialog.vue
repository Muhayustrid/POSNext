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
					<p class="text-sm text-gray-500">
						{{ __("Report unsold or damaged stock for Head Office review") }}
					</p>
					<div class="flex items-center gap-2">
						<RefreshButton :loading="loading" @click="loadList()" />
						<Button variant="solid" @click="openNew">
							<template #prefix>
								<FeatherIcon name="plus" class="h-4 w-4" />
							</template>
							{{ __("New Request") }}
						</Button>
					</div>
				</div>

				<div class="-mx-1 flex gap-1 overflow-x-auto px-1 pb-0.5" role="group" :aria-label="__('Status')">
					<button
						v-for="tab in statusTabs"
						:key="tab.value"
						type="button"
						class="shrink-0 rounded-full border px-3 py-1 text-xs font-medium transition-colors"
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

				<div class="rounded-xl border border-gray-200 bg-white">
					<!-- desktop column header; rows below share the same grid -->
					<div
						class="hidden border-b border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500 md:grid md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.2fr)_7rem_8rem_7rem] md:gap-x-3"
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
							class="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-4 py-3 text-start transition-colors hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-none md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.2fr)_7rem_8rem_7rem] md:gap-y-0"
							@click="openDetail(r.name)"
						>
							<span class="flex min-w-0 items-center gap-1.5">
								<span class="truncate text-sm font-semibold text-gray-900">{{ r.name }}</span>
								<span
									v-if="r.is_late"
									class="shrink-0 rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700 ring-1 ring-inset ring-red-200"
									>{{ __("Late") }}</span
								>
							</span>
							<!-- status sits top-right on cards, in its own column on desktop -->
							<span class="justify-self-end md:order-last md:justify-self-start">
								<span :class="statusPill(r.status)">{{ __(r.status) }}</span>
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

			<!-- NEW REQUEST (crew purposes) -->
			<div v-else-if="view === 'new'" class="mx-auto flex w-full max-w-6xl flex-col gap-4">
				<div class="flex items-center gap-2">
					<button
						type="button"
						class="-ms-1 rounded p-1 text-gray-500 hover:text-gray-900"
						:aria-label="__('Back')"
						:title="__('Back')"
						:disabled="submitting"
						@click="view = 'list'"
					>
						<FeatherIcon name="arrow-left" class="h-4 w-4" />
					</button>
					<h3 class="truncate text-base font-semibold text-gray-900">{{ __("New Reject Request") }}</h3>
				</div>

				<section class="rounded-xl border border-gray-200 bg-white p-4">
					<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Request Information") }}</h4>
					<div class="grid gap-3 sm:grid-cols-2">
						<div>
							<label for="rr-purpose" class="mb-1 block text-xs font-medium text-gray-700">
								{{ __("Purpose") }} <span class="text-red-500">*</span>
							</label>
							<select
								id="rr-purpose"
								v-model="form.purpose"
								data-testid="purpose-select"
								class="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
							>
								<option v-for="p in purposes" :key="p" :value="p">{{ __(p) }}</option>
							</select>
						</div>
						<div>
							<label for="rr-incident-date" class="mb-1 block text-xs font-medium text-gray-700">
								{{ __("Incident Date") }} <span class="text-red-500">*</span>
							</label>
							<input
								id="rr-incident-date"
								v-model="form.incident_date"
								type="date"
								class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
							/>
						</div>
					</div>
					<p v-if="posProfile" class="mt-3 border-t border-gray-100 pt-3 text-xs text-gray-500">
						{{ __("Outlet") }}: <b class="font-medium text-gray-700">{{ posProfile }}</b>
					</p>
				</section>

				<!-- items: ERPNext-style grid, the search row sits below the table -->
				<section class="rounded-xl border border-gray-200 bg-white">
					<h4 class="px-4 pb-2 pt-4 text-sm font-semibold text-gray-900">
						{{ __("Items") }} <span class="text-red-500">*</span>
					</h4>
					<div
						class="hidden border-y border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500 md:grid md:grid-cols-[2rem_minmax(0,1fr)_7rem_2rem] md:gap-3"
					>
						<span>#</span>
						<span>{{ __("Item") }}</span>
						<span>{{ __("Qty") }}</span>
						<span></span>
					</div>
					<div
						v-for="(row, idx) in form.items"
						:key="row.item_code"
						class="grid grid-cols-[minmax(0,1fr)_6rem_2rem] items-center gap-2 border-b border-gray-100 px-4 py-2 hover:bg-gray-50 md:grid-cols-[2rem_minmax(0,1fr)_7rem_2rem] md:gap-3 md:py-1.5"
					>
						<span class="hidden text-xs tabular-nums text-gray-400 md:block">{{ idx + 1 }}</span>
						<div class="min-w-0">
							<p class="truncate text-sm font-medium text-gray-900">{{ row.item_name }}</p>
							<p v-if="row.item_name !== row.item_code" class="truncate text-xs text-gray-400">
								{{ row.item_code }}
							</p>
						</div>
						<input
							v-model.number="row.qty"
							type="number"
							min="0"
							step="any"
							inputmode="decimal"
							:aria-label="`${row.item_name} — ${__('Qty')}`"
							class="w-full rounded-md border px-2 py-1.5 text-sm tabular-nums focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
							:class="row.qty > 0 ? 'border-gray-300' : 'border-red-400 bg-red-50'"
						/>
						<button
							type="button"
							class="flex h-7 w-7 items-center justify-center rounded text-gray-400 hover:bg-red-50 hover:text-red-600"
							:aria-label="__('Remove item')"
							:title="__('Remove item')"
							@click="form.items.splice(idx, 1)"
						>
							<FeatherIcon name="x" class="h-4 w-4" />
						</button>
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
						<p v-if="!form.items.length" class="mt-2 text-xs text-gray-400">
							{{ __("Search and pick the rejected items. Picking the same item twice keeps one row.") }}
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
							class="mb-3 w-full resize-y rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
						/>
						<label for="rr-video" class="mb-1 block text-xs font-medium text-gray-700">
							{{ __("Video Link") }}
						</label>
						<input
							id="rr-video"
							v-model="form.video_link"
							type="text"
							inputmode="url"
							data-testid="video-link"
							class="w-full rounded-lg border px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
							:class="form.video_link && !videoLinkOk ? 'border-red-400' : 'border-gray-300'"
						/>
						<p v-if="form.video_link && !videoLinkOk" class="mt-1 text-xs text-red-600">
							{{ __("Video link must start with http:// or https://") }}
						</p>
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

					<!-- what Submit still waits for, so a disabled button is never a mystery -->
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

			<!-- DETAIL -->
			<div v-else class="mx-auto flex w-full max-w-6xl flex-col gap-4">
				<div v-if="loadingDetail" class="py-10 text-center text-sm text-gray-500">
					{{ __("Loading...") }}
				</div>
				<template v-else-if="detail">
					<div class="flex flex-wrap items-center gap-2">
						<button
							type="button"
							class="-ms-1 rounded p-1 text-gray-500 hover:text-gray-900"
							:aria-label="__('Back')"
							:title="__('Back')"
							@click="backToList"
						>
							<FeatherIcon name="arrow-left" class="h-4 w-4" />
						</button>
						<h3 class="truncate text-base font-semibold text-gray-900">{{ detail.name }}</h3>
						<span :class="statusPill(detail.status)">{{ __(detail.status) }}</span>
						<span
							v-if="detail.is_late"
							class="rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700 ring-1 ring-inset ring-red-200"
							>{{ __("Late") }}</span
						>
					</div>

					<div class="grid gap-4 md:grid-cols-[minmax(0,1fr)_18rem]">
						<div class="flex min-w-0 flex-col gap-4">
							<section class="rounded-xl border border-gray-200 bg-white">
								<h4 class="px-4 pb-2 pt-4 text-sm font-semibold text-gray-900">{{ __("Items") }}</h4>
								<div
									class="grid grid-cols-[minmax(0,1fr)_4rem_7rem] gap-3 border-y border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500"
								>
									<span>{{ __("Item") }}</span>
									<span class="text-end">{{ __("Qty") }}</span>
									<span class="text-end">{{ __("Amount") }}</span>
								</div>
								<div
									v-for="row in detail.items"
									:key="row.item_code"
									class="grid grid-cols-[minmax(0,1fr)_4rem_7rem] items-center gap-3 border-b border-gray-100 px-4 py-2 text-sm last:border-b-0"
								>
									<span class="truncate text-gray-900">{{ row.item_name || row.item_code }}</span>
									<span class="text-end tabular-nums" data-testid="item-qty">{{ row.qty }}</span>
									<span class="text-end tabular-nums text-gray-700">{{ formatCurrency(row.amount) }}</span>
								</div>
								<p v-if="!detail.items?.length" class="px-4 py-4 text-center text-sm text-gray-500">
									{{ __("No items") }}
								</p>
							</section>

							<!-- Draft: crew-editable reason + video link + photos. Items are never
							     editable here — qty on a system Production Loss is locked
							     server-side, and a new Unsold request picks items in the New form. -->
							<section v-if="detail.docstatus === 0" class="rounded-xl border border-gray-200 bg-white p-4">
								<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Reason & Evidence") }}</h4>
								<label for="rr-detail-reason" class="mb-1 block text-xs font-medium text-gray-700">
									{{ __("Reason") }} <span class="text-red-500">*</span>
								</label>
								<textarea
									id="rr-detail-reason"
									v-model="detail.reason"
									rows="3"
									class="mb-3 w-full resize-y rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
								/>
								<label for="rr-detail-video" class="mb-1 block text-xs font-medium text-gray-700">
									{{ __("Video Link") }}
								</label>
								<input
									id="rr-detail-video"
									v-model="detail.video_link"
									type="text"
									inputmode="url"
									data-testid="video-link"
									class="w-full rounded-lg border px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
									:class="detail.video_link && !videoLinkOk ? 'border-red-400' : 'border-gray-300'"
								/>
								<a
									v-if="detail.video_link && videoLinkOk"
									:href="detail.video_link"
									target="_blank"
									rel="noopener"
									class="mt-1 block break-all text-xs text-blue-600 hover:underline"
									>{{ detail.video_link }}</a
								>
								<p v-if="detail.video_link && !videoLinkOk" class="mt-1 text-xs text-red-600">
									{{ __("Video link must start with http:// or https://") }}
								</p>
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

							<!-- submitted: read-only reason, video link and evidence -->
							<section v-else class="rounded-xl border border-gray-200 bg-white p-4">
								<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Reason & Evidence") }}</h4>
								<dl class="space-y-2 text-sm">
									<div>
										<dt class="text-xs text-gray-500">{{ __("Reason") }}</dt>
										<dd class="whitespace-pre-line text-gray-900">{{ detail.reason || "—" }}</dd>
									</div>
									<div v-if="detail.video_link">
										<dt class="text-xs text-gray-500">{{ __("Video Link") }}</dt>
										<dd>
											<a
												:href="detail.video_link"
												target="_blank"
												rel="noopener"
												class="break-all text-blue-600 hover:underline"
												>{{ detail.video_link }}</a
											>
										</dd>
									</div>
								</dl>
								<div class="mt-3">
									<PurchaseAttachments :attached="detail.evidence" :label="__('Evidence Photos')" />
								</div>
							</section>

							<!-- Approved/Rejected: upload destruction proof, then close -->
							<section
								v-if="detail.status === 'Approved' || detail.status === 'Rejected'"
								class="rounded-xl border border-gray-200 bg-white p-4"
							>
								<h4 class="mb-1 text-sm font-semibold text-gray-900">{{ __("Destruction Photos") }}</h4>
								<p class="mb-3 text-xs text-gray-500">
									{{ __("Upload proof the stock was destroyed, then close the request.") }}
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
						</div>

						<div class="flex flex-col gap-4">
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
										<dd class="font-medium tabular-nums text-gray-900">{{ detail.evidence.length }}</dd>
									</div>
									<div class="flex justify-between gap-3 border-t border-gray-200 pt-2">
										<dt class="text-gray-500">{{ __("Total Amount") }}</dt>
										<dd class="font-semibold tabular-nums text-gray-900">{{ formatCurrency(detail.total_amount) }}</dd>
									</div>
								</dl>
							</section>

							<!-- Head Office verdict: who bears the loss -->
							<section
								v-if="detail.decision"
								class="rounded-xl border p-4"
								:class="detail.decision === 'Rejected' ? 'border-red-200 bg-red-50/40' : 'border-green-200 bg-green-50/40'"
							>
								<h4 class="text-sm font-semibold text-gray-900">{{ __("Decision") }}: {{ __(detail.decision) }}</h4>
								<p class="mt-0.5 text-xs text-gray-600">
									{{
										detail.decision === "Approved"
											? __("Borne by company")
											: detail.decision === "Rejected"
												? __("Borne by crew")
												: ""
									}}
								</p>
								<p v-if="detail.decision_notes" class="mt-3 whitespace-pre-line text-sm text-gray-900">
									{{ detail.decision_notes }}
								</p>
								<ul
									v-if="detail.decision === 'Rejected' && detail.liabilities?.length"
									class="mt-3 divide-y divide-red-100 border-t border-red-100 text-sm"
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

		<template #actions>
			<div v-if="view === 'new'" class="mx-auto flex w-full max-w-6xl justify-between gap-2">
				<Button variant="subtle" :disabled="submitting" @click="view = 'list'">{{ __("Back") }}</Button>
				<Button variant="solid" :loading="submitting" :disabled="!canSubmit" @click="submitNew">
					{{ __("Submit") }}
				</Button>
			</div>
			<div v-else-if="view === 'detail' && detail && !loadingDetail" class="mx-auto flex w-full max-w-6xl justify-between gap-2">
				<Button variant="subtle" @click="backToList">{{ __("Back") }}</Button>
				<Button
					v-if="detail.docstatus === 0"
					variant="solid"
					:loading="submitting"
					:disabled="!canSubmitDetail"
					@click="submitDetail"
				>
					{{ __("Submit") }}
				</Button>
				<!-- Pending: the crew can still withdraw the request -->
				<Button
					v-else-if="detail.status === 'Pending'"
					variant="subtle"
					theme="red"
					:loading="acting"
					@click="doCancel"
				>
					{{ __("Cancel") }}
				</Button>
				<Button
					v-else-if="detail.status === 'Approved' || detail.status === 'Rejected'"
					variant="solid"
					:loading="acting"
					:disabled="!destructionCount"
					@click="doClose"
				>
					{{ __("Close") }}
				</Button>
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

const MAX_EVIDENCE = 5
const PAGE = 30

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
	{ value: "Pending", label: __("Pending") },
	{ value: "Approved", label: __("Approved") },
	{ value: "Rejected", label: __("Rejected") },
	{ value: "Closed", label: __("Closed") },
	{ value: "Cancelled", label: __("Cancelled") },
]

const acting = ref(false)
const submitting = ref(false)
const detail = ref(null)
// newly picked files waiting to be uploaded with the save
const newEvidence = ref([])
const newDestruction = ref([])

const itemSearch = ref("")
const itemResults = ref([])

// name of the draft created by a first submit attempt — kept across retries
// so a failed submit updates the same draft instead of inserting a duplicate
const draftName = ref(null)
// files already uploaded in this session — retries after a failed final
// save/close must not be blocked by gates that only count pending files
const uploadedEvidence = ref(0)
const uploadedDestruction = ref(0)

const form = ref({
	incident_date: "",
	purpose: "",
	reason: "",
	video_link: "",
	items: [],
	files: [],
})

// enabled non-system purposes for the crew dropdown; a Production Loss
// draft keeps its purpose read-only since it is not in this list
const purposes = ref([])

async function loadPurposes() {
	try {
		purposes.value =
			(await call("pos_next.api.reject_request.get_reject_purposes")) || []
		// the New form may already be open while purposes were loading
		if (!form.value.purpose && purposes.value.length)
			form.value.purpose = purposes.value[0]
	} catch (err) {
		showError(errMsg(err, "Failed to load reject purposes"))
	}
}

const STATUS_TONES = {
	Draft: "bg-orange-50 text-orange-700 ring-orange-200",
	Pending: "bg-blue-50 text-blue-700 ring-blue-200",
	Approved: "bg-green-50 text-green-700 ring-green-200",
	Rejected: "bg-red-50 text-red-700 ring-red-200",
	Closed: "bg-gray-100 text-gray-600 ring-gray-200",
	Cancelled: "bg-gray-50 text-gray-500 ring-gray-200",
}

function statusPill(status) {
	return [
		"inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
		STATUS_TONES[status] || "bg-gray-50 text-gray-700 ring-gray-200",
	]
}

const dialogTitle = computed(() =>
	view.value === "new" ? __("New Reject Request") : __("Reject Requests"),
)

const videoLinkOk = computed(() => {
	const link = (view.value === "detail" ? detail.value?.video_link : form.value.video_link) || ""
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

const canSubmit = computed(
	() =>
		!!props.posProfile &&
		!!form.value.purpose &&
		!!form.value.reason.trim() &&
		form.value.items.length > 0 &&
		form.value.items.every((i) => i.qty > 0) &&
		form.value.files.length + uploadedEvidence.value >= 1 &&
		form.value.files.length + uploadedEvidence.value <= MAX_EVIDENCE &&
		videoLinkOk.value,
)

// mirrors canSubmit item by item so the summary card explains a disabled Submit
const newChecklist = computed(() => {
	const photos = form.value.files.length + uploadedEvidence.value
	const items = form.value.items
	return [
		{ label: __("Items"), value: items.length, ok: items.length > 0 && items.every((i) => i.qty > 0) },
		{ label: __("Reason"), value: "", ok: !!form.value.reason.trim() },
		{ label: __("Evidence Photos"), value: `${photos}/${MAX_EVIDENCE}`, ok: photos >= 1 && photos <= MAX_EVIDENCE },
		{ label: __("Video Link"), value: form.value.video_link ? "" : __("Optional"), ok: !!form.value.video_link && videoLinkOk.value },
	]
})

const canSubmitDetail = computed(
	() =>
		!!detail.value?.reason?.trim() &&
		(detail.value?.evidence?.length || 0) +
			newEvidence.value.length +
			uploadedEvidence.value >=
			1 &&
		(detail.value?.evidence?.length || 0) +
			newEvidence.value.length +
			uploadedEvidence.value <=
			MAX_EVIDENCE &&
		videoLinkOk.value,
)

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

async function loadList(reset = true) {
	if (!props.posProfile) return
	if (!reset && loading.value) return // Load More waits for the page in flight
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

// A filter click during a load must not be swallowed — reset loads always
// re-run with the latest filter.
function setStatusFilter(value) {
	if (statusFilter.value === value) return
	statusFilter.value = value
	loadList()
}

// ---------------------------------------------------------------------------
// New request
// ---------------------------------------------------------------------------

// local date string for <input type="date"> — toISOString would shift a day
// for any timezone east of UTC
function localDate() {
	const d = new Date()
	const m = String(d.getMonth() + 1).padStart(2, "0")
	const day = String(d.getDate()).padStart(2, "0")
	return `${d.getFullYear()}-${m}-${day}`
}

function openNew() {
	form.value = {
		incident_date: localDate(),
		// default = first purpose of the list; empty until loadPurposes lands
		purpose: purposes.value[0] || "",
		reason: "",
		video_link: "",
		items: [],
		files: [],
	}
	itemSearch.value = ""
	itemResults.value = []
	draftName.value = null
	uploadedEvidence.value = 0
	if (!purposes.value.length) loadPurposes()
	view.value = "new"
}

let searchTimer = null
let searchSeq = 0
// trailing-only debounce: one request per settled term; stale responses and
// in-flight searches after a selection are discarded via the sequence guard
function searchItems() {
	clearTimeout(searchTimer)
	const term = itemSearch.value.trim()
	if (!term) {
		// invalidate in-flight searches when the field is cleared
		searchSeq++
		itemResults.value = []
		return
	}
	searchTimer = setTimeout(() => doSearchItems(term), 350)
}

async function doSearchItems(term) {
	const seq = ++searchSeq // starting a request invalidates older ones
	try {
		const rows = await call("pos_next.api.items.get_items", {
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

function addItem(item) {
	if (!form.value.items.some((r) => r.item_code === item.item_code)) {
		form.value.items.push({ item_code: item.item_code, item_name: item.item_name || item.item_code, qty: 1 })
	}
	// invalidate any in-flight search so a late response can't repopulate
	searchSeq++
	clearTimeout(searchTimer)
	itemSearch.value = ""
	itemResults.value = []
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
		files.map(async (f) => ({ file_name: f.name, filedata: await fileToBase64(f) })),
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

/**
 * Draft save → attach evidence → submit save. On any failure the draft name
 * is kept, so a retry updates the same draft instead of inserting another.
 * Pending files are cleared right after a successful attach so retries
 * never re-upload them. `getPending()` returns the pending files and clears
 * them when called with true.
 */
async function submitRequest(buildData, draftNameRef, getPending) {
	if (!draftNameRef.value) {
		const draft = await call("pos_next.api.reject_request.save_reject_request", {
			data: JSON.stringify(buildData()),
			submit: 0,
		})
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
			video_link: form.value.video_link.trim() || null,
			items: form.value.items.map((i) => ({ item_code: i.item_code, qty: i.qty })),
		})
		await submitRequest(
			buildData,
			draftName,
			(clear) => {
				const files = form.value.files
				if (clear) form.value.files = []
				return files
			},
		)
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
	itemSearch.value = ""
	itemResults.value = []
	try {
		detail.value = await call("pos_next.api.reject_request.get_reject_request", { name })
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
	view.value = "list"
	loadList()
}

async function submitDetail() {
	if (!canSubmitDetail.value || submitting.value || !detail.value) return
	submitting.value = true
	try {
		const buildData = () => ({
			name: detail.value.name,
			reason: detail.value.reason.trim(),
			video_link: detail.value.video_link || null,
		})
		// the draft already exists — reuse its name, never insert again
		await submitRequest(
			buildData,
			draftName,
			(clear) => {
				const files = newEvidence.value
				if (clear) newEvidence.value = []
				return files
			},
		)
		showSuccess(__("Reject request submitted"))
		backToList()
	} catch (err) {
		showError(errMsg(err, "Failed to submit reject request"))
	} finally {
		submitting.value = false
	}
}

async function doCancel() {
	if (acting.value || !detail.value) return
	if (!window.confirm(__("Cancel this reject request?"))) return
	acting.value = true
	try {
		await call("pos_next.api.reject_request.cancel_reject_request", {
			name: detail.value.name,
		})
		showSuccess(__("Reject request cancelled"))
		backToList()
	} catch (err) {
		showError(errMsg(err, "Failed to cancel reject request"))
	} finally {
		acting.value = false
	}
}

async function doClose() {
	if (acting.value || !detail.value) return
	if (!window.confirm(__("Close this reject request after destruction?"))) return
	acting.value = true
	try {
		uploadedDestruction.value += await attachFiles(
			detail.value.name,
			"destruction",
			newDestruction.value,
		)
		// clear before closing: a failed close retry must not re-upload
		newDestruction.value = []
		await call("pos_next.api.reject_request.close_reject_request", {
			name: detail.value.name,
		})
		showSuccess(__("Reject request closed"))
		backToList()
	} catch (err) {
		showError(errMsg(err, "Failed to close reject request"))
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

if (props.modelValue) {
	view.value = "list"
	loadList()
}

// Task 6 deep link: when the dialog opens with a request name, jump
// straight into its detail view. Declared last so `view` exists before
// the immediate callback can touch it.
watch(
	[() => props.initialRequest, show],
	([name, open]) => {
		if (name && open) openDetail(name)
	},
	{ immediate: true },
)
</script>
