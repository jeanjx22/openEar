package com.example.openeartodo

import android.app.Activity
import android.os.Bundle
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.gms.auth.UserRecoverableAuthException
import com.google.android.gms.auth.api.signin.GoogleSignIn
import com.google.android.gms.auth.api.signin.GoogleSignInOptions
import com.google.android.gms.common.api.ApiException
import com.google.android.gms.common.api.Scope
import kotlinx.coroutines.launch

class EmailSelectorActivity : AppCompatActivity() {

    private lateinit var emailAdapter: EmailAdapter
    private lateinit var btnProcess: Button
    private lateinit var progress: ProgressBar
    private lateinit var etSearch: EditText
    private var accessToken: String? = null
    private var nextPageToken: String? = null
    private var isLoading = false
    private var currentQuery: String? = null

    private val signInLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        try {
            val account = GoogleSignIn.getSignedInAccountFromIntent(result.data)
                .getResult(ApiException::class.java)
            Prefs.setGmailAccount(this, account.email ?: "")
            loadEmails(reset = true)
        } catch (e: ApiException) {
            Toast.makeText(this, "Sign-in failed: ${e.statusCode}", Toast.LENGTH_LONG).show()
        }
    }

    private val consentLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { loadEmails(reset = true) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_email_selector)

        val toolbar: Toolbar = findViewById(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = "Select Emails"
        toolbar.setNavigationOnClickListener { finish() }

        val layoutManager = LinearLayoutManager(this)
        val recycler: RecyclerView = findViewById(R.id.emailList)
        recycler.layoutManager = layoutManager
        emailAdapter = EmailAdapter()
        recycler.adapter = emailAdapter

        recycler.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrolled(rv: RecyclerView, dx: Int, dy: Int) {
                if (isLoading || nextPageToken == null) return
                val total = layoutManager.itemCount
                val lastVisible = layoutManager.findLastVisibleItemPosition()
                if (lastVisible >= total - 5) {
                    loadEmails(reset = false)
                }
            }
        })

        btnProcess = findViewById(R.id.btnProcess)
        progress = findViewById(R.id.progress)
        etSearch = findViewById(R.id.etSearch)

        etSearch.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEARCH) {
                val query = etSearch.text.toString().trim()
                currentQuery = query.ifEmpty { null }
                loadEmails(reset = true)
                true
            } else false
        }

        btnProcess.setOnClickListener { watchAndProcess() }

        val account = Prefs.getGmailAccount(this)
        if (account != null) {
            loadEmails(reset = true)
        } else {
            startSignIn()
        }
    }

    private fun startSignIn() {
        val gso = GoogleSignInOptions.Builder(GoogleSignInOptions.DEFAULT_SIGN_IN)
            .requestEmail()
            .requestScopes(Scope("https://www.googleapis.com/auth/gmail.readonly"))
            .build()
        signInLauncher.launch(GoogleSignIn.getClient(this, gso).signInIntent)
    }

    private fun loadEmails(reset: Boolean) {
        val account = Prefs.getGmailAccount(this)
        if (account == null) {
            startSignIn()
            return
        }

        if (isLoading) return
        isLoading = true
        progress.visibility = View.VISIBLE

        val pageToken = if (reset) null else nextPageToken

        lifecycleScope.launch {
            try {
                accessToken = GmailClient.getAccessToken(this@EmailSelectorActivity, account)
                val page = GmailClient.fetchEmails(
                    accessToken!!, query = currentQuery, pageToken = pageToken
                )
                nextPageToken = page.nextPageToken

                if (reset) {
                    emailAdapter.setItems(page.emails)
                } else {
                    emailAdapter.appendItems(page.emails)
                }

                if (reset && page.emails.isEmpty()) {
                    Toast.makeText(this@EmailSelectorActivity, "No emails found", Toast.LENGTH_SHORT).show()
                }
            } catch (e: UserRecoverableAuthException) {
                consentLauncher.launch(e.intent)
            } catch (e: Exception) {
                Toast.makeText(
                    this@EmailSelectorActivity,
                    "Failed to load emails: ${e.message}",
                    Toast.LENGTH_LONG
                ).show()
            } finally {
                isLoading = false
                progress.visibility = View.GONE
            }
        }
    }

    private fun watchAndProcess() {
        val selected = emailAdapter.getSelectedItems()
        if (selected.isEmpty()) {
            Toast.makeText(this, "Select at least one email", Toast.LENGTH_SHORT).show()
            return
        }

        val apiKey = Prefs.getLlmApiKey(this)
        if (apiKey.isBlank()) {
            Toast.makeText(this, "Set your LLM API key in Settings first", Toast.LENGTH_LONG).show()
            return
        }

        val token = accessToken ?: run {
            Toast.makeText(this, "Gmail not connected", Toast.LENGTH_SHORT).show()
            return
        }

        btnProcess.isEnabled = false
        btnProcess.text = "Processing..."
        progress.visibility = View.VISIBLE

        val provider = Prefs.getLlmProvider(this)

        lifecycleScope.launch {
            try {
                // Refresh Gmail token in case it expired while browsing
                val account = Prefs.getGmailAccount(this@EmailSelectorActivity)!!
                val freshToken = GmailClient.getAccessToken(this@EmailSelectorActivity, account)
                accessToken = freshToken

                val db = TodoDatabase.getInstance(applicationContext)

                val senderPatterns = selected.map { EmailProcessor.extractSenderPattern(it.sender) }.toSet()
                for (pattern in senderPatterns) {
                    db.allowedSenderDao().insert(AllowedSender(pattern = pattern, label = pattern))
                }

                var todoCount = 0
                for (email in selected) {
                    val body = try {
                        GmailClient.fetchEmailBody(freshToken, email.gmailId)
                    } catch (e: Exception) {
                        throw Exception("Gmail fetch failed: ${e.message}", e)
                    }
                    val result = try {
                        LlmClient.extractTodosWithSummary(apiKey, provider, email.sender, email.subject, body)
                    } catch (e: Exception) {
                        throw Exception("LLM call failed ($provider): ${e.message}", e)
                    }
                    val summary = "From: ${email.sender}\nSubject: ${email.subject}\n\n${result.summary}\n\n--- Full Email ---\n${body.take(5000)}"
                    for (extracted in result.todos) {
                        val eventAt = ReminderDefaults.parseEventTime(extracted.eventTime)
                        val todo = TodoItem(
                            text = extracted.text,
                            eventAt = eventAt,
                            reminderAt = eventAt?.let { ReminderDefaults.defaultNotificationTime(it) },
                            alarmAt = eventAt?.let { ReminderDefaults.defaultAlarmTime(it) },
                            reminderType = if (eventAt != null) "both" else null,
                            sourceGmailId = email.gmailId,
                            sourceRfc822Id = email.rfc822MsgId,
                            sourceEmailSummary = summary
                        )
                        db.todoDao().insert(todo)
                        if (eventAt != null) AlarmScheduler.schedule(applicationContext, todo)
                    }
                    db.processedEmailDao().insert(ProcessedEmail(gmailId = email.gmailId))
                    todoCount += result.todos.size
                }

                Toast.makeText(
                    this@EmailSelectorActivity,
                    "Watching ${senderPatterns.size} sender(s), extracted $todoCount todo(s)",
                    Toast.LENGTH_SHORT
                ).show()
                setResult(Activity.RESULT_OK)
                finish()
            } catch (e: Exception) {
                Toast.makeText(
                    this@EmailSelectorActivity,
                    "Failed: ${e.message}",
                    Toast.LENGTH_LONG
                ).show()
            } finally {
                btnProcess.isEnabled = true
                btnProcess.text = "Watch & Process"
                progress.visibility = View.GONE
            }
        }
    }
}
