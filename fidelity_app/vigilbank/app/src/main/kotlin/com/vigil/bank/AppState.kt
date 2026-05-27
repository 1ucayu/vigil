package com.vigil.bank

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.vigil.bank.data.Account
import com.vigil.bank.data.HistoryEntry
import com.vigil.bank.data.History
import com.vigil.bank.data.Recipient

enum class Screen(val id: String) {
    HOME("home"),
    RECIPIENTS("recipients"),
    TRANSFER_FORM("transfer_form"),
    TRANSFER_CONFIRM("transfer_confirm"),
    OTP_CONFIRM("otp_confirm"),
    TRANSFER_SUCCESS("transfer_success"),
    HISTORY("history"),
    SETTINGS("settings"),
}

class AppState {
    var screen by mutableStateOf(Screen.HOME)
        private set

    var selectedRecipient by mutableStateOf<Recipient?>(null)
        private set
    var amountCents by mutableStateOf(0)
        private set
    var memo by mutableStateOf("")
        private set
    var otpDigits by mutableStateOf("")
        private set

    var balanceCents by mutableStateOf(Account.balanceCents)
        private set
    var dailyLimitCents by mutableStateOf(Account.dailyLimitCents)
        private set

    var transferError by mutableStateOf<String?>(null)
        private set

    // Mutable, includes the seed history plus any newly committed transfers.
    var history by mutableStateOf(History.seed.toList())
        private set

    // Inert settings toggles (do not affect topology).
    var biometricUnlock by mutableStateOf(false)
    var transferNotifications by mutableStateOf(false)

    fun navigate(target: Screen) {
        screen = target
    }

    fun openRecipients() {
        screen = Screen.RECIPIENTS
    }

    fun selectRecipient(r: Recipient) {
        selectedRecipient = r
        screen = Screen.TRANSFER_FORM
    }

    fun updateAmountCents(v: Int) {
        amountCents = if (v < 0) 0 else v
    }

    fun updateMemo(s: String) {
        memo = s
    }

    fun updateOtpDigits(s: String) {
        // Restrict to digits, max length 6, for determinism.
        otpDigits = s.filter { it.isDigit() }.take(6)
    }

    /** Guard: amount > 0 AND selectedRecipient != null. */
    fun continueToConfirm() {
        if (amountCents > 0 && selectedRecipient != null) {
            transferError = null
            screen = Screen.TRANSFER_CONFIRM
        }
    }

    /** Guards: amount <= balance AND amount <= dailyLimit. */
    fun submitTransfer() {
        when {
            amountCents > balanceCents -> {
                transferError = "Insufficient funds"
            }
            amountCents > dailyLimitCents -> {
                transferError = "Daily limit exceeded"
            }
            else -> {
                transferError = null
                screen = Screen.OTP_CONFIRM
            }
        }
    }

    /** Clear intent fields except selectedRecipient; return to form. */
    fun cancelTransfer() {
        amountCents = 0
        memo = ""
        otpDigits = ""
        transferError = null
        screen = Screen.TRANSFER_FORM
    }

    /** IRREVERSIBLE: append to history, navigate to success. */
    fun confirmOtp() {
        val r = selectedRecipient ?: return
        val nextId = "T-%03d".format(history.size + 1)
        val entry = HistoryEntry(
            id = nextId,
            recipientName = r.displayName,
            amountCents = amountCents,
            memo = memo,
        )
        history = history + entry
        balanceCents -= amountCents
        screen = Screen.TRANSFER_SUCCESS
    }

    fun cancelOtp() {
        otpDigits = ""
        screen = Screen.TRANSFER_CONFIRM
    }

    fun backHomeFromSuccess() {
        // Reset transient intent fields, keep updated balance + history.
        selectedRecipient = null
        amountCents = 0
        memo = ""
        otpDigits = ""
        transferError = null
        screen = Screen.HOME
    }

    fun backHomeFromHistory() {
        screen = Screen.HOME
    }

    /** Reset all FSM-visible state plus inert toggles to seed defaults. */
    fun resetDemo() {
        screen = Screen.HOME
        selectedRecipient = null
        amountCents = 0
        memo = ""
        otpDigits = ""
        balanceCents = Account.balanceCents
        dailyLimitCents = Account.dailyLimitCents
        transferError = null
        history = History.seed.toList()
        biometricUnlock = false
        transferNotifications = false
    }

    /** Deterministic system-back routing. Returns true iff consumed. */
    fun handleBack(): Boolean {
        val target = when (screen) {
            Screen.HOME -> return false
            Screen.RECIPIENTS -> Screen.HOME
            Screen.TRANSFER_FORM -> Screen.RECIPIENTS
            Screen.TRANSFER_CONFIRM -> Screen.TRANSFER_FORM
            Screen.OTP_CONFIRM -> Screen.TRANSFER_CONFIRM
            Screen.TRANSFER_SUCCESS -> Screen.HOME
            Screen.HISTORY -> Screen.HOME
            Screen.SETTINGS -> Screen.HOME
        }
        screen = target
        return true
    }
}
