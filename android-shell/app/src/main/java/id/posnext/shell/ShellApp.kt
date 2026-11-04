package id.posnext.shell

import android.app.Application
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class ShellApp : Application() {
    val printer = IminPrinter()

    /** The one print thread: jobs never interleave and never block the UI. */
    val printQueue: ExecutorService = Executors.newSingleThreadExecutor()

    override fun onCreate() {
        super.onCreate()
        printer.bind(this)
    }
}
