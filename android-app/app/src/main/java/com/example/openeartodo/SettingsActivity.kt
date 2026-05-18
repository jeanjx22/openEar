package com.example.openeartodo

import android.os.Bundle
import android.view.LayoutInflater
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import com.google.android.gms.auth.api.signin.GoogleSignIn
import com.google.android.gms.auth.api.signin.GoogleSignInOptions
import com.google.android.gms.common.api.ApiException
import com.google.android.gms.common.api.Scope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class SettingsActivity : AppCompatActivity() {

    private lateinit var etApiKey: EditText
    private lateinit var etLookback: EditText
    private lateinit var spinnerProvider: Spinner
    private lateinit var accountsList: LinearLayout
    private lateinit var tvLastSync: TextView
    private lateinit var btnAddAccount: Button

    private val providerNames = arrayOf("Cohere", "Groq")
    private val providerKeys = arrayOf("cohere", "groq")

    private val signInLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        try {
            val account = GoogleSignIn.getSignedInAccountFromIntent(result.data)
                .getResult(ApiException::class.java)
            val email = account.email ?: return@registerForActivityResult
            Prefs.addGmailAccount(this, email)
            refreshAccountsUI()
            Toast.makeText(this, "Added $email", Toast.LENGTH_SHORT).show()
        } catch (e: ApiException) {
            Toast.makeText(this, "Sign-in failed: ${e.statusCode}", Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        val toolbar: Toolbar = findViewById(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        toolbar.setNavigationOnClickListener { finish() }

        etApiKey = findViewById(R.id.etApiKey)
        etLookback = findViewById(R.id.etLookback)
        spinnerProvider = findViewById(R.id.spinnerProvider)
        accountsList = findViewById(R.id.accountsList)
        tvLastSync = findViewById(R.id.tvLastSync)
        btnAddAccount = findViewById(R.id.btnAddAccount)

        spinnerProvider.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, providerNames)

        etApiKey.setText(Prefs.getLlmApiKey(this))
        etLookback.setText(Prefs.getLookbackOverride(this))
        val savedProvider = Prefs.getLlmProvider(this)
        spinnerProvider.setSelection(providerKeys.indexOf(savedProvider).coerceAtLeast(0))

        refreshAccountsUI()
        updateLastSyncUI()

        btnAddAccount.setOnClickListener { startGmailSignIn() }

        findViewById<Button>(R.id.btnSave).setOnClickListener {
            Prefs.setLlmApiKey(this, etApiKey.text.toString().trim())
            Prefs.setLlmProvider(this, providerKeys[spinnerProvider.selectedItemPosition])
            Prefs.setLookbackOverride(this, etLookback.text.toString().trim())
            Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
            finish()
        }
    }

    private fun refreshAccountsUI() {
        accountsList.removeAllViews()
        val accounts = Prefs.getGmailAccounts(this)
        if (accounts.isEmpty()) {
            val tv = TextView(this).apply {
                text = "No accounts. Tap \"Add Account\" to sign in."
                setPadding(0, 8, 0, 8)
            }
            accountsList.addView(tv)
            return
        }
        for (email in accounts) {
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(0, 8, 0, 8)
            }
            val emailView = TextView(this).apply {
                text = email
                textSize = 15f
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                    gravity = android.view.Gravity.CENTER_VERTICAL
                }
            }
            val removeBtn = ImageButton(this).apply {
                setImageResource(android.R.drawable.ic_menu_delete)
                background = null
                contentDescription = "Remove account"
                setOnClickListener { confirmRemoveAccount(email) }
            }
            row.addView(emailView)
            row.addView(removeBtn)
            accountsList.addView(row)
        }
    }

    private fun confirmRemoveAccount(email: String) {
        MaterialAlertDialogBuilder(this)
            .setTitle("Remove account?")
            .setMessage("Stop syncing emails from $email?")
            .setPositiveButton("Remove") { _, _ ->
                Prefs.removeGmailAccount(this, email)
                refreshAccountsUI()
                Toast.makeText(this, "Removed $email", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun updateLastSyncUI() {
        val lastSync = Prefs.getLastSyncTime(this)
        if (lastSync > 0) {
            val fmt = SimpleDateFormat("MMM d, yyyy h:mm a", Locale.getDefault())
            tvLastSync.text = "Last sync: ${fmt.format(Date(lastSync))}"
        } else {
            tvLastSync.text = "Never synced"
        }
    }

    private fun startGmailSignIn() {
        // Sign out current Google client first to force the account picker
        val gso = GoogleSignInOptions.Builder(GoogleSignInOptions.DEFAULT_SIGN_IN)
            .requestEmail()
            .requestScopes(Scope("https://www.googleapis.com/auth/gmail.readonly"))
            .build()
        val client = GoogleSignIn.getClient(this, gso)
        client.signOut().addOnCompleteListener {
            signInLauncher.launch(client.signInIntent)
        }
    }
}
