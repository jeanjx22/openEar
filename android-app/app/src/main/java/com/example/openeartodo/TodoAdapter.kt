package com.example.openeartodo

import android.content.Intent
import android.net.Uri
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

class TodoAdapter(
    private val items: MutableList<TodoItem>,
    private val onCheckedChanged: (TodoItem, Boolean) -> Unit
) : RecyclerView.Adapter<TodoAdapter.ViewHolder>() {

    inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        val cbComplete: CheckBox = itemView.findViewById(R.id.cbComplete)
        val tvText: TextView = itemView.findViewById(R.id.tvTodoText)
        val ivEmail: ImageView = itemView.findViewById(R.id.ivEmail)
    }

    fun setItems(newItems: List<TodoItem>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return ViewHolder(inflater.inflate(R.layout.item_todo, parent, false))
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = items[position]
        holder.tvText.text = item.text
        holder.cbComplete.setOnCheckedChangeListener(null)
        holder.cbComplete.isChecked = item.isCompleted
        holder.cbComplete.setOnCheckedChangeListener { _, isChecked ->
            onCheckedChanged(item, isChecked)
        }

        if (item.sourceGmailId != null) {
            holder.ivEmail.visibility = View.VISIBLE
            holder.ivEmail.setOnClickListener {
                val url = "https://mail.google.com/mail/u/0/#all/${item.sourceGmailId}"
                it.context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
            }
        } else {
            holder.ivEmail.visibility = View.GONE
            holder.ivEmail.setOnClickListener(null)
        }
    }

    override fun getItemCount() = items.size
}
