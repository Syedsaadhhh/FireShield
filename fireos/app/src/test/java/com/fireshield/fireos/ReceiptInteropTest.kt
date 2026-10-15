package com.fireshield.fireos

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ReceiptInteropTest {
    @Test fun pythonGoldenReceiptMatchesWithUnicodeNullAndSlash() {
        val raw = javaClass.getResourceAsStream("/receipt-v1-unicode.json")!!
            .bufferedReader().use { it.readText() }
        val receipt = JSONObject(raw)
        assertTrue(RecoveryVerifier.validateReceiptDigest(receipt))
        assertEquals(receipt.getString("receipt_digest"), RecoveryVerifier.computeReceiptDigest(receipt))
        receipt.put("position_delta_ms", receipt.getLong("position_delta_ms") + 1)
        assertFalse(RecoveryVerifier.validateReceiptDigest(receipt))
    }

    @Test fun receiptRejectsNonIntegerNumbersInsteadOfRoundingThem() {
        val receipt = JSONObject().put("position_ms", 1.5)
        try {
            RecoveryVerifier.computeReceiptDigest(receipt)
            fail("Fractional numbers are outside the receipt profile")
        } catch (_: FireShieldContractException) { }
    }
}
