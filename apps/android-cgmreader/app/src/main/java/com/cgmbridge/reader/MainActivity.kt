package com.cgmbridge.reader

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * MainActivity: CGMReader 单 Activity 界面。
 * 提供 BLE 扫描/连接/断开控制、目标设备名动态配置、手动十六进制指令发送及实时帧日志展示。
 */
class MainActivity : Activity() {
    companion object {
        private const val PERMISSION_REQUEST_CODE = 1001
    }

    private lateinit var bleScanner: BleScanner
    private lateinit var statusTextView: TextView
    private lateinit var logPathTextView: TextView
    private lateinit var targetNameEditText: EditText
    private lateinit var hexInputEditText: EditText
    private lateinit var eventsTextView: TextView
    private lateinit var eventsScrollView: ScrollView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 1. Eager Initialization: 确保在任何 BLE 操作之前完成日志文件创建与打开
        // 与 iOS 端 CGMReader openLog-before-centralManager 经验完全对齐
        FrameLog.init(applicationContext)

        // 2. 初始化 BLE 扫描与通信控制器
        bleScanner = BleScanner(
            context = applicationContext,
            targetBleName = BuildConfig.TARGET_BLE_NAME,
            onStatusChange = { status ->
                updateStatus(status)
            }
        )

        // 3. 构建原生无 XML 依赖的流式 UI 布局
        setContentView(buildContentView())

        // 4. 注册 FrameLog UI 监听器以实时回显日志
        FrameLog.setEventListener { line ->
            appendLogToUi(line)
        }

        // 5. 填充已有历史日志
        for (event in FrameLog.getRecentEvents()) {
            appendLogToUi(event)
        }

        // 6. 检查并请求蓝牙与定位运行时权限
        checkAndRequestPermissions()
    }

    private fun dpToPx(dp: Int): Int {
        return TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP,
            dp.toFloat(),
            resources.displayMetrics
        ).toInt()
    }

    private fun buildContentView(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#121212"))
            setPadding(dpToPx(16), dpToPx(16), dpToPx(16), dpToPx(16))
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        }

        // 标题区
        val titleView = TextView(this).apply {
            text = "CGMReader"
            textSize = 22f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.WHITE)
        }
        root.addView(titleView)

        val subtitleView = TextView(this).apply {
            text = "SiSensing ECO CGM Standalone Reader (v0)"
            textSize = 12f
            setTextColor(Color.parseColor("#AAAAAA"))
            setPadding(0, 0, 0, dpToPx(8))
        }
        root.addView(subtitleView)

        // 状态与日志路径指示
        statusTextView = TextView(this).apply {
            text = "Status: init"
            textSize = 14f
            setTypeface(Typeface.MONOSPACE, Typeface.BOLD)
            setTextColor(Color.parseColor("#64B5F6"))
            setPadding(0, dpToPx(4), 0, dpToPx(2))
        }
        root.addView(statusTextView)

        logPathTextView = TextView(this).apply {
            text = "Log: ${FrameLog.getLogPath()}"
            textSize = 11f
            setTypeface(Typeface.MONOSPACE, Typeface.NORMAL)
            setTextColor(Color.parseColor("#888888"))
            setPadding(0, 0, 0, dpToPx(8))
        }
        root.addView(logPathTextView)

        // 目标设备名配置栏
        val nameRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 0, 0, dpToPx(8))
        }
        val nameLabel = TextView(this).apply {
            text = "Target BLE: "
            textSize = 13f
            setTextColor(Color.LTGRAY)
            gravity = Gravity.CENTER_VERTICAL
        }
        nameRow.addView(nameLabel)

        targetNameEditText = EditText(this).apply {
            setText(BuildConfig.TARGET_BLE_NAME)
            hint = "<BLE_NAME>"
            setHintTextColor(Color.DKGRAY)
            textSize = 13f
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.parseColor("#1E1E1E"))
            setPadding(dpToPx(8), dpToPx(4), dpToPx(8), dpToPx(4))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.0f)
            inputType = InputType.TYPE_CLASS_TEXT
        }
        nameRow.addView(targetNameEditText)
        root.addView(nameRow)

        // 动作按钮栏
        val btnRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 0, 0, dpToPx(8))
        }

        val btnStart = Button(this).apply {
            text = "Scan"
            textSize = 12f
            setOnClickListener {
                val inputName = targetNameEditText.text.toString().trim()
                bleScanner.targetBleName = inputName
                bleScanner.startScan()
            }
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.0f)
        }
        btnRow.addView(btnStart)

        val btnStop = Button(this).apply {
            text = "Stop"
            textSize = 12f
            setOnClickListener {
                bleScanner.stopScan()
            }
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.0f)
        }
        btnRow.addView(btnStop)

        val btnDisconnect = Button(this).apply {
            text = "Disconnect"
            textSize = 12f
            setOnClickListener {
                bleScanner.disconnect()
            }
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.0f)
        }
        btnRow.addView(btnDisconnect)

        root.addView(btnRow)

        // 手动 Hex 写入测试栏 (v1 协议就绪前用于实验)
        val sendRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 0, 0, dpToPx(8))
        }

        hexInputEditText = EditText(this).apply {
            hint = "Hex cmd (e.g. 01 00)"
            setHintTextColor(Color.DKGRAY)
            textSize = 12f
            setTypeface(Typeface.MONOSPACE, Typeface.NORMAL)
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.parseColor("#1E1E1E"))
            setPadding(dpToPx(8), dpToPx(4), dpToPx(8), dpToPx(4))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.0f)
        }
        sendRow.addView(hexInputEditText)

        val btnSend = Button(this).apply {
            text = "Send"
            textSize = 12f
            setOnClickListener {
                val hexStr = hexInputEditText.text.toString().trim()
                if (hexStr.isNotEmpty()) {
                    bleScanner.send(hexStr)
                }
            }
        }
        sendRow.addView(btnSend)
        root.addView(sendRow)

        // 实时帧日志控制台
        eventsScrollView = ScrollView(this).apply {
            setBackgroundColor(Color.parseColor("#0A0A0A"))
            setPadding(dpToPx(8), dpToPx(8), dpToPx(8), dpToPx(8))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1.0f
            )
        }

        eventsTextView = TextView(this).apply {
            textSize = 11f
            setTypeface(Typeface.MONOSPACE, Typeface.NORMAL)
            setTextColor(Color.parseColor("#00E676"))
            setLineSpacing(0f, 1.2f)
        }
        eventsScrollView.addView(eventsTextView)
        root.addView(eventsScrollView)

        return root
    }

    private fun updateStatus(status: String) {
        runOnUiThread {
            statusTextView.text = "Status: $status"
        }
    }

    private fun appendLogToUi(line: String) {
        runOnUiThread {
            eventsTextView.append("$line\n")
            eventsScrollView.post {
                eventsScrollView.fullScroll(View.FOCUS_DOWN)
            }
        }
    }

    private fun getRequiredPermissions(): Array<String> {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(
                Manifest.permission.BLUETOOTH_SCAN,
                Manifest.permission.BLUETOOTH_CONNECT
            )
        } else {
            arrayOf(
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION
            )
        }
    }

    private fun checkAndRequestPermissions() {
        val missing = getRequiredPermissions().filter {
            checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
        }

        if (missing.isNotEmpty()) {
            requestPermissions(missing.toTypedArray(), PERMISSION_REQUEST_CODE)
        } else {
            onPermissionsGranted()
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSION_REQUEST_CODE) {
            val allGranted = grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }
            if (allGranted) {
                FrameLog.log("Permissions granted, starting BLE operations")
                onPermissionsGranted()
            } else {
                FrameLog.log("Permissions denied by user")
                updateStatus("permission denied")
            }
        }
    }

    private fun onPermissionsGranted() {
        updateStatus("permissions granted")
        val inputName = targetNameEditText.text.toString().trim()
        bleScanner.targetBleName = inputName
        bleScanner.startScan()
    }

    override fun onDestroy() {
        super.onDestroy()
        FrameLog.setEventListener(null)
        bleScanner.stopScan()
        bleScanner.disconnect()
    }
}
