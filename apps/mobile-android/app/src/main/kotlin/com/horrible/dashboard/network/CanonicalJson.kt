package com.horrible.dashboard.network

import org.json.JSONArray
import org.json.JSONObject
import java.math.BigDecimal
import java.math.MathContext
import java.math.RoundingMode
import java.util.Locale

/**
 * A byte-exact re-implementation of CPython's
 * `json.dumps(obj, sort_keys=True, separators=(",", ":"))`.
 *
 * Every peer envelope carries an Ed25519 signature over a *canonicalization* of
 * its authenticated fields, so this app and the desktop node (Python) must agree
 * on those bytes down to the last character. Moshi's writer disagrees with
 * Python on three counts, each of which silently produces envelopes the desktop
 * rejects rather than an error we could see:
 *
 *  - **Floats.** `Double.toString(1.7536e9)` is `"1.753632E9"`; Python's `repr`
 *    is `"1753632000.0"`. Timestamps land squarely in the range where Java
 *    switches to scientific notation and Python does not.
 *  - **Key order.** Python sorts keys *recursively*; a Kotlin `mapOf` keeps
 *    insertion order, so nested `data` objects came out shuffled.
 *  - **Non-ASCII.** Python defaults to `ensure_ascii=True` and escapes anything
 *    outside `0x20..0x7E`; Moshi emits raw UTF-8. A device name with an accent
 *    or emoji was enough to break pairing.
 *
 * Input is `org.json` values parsed from the raw wire text rather than Moshi's
 * `Map<String, Any>`: Moshi widens every JSON number to `Double`, which would
 * turn the peer's `1` into `1.0` and break verification of inbound envelopes.
 */
object CanonicalJson {

    fun dumps(value: Any?): String = StringBuilder().also { write(it, value) }.toString()

    private fun write(sb: StringBuilder, value: Any?) {
        when (value) {
            null, JSONObject.NULL -> sb.append("null")
            is JSONObject -> writeObject(sb, value)
            is JSONArray -> writeArray(sb, value)
            is Boolean -> sb.append(if (value) "true" else "false")
            is Double -> sb.append(pythonRepr(value))
            is Float -> sb.append(pythonRepr(value.toDouble()))
            is Number -> sb.append(value.toString()) // Int / Long / BigInteger
            is String -> writeString(sb, value)
            else -> writeString(sb, value.toString())
        }
    }

    private fun writeObject(sb: StringBuilder, obj: JSONObject) {
        val keys = obj.keys().asSequence().sortedWith(CODE_POINT_ORDER)
        sb.append('{')
        var first = true
        for (key in keys) {
            if (!first) sb.append(',')
            first = false
            writeString(sb, key)
            sb.append(':')
            write(sb, obj.opt(key))
        }
        sb.append('}')
    }

    private fun writeArray(sb: StringBuilder, arr: JSONArray) {
        sb.append('[')
        for (i in 0 until arr.length()) {
            if (i > 0) sb.append(',')
            write(sb, arr.opt(i))
        }
        sb.append(']')
    }

    /**
     * Python compares strings by code point; Java's natural `String` order is by
     * UTF-16 code unit. They only diverge above the BMP, but protocol keys are
     * caller-supplied so the safe comparator costs nothing.
     */
    private val CODE_POINT_ORDER = Comparator<String> { a, b ->
        var i = 0
        var j = 0
        while (i < a.length && j < b.length) {
            val ca = a.codePointAt(i)
            val cb = b.codePointAt(j)
            if (ca != cb) return@Comparator ca - cb
            i += Character.charCount(ca)
            j += Character.charCount(cb)
        }
        (a.length - i) - (b.length - j)
    }

    /** Python's `ESCAPE_ASCII` rules: escape `"` `\`, C0 controls, and everything
     *  outside `0x20..0x7E` as a lowercase `\uXXXX` (surrogate pairs as two). */
    private fun writeString(sb: StringBuilder, s: String) {
        sb.append('"')
        for (ch in s) {
            when {
                ch == '"' -> sb.append("\\\"")
                ch == '\\' -> sb.append("\\\\")
                ch == '\b' -> sb.append("\\b")
                ch == '\u000C' -> sb.append("\\f")
                ch == '\n' -> sb.append("\\n")
                ch == '\r' -> sb.append("\\r")
                ch == '\t' -> sb.append("\\t")
                ch < '\u0020' || ch > '\u007E' ->
                    sb.append(String.format(Locale.ROOT, "\\u%04x", ch.code))
                else -> sb.append(ch)
            }
        }
        sb.append('"')
    }

    /**
     * CPython's `repr(float)` — the shortest decimal that round-trips, formatted
     * fixed unless the decimal exponent is `<= -4` or `> 16`.
     *
     * The ascending-precision search is the same result CPython's `dtoa` shortest
     * mode produces; `HALF_EVEN` matches its tie-breaking. `BigDecimal(double)` is
     * the *exact* binary value, so rounding it to `p` significant digits gives the
     * correctly-rounded `p`-digit decimal.
     */
    fun pythonRepr(d: Double): String {
        if (d.isNaN()) return "NaN"
        if (d == Double.POSITIVE_INFINITY) return "Infinity"
        if (d == Double.NEGATIVE_INFINITY) return "-Infinity"
        if (d == 0.0) return if (1.0 / d < 0) "-0.0" else "0.0"

        val negative = d < 0
        val abs = Math.abs(d)

        var rounded = BigDecimal(abs)
        for (precision in 1..17) {
            val candidate = BigDecimal(abs).round(MathContext(precision, RoundingMode.HALF_EVEN))
            if (candidate.toDouble() == abs) {
                rounded = candidate
                break
            }
        }
        val stripped = rounded.stripTrailingZeros()
        val digits = stripped.unscaledValue().toString()
        // value == 0.<digits> * 10^decpt
        val decpt = digits.length - stripped.scale()

        val sb = StringBuilder()
        if (negative) sb.append('-')
        when {
            decpt <= -4 || decpt > 16 -> {
                sb.append(digits[0])
                if (digits.length > 1) sb.append('.').append(digits, 1, digits.length)
                val exp = decpt - 1
                sb.append('e').append(if (exp < 0) '-' else '+')
                val magnitude = Math.abs(exp).toString()
                if (magnitude.length < 2) sb.append('0')
                sb.append(magnitude)
            }
            decpt <= 0 -> {
                sb.append("0.")
                repeat(-decpt) { sb.append('0') }
                sb.append(digits)
            }
            decpt >= digits.length -> {
                sb.append(digits)
                repeat(decpt - digits.length) { sb.append('0') }
                sb.append(".0")
            }
            else -> sb.append(digits, 0, decpt).append('.').append(digits, decpt, digits.length)
        }
        return sb.toString()
    }
}
