package id.posnext.shell

import android.content.Context

object Prefs {
    private const val FILE = "posnext_shell"
    private const val KEY_URL = "server_url"

    fun url(ctx: Context): String? =
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(KEY_URL, null)

    fun setUrl(ctx: Context, url: String) {
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().putString(KEY_URL, url).apply()
    }
}
