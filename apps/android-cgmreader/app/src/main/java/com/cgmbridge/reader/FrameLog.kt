package com.cgmbridge.reader

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStreamWriter
import java.nio.charset.StandardCharsets
import java.util.Collections
import java.util.concurrent.Executors

/**
 * FrameLog: 设备端 BLE 帧记录器（应用私有目录 ble_frames.log + Logcat）。
 * 与 iOS CGMReader v0 的帧记录与日志格式完全语义对齐。
 */
object FrameLog {
    private const val TAG = "CGMReader"
    private const val LOG_FILENAME = "ble_frames.log"
    private const val MAX_EVENTS = 200

    private val executor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val events = Collections.synchronizedList(ArrayList<String>())
    private var eventListener: ((String) -> Unit)? = null

    private var logFile: File? = null
    private var outputStream: FileOutputStream? = null
    private var writer: OutputStreamWriter? = null

    @Synchronized
    fun init(context: Context) {
        if (logFile != null) return
        val targetDir = context.filesDir
        if (!targetDir.exists()) {
            targetDir.mkdirs()
        }
        val file = File(targetDir, LOG_FILENAME)
        if (!file.exists()) {
            file.createNewFile()
        }
        logFile = file
        val stream = FileOutputStream(file, true)
        outputStream = stream
        writer = OutputStreamWriter(stream, StandardCharsets.UTF_8)
        Log.i(TAG, "FrameLog initialized at: ${file.absolutePath}")
    }

    fun getLogPath(): String {
        return logFile?.absolutePath ?: "uninitialized"
    }

    fun getLogFile(): File? = logFile

    fun setEventListener(listener: ((String) -> Unit)?) {
        this.eventListener = listener
    }

    fun getRecentEvents(): List<String> {
        synchronized(events) {
            return ArrayList(events)
        }
    }

    fun log(line: String) {
        val timestamp = System.currentTimeMillis()
        val entry = "$timestamp $line\n"

        // 1. Android Logcat
        Log.i(TAG, line)

        // 2. In-memory circular buffer for UI
        synchronized(events) {
            events.add(line)
            while (events.size > MAX_EVENTS) {
                events.removeAt(0)
            }
        }

        // 3. Notify UI listener on main thread
        mainHandler.post {
            eventListener?.invoke(line)
        }

        // 4. Asynchronously append to ble_frames.log in app private storage
        executor.execute {
            try {
                writer?.apply {
                    write(entry)
                    flush()
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed writing to ble_frames.log: ${e.message}", e)
            }
        }
    }

    fun hex(data: ByteArray): String {
        return data.joinToString(" ") { "%02x".format(it) }
    }

    fun parseHex(hexString: String): ByteArray? {
        val cleaned = hexString.replace(" ", "").trim()
        if (cleaned.isEmpty() || cleaned.length % 2 != 0) return null
        return try {
            val len = cleaned.length
            val result = ByteArray(len / 2)
            for (i in 0 until len step 2) {
                result[i / 2] = cleaned.substring(i, i + 2).toInt(16).toByte()
            }
            result
        } catch (_: NumberFormatException) {
            null
        }
    }
}
