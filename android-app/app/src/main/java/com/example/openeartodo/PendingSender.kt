package com.example.openeartodo

import androidx.lifecycle.LiveData
import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query

@Entity(tableName = "pending_sender")
data class PendingSender(
    @PrimaryKey val pattern: String,
    val displayName: String,
    val sampleSubject: String,
    val sampleTodos: String,
    val sampleGmailId: String?,
    val createdAt: Long = System.currentTimeMillis()
)

@Dao
interface PendingSenderDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(sender: PendingSender)

    @Query("SELECT * FROM pending_sender ORDER BY createdAt DESC")
    fun getAll(): LiveData<List<PendingSender>>

    @Query("SELECT COUNT(*) FROM pending_sender")
    suspend fun count(): Int

    @Delete
    suspend fun delete(sender: PendingSender)

    @Query("DELETE FROM pending_sender WHERE pattern = :pattern")
    suspend fun deleteByPattern(pattern: String)
}
