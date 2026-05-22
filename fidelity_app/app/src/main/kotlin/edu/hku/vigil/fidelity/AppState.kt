package edu.hku.vigil.fidelity

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import edu.hku.vigil.fidelity.data.Address
import edu.hku.vigil.fidelity.data.Product

enum class Screen(val id: String) {
    HOME("home"),
    SEARCH("search"),
    CATALOG("catalog"),
    PRODUCT_DETAIL("product_detail"),
    CART_EMPTY("cart_empty"),
    CART("cart"),
    ADDRESS_SELECT("address_select"),
    PAYMENT_CONFIRM("payment_confirm"),
    PAYMENT_SUCCESS("payment_success"),
    ORDERS("orders"),
    SETTINGS("settings"),
}

class AppState {
    var screen by mutableStateOf(Screen.HOME)
        private set

    var selectedProduct by mutableStateOf<Product?>(null)
        private set
    var quantity by mutableStateOf(1)
        private set
    var selectedAddress by mutableStateOf<Address?>(null)
        private set
    var searchQuery by mutableStateOf("")
        private set

    var removeDialogOpen by mutableStateOf(false)
        private set
    var paymentDialogOpen by mutableStateOf(false)
        private set

    var highContrast by mutableStateOf(false)
    var confirmPayments by mutableStateOf(false)

    fun navigate(target: Screen) {
        screen = target
    }

    fun updateSearchQuery(q: String) {
        searchQuery = q
    }

    fun openProductDetail(p: Product) {
        selectedProduct = p
        screen = Screen.PRODUCT_DETAIL
    }

    fun addToCart() {
        if (selectedProduct == null) return
        if (quantity < 1) quantity = 1
        screen = Screen.CART
    }

    fun incQty() { if (quantity < 9) quantity += 1 }
    fun decQty() { if (quantity > 1) quantity -= 1 }

    fun openCart() {
        screen = if (selectedProduct == null) Screen.CART_EMPTY else Screen.CART
    }

    fun openRemoveDialog() { removeDialogOpen = true }
    fun closeRemoveDialog() { removeDialogOpen = false }
    fun confirmRemove() {
        removeDialogOpen = false
        selectedProduct = null
        quantity = 1
        screen = Screen.CART_EMPTY
    }

    fun startCheckout() {
        if (selectedProduct == null) return
        screen = Screen.ADDRESS_SELECT
    }

    fun selectAddress(a: Address) {
        selectedAddress = a
        screen = Screen.PAYMENT_CONFIRM
    }

    fun openPaymentDialog() { paymentDialogOpen = true }
    fun closePaymentDialog() { paymentDialogOpen = false }
    fun confirmPayment() {
        paymentDialogOpen = false
        screen = Screen.PAYMENT_SUCCESS
    }

    fun totalCents(): Int = (selectedProduct?.priceCents ?: 0) * quantity

    /**
     * Resets every piece of FSM-visible state plus the inert settings toggles,
     * so a fresh launch and an in-app reset produce indistinguishable UI fingerprints.
     */
    fun resetDemo() {
        screen = Screen.HOME
        selectedProduct = null
        quantity = 1
        selectedAddress = null
        searchQuery = ""
        removeDialogOpen = false
        paymentDialogOpen = false
        highContrast = false
        confirmPayments = false
    }

    /**
     * Deterministic system-back routing. Returns true if the back press was consumed.
     */
    fun handleBack(): Boolean {
        if (removeDialogOpen) { removeDialogOpen = false; return true }
        if (paymentDialogOpen) { paymentDialogOpen = false; return true }
        val target = when (screen) {
            Screen.HOME -> return false
            Screen.SEARCH -> Screen.HOME
            Screen.CATALOG -> Screen.HOME
            Screen.PRODUCT_DETAIL -> Screen.CATALOG
            Screen.CART_EMPTY -> Screen.HOME
            Screen.CART -> Screen.HOME
            Screen.ADDRESS_SELECT -> Screen.CART
            Screen.PAYMENT_CONFIRM -> Screen.ADDRESS_SELECT
            Screen.PAYMENT_SUCCESS -> Screen.HOME
            Screen.ORDERS -> Screen.HOME
            Screen.SETTINGS -> Screen.HOME
        }
        screen = target
        return true
    }
}
