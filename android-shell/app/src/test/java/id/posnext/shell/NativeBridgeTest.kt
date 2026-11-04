package id.posnext.shell

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class NativeBridgeTest {
    private class FakePrinter(var code: Int = 0, val fail: Boolean = false) : PrinterPort {
        val raw = mutableListOf<ByteArray>()
        override fun status() = code
        override fun printRaw(data: ByteArray) {
            if (fail) error("binder died")
            raw += data
        }
        override fun printBitmap(png: ByteArray, feedDots: Int, cut: Boolean) { raw += png }
        override fun sdkVersion() = "V2.0.0.19"
        override fun serviceVersion() = "2.0.11"
    }

    private fun bridge(p: PrinterPort) =
        NativeBridge(p, "1.0.0", "iMin Swan 1", "13") { Base64.getDecoder().decode(it) }

    private fun call(b: NativeBridge, json: String) = JSONObject(b.handle(json))
    private val b64 = Base64.getEncoder().encodeToString(byteArrayOf(0x1b, 0x40, 0x41))

    @Test fun helloEchoesIdAndVersions() {
        val r = call(bridge(FakePrinter()), """{"id":"7","cmd":"hello"}""")
        assertEquals("7", r.getString("id"))
        assertTrue(r.getBoolean("ok"))
        assertEquals("posnext-shell", r.getString("app"))
        assertEquals("V2.0.0.19", r.getString("sdk"))
        assertEquals("13", r.getString("android"))
    }

    @Test fun statusReportsFaultCode() {
        val r = call(bridge(FakePrinter(code = 7)), """{"id":1,"cmd":"status"}""")
        assertFalse(r.getBoolean("ok"))
        assertEquals(7, r.getInt("code"))
        assertEquals("Printer out of paper", r.getString("message"))
    }

    @Test fun printRawSendsDecodedBytes() {
        val p = FakePrinter()
        val r = call(bridge(p), """{"id":2,"cmd":"printRaw","data":"$b64"}""")
        assertTrue(r.getBoolean("ok"))
        assertEquals(listOf(0x1b, 0x40, 0x41), p.raw.single().map { it.toInt() })
    }

    @Test fun printRawRefusedWhenCoverOpenWithoutPrinted() {
        val p = FakePrinter(code = 3)
        val r = call(bridge(p), """{"id":3,"cmd":"printRaw","data":"$b64"}""")
        assertFalse(r.getBoolean("ok"))
        assertEquals(3, r.getInt("code"))
        assertFalse(r.optBoolean("printed"))
        assertTrue(p.raw.isEmpty())
    }

    @Test fun sdkFailureMarksPrinted() {
        val r = call(bridge(FakePrinter(fail = true)), """{"id":4,"cmd":"printRaw","data":"$b64"}""")
        assertFalse(r.getBoolean("ok"))
        assertTrue(r.getBoolean("printed"))
    }

    @Test fun badBase64IsPlainFailure() {
        val r = call(bridge(FakePrinter()), """{"id":5,"cmd":"printRaw","data":"%%%"}""")
        assertFalse(r.getBoolean("ok"))
        assertFalse(r.optBoolean("printed"))
    }

    @Test fun unknownCommandAndBadJson() {
        assertFalse(call(bridge(FakePrinter()), """{"id":6,"cmd":"x"}""").getBoolean("ok"))
        val bad = call(bridge(FakePrinter()), "not json")
        assertFalse(bad.getBoolean("ok"))
        assertNull(bad.opt("id"))
    }

    @Test fun originRuleDropsPathAndKeepsExplicitPort() {
        assertEquals("http://dev.localhost:8000", ServerUrl.originRule("http://dev.localhost:8000/pos"))
        assertEquals("https://erp.contoh.id", ServerUrl.originRule("https://ERP.contoh.id/pos?x=1"))
    }

    @Test fun normalizeRejectsNonHttp() {
        assertEquals("https://a.id/pos", ServerUrl.normalize("  https://a.id/pos "))
        assertNull(ServerUrl.normalize("javascript:alert(1)"))
        assertNull(ServerUrl.normalize("file:///sdcard/x"))
        assertNull(ServerUrl.normalize("http://"))
    }
}
