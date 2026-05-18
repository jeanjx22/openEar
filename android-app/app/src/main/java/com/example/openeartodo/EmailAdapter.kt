package com.example.openeartodo

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

class EmailAdapter(
    private val items: MutableList<GmailClient.EmailInfo> = mutableListOf(),
    private val selectedIds: MutableSet<String> = mutableSetOf()
) : RecyclerView.Adapter<EmailAdapter.ViewHolder>() {

    inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        val cbSelect: CheckBox = itemView.findViewById(R.id.cbSelect)
        val tvSender: TextView = itemView.findViewById(R.id.tvSender)
        val tvAccount: TextView = itemView.findViewById(R.id.tvAccount)
        val tvSubject: TextView = itemView.findViewById(R.id.tvSubject)
        val tvSummary: TextView = itemView.findViewById(R.id.tvSummary)
    }

    fun setItems(newItems: List<GmailClient.EmailInfo>) {
        items.clear()
        items.addAll(newItems)
        selectedIds.clear()
        notifyDataSetChanged()
    }

    fun appendItems(newItems: List<GmailClient.EmailInfo>) {
        val start = items.size
        items.addAll(newItems)
        notifyItemRangeInserted(start, newItems.size)
    }

    fun getSelectedItems(): List<GmailClient.EmailInfo> =
        items.filter { it.gmailId in selectedIds }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_email, parent, false)
        return ViewHolder(view)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val email = items[position]

        val senderName = email.sender
            .substringBefore('<').trim()
            .ifEmpty { email.sender }
        holder.tvSender.text = senderName
        if (email.accountEmail != null) {
            holder.tvAccount.text = "✉️ ${email.accountEmail}"
            holder.tvAccount.visibility = View.VISIBLE
        } else {
            holder.tvAccount.visibility = View.GONE
        }
        holder.tvSubject.text = email.subject
        holder.tvSummary.text = email.snippet
        holder.tvSummary.visibility =
            if (email.snippet.isBlank()) View.GONE else View.VISIBLE

        holder.cbSelect.setOnCheckedChangeListener(null)
        holder.cbSelect.isChecked = email.gmailId in selectedIds
        holder.cbSelect.setOnCheckedChangeListener { _, isChecked ->
            if (isChecked) selectedIds.add(email.gmailId) else selectedIds.remove(email.gmailId)
        }

        holder.itemView.setOnClickListener {
            holder.cbSelect.isChecked = !holder.cbSelect.isChecked
        }
    }

    override fun getItemCount() = items.size
}
