package com.example.openeartodo

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query

@Entity(tableName = "ignored_sender")
data class IgnoredSender(
    @PrimaryKey val pattern: String,
    val createdAt: Long = System.currentTimeMillis()
)

@Dao
interface IgnoredSenderDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(sender: IgnoredSender)

    @Query("SELECT pattern FROM ignored_sender")
    suspend fun getAllPatterns(): List<String>

    @Query("DELETE FROM ignored_sender WHERE pattern = :pattern")
    suspend fun delete(pattern: String)
}
