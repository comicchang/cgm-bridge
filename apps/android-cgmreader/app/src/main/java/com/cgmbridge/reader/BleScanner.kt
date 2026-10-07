package com.cgmbridge.reader

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import java.util.UUID

/**
 * BleScanner: Android BLE 通信核心。
 * 实现设备发现、连接、服务发现、FF32 特征通知订阅以及帧日志记录。
 * 语义与 iOS CGMReader BLELogger 完全对齐。
 */
class BleScanner(
    private val context: Context,
    var targetBleName: String = BuildConfig.TARGET_BLE_NAME,
    private val onStatusChange: (String) -> Unit = {}
) {
    companion object {
        // SiSensing ECO CGM BLE 协议服务与特征 UUID 事实
        val SERVICE_FF30: UUID = UUID.fromString("0000ff30-0000-1000-8000-00805f9b34fb")
        val CHAR_FF31: UUID = UUID.fromString("0000ff31-0000-1000-8000-00805f9b34fb") // 写 (TX)
        val CHAR_FF32: UUID = UUID.fromString("0000ff32-0000-1000-8000-00805f9b34fb") // 通知 (RX)
        val SERVICE_180A: UUID = UUID.fromString("0000180a-0000-1000-8000-00805f9b34fb") // 设备信息
        val SERVICE_180F: UUID = UUID.fromString("0000180f-0000-1000-8000-00805f9b34fb") // 电池服务
        val CCCD_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private val bluetoothManager = context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
    private val bluetoothAdapter: BluetoothAdapter? = bluetoothManager?.adapter

    private var isScanning = false
    private var connectedGatt: BluetoothGatt? = null
    private var writeChar: BluetoothGattCharacteristic? = null
    private var notifyChar: BluetoothGattCharacteristic? = null

    var currentStatus: String = "init"
        private set(value) {
            field = value
            mainHandler.post { onStatusChange(value) }
        }

    private val scanCallback = object : ScanCallback() {
        @SuppressLint("MissingPermission")
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val device = result.device
            val record = result.scanRecord
            val advName = record?.deviceName
            val devName = device.name ?: advName ?: "?"
            val svcList = record?.serviceUuids?.joinToString(",") { it.uuid.toString() } ?: "-"

            FrameLog.log("DISCOVER id=${device.address} name=$devName rssi=${result.rssi} svc=$svcList")

            // 目标匹配逻辑：名称匹配且当前无连接
            if (devName == targetBleName && connectedGatt == null) {
                FrameLog.log("CONNECT $devName")
                currentStatus = "connecting"
                stopScan()
                connect(device)
            }
        }

        override fun onScanFailed(errorCode: Int) {
            currentStatus = "scan failed ($errorCode)"
            FrameLog.log("SCAN failed: errorCode=$errorCode")
            isScanning = false
        }
    }

    private val gattCallback = object : BluetoothGattCallback() {
        @SuppressLint("MissingPermission")
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    currentStatus = "discovering"
                    FrameLog.log("CONNECTED ${gatt.device.address}")
                    // 请求提高连接优先级并开始服务发现
                    gatt.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)
                    gatt.discoverServices()
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    currentStatus = "disconnected, rescanning"
                    FrameLog.log("DISCONNECT status=$status")
                    cleanUpConnection()
                    // 重新开启扫描（与 iOS 行为对齐）
                    startScan()
                }
            }
        }

        @SuppressLint("MissingPermission")
        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                currentStatus = "svc-disc-fail"
                FrameLog.log("SVC-DISC-FAIL status=$status")
                return
            }

            for (service in gatt.services) {
                FrameLog.log("SVC ${service.uuid}")
                for (chr in service.characteristics) {
                    val props = chr.properties
                    val propStr = buildString {
                        if (props and BluetoothGattCharacteristic.PROPERTY_WRITE != 0) append("W")
                        if (props and BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE != 0) append("w")
                        if (props and BluetoothGattCharacteristic.PROPERTY_NOTIFY != 0) append("N")
                        if (props and BluetoothGattCharacteristic.PROPERTY_READ != 0) append("R")
                    }
                    FrameLog.log("CHR ${service.uuid}/${chr.uuid} props=$propStr")

                    if (service.uuid == SERVICE_FF30 && chr.uuid == CHAR_FF31 && writeChar == null) {
                        writeChar = chr
                    }

                    if (service.uuid == SERVICE_FF30 && chr.uuid == CHAR_FF32 && notifyChar == null) {
                        notifyChar = chr
                        // 1. 本地通知开关
                        gatt.setCharacteristicNotification(chr, true)
                        // 2. 写入 CCCD (0x2902) 描述符以开启固件推送
                        val cccd = chr.getDescriptor(CCCD_UUID)
                        if (cccd != null) {
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                                gatt.writeDescriptor(cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
                            } else {
                                @Suppress("DEPRECATION")
                                cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                                @Suppress("DEPRECATION")
                                gatt.writeDescriptor(cccd)
                            }
                        }
                        FrameLog.log("NOTIFY on FF32")
                    }
                }
            }

            if (writeChar != null && notifyChar != null) {
                currentStatus = "ready (v0 logger)"
                FrameLog.log("READY write=${writeChar?.uuid} notify=${notifyChar?.uuid}")
            }
        }

        override fun onDescriptorWrite(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            if (descriptor.characteristic.uuid == CHAR_FF32) {
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    FrameLog.log("NOTIFY-CONFIRMED on FF32")
                } else {
                    FrameLog.log("NOTIFY-FAIL on FF32 status=$status")
                }
            }
        }

        // Android 13+ (API 33+) 回调
        override fun onCharacteristicChanged(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray
        ) {
            FrameLog.log("RX ${characteristic.uuid} len=${value.size} ${FrameLog.hex(value)}")
        }

        // Android 12 及以下兼容回调
        @Deprecated("Deprecated in Java")
        override fun onCharacteristicChanged(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic
        ) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
                @Suppress("DEPRECATION")
                val value = characteristic.value ?: return
                FrameLog.log("RX ${characteristic.uuid} len=${value.size} ${FrameLog.hex(value)}")
            }
        }

        override fun onCharacteristicWrite(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int
        ) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                FrameLog.log("TX-OK ${characteristic.uuid}")
            } else {
                FrameLog.log("TX-ERR ${characteristic.uuid} status=$status")
            }
        }
    }

    @SuppressLint("MissingPermission")
    fun startScan() {
        if (bluetoothAdapter == null || !bluetoothAdapter.isEnabled) {
            currentStatus = "bt off"
            FrameLog.log("SCAN aborted: bluetooth poweredOff or unavailable")
            return
        }

        val scanner = bluetoothAdapter.bluetoothLeScanner
        if (scanner == null) {
            currentStatus = "scanner null"
            FrameLog.log("SCAN aborted: bluetoothLeScanner is null")
            return
        }

        if (isScanning) {
            return
        }

        isScanning = true
        currentStatus = "scanning"
        FrameLog.log("SCAN start (nil-services, match name=$targetBleName)")
        try {
            scanner.startScan(scanCallback)
        } catch (e: SecurityException) {
            currentStatus = "bt unauthorized"
            FrameLog.log("SCAN aborted: SecurityException (missing BLUETOOTH_SCAN permission)")
            isScanning = false
        }
    }

    @SuppressLint("MissingPermission")
    fun stopScan() {
        if (!isScanning) return
        isScanning = false
        try {
            bluetoothAdapter?.bluetoothLeScanner?.stopScan(scanCallback)
            FrameLog.log("SCAN stopped")
        } catch (e: Exception) {
            FrameLog.log("SCAN stop error: ${e.message}")
        }
    }

    @SuppressLint("MissingPermission")
    private fun connect(device: BluetoothDevice) {
        try {
            connectedGatt = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
            } else {
                device.connectGatt(context, false, gattCallback)
            }
        } catch (e: SecurityException) {
            currentStatus = "bt unauthorized"
            FrameLog.log("CONNECT aborted: SecurityException (missing BLUETOOTH_CONNECT permission)")
        }
    }

    @SuppressLint("MissingPermission")
    fun disconnect() {
        try {
            connectedGatt?.disconnect()
            connectedGatt?.close()
        } catch (e: Exception) {
            FrameLog.log("DISCONNECT error: ${e.message}")
        } finally {
            cleanUpConnection()
            currentStatus = "disconnected"
        }
    }

    private fun cleanUpConnection() {
        writeChar = null
        notifyChar = null
        connectedGatt = null
    }

    @SuppressLint("MissingPermission")
    fun send(hexString: String): Boolean {
        val gatt = connectedGatt
        val char = writeChar
        if (gatt == null || char == null) {
            FrameLog.log("SEND-REJECT not-ready: writeChar or gatt is null")
            return false
        }

        val data = FrameLog.parseHex(hexString)
        if (data == null) {
            FrameLog.log("SEND-REJECT bad-hex: $hexString")
            return false
        }

        FrameLog.log("TX ${char.uuid} len=${data.size} ${FrameLog.hex(data)}")

        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                val res = gatt.writeCharacteristic(char, data, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)
                res == 0 // BluetoothStatusCodes.SUCCESS
            } else {
                @Suppress("DEPRECATION")
                char.value = data
                @Suppress("DEPRECATION")
                char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                @Suppress("DEPRECATION")
                gatt.writeCharacteristic(char)
            }
        } catch (e: Exception) {
            FrameLog.log("TX-EXCEPTION ${e.message}")
            false
        }
    }
}
