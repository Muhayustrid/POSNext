package id.posnext.shell

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Base64
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature

class MainActivity : ComponentActivity() {
    private lateinit var web: WebView
    private var loadedUrl: String? = null
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    private val pickFile = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res.resultCode, res.data))
        fileCallback = null
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val url = Prefs.url(this)
        if (url == null) {
            startActivity(Intent(this, SettingsActivity::class.java))
            finish()
            return
        }

        web = WebView(this)
        // Service workers run once JavaScript and DOM storage are on (WebView default).
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
        }
        web.webViewClient = WebViewClient()
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams,
            ): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                return runCatching { pickFile.launch(params.createIntent()); true }.getOrElse {
                    fileCallback = null
                    false
                }
            }
        }
        installBridge(url)

        // Hidden settings entry: long-press the top-right corner.
        val corner = View(this).apply {
            contentDescription = getString(R.string.corner_hint)
            setOnLongClickListener {
                startActivity(Intent(this@MainActivity, SettingsActivity::class.java)); true
            }
        }
        val size = (56 * resources.displayMetrics.density).toInt()
        setContentView(FrameLayout(this).apply {
            addView(web, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            addView(corner, FrameLayout.LayoutParams(size, size, Gravity.TOP or Gravity.END))
        })

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        loadedUrl = url
        if (savedInstanceState == null) web.loadUrl(url) else web.restoreState(savedInstanceState)
    }

    /** window.PosNextNative, injected only into the configured server origin. */
    private fun installBridge(url: String) {
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            Toast.makeText(this, R.string.bridge_unsupported, Toast.LENGTH_LONG).show()
            return
        }
        val app = application as ShellApp
        val bridge = NativeBridge(
            printer = app.printer,
            appVersion = BuildConfig.VERSION_NAME,
            deviceModel = "${Build.MANUFACTURER} ${Build.MODEL}",
            androidVersion = Build.VERSION.RELEASE,
            decodeBase64 = { Base64.decode(it, Base64.DEFAULT) },
        )
        WebViewCompat.addWebMessageListener(
            web, "PosNextNative", setOf(ServerUrl.originRule(url)),
        ) { _, message, _, _, reply ->
            val raw = message.data ?: return@addWebMessageListener
            app.printQueue.execute {
                val answer = bridge.handle(raw)
                // JavaScriptReplyProxy must be used on the UI thread.
                runOnUiThread { if (!isDestroyed) reply.postMessage(answer) }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // A new server URL also means a new allowed origin: rebuild the page.
        if (loadedUrl != null && Prefs.url(this) != loadedUrl) {
            finish()
            startActivity(Intent(this, MainActivity::class.java))
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::web.isInitialized) web.saveState(outState)
    }

    override fun onDestroy() {
        if (::web.isInitialized) web.destroy()
        super.onDestroy()
    }
}
