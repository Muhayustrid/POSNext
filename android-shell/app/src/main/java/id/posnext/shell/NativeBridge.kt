package id.posnext.shell

import org.json.JSONObject

/**
 * The PosNextNative JSON protocol (see POS/src/utils/print/native_client.js).
 * Every request carries an `id`; the reply echoes it.
 *   hello                       -> {id, ok, app, version, sdk, service, model, android}
 *   status                      -> {id, ok, code, message}
 *   printRaw {data}             -> {id, ok, code?, message?, printed?}
 *   printBitmap {data, feedDots, cut}
 * [handle] is blocking: the caller runs it on the single print thread.
 */
class NativeBridge(
    private val printer: PrinterPort,
    private val appVersion: String,
    private val deviceModel: String,
    private val androidVersion: String,
    private val decodeBase64: (String) -> ByteArray,
) {
    fun handle(raw: String): String {
        val req = runCatching { JSONObject(raw) }.getOrNull()
            ?: return JSONObject().put("ok", false).put("message", "bad request").toString()
        val reply = JSONObject().put("id", req.opt("id"))
        try {
            when (req.optString("cmd")) {
                "hello" -> reply.put("ok", true)
                    .put("app", "posnext-shell")
                    .put("version", appVersion)
                    .put("sdk", printer.sdkVersion())
                    .put("service", printer.serviceVersion())
                    .put("model", deviceModel)
                    .put("android", androidVersion)

                "status" -> {
                    val code = printer.status()
                    reply.put("ok", code == 0).put("code", code).put("message", statusMessage(code))
                }

                "printRaw", "printBitmap" -> print(req, reply)

                else -> reply.put("ok", false).put("message", "unknown cmd")
            }
        } catch (e: Exception) {
            // printed stays false: nothing reached the SDK, the web side may fall back.
            reply.put("ok", false).put("message", e.message ?: e.javaClass.simpleName)
        }
        return reply.toString()
    }

    private fun print(req: JSONObject, reply: JSONObject) {
        val data = decodeBase64(req.optString("data"))
        if (data.isEmpty()) {
            reply.put("ok", false).put("message", "empty data")
            return
        }
        // Refuse before sending, so a fault never leaves half a receipt.
        val code = printer.status()
        if (code != 0) {
            reply.put("ok", false).put("code", code).put("message", statusMessage(code))
            return
        }
        try {
            if (req.optString("cmd") == "printRaw") {
                printer.printRaw(data)
            } else {
                printer.printBitmap(data, req.optInt("feedDots", 0), req.optBoolean("cut", false))
            }
        } catch (e: Exception) {
            // The SDK may have started printing: tell the web side not to retry elsewhere.
            reply.put("ok", false).put("printed", true).put("message", e.message ?: "print failed")
            return
        }
        reply.put("ok", true).put("code", 0)
    }
}
