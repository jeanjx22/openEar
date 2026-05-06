package com.example.openeartodo

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query

@Entity(tableName = "processed_email")
data class ProcessedEmail(
    @PrimaryKey val gmailId: String,
    val processedAt: Long = System.currentTimeMillis()
)

@Dao
interface ProcessedEmailDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(email: ProcessedEmail)

    @Query("SELECT EXISTS(SELECT 1 FROM processed_email WHERE gmailId = :gmailId)")
    suspend fun isProcessed(gmailId: String): Boolean
}
