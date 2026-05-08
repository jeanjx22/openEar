package com.example.openeartodo

import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
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
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class SettingsActivity : AppCompatActivity() {

    private lateinit var etApiKey: EditText
    private lateinit var etLookback: EditText
    private lateinit var spinnerProvider: Spinner
    private lateinit var tvGmailAccount: TextView
    private lateinit var tvLastSync: TextView
    private lateinit var btnGmail: Button

    private val providerNames = arrayOf("Cohere", "Groq")
    private val providerKeys = arrayOf("cohere", "groq")

    private val signInLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        try {
            val account = GoogleSignIn.getSignedInAccountFromIntent(result.data)
                .getResult(ApiException::class.java)
            val email = account.email ?: return@registerForActivityResult
            Prefs.setGmailAccount(this, email)
            updateGmailUI(email)
            Toast.makeText(this, "Signed in as $email", Toast.LENGTH_SHORT).show()
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
        tvGmailAccount = findViewById(R.id.tvGmailAccount)
        tvLastSync = findViewById(R.id.tvLastSync)
        btnGmail = findViewById(R.id.btnGmail)

        spinnerProvider.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, providerNames)

        etApiKey.setText(Prefs.getLlmApiKey(this))
        etLookback.setText(Prefs.getLookbackOverride(this))
        val savedProvider = Prefs.getLlmProvider(this)
        spinnerProvider.setSelection(providerKeys.indexOf(savedProvider).coerceAtLeast(0))

        val gmailAccount = Prefs.getGmailAccount(this)
        updateGmailUI(gmailAccount)
        updateLastSyncUI()

        btnGmail.setOnClickListener {
            if (Prefs.getGmailAccount(this) != null) {
                signOut()
            } else {
                startGmailSignIn()
            }
        }

        findViewById<Button>(R.id.btnSave).setOnClickListener {
            Prefs.setLlmApiKey(this, etApiKey.text.toString().trim())
            Prefs.setLlmProvider(this, providerKeys[spinnerProvider.selectedItemPosition])
            Prefs.setLookbackOverride(this, etLookback.text.toString().trim())
            Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
            finish()
        }
    }

    private fun updateGmailUI(email: String?) {
        if (email != null) {
            tvGmailAccount.text = email
            btnGmail.text = "Sign Out"
        } else {
            tvGmailAccount.text = "Not signed in"
            btnGmail.text = "Sign In"
        }
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
        val gso = GoogleSignInOptions.Builder(GoogleSignInOptions.DEFAULT_SIGN_IN)
            .requestEmail()
            .requestScopes(Scope("https://www.googleapis.com/auth/gmail.readonly"))
            .build()
        signInLauncher.launch(GoogleSignIn.getClient(this, gso).signInIntent)
    }

    private fun signOut() {
        val gso = GoogleSignInOptions.Builder(GoogleSignInOptions.DEFAULT_SIGN_IN)
            .requestEmail()
            .build()
        GoogleSignIn.getClient(this, gso).signOut().addOnCompleteListener {
            Prefs.setGmailAccount(this, null)
            updateGmailUI(null)
            Toast.makeText(this, "Signed out", Toast.LENGTH_SHORT).show()
        }
    }
}
