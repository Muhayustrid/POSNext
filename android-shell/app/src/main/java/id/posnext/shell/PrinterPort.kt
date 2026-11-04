package id.posnext.shell

/** What the bridge needs from the printer; faked in unit tests. */
interface PrinterPort {
    /** iMin getPrinterStatus(): 0 normal, -1 service not bound, see [statusMessage]. */
    fun status(): Int

    /** Hands raw ESC/POS bytes to the SDK; returns once the service took them. */
    fun printRaw(data: ByteArray)

    /** Prints a PNG, then feeds [feedDots] and cuts when [cut]. */
    fun printBitmap(png: ByteArray, feedDots: Int, cut: Boolean)

    fun sdkVersion(): String
    fun serviceVersion(): String
}

/**
 * iMin SDK v2 printer status codes (official iMin PrinterDemo, Utils.getPrinterStatusTip).
 */
fun statusMessage(code: Int): String = when (code) {
    0 -> "Printer normal"
    3 -> "Printer cover is open"
    4 -> "Printer low power"
    5 -> "Printer overheated"
    6 -> "Printer cutter error"
    7 -> "Printer out of paper"
    -1 -> "Printer service not connected"
    else -> "Printer status $code"
}
