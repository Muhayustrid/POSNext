package id.posnext.shell

import android.content.Context
import android.graphics.BitmapFactory
import android.util.Log
import com.imin.printer.InitPrinterCallback
import com.imin.printer.PrinterHelper

/**
 * iMin Printer SDK 2.0 (com.imin.printer.PrinterHelper, IminPrinterLibrary V2.0.0.18).
 * API names checked against the official SDK jar and iMin's PrinterDemo.
 * Calls must run on the single print thread, never on the UI thread.
 */
class IminPrinter : PrinterPort {
    private val helper: PrinterHelper get() = PrinterHelper.getInstance()

    /** Binds the printer service; onConnected also runs the SDK's initPrinter. */
    fun bind(ctx: Context) {
        helper.initPrinterService(ctx, object : InitPrinterCallback {
            override fun onConnected() {
                Log.i(TAG, "iMin printer service connected")
            }

            override fun onDisconnected() {
                Log.w(TAG, "iMin printer service disconnected")
            }
        })
    }

    override fun status(): Int = helper.printerStatus

    // The binder call is synchronous: when it returns the service owns the job.
    // Null callback as in iMin's demo; a missing service is caught by status() first.
    override fun printRaw(data: ByteArray) = helper.sendRAWData(data, null)

    override fun printBitmap(png: ByteArray, feedDots: Int, cut: Boolean) {
        val bmp = BitmapFactory.decodeByteArray(png, 0, png.size)
            ?: throw IllegalArgumentException("not an image")
        helper.printBitmap(bmp, null)
        if (feedDots > 0) helper.printAndFeedPaper(feedDots)
        if (cut) helper.partialCut()
    }

    override fun sdkVersion(): String = PrinterHelper.getSDKVersionName() ?: ""
    override fun serviceVersion(): String = helper.serviceVersion ?: ""

    private companion object { const val TAG = "IminPrinter" }
}
