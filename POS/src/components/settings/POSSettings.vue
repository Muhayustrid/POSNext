<template>
	<!-- Full Page Overlay -->
	<Transition name="fade">
		<div
			v-if="show"
			class="fixed inset-0 bg-black bg-opacity-50 z-[300]"
			@click.self="handleClose"
		>
			<!-- Main Container -->
			<div class="fixed inset-0 flex items-center justify-center p-4 md:p-6">
				<div
					class="w-full max-w-5xl max-h-[90vh] bg-white rounded-xl shadow-2xl overflow-hidden flex flex-col"
				>
					<!-- Header -->
					<div
						class="flex items-center justify-between px-6 py-5 border-b bg-gradient-to-r from-blue-50 to-indigo-50"
					>
						<div class="flex items-center gap-3">
							<div class="p-2 bg-blue-100 rounded-lg">
								<svg
									class="w-6 h-6 text-blue-600"
									fill="none"
									stroke="currentColor"
									viewBox="0 0 24 24"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
									/>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
									/>
								</svg>
							</div>
							<div>
								<h2 class="text-xl font-bold text-gray-900">
									{{ __("POS Settings") }}
								</h2>
								<p class="text-sm text-gray-600 flex items-center mt-0.5">
									<svg
										class="w-4 h-4 me-1.5"
										fill="none"
										stroke="currentColor"
										viewBox="0 0 24 24"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
										/>
									</svg>
									{{ settings.pos_profile || posProfile }}
								</p>
							</div>
						</div>
						<div class="flex items-center gap-2">
							<Button
								@click="loadSettings"
								:loading="loading"
								variant="ghost"
								size="sm"
							>
								<template #prefix>
									<svg
										class="w-4 h-4"
										fill="none"
										stroke="currentColor"
										viewBox="0 0 24 24"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
										/>
									</svg>
								</template>
								{{ __("Refresh") }}
							</Button>
							<Button
								@click="saveSettings"
								:loading="saving"
								variant="solid"
								theme="blue"
							>
								<template #prefix>
									<svg
										class="w-4 h-4"
										fill="none"
										stroke="currentColor"
										viewBox="0 0 24 24"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M5 13l4 4L19 7"
										/>
									</svg>
								</template>
								{{ __("Save Changes") }}
							</Button>
							<button
								@click="handleClose"
								class="p-2 hover:bg-white/50 rounded-lg transition-colors"
							>
								<svg
									class="w-5 h-5 text-gray-600"
									fill="none"
									stroke="currentColor"
									viewBox="0 0 24 24"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M6 18L18 6M6 6l12 12"
									/>
								</svg>
							</button>
						</div>
					</div>

					<!-- Main Content -->
					<div class="flex-1 overflow-y-auto bg-gray-50">
						<!-- Loading State -->
						<div
							v-if="loading"
							class="flex flex-col items-center justify-center py-16"
						>
							<div
								class="animate-spin rounded-full h-12 w-12 border-b-3 border-blue-500 mb-4"
							></div>
							<p class="text-sm font-medium text-gray-600">
								{{ __("Loading settings...") }}
							</p>
						</div>

						<!-- Settings Form -->
						<div
							v-else-if="settings.pos_profile || posProfile"
							class="p-6 flex flex-col gap-6"
						>
							<!-- Tabs Navigation -->
							<div class="flex p-1 bg-gray-200 rounded-lg self-start">
								<button
									@click="activeTab = 'stock'"
									:class="[
										'px-4 py-2 text-sm font-medium rounded-md transition-all duration-200',
										activeTab === 'stock'
											? 'bg-white text-gray-900 shadow-sm'
											: 'text-gray-600 hover:text-gray-900 hover:bg-gray-200/50',
									]"
								>
									{{ __("Stock Management") }}
								</button>
								<button
									@click="activeTab = 'sales'"
									:class="[
										'px-4 py-2 text-sm font-medium rounded-md transition-all duration-200',
										activeTab === 'sales'
											? 'bg-white text-gray-900 shadow-sm'
											: 'text-gray-600 hover:text-gray-900 hover:bg-gray-200/50',
									]"
								>
									{{ __("Sales Management") }}
								</button>
								<button
									@click="activeTab = 'printing'"
									:class="[
										'px-4 py-2 text-sm font-medium rounded-md transition-all duration-200',
										activeTab === 'printing'
											? 'bg-white text-gray-900 shadow-sm'
											: 'text-gray-600 hover:text-gray-900 hover:bg-gray-200/50',
									]"
								>
									{{ __("Printing") }}
								</button>
							</div>

							<!-- Stock Settings Section - Prominent -->
							<div
								v-if="activeTab === 'stock'"
								class="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden"
							>
								<div :class="stockSectionClasses.header">
									<div class="flex items-center justify-between">
										<div class="flex items-center gap-3">
											<div :class="stockSectionClasses.iconContainer">
												<svg
													:class="stockSectionClasses.icon"
													fill="none"
													stroke="currentColor"
													viewBox="0 0 24 24"
												>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														stroke-width="2"
														:d="icons.warehouse"
													/>
												</svg>
											</div>
											<div>
												<h3 class="text-lg font-bold text-gray-900">
													{{ __("Stock Management") }}
												</h3>
												<p class="text-xs text-gray-600 mt-0.5">
													{{
														__(
															"Configure warehouse and inventory settings"
														)
													}}
												</p>
											</div>
										</div>
										<div :class="stockSectionClasses.badge">
											<svg
												:class="stockSectionClasses.badgeIcon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.checkCircle"
												/>
											</svg>
											<span :class="stockSectionClasses.badgeText">{{
												__("Stock Controls")
											}}</span>
										</div>
									</div>
								</div>
								<div class="p-6 flex flex-col gap-6">
									<!-- Warehouse Selection -->
									<div :class="warehouseSubsectionClasses.container">
										<div class="flex items-center gap-2 mb-4">
											<svg
												:class="warehouseSubsectionClasses.icon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.location"
												/>
											</svg>
											<h4 class="text-sm font-semibold text-gray-900">
												{{ __("Warehouse Selection") }}
											</h4>
										</div>
										<div
											v-if="warehouseOptions.length === 0"
											class="flex items-center p-4 bg-yellow-50 border border-yellow-200 rounded-lg"
										>
											<svg
												class="w-5 h-5 text-yellow-600 me-3 flex-shrink-0"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.warning"
												/>
											</svg>
											<p class="text-sm text-yellow-800 font-medium">
												{{ __("Loading warehouses...") }}
											</p>
										</div>
										<SelectField
											v-else
											v-model="selectedWarehouse"
											:label="__('Active Warehouse')"
											:options="warehouseOptions"
											:description="
												__(
													'All stock operations will use this warehouse. Stock quantities will refresh after saving.'
												)
											"
										/>
									</div>

									<!-- Stock Policy Settings -->
									<div :class="stockPolicySubsectionClasses.container">
										<div class="flex items-center gap-2 mb-4">
											<svg
												:class="stockPolicySubsectionClasses.icon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.clipboard"
												/>
											</svg>
											<h4 class="text-sm font-semibold text-gray-900">
												{{ __("Stock Validation Policy") }}
											</h4>
										</div>
										<div class="flex flex-col gap-3">
											<CheckboxField
												v-model="settings.allow_negative_stock"
												:label="__('Allow Negative Stock')"
												:description="
													__(
														'Enable selling items even when stock reaches zero or below. Integrates with Back Office stock settings.'
													)
												"
											/>
											<div class="mt-3 p-3 bg-blue-100 rounded-md">
												<div class="flex items-start gap-2">
													<svg
														class="w-4 h-4 text-blue-600 mt-0.5 flex-shrink-0"
														fill="none"
														stroke="currentColor"
														viewBox="0 0 24 24"
													>
														<path
															stroke-linecap="round"
															stroke-linejoin="round"
															stroke-width="2"
															:d="icons.info"
														/>
													</svg>
													<TranslatedHTML
														:tag="'p'"
														class="text-xs text-blue-800 leading-relaxed"
														:inner="
															__(
																'&lt;strong&gt;Note:&lt;strong&gt; When enabled, the system will allow sales even when stock quantity is zero or negative. This is useful for handling stock sync delays or backorders. All transactions are tracked in the stock ledger.'
															)
														"
													/>
												</div>
											</div>
										</div>
									</div>

									<!-- Background Stock Sync Settings -->
									<div :class="stockSyncSubsectionClasses.container">
										<div class="flex items-center gap-2 mb-4">
											<svg
												:class="stockSyncSubsectionClasses.icon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
												/>
											</svg>
											<h4 class="text-sm font-semibold text-gray-900">
												{{ __("Background Stock Sync") }}
											</h4>
											<div
												v-if="stockSyncStatus.enabled"
												class="ms-auto flex items-center px-2.5 py-1 bg-green-100 border border-green-300 rounded-full"
											>
												<div
													class="w-2 h-2 bg-green-500 rounded-full animate-pulse me-2"
												></div>
												<span class="text-xs font-medium text-green-800">{{
													__("Active")
												}}</span>
											</div>
											<div
												v-else
												class="ms-auto flex items-center px-2.5 py-1 bg-gray-100 border border-gray-300 rounded-full"
											>
												<div
													class="w-2 h-2 bg-gray-400 rounded-full me-2"
												></div>
												<span class="text-xs font-medium text-gray-600">{{
													__("Inactive")
												}}</span>
											</div>
										</div>

										<div class="flex flex-col gap-4">
											<!-- Enable Sync Toggle -->
											<CheckboxField
												v-model="stockSyncEnabled"
												:label="__('Enable Automatic Stock Sync')"
												:description="
													__(
														'Periodically sync stock quantities from server in the background (runs in Web Worker)'
													)
												"
											/>

											<!-- Sync Interval -->
											<div
												v-if="stockSyncEnabled"
												class="ps-6 flex flex-col gap-3 border-s-2 border-blue-200"
											>
												<NumberField
													v-model="stockSyncIntervalSeconds"
													:label="__('Sync Interval (seconds)')"
													:description="
														__(
															'How often to check server for stock updates (minimum 10 seconds)'
														)
													"
													:min="10"
													:max="300"
													:step="10"
												/>

												<!-- Sync Status Info -->
												<div
													class="p-3 bg-blue-50 border border-blue-200 rounded-lg"
												>
													<div class="flex items-start gap-2">
														<svg
															class="w-4 h-4 text-blue-600 mt-0.5 flex-shrink-0"
															fill="none"
															stroke="currentColor"
															viewBox="0 0 24 24"
														>
															<path
																stroke-linecap="round"
																stroke-linejoin="round"
																stroke-width="2"
																:d="icons.info"
															/>
														</svg>
														<div
															class="text-xs text-blue-800 flex flex-col gap-1"
														>
															<TranslatedHTML
																:tag="'p'"
																:inner="
																	stockSyncStatus.enabled
																		? __(
																				'&lt;strong&gt;Status:&lt;strong&gt; Running'
																		  )
																		: __(
																				'&lt;strong&gt;Status:&lt;strong&gt; Stopped'
																		  )
																"
															/>
															<TranslatedHTML
																:tag="'p'"
																:inner="
																	__(
																		'&lt;strong&gt;Items Tracked:&lt;strong&gt; {0}',
																		[
																			stockSyncStatus.itemCount ||
																				0,
																		]
																	)
																"
															/>
															<TranslatedHTML
																:tag="'p'"
																:inner="
																	stockSyncStatus.warehouse
																		? __(
																				'&lt;strong&gt;Warehouse:&lt;strong&gt; {0}',
																				[
																					stockSyncStatus.warehouse,
																				]
																		  )
																		: __('Warehouse not set')
																"
															/>
															<TranslatedHTML
																:tag="'p'"
																:inner="
																	stockSyncStatus.lastSync
																		? __(
																				'&lt;strong&gt;Last Sync:&lt;strong&gt; {0}',
																				[
																					formatSyncTime(
																						stockSyncStatus.lastSync
																					),
																				]
																		  )
																		: __(
																				'&lt;strong&gt;Last Sync:&lt;strong&gt; Never'
																		  )
																"
															/>
														</div>
													</div>
												</div>

												<!-- Network Usage Info -->
												<div
													class="p-3 bg-gray-50 border border-gray-200 rounded-lg"
												>
													<div class="flex items-start gap-2">
														<svg
															class="w-4 h-4 text-gray-600 mt-0.5 flex-shrink-0"
															fill="none"
															stroke="currentColor"
															viewBox="0 0 24 24"
														>
															<path
																stroke-linecap="round"
																stroke-linejoin="round"
																stroke-width="2"
																d="M13 10V3L4 14h7v7l9-11h-7z"
															/>
														</svg>
														<div class="text-xs text-gray-700">
															<p class="font-medium mb-1">
																{{ __("Network Usage:") }}
															</p>
															<p>
																{{ __("~15 KB per sync cycle") }}
															</p>
															<p>
																{{
																	__("~{0} MB per hour", [
																		Math.round(
																			((3600 /
																				stockSyncIntervalSeconds) *
																				15) /
																				1024
																		),
																	])
																}}
															</p>
														</div>
													</div>
												</div>
											</div>
										</div>
									</div>
								</div>
							</div>

							<!-- Sales Management Section - Prominent -->
							<div
								v-if="activeTab === 'sales'"
								class="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden"
							>
								<div :class="salesSectionClasses.header">
									<div class="flex items-center justify-between">
										<div class="flex items-center gap-3">
											<div :class="salesSectionClasses.iconContainer">
												<svg
													:class="salesSectionClasses.icon"
													fill="none"
													stroke="currentColor"
													viewBox="0 0 24 24"
												>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														stroke-width="2"
														:d="icons.shoppingCart"
													/>
												</svg>
											</div>
											<div>
												<h3 class="text-lg font-bold text-gray-900">
													{{ __("Sales Management") }}
												</h3>
												<p class="text-xs text-gray-600 mt-0.5">
													{{
														__(
															"Configure pricing, discounts, and sales operations"
														)
													}}
												</p>
											</div>
										</div>
										<div :class="salesSectionClasses.badge">
											<svg
												:class="salesSectionClasses.badgeIcon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.currency"
												/>
											</svg>
											<span :class="salesSectionClasses.badgeText">{{
												__("Sales Controls")
											}}</span>
										</div>
									</div>
								</div>
								<div class="p-6 flex flex-col gap-6">
									<!-- Pricing & Discounts -->
									<div :class="pricingSubsectionClasses.container">
										<div class="flex items-center gap-2 mb-4">
											<svg
												:class="pricingSubsectionClasses.icon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.tag"
												/>
											</svg>
											<h4 class="text-sm font-semibold text-gray-900">
												{{ __("Pricing & Discounts") }}
											</h4>
										</div>
										<div class="flex flex-col gap-3">
											<CheckboxField
												v-model="settings.tax_inclusive"
												:label="__('Tax Inclusive')"
												:description="
													__(
														'When enabled, displayed prices include tax. When disabled, tax is calculated separately. Changes apply immediately to your cart when you save.'
													)
												"
											/>
											<NumberField
												v-model="settings.max_discount_allowed"
												:label="__('Max Discount (%)')"
												:description="__('Maximum discount per item')"
												:min="0"
												:max="100"
											/>
											<CheckboxField
												v-model="settings.use_percentage_discount"
												:label="__('Use Percentage Discount')"
												:description="__('Show discounts as percentages')"
											/>
											<CheckboxField
												v-model="
													settings.allow_user_to_edit_additional_discount
												"
												:label="__('Allow Additional Discount')"
												:description="__('Enable invoice-level discount')"
											/>
											<CheckboxField
												v-model="settings.allow_user_to_edit_item_discount"
												:label="__('Allow Item Discount')"
												:description="
													__('Enable item-level discount in edit dialog')
												"
											/>
											<CheckboxField
												v-model="settings.allow_user_to_edit_rate"
												:label="__('Allow User To Edit Rate')"
												:description="
													__(
														'Allow editing item rate in cart. Disabled when offers are applied.'
													)
												"
											/>
											<CheckboxField
												v-model="settings.disable_rounded_total"
												:label="__('Disable Rounded Total')"
												:description="
													__('Show exact totals without rounding')
												"
											/>
										</div>
									</div>

									<!-- Sales Operations -->
									<div :class="operationsSubsectionClasses.container">
										<div class="flex items-center gap-2 mb-4">
											<svg
												:class="operationsSubsectionClasses.icon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.checkCircle"
												/>
											</svg>
											<h4 class="text-sm font-semibold text-gray-900">
												{{ __("Sales Operations") }}
											</h4>
										</div>
										<div class="flex flex-col gap-3">
											<CheckboxField
												v-model="settings.allow_credit_sale"
												:label="__('Allow Credit Sale')"
												:description="__('Enable sales on credit')"
											/>
											<CheckboxField
												v-model="settings.allow_return"
												:label="__('Allow Return')"
												:description="__('Enable product returns')"
											/>
											<CheckboxField
												v-model="settings.allow_write_off_change"
												:label="__('Allow Write Off Change')"
												:description="__('Write off small change amounts')"
											/>
											<CheckboxField
												v-model="settings.allow_partial_payment"
												:label="__('Allow Partial Payment')"
												:description="
													__('Enable partial payment for invoices')
												"
											/>
											<CheckboxField
												v-model="settings.silent_print"
												:label="__('Silent Print')"
												:description="
													__(
														'Send receipts directly to a thermal printer via QZ Tray (no browser dialog)'
													)
												"
											/>

											<!-- QZ Tray Printer Settings (shown when silent print is enabled) -->
											<div
												v-if="settings.silent_print"
												class="ps-6 flex flex-col gap-3 border-s-2 border-teal-200"
											>
												<!-- Connection Status -->
												<div class="flex items-center gap-2">
													<div
														class="w-2.5 h-2.5 rounded-full flex-shrink-0"
														:class="
															qzConnecting
																? 'bg-yellow-500 animate-pulse'
																: qzConnected
																? 'bg-green-500'
																: 'bg-red-500'
														"
													></div>
													<span
														class="text-xs font-medium"
														:class="
															qzConnecting
																? 'text-yellow-700'
																: qzConnected
																? 'text-green-700'
																: 'text-red-700'
														"
													>
														{{
															qzConnecting
																? __("Connecting to QZ Tray...")
																: qzConnected
																? __("QZ Tray Connected")
																: __("QZ Tray Not Connected")
														}}
													</span>
													<button
														v-if="!qzConnected && !qzConnecting"
														@click="handleQzConnect"
														class="ms-auto text-xs px-2 py-1 bg-blue-100 hover:bg-blue-200 text-blue-700 rounded transition-colors"
													>
														{{ __("Retry") }}
													</button>
												</div>

												<!-- Printer Selection -->
												<div class="flex items-end gap-2">
													<div class="flex-1">
														<SelectField
															v-model="selectedPrinter"
															:label="__('Printer')"
															:options="printerOptions"
															:description="
																qzPrinters.length === 0 &&
																!loadingPrinters
																	? __(
																			'No printers found. Is QZ Tray running?'
																	  )
																	: ''
															"
														/>
													</div>
													<button
														@click="handleRefreshPrinters"
														:disabled="loadingPrinters"
														class="px-2 py-2 mb-0.5 bg-gray-100 hover:bg-gray-200 rounded transition-colors"
														:title="__('Refresh printer list')"
													>
														<svg
															class="w-4 h-4 text-gray-600"
															:class="
																loadingPrinters
																	? 'animate-spin'
																	: ''
															"
															fill="none"
															stroke="currentColor"
															viewBox="0 0 24 24"
														>
															<path
																stroke-linecap="round"
																stroke-linejoin="round"
																stroke-width="2"
																d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
															/>
														</svg>
													</button>
												</div>

												<!-- QZ Certificate Status & Setup -->
												<div
													:class="[
														'p-3 rounded-lg border',
														qzCertStatus === 'trusted'
															? 'bg-green-50 border-green-200'
															: qzCertStatus === 'untrusted'
															? 'bg-red-50 border-red-200'
															: 'bg-amber-50 border-amber-200',
													]"
												>
													<div class="flex items-start gap-2">
														<!-- Icon changes based on status -->
														<svg
															class="w-4 h-4 mt-0.5 flex-shrink-0"
															:class="
																qzCertStatus === 'trusted'
																	? 'text-green-600'
																	: qzCertStatus === 'untrusted'
																	? 'text-red-600'
																	: 'text-amber-600'
															"
															fill="none"
															stroke="currentColor"
															viewBox="0 0 24 24"
														>
															<path
																v-if="qzCertStatus === 'trusted'"
																stroke-linecap="round"
																stroke-linejoin="round"
																stroke-width="2"
																d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
															/>
															<path
																v-else
																stroke-linecap="round"
																stroke-linejoin="round"
																stroke-width="2"
																d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
															/>
														</svg>
														<div class="flex-1">
															<!-- Title with inline status badge -->
															<div
																class="flex items-center gap-2 mb-1"
															>
																<p
																	class="text-xs font-semibold"
																	:class="
																		qzCertStatus === 'trusted'
																			? 'text-green-900'
																			: qzCertStatus ===
																			  'untrusted'
																			? 'text-red-900'
																			: 'text-amber-900'
																	"
																>
																	{{
																		__(
																			"Silent Print Certificate"
																		)
																	}}
																</p>
																<span
																	v-if="
																		qzCertStatus === 'trusted'
																	"
																	class="inline-flex items-center gap-1 px-1.5 py-0.5 bg-green-100 border border-green-300 rounded-full"
																>
																	<span
																		class="w-1.5 h-1.5 bg-green-500 rounded-full"
																	></span>
																	<span
																		class="text-[10px] font-medium text-green-800"
																		>{{
																			__("Installed")
																		}}</span
																	>
																</span>
																<span
																	v-else-if="
																		qzCertStatus ===
																		'untrusted'
																	"
																	class="inline-flex items-center gap-1 px-1.5 py-0.5 bg-red-100 border border-red-300 rounded-full"
																>
																	<span
																		class="w-1.5 h-1.5 bg-red-500 rounded-full"
																	></span>
																	<span
																		class="text-[10px] font-medium text-red-800"
																		>{{
																			__("Not Installed")
																		}}</span
																	>
																</span>
																<span
																	v-else
																	class="inline-flex items-center gap-1 px-1.5 py-0.5 bg-amber-100 border border-amber-300 rounded-full"
																>
																	<span
																		class="w-1.5 h-1.5 bg-amber-500 rounded-full"
																	></span>
																	<span
																		class="text-[10px] font-medium text-amber-800"
																		>{{
																			__("Checking...")
																		}}</span
																	>
																</span>
															</div>

															<!-- Status message -->
															<p
																v-if="qzCertStatus === 'trusted'"
																class="text-xs text-green-800 leading-relaxed mb-2"
															>
																{{
																	__(
																		"Certificate is installed and signing is active. Print jobs will be sent silently without confirmation dialogs."
																	)
																}}
															</p>
															<p
																v-else-if="
																	qzCertStatus === 'untrusted'
																"
																class="text-xs text-red-800 leading-relaxed mb-2"
															>
																{{
																	__(
																		"Certificate is not installed on this machine. Generate a certificate, download it, and import it into QZ Tray."
																	)
																}}
															</p>
															<p
																v-else
																class="text-xs text-amber-800 leading-relaxed mb-2"
															>
																{{
																	__(
																		"To print without confirmation dialogs, generate a signing certificate and install it on each POS machine."
																	)
																}}
															</p>

															<!-- Action buttons -->
															<div
																class="flex items-center gap-2 flex-wrap"
															>
																<button
																	v-if="
																		qzCertStatus !== 'trusted'
																	"
																	@click="
																		handleSetupQzCertificate
																	"
																	:disabled="qzCertLoading"
																	class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white rounded-md transition-colors bg-amber-600 hover:bg-amber-700 disabled:bg-amber-400"
																>
																	<svg
																		v-if="qzCertLoading"
																		class="w-3.5 h-3.5 animate-spin"
																		fill="none"
																		viewBox="0 0 24 24"
																	>
																		<circle
																			class="opacity-25"
																			cx="12"
																			cy="12"
																			r="10"
																			stroke="currentColor"
																			stroke-width="4"
																		/>
																		<path
																			class="opacity-75"
																			fill="currentColor"
																			d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
																		/>
																	</svg>
																	<svg
																		v-else
																		class="w-3.5 h-3.5"
																		fill="none"
																		stroke="currentColor"
																		viewBox="0 0 24 24"
																	>
																		<path
																			stroke-linecap="round"
																			stroke-linejoin="round"
																			stroke-width="2"
																			d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
																		/>
																	</svg>
																	{{
																		__("Generate Certificate")
																	}}
																</button>
																<button
																	v-if="qzCertReady"
																	@click="
																		handleDownloadQzCertificate
																	"
																	class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-600 hover:bg-blue-700 text-white rounded-md transition-colors"
																>
																	<svg
																		class="w-3.5 h-3.5"
																		fill="none"
																		stroke="currentColor"
																		viewBox="0 0 24 24"
																	>
																		<path
																			stroke-linecap="round"
																			stroke-linejoin="round"
																			stroke-width="2"
																			d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
																		/>
																	</svg>
																	{{
																		__("Download Certificate")
																	}}
																</button>
															</div>

															<!-- Install instructions (only when cert exists but not trusted yet) -->
															<p
																v-if="
																	qzCertReady &&
																	qzCertStatus !== 'trusted'
																"
																class="text-xs mt-2"
																:class="
																	qzCertStatus === 'untrusted'
																		? 'text-red-700'
																		: 'text-amber-700'
																"
															>
																{{
																	__(
																		"Download the certificate and import it into QZ Tray, then restart QZ Tray."
																	)
																}}
															</p>
														</div>
													</div>
												</div>

												<!-- Help text -->
												<div
													class="p-3 bg-teal-50 border border-teal-200 rounded-lg"
												>
													<div class="flex items-start gap-2">
														<svg
															class="w-4 h-4 text-teal-600 mt-0.5 flex-shrink-0"
															fill="none"
															stroke="currentColor"
															viewBox="0 0 24 24"
														>
															<path
																stroke-linecap="round"
																stroke-linejoin="round"
																stroke-width="2"
																d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
															/>
														</svg>
														<p
															class="text-xs text-teal-800 leading-relaxed"
														>
															{{
																__(
																	"QZ Tray must be installed and running on this computer. Download from"
																)
															}}
															<a
																href="https://qz.io/download/"
																target="_blank"
																class="font-semibold underline"
																>qz.io</a
															>.
															{{
																__(
																	"If QZ Tray is unavailable, printing will fall back to the browser dialog."
																)
															}}
														</p>
													</div>
												</div>
											</div>
										</div>
									</div>
								</div>
							</div>

							<!-- Printing Section -->
							<div
								v-if="activeTab === 'printing'"
								class="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden"
							>
								<div :class="printingSectionClasses.header">
									<div class="flex items-center justify-between">
										<div class="flex items-center gap-3">
											<div :class="printingSectionClasses.iconContainer">
												<svg
													:class="printingSectionClasses.icon"
													fill="none"
													stroke="currentColor"
													viewBox="0 0 24 24"
												>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														stroke-width="2"
														:d="icons.printer"
														/>
												</svg>
											</div>
											<div>
												<h3 class="text-lg font-bold text-gray-900">
													{{ __("Printing") }}
												</h3>
												<p class="text-xs text-gray-600 mt-0.5">
													{{
														__(
															"Nomor antrian, driver cetak, dan tata letak struk untuk profile ini"
														)
													}}
												</p>
											</div>
										</div>
										<div :class="printingSectionClasses.badge">
											<svg
												:class="printingSectionClasses.badgeIcon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.checkCircle"
													/>
											</svg>
												<span :class="printingSectionClasses.badgeText">{{
													__("Print Controls")
												}}</span>
										</div>
									</div>
								</div>
								<div class="p-6 flex flex-col gap-6">
									<!-- Queue Number -->
									<div :class="queueSubsectionClasses.container">
										<div class="flex items-center gap-2 mb-4">
											<svg
												:class="queueSubsectionClasses.icon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.tag"
													/>
											</svg>
											<h4 class="text-sm font-semibold text-gray-900">
												{{ __("Nomor Antrian") }}
											</h4>
										</div>
										<div class="flex flex-col gap-3">
											<CheckboxField
												v-model="settings.enable_pos_queue"
												:label="__('Aktifkan Nomor Antrian POS')"
												:description="
													__(
														'Cetak nomor antrian harian di struk. Nomor dihitung per perusahaan per hari, reset tengah malam.'
													)
												"
											/>
										</div>
									</div>
									<!-- Print Driver -->
									<div :class="driverSubsectionClasses.container">
										<div class="flex items-center gap-2 mb-4">
											<svg
												:class="driverSubsectionClasses.icon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.info"
													/>
											</svg>
											<h4 class="text-sm font-semibold text-gray-900">
												{{ __("Driver Cetak") }}
											</h4>
										</div>
										<div class="flex flex-col gap-3">
											<SelectField
												v-model="settings.print_driver"
												:label="__('Driver Cetak')"
												:options="printDriverOptions"
												:description="
													__(
														'Jalur cetak tanpa dialog: browser, QZ Tray, atau servis printer iMin.'
													)
												"
											/>
												<CheckboxField
													v-model="settings.print_fallback_enabled"
													:label="__('Aktifkan Print Fallback')"
													:description="
														__('Bila gagal, turun ke jalur berikutnya (iMin → QZ → Browser).')
													"
												/>

												<!-- iMin access trigger: same bait as the Direct
													 Print page — loads the SDK and probes the
													 print service so the device asks permission
													 for this origin without leaving Settings. -->
												<div
													class="flex flex-col gap-2 pt-3 border-t border-indigo-200"
												>
													<div class="flex items-center gap-3 flex-wrap">
														<Button
															variant="solid"
															size="sm"
															:loading="iminProbing"
															@click="requestIminAccess"
														>
															{{ __("Minta Akses Printer iMin") }}
														</Button>
														<div
															v-if="iminAccess"
															class="flex items-center gap-2"
														>
															<div
																class="w-2.5 h-2.5 rounded-full flex-shrink-0"
																:class="
																	iminAccess.ok
																		? 'bg-green-500'
																		: 'bg-red-500'
																"
															></div>
															<span
																class="text-xs font-medium"
																:class="
																	iminAccess.ok
																		? 'text-green-700'
																		: 'text-red-700'
																"
															>
																{{
																	iminAccess.ok
																		? __("Printer iMin terhubung")
																		: iminAccess.message ||
																		  __("Tidak terhubung")
																}}
															</span>
														</div>
													</div>
													<p class="text-xs text-gray-500 leading-tight">
														{{
															__(
																'Tekan sekali per device: servis iMin akan meminta izin untuk situs ini — sama seperti membuka halaman Direct Print.'
															)
														}}
													</p>
												</div>
											</div>
										</div>
									<!-- iMin Sales Receipt -->
									<div
										v-if="settings.print_driver === 'imin'"
										:class="iminSubsectionClasses.container"
									>
										<div class="flex items-center gap-2 mb-4">
											<svg
												:class="iminSubsectionClasses.icon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.printer"
													/>
											</svg>
											<h4 class="text-sm font-semibold text-gray-900">
												{{ __('Struk Penjualan — iMin') }}
											</h4>
										</div>
										<div class="flex flex-col gap-3">
											<SelectField
												v-model="settings.imin_paper_width"
												:label="__('Lebar Kertas')"
												:options="paperWidthOptions"
												:description="
													__('58mm = 384 dots, 80mm = 576 dots, custom = isi jumlah dots di bawah.')
												"
											/>
											<NumberField
												v-if="settings.imin_paper_width === 'custom'"
												v-model="settings.imin_custom_dots"
												:label="__('Jumlah Dots Custom')"
												:description="
													__('Lebar cetak dalam dots (kelipatan 8, maks 576).')
												"
												:min="8"
												:max="576"
												:step="8"
											/>
											<CheckboxField
												v-model="settings.imin_cut_paper"
												:label="__('Gunting Kertas Setelah Cetak')"
												:description="__('Gunting kertas setelah struk selesai dicetak.')"
											/>
											<NumberField
												v-model="settings.imin_print_copies"
												:label="__('Jumlah Rangkap')"
												:description="
													__(
														'Jumlah rangkap per transaksi (rangkap customer dulu, lalu crew/outlet).'
													)
												"
												:min="1"
												:max="5"
											/>
											<NumberField
												v-if="(settings.imin_print_copies || 1) > 1"
												v-model="settings.imin_copy_delay_ms"
												:label="__('Jeda Antar Rangkap (ms)')"
												:description="
													__('Jeda antar rangkap agar rangkap pertama bisa disobek. Default 800.')
												"
												:min="0"
												:max="10000"
												:step="100"
											/>
											<CheckboxField
												v-model="settings.imin_crew_slip_enabled"
												:label="__('Cetak Slip Crew')"
												:description="
													__(
														'Satu slip order pendek untuk outlet, dicetak setelah rangkap customer — terpisah dari jumlah rangkap.'
													)
												"
											/>
											<NumberField
												v-model="settings.imin_font_scale"
												:label="__('Skala Font Struk (%)')"
												:description="
													__(
														'Ukuran teks struk (%). 100 = normal; naikkan bila cetakan terlalu kecil. 60–250.'
													)
												"
												:min="60"
												:max="250"
												:step="5"
											/>
											<NumberField
												v-model="settings.imin_crew_font_scale"
												:label="__('Skala Font Slip Crew (%)')"
												:description="
													__(
														'Ukuran teks khusus slip crew (%). 60–250.'
													)
												"
												:min="60"
												:max="250"
												:step="5"
											/>
											<NumberField
												v-model="settings.imin_line_spacing"
												:label="__('Jarak Baris (%)')"
												:description="
													__(
														'Kerapatan baris (%). Lebih kecil = lebih rapat, ukuran teks tetap. 50–150.'
													)
												"
												:min="50"
												:max="150"
												:step="5"
											/>
											<NumberField
												v-model="settings.imin_side_margin"
												:label="__('Margin Kiri-Kanan (dots)')"
												:description="
													__(
														'Margin kiri-kanan dalam dots (8 dots ≈ 1 mm). 0–64.'
													)
												"
												:min="0"
												:max="64"
											/>
											<NumberField
												v-model="settings.imin_top_margin"
												:label="__('Margin Atas (dots)')"
												:description="
													__(
														'Ruang kosong di atas isi struk (dots). 0 = pakai padding bawaan format. 0–128.'
													)
												"
												:min="0"
												:max="128"
											/>
											<NumberField
												v-model="settings.imin_queue_gap"
												:label="__('Jarak Nomor Antrian (dots)')"
												:description="
													__(
														'Jarak nomor antrian ke isi struk di bawahnya (dots, 40 ≈ 5 mm). 0–128.'
													)
												"
												:min="0"
												:max="128"
											/>
											<NumberField
												v-model="settings.imin_feed_dots"
												:label="__('Majukan Kertas (dots)')"
												:description="
													__(
														'Majukan kertas tiap rangkap selesai agar lewat bibir sobek. 160 ≈ 20 mm.'
													)
												"
												:min="0"
												:max="500"
												:step="8"
											/>
											<NumberField
												v-model="settings.imin_tail_dots"
												:label="__('Ruang Ekor (dots)')"
												:description="
													__(
														'Ruang kosong di dalam bitmap agar baris terakhir lewat bibir sobek. 24 ≈ 3 mm.'
													)
												"
												:min="0"
												:max="255"
											/>
										</div>
									</div>
									<!-- iMin Closing / EOD -->
									<div
										v-if="settings.print_driver === 'imin'"
										:class="eodSubsectionClasses.container"
									>
										<div class="flex items-center gap-2 mb-4">
											<svg
												:class="eodSubsectionClasses.icon"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.clipboard"
													/>
											</svg>
											<h4 class="text-sm font-semibold text-gray-900">
												{{ __('Tutup Kasir / EOD — iMin') }}
											</h4>
										</div>
										<div class="flex flex-col gap-3">
											<NumberField
												v-model="settings.imin_eod_print_copies"
												:label="__('Jumlah Rangkap EOD')"
												:description="
													__(
														'Jumlah rangkap struk tutup kasir (EOD) — jalur cetak terpisah. 1–5.'
													)
												"
												:min="1"
												:max="5"
											/>
											<NumberField
												v-if="(settings.imin_eod_print_copies || 1) > 1"
												v-model="settings.imin_eod_copy_delay_ms"
												:label="__('Jeda Antar Rangkap EOD (ms)')"
												:description="
													__('Jeda antar rangkap struk EOD. Default 800.')
												"
												:min="0"
												:max="10000"
												:step="100"
											/>
											<NumberField
												v-model="settings.imin_eod_feed_dots"
												:label="__('Majukan Kertas EOD (dots)')"
												:description="
													__('Majukan kertas setelah struk EOD. 160 ≈ 20 mm.')
												"
												:min="8"
												:max="500"
												:step="8"
											/>
											<NumberField
												v-model="settings.imin_eod_tail_dots"
												:label="__('Ruang Ekor EOD (dots)')"
												:description="
													__('Ruang kosong di bawah baris terakhir struk EOD. 24 ≈ 3 mm.')
												"
												:min="0"
												:max="255"
											/>
											<NumberField
												v-model="settings.imin_eod_font_scale"
												:label="__('Skala Font EOD (%)')"
												:description="
													__(
														'Ukuran teks laporan EOD (%). Lebih kecil = lebih banyak baris muat. 60–250.'
													)
												"
												:min="60"
												:max="250"
												:step="5"
											/>
											<NumberField
												v-model="settings.imin_eod_line_spacing"
												:label="__('Jarak Baris EOD (%)')"
												:description="
													__('Kerapatan baris laporan EOD (%). 50–150.')
												"
												:min="50"
												:max="150"
												:step="5"
											/>
											<NumberField
												v-model="settings.imin_eod_side_margin"
												:label="__('Margin Kiri-Kanan EOD (dots)')"
												:description="
													__('Margin kiri-kanan struk EOD. 0 = sampai tepi. 0–64.')
												"
												:min="0"
												:max="64"
											/>
											<NumberField
												v-model="settings.imin_eod_top_margin"
												:label="__('Margin Atas EOD (dots)')"
												:description="
													__(
														'Ruang kosong di atas isi laporan EOD (dots). 0 = pakai padding bawaan format.'
													)
												"
												:min="0"
												:max="128"
											/>
										</div>
									</div>
									<!-- Per-device overrides note -->
									<div class="p-3 bg-blue-50 border border-blue-200 rounded-lg">
										<div class="flex items-start gap-2">
											<svg
												class="w-4 h-4 text-blue-600 mt-0.5 flex-shrink-0"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													:d="icons.info"
													/>
											</svg>
											<p class="text-xs text-blue-800 leading-relaxed">
												{{
													__(
														'Ini nilai default server untuk profile ini. Tiap device bisa menimpanya dan melakukan test print lewat halaman'
													)
												}}
												<router-link to="/direct-print" class="font-semibold underline">
													{{ __("Direct Print") }}
												</router-link>
												{{ __('.') }}
											</p>
										</div>
									</div>
								</div>
							</div>
						</div>

						<!-- Empty State -->
						<div
							v-else
							class="flex flex-col items-center justify-center py-16 text-center"
						>
							<svg
								class="w-16 h-16 text-gray-400 mb-4"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
								/>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
								/>
							</svg>
							<p class="text-gray-600 font-medium">
								{{ __("No POS Profile Selected") }}
							</p>
							<p class="text-gray-500 text-sm mt-1">
								{{ __("Please select a POS Profile to configure settings") }}
							</p>
						</div>
					</div>
				</div>
			</div>
		</div>
	</Transition>
</template>

<script setup>
import CheckboxField from "@/components/settings/CheckboxField.vue";
import NumberField from "@/components/settings/NumberField.vue";
import SelectField from "@/components/settings/SelectField.vue";
import { useToast } from "@/composables/useToast";
import { Button, call, createResource } from "frappe-ui";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { getSectionHeaderClasses, getSubsectionClasses, icons } from "./settingsConfig";
import { offlineWorker } from "@/utils/offline/workerClient";
import { logger } from "@/utils/logger";
import { usePOSEvents } from "@/composables/usePOSEvents";
import TranslatedHTML from "../common/TranslatedHTML.vue";
import { useQzTray } from "@/composables/useQzTray";
import { ensureIminSdk, getTransport, initTransportFromServer } from "@/utils/print/transport";

const log = logger.create("POSSettings");
const { detectSettingsChanges, updateSettingsSnapshot, emitStockSyncConfigured } = usePOSEvents();
const { showSuccess, showError } = useToast();

const props = defineProps({
	modelValue: Boolean,
	posProfile: String,
	currentWarehouse: String,
});

const emit = defineEmits(["update:modelValue"]);

const show = ref(props.modelValue);

// State
const activeTab = ref("stock");
const loading = ref(true);
const saving = ref(false);
const warehousesList = ref([]);
const selectedWarehouse = ref(props.currentWarehouse || "");
const settings = ref({
	pos_profile: props.posProfile || "",
	enabled: 1,
	// Core Settings
	max_discount_allowed: 0,
	use_percentage_discount: 0,
	allow_user_to_edit_additional_discount: 0,
	allow_user_to_edit_item_discount: 1,
	allow_user_to_edit_rate: 0,
	disable_rounded_total: 1,
	allow_credit_sale: 0,
	allow_return: 0,
	allow_write_off_change: 0,
	allow_partial_payment: 0,
	silent_print: 0,
	allow_negative_stock: 0,
	tax_inclusive: 0,
	// Printing
	enable_pos_queue: 0,
	print_driver: "browser",
	print_fallback_enabled: 1,
	imin_paper_width: "58mm",
	imin_custom_dots: 384,
	imin_cut_paper: 1,
	imin_print_copies: 1,
	imin_copy_delay_ms: 800,
	imin_feed_dots: 160,
	imin_tail_dots: 24,
	imin_font_scale: 100,
	imin_crew_font_scale: 100,
	imin_crew_slip_enabled: 0,
	imin_line_spacing: 100,
	imin_side_margin: 16,
	imin_top_margin: 0,
	imin_queue_gap: 40,
	imin_eod_print_copies: 1,
	imin_eod_copy_delay_ms: 800,
	imin_eod_feed_dots: 160,
	imin_eod_tail_dots: 24,
	imin_eod_font_scale: 100,
	imin_eod_line_spacing: 100,
	imin_eod_side_margin: 16,
	imin_eod_top_margin: 0,
});

// Stock Sync Settings (localStorage persisted)
const stockSyncEnabled = ref(false);
const stockSyncIntervalSeconds = ref(60); // Default 60 seconds
const stockSyncStatus = ref({
	enabled: false,
	warehouse: null,
	itemCount: 0,
	intervalMs: 60000,
	lastSync: null,
	running: false,
});

// QZ Tray composable
const {
	qzConnected,
	qzConnecting,
	qzCertStatus,
	printers: qzPrinters,
	selectedPrinter,
	loadingPrinters,
	printerOptions,
	certLoading: qzCertLoading,
	certReady: qzCertReady,
	handleConnect: handleQzConnect,
	refreshPrinters: handleRefreshPrinters,
	generateCertificate: handleSetupQzCertificate,
	downloadCertificate: handleDownloadQzCertificate,
} = useQzTray();

// Warehouse options
const warehouseOptions = computed(() => {
	if (warehousesList.value.length === 0) return [];
	return warehousesList.value.map((w) => ({
		label: w.warehouse_name || w.name,
		value: w.name,
	}));
});

// Dynamic classes using configuration helpers (DRY principle)
const stockSectionClasses = computed(() => getSectionHeaderClasses("purple"));
const salesSectionClasses = computed(() => getSectionHeaderClasses("green"));
const printingSectionClasses = computed(() => getSectionHeaderClasses("teal"));
const warehouseSubsectionClasses = computed(() => getSubsectionClasses("gray"));
const stockPolicySubsectionClasses = computed(() => getSubsectionClasses("blue"));
const stockSyncSubsectionClasses = computed(() => getSubsectionClasses("indigo"));
const pricingSubsectionClasses = computed(() => getSubsectionClasses("emerald"));
const operationsSubsectionClasses = computed(() => getSubsectionClasses("teal"));
const queueSubsectionClasses = computed(() => getSubsectionClasses("blue"));
const driverSubsectionClasses = computed(() => getSubsectionClasses("indigo"));
const iminSubsectionClasses = computed(() => getSubsectionClasses("emerald"));
const eodSubsectionClasses = computed(() => getSubsectionClasses("gray"));

// Printing options
const printDriverOptions = [
	{ label: "Browser", value: "browser" },
	{ label: "QZ Tray", value: "qz" },
	{ label: "iMin", value: "imin" },
];
const paperWidthOptions = [
	{ label: "58mm (384 dots)", value: "58mm" },
	{ label: "80mm (576 dots)", value: "80mm" },
	{ label: "Custom", value: "custom" },
];

// iMin access trigger — the same bait the Direct Print page performs on
// mount: inject the SDK, connect to the device's print service and probe
// status. The service asks the operator to allow this origin (once per
// device), so silent print works without opening /pos/direct-print first.
const iminProbing = ref(false);
const iminAccess = ref(null);

async function requestIminAccess() {
	iminProbing.value = true;
	iminAccess.value = null;
	try {
		const loaded = await ensureIminSdk();
		if (!loaded) throw new Error("iMin SDK gagal dimuat");
		const driver = getTransport().getDriver("imin");
		const s = await driver.getStatus();
		iminAccess.value = {
			ok: Boolean(s?.ok),
			message: s?.ok ? "" : s?.message || "",
		};
	} catch (e) {
		iminAccess.value = { ok: false, message: e?.message || String(e) };
	} finally {
		iminProbing.value = false;
	}
}

// Resources
const warehousesResource = createResource({
	url: "pos_next.api.pos_profile.get_warehouses",
	makeParams() {
		return {
			pos_profile: props.posProfile,
		};
	},
	auto: false,
	onSuccess(data) {
		const warehouses = data?.message || data || [];
		warehousesList.value = warehouses;
	},
	onError(error) {
		warehousesList.value = [];
	},
});

// Track original allow_negative_stock value for detecting changes
const originalAllowNegativeStock = ref(null);

const settingsResource = createResource({
	url: "pos_next.pos_next.doctype.pos_settings.pos_settings.get_pos_settings",
	makeParams() {
		return {
			pos_profile: props.posProfile,
		};
	},
	onSuccess(data) {
		if (data) {
			Object.assign(settings.value, data);
			settings.value.pos_profile = props.posProfile;
			// Store original value
			originalAllowNegativeStock.value = data.allow_negative_stock;
			// Update event system snapshot
			updateSettingsSnapshot(settings.value);
		}
		loading.value = false;
	},
	onError(error) {
		loading.value = false;
		showError(__("Failed to load settings"));
	},
});

// Watchers
watch(
	() => props.modelValue,
	(val) => {
		show.value = val;
		if (val) {
			loadSettings();
		}
	}
);

watch(show, (val) => {
	emit("update:modelValue", val);
});

// Watch for currentWarehouse prop changes and always sync
watch(
	() => props.currentWarehouse,
	(newWarehouse) => {
		if (newWarehouse) {
			selectedWarehouse.value = newWarehouse;
		}
	},
	{ immediate: true }
);

// Watch for tax_inclusive changes to provide immediate feedback
const originalTaxInclusive = ref(null);
watch(
	() => settings.value.tax_inclusive,
	(newValue, oldValue) => {
		// Store original value on first load
		if (originalTaxInclusive.value === null && oldValue !== undefined) {
			originalTaxInclusive.value = oldValue;
		}

		// Only show feedback if value actually changed from original
		if (originalTaxInclusive.value !== null && newValue !== originalTaxInclusive.value) {
			const mode = newValue ? "inclusive" : "exclusive";
			log.info(`Tax mode toggled to: ${mode}`);
		}
	}
);

// Methods
function handleClose() {
	show.value = false;
}

async function loadSettings() {
	if (!props.posProfile) return;
	loading.value = true;
	settings.value.pos_profile = props.posProfile;

	// Always set the current warehouse from props (from current shift/profile)
	selectedWarehouse.value = props.currentWarehouse || "";

	try {
		// Load warehouses first using call API directly
		const warehousesData = await call("pos_next.api.pos_profile.get_warehouses", {
			pos_profile: props.posProfile,
		});

		// Handle frappe-ui call response format { message: [...] }
		warehousesList.value = warehousesData?.message || warehousesData || [];

		// Load settings
		settingsResource.reload();
	} catch (error) {
		log.error("Error loading warehouses:", error);
		warehousesList.value = [];
		// Still load settings even if warehouses fail
		settingsResource.reload();
	}
}

async function saveSettings() {
	if (!props.posProfile) {
		showError(__("POS Profile not found"));
		return;
	}

	saving.value = true;
	const oldWarehouse = props.currentWarehouse;
	const warehouseChanged = selectedWarehouse.value !== oldWarehouse;
	const negativeStockChanged =
		originalAllowNegativeStock.value !== settings.value.allow_negative_stock;
	const taxInclusiveChanged =
		originalTaxInclusive.value !== null &&
		originalTaxInclusive.value !== settings.value.tax_inclusive;

	// Capture old settings for change detection
	const oldSettings = {
		...settings.value,
		warehouse: oldWarehouse, // Include warehouse in change detection
	};

	try {
		// Save POS Settings (without warehouse)
		const result = await call(
			"pos_next.pos_next.doctype.pos_settings.pos_settings.update_pos_settings",
			{
				pos_profile: props.posProfile,
				settings: settings.value,
			}
		);

		if (result) {
			Object.assign(settings.value, result);
			settings.value.pos_profile = props.posProfile;
			// Update original values after successful save
			originalAllowNegativeStock.value = result.allow_negative_stock;
			originalTaxInclusive.value = result.tax_inclusive;
		}

		// Print knobs live in the transport singleton, which is loaded once
		// per session — without this refresh, copies/crew/margins keep their
		// pre-save values until the next full page reload.
		initTransportFromServer(props.posProfile).catch((err) => {
			log.warn("print config refresh failed:", err?.message || err);
		});

		// Update warehouse in POS Profile if changed
		if (warehouseChanged && selectedWarehouse.value) {
			const warehouseResult = await call("pos_next.api.pos_profile.update_warehouse", {
				pos_profile: props.posProfile,
				warehouse: selectedWarehouse.value,
			});

			if (warehouseResult && warehouseResult.success) {
				// Add warehouse to new settings for change detection
				// (detectSettingsChanges below will emit settings:warehouse-changed via event bus)
				settings.value.warehouse = selectedWarehouse.value;
			}
		}

		// Detect and emit settings changes through event system
		// This will notify all listeners (POSSale, stock store, cart store, etc.)
		detectSettingsChanges(settings.value, oldSettings);

		// IMPORTANT: Page reload for critical stock policy change
		// The allow_negative_stock setting affects deep stock validation logic
		// throughout the app, including:
		// - Stock validation in cart operations (posCart.js:59)
		// - Stock enforcement checks (posSettings.js:268)
		// - Item addition logic and error handling
		// A page reload ensures all components get the fresh setting and
		// prevents inconsistent state. Event listeners are still notified
		// before reload for any cleanup needed.
		if (negativeStockChanged) {
			log.info("Stock policy changed, reloading page for consistency...");
			window.location.reload();
			return;
		}

		// Show success toast for other changes
		let successMessage = __("Settings saved successfully");
		if (warehouseChanged && taxInclusiveChanged) {
			successMessage = __(
				"Settings saved, warehouse updated, and tax mode changed. Cart will be recalculated."
			);
		} else if (warehouseChanged) {
			successMessage = __("Settings saved and warehouse updated. Reloading stock...");
		} else if (taxInclusiveChanged) {
			successMessage = settings.value.tax_inclusive
				? __('Settings saved. Tax mode is now "inclusive". Cart will be recalculated.')
				: __('Settings saved. Tax mode is now "exclusive". Cart will be recalculated.');
		}

		showSuccess(successMessage);
	} catch (error) {
		log.error("Error saving settings:", error);
		showError(error.message || __("Failed to save settings"));
	} finally {
		saving.value = false;
	}
}

// Auto-connect and discover printers when silent_print is toggled on
watch(
	() => settings.value.silent_print,
	async (enabled) => {
		if (enabled) {
			await handleQzConnect();
		}
	}
);

// ============================================================================
// STOCK SYNC FUNCTIONS
// ============================================================================

// Load stock sync settings from localStorage
function loadStockSyncSettings() {
	try {
		const saved = localStorage.getItem("pos_stock_sync_settings");
		if (saved) {
			const parsed = JSON.parse(saved);
			stockSyncEnabled.value = parsed.enabled ?? false;
			stockSyncIntervalSeconds.value = parsed.intervalSeconds ?? 60;
		}
	} catch (error) {
		log.error("Failed to load stock sync settings:", error);
	}
}

// Save stock sync settings to localStorage
function saveStockSyncSettings() {
	try {
		localStorage.setItem(
			"pos_stock_sync_settings",
			JSON.stringify({
				enabled: stockSyncEnabled.value,
				intervalSeconds: stockSyncIntervalSeconds.value,
			})
		);
	} catch (error) {
		log.error("Failed to save stock sync settings:", error);
	}
}

// Update stock sync status
async function updateStockSyncStatus() {
	try {
		const status = await offlineWorker.getStockSyncStatus();
		stockSyncStatus.value = status;
	} catch (error) {
		log.error("Failed to get stock sync status:", error);
	}
}

// Apply stock sync configuration to worker
async function applyStockSyncConfig() {
	try {
		const intervalMs = stockSyncIntervalSeconds.value * 1000;

		if (stockSyncEnabled.value) {
			// Configure and start sync
			await offlineWorker.configureStockSync({
				intervalMs,
			});
			await offlineWorker.startStockSync();
		} else {
			// Stop sync
			await offlineWorker.stopStockSync();
		}

		// Update status
		await updateStockSyncStatus();

		// Save to localStorage
		saveStockSyncSettings();

		// Emit sync configuration change event
		emitStockSyncConfigured({
			enabled: stockSyncEnabled.value,
			intervalMs: intervalMs,
		});
	} catch (error) {
		log.error("Failed to apply stock sync config:", error);
	}
}

// Format sync time for display
function formatSyncTime(timestamp) {
	if (!timestamp) return __("Never");

	const now = Date.now();
	const diff = now - timestamp;

	if (diff < 60000) {
		return __("{0}s ago", [Math.floor(diff / 1000)]);
	} else if (diff < 3600000) {
		return __("{0}m ago", [Math.floor(diff / 60000)]);
	} else {
		const date = new Date(timestamp);
		return date.toLocaleTimeString();
	}
}

// Watch for changes and apply
watch(stockSyncEnabled, () => {
	applyStockSyncConfig();
});

watch(stockSyncIntervalSeconds, () => {
	if (stockSyncEnabled.value) {
		applyStockSyncConfig();
	}
});

// Lifecycle hooks
onMounted(async () => {
	// Load settings
	loadStockSyncSettings();

	// Update status initially
	await updateStockSyncStatus();

	// Poll status every 5 seconds
	const statusInterval = setInterval(() => {
		updateStockSyncStatus();
	}, 5000);

	// Cleanup on unmount
	onUnmounted(() => {
		clearInterval(statusInterval);
	});
});
</script>

<style scoped>
/* Fade transition for overlay */
.fade-enter-active,
.fade-leave-active {
	transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
	opacity: 0;
}
</style>
