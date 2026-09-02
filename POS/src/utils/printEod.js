import { silentPrintDoc } from "./printInvoice"

const EOD_PRINT_FORMAT = "POS Next EOD Report"

export async function printEODReport(closingShiftName, posProfile) {
	// posProfile, when available, lets the iMin lane re-resolve paper/copies
	// per-POS Profile instead of falling back to browser defaults. The caller
	// (ShiftClosingDialog) has closingData.pos_profile available.
	await silentPrintDoc(
		"POS Closing Shift",
		closingShiftName,
		EOD_PRINT_FORMAT,
		posProfile,
	)
}
