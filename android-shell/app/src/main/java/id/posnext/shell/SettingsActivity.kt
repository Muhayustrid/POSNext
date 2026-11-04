package id.posnext.shell

import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity

/** Server URL + "Test print native"; opened by long-pressing the top-right corner. */
class SettingsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val app = application as ShellApp

        val url = EditText(this).apply {
            hint = getString(R.string.server_url)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine()
            setText(Prefs.url(this@SettingsActivity).orEmpty())
        }
        val info = TextView(this)
        val save = Button(this).apply {
            text = getString(R.string.save)
            setOnClickListener {
                val value = ServerUrl.normalize(url.text.toString())
                if (value == null) {
                    url.error = getString(R.string.invalid_url)
                    return@setOnClickListener
                }
                Prefs.setUrl(this@SettingsActivity, value)
                startActivity(Intent(this@SettingsActivity, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
                finish()
            }
        }
        val test = Button(this).apply {
            text = getString(R.string.test_print)
            setOnClickListener { testPrint(app, info) }
        }

        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
            addView(url)
            addView(save)
            addView(test)
            addView(info)
        })
        info.text = "App ${BuildConfig.VERSION_NAME}"
    }

    private fun testPrint(app: ShellApp, info: TextView) {
        app.printQueue.execute {
            val text = runCatching {
                val code = app.printer.status()
                if (code != 0) return@runCatching statusMessage(code)
                app.printer.printRaw(TEST_PAGE)
                "OK · SDK ${app.printer.sdkVersion()} · service ${app.printer.serviceVersion()}"
            }.getOrElse { it.message ?: it.javaClass.simpleName }
            runOnUiThread {
                info.text = text
                Toast.makeText(this, text, Toast.LENGTH_SHORT).show()
            }
        }
    }

    private companion object {
        // ESC @, centred "POSNext native test", feed 3 lines, GS V 1 partial cut.
        val TEST_PAGE: ByteArray =
            byteArrayOf(0x1b, 0x40, 0x1b, 0x61, 0x01) +
                "POSNext native test\nSDK 2.0\n".toByteArray(Charsets.US_ASCII) +
                byteArrayOf(0x1b, 0x64, 0x03, 0x1d, 0x56, 0x01)
    }
}
