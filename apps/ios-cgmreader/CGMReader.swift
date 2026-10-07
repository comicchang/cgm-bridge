// CGMReader.swift — 备份通路 B v0：设备端 BLE 帧记录器（SiSensing ECO CGM）
// 完整交付物：扫描→连接→订阅→十六进制记录全部 TX/RX 帧到 Documents/ble_frames.log
// v1（待 protocol-spec.md）：auth/activate/getData 命令序列 + 数据帧解析
// v2（待 algorithm-feasibility.md）：厂商算法换算 + HealthKit 写入
// 与主通路（ECO App 补丁）完全独立，互不影响。

import SwiftUI
import CoreBluetooth

final class BLELogger: NSObject, ObservableObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    @Published var status = "init"
    @Published var events: [String] = []

    private var central: CBCentralManager!
    private var sensor: CBPeripheral?
    private var writeChar: CBCharacteristic?
    private var notifyChar: CBCharacteristic?

    // 目标设备 BLE 名称：优先读取启动参数 (--ble-name <NAME>)，其次读取环境变量 CGM_BLE_NAME，最后使用占位符
    let targetName: String
    private let serviceFF30 = CBUUID(string: "FF30")
    private let charFF31 = CBUUID(string: "FF31") // 写
    private let charFF32 = CBUUID(string: "FF32") // 通知
    private let svc180A = CBUUID(string: "180A")
    private let svc180F = CBUUID(string: "180F")

    private let logQueue = DispatchQueue(label: "cgmreader.log")
    private var logHandle: FileHandle?

    override init() {
        // 读取目标设备名称
        let args = ProcessInfo.processInfo.arguments
        if let idx = args.firstIndex(of: "--ble-name"), idx + 1 < args.count {
            self.targetName = args[idx + 1]
        } else if let env = ProcessInfo.processInfo.environment["CGM_BLE_NAME"], !env.isEmpty {
            self.targetName = env
        } else {
            self.targetName = "<BLE_NAME>"
        }

        super.init()
        // 必须先 openLog 再创建 central：didUpdateState 回调可能在 BLE 队列
        // 上先于 openLog 触发，导致首条日志（SCAN start）写入 nil handle 丢失
        openLog()
        central = CBCentralManager(delegate: self, queue: DispatchQueue(label: "cgmreader.ble"))
    }

    // MARK: - 日志

    // no-container entitlement 下 documentDirectory 不可用；写固定越狱路径
    private let hardLogDir = "/var/mobile/Documents/CGMReader"

    private var logURL: URL { URL(fileURLWithPath: hardLogDir + "/ble_frames.log") }

    private func openLog() {
        let fm = FileManager.default
        func p(_ s: String) { print("[CGMReader] " + s); fflush(stdout) }
        p("openLog start dir=\(hardLogDir)")
        do {
            try fm.createDirectory(atPath: hardLogDir, withIntermediateDirectories: true)
            p("createDirectory ok")
            if !fm.fileExists(atPath: logURL.path) {
                guard fm.createFile(atPath: logURL.path, contents: nil) else {
                    p("createFile FAILED \(logURL.path)")
                    DispatchQueue.main.async { self.status = "LOG CREATE FAILED: \(self.logURL.path)" }
                    return
                }
                p("createFile ok \(logURL.path)")
            }
            logHandle = try FileHandle(forWritingTo: logURL)
            try logHandle?.seekToEnd()
            p("FileHandle open ok")
            DispatchQueue.main.async { self.status = "log: \(self.logURL.path)" }
        } catch {
            p("openLog ERROR: \(error.localizedDescription)")
            DispatchQueue.main.async { self.status = "LOG OPEN FAILED: \(error.localizedDescription)" }
        }
    }

    static func hex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined(separator: " ")
    }

    func log(_ line: String) {
        let entry = "\(Int(Date().timeIntervalSince1970 * 1000)) \(line)\n"
        logQueue.async {
            self.logHandle?.write(entry.data(using: .utf8) ?? Data())
        }
        DispatchQueue.main.async {
            self.events.append(line)
            if self.events.count > 200 { self.events.removeFirst(self.events.count - 200) }
        }
    }

    // MARK: - CBCentralManagerDelegate

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            status = "scanning"
            // 快路径：系统已持有 FF30 连接的外设（ECO 退出后 bluetoothd 可能仍保持连接）
            let connected = central.retrieveConnectedPeripherals(withServices: [serviceFF30])
            if !connected.isEmpty, sensor == nil {
                let p = connected[0]
                sensor = p
                p.delegate = self
                status = "reusing system-connected"
                log("RETRIEVE \(p.identifier.uuidString) name=\(p.name ?? "?")")
                central.connect(p, options: nil)
            }
            // 全量扫描兜底：部分固件广播不带服务 UUID，靠名称过滤
            log("SCAN start (nil-services, match name=\(targetName))")
            central.scanForPeripherals(withServices: nil, options: nil)
        case .poweredOff:
            status = "bt off"
            log("SCAN aborted: bluetooth poweredOff")
        case .unauthorized:
            status = "bt unauthorized"
            log("SCAN aborted: unauthorized (检查 Info.plist NSBluetoothAlwaysUsageDescription)")
        default:
            status = "bt state \(central.state.rawValue)"
            log("SCAN waiting: state=\(central.state.rawValue)")
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let advName = advertisementData[CBAdvertisementDataLocalNameKey] as? String
        let name = peripheral.name ?? advName ?? "?"
        let svcUUIDs = (advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID])?
            .map { $0.uuidString }.joined(separator: ",") ?? "-"
        log("DISCOVER id=\(peripheral.identifier.uuidString) name=\(name) rssi=\(RSSI) svc=\(svcUUIDs)")

        guard name == targetName, sensor == nil else { return }
        sensor = peripheral
        peripheral.delegate = self
        central.stopScan()
        status = "connecting"
        log("CONNECT \(name)")
        central.connect(peripheral, options: nil)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        status = "discovering"
        log("CONNECTED \(peripheral.identifier.uuidString)")
        peripheral.discoverServices([serviceFF30, svc180A, svc180F])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        status = "connect failed"
        log("CONNECT-FAIL \(error?.localizedDescription ?? "?")")
        sensor = nil
        central.scanForPeripherals(withServices: [serviceFF30], options: nil)
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        status = "disconnected, rescanning"
        log("DISCONNECT \(error?.localizedDescription ?? "clean")")
        sensor = nil
        writeChar = nil
        notifyChar = nil
        central.scanForPeripherals(withServices: [serviceFF30], options: nil)
    }

    // MARK: - CBPeripheralDelegate

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error { log("SVC-DISC-FAIL \(error.localizedDescription)"); return }
        for svc in peripheral.services ?? [] {
            log("SVC \(svc.uuid.uuidString)")
            peripheral.discoverCharacteristics(nil, for: svc)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        if let error { log("CHR-DISC-FAIL \(service.uuid) \(error.localizedDescription)"); return }
        for chr in service.characteristics ?? [] {
            let props = chr.properties
            var propStr = ""
            if props.contains(.write) { propStr += "W" }
            if props.contains(.writeWithoutResponse) { propStr += "w" }
            if props.contains(.notify) { propStr += "N" }
            if props.contains(.read) { propStr += "R" }
            log("CHR \(service.uuid.uuidString)/\(chr.uuid.uuidString) props=\(propStr)")

            if service.uuid == serviceFF30 && chr.uuid == charFF31 && writeChar == nil {
                writeChar = chr
            }
            if service.uuid == serviceFF30 && chr.uuid == charFF32 && notifyChar == nil {
                notifyChar = chr
                peripheral.setNotifyValue(true, for: chr)
                log("NOTIFY on FF32")
            }
        }
        if writeChar != nil && notifyChar != nil {
            DispatchQueue.main.async { self.status = "ready (v0 logger)" }
            log("READY write=\(writeChar!.uuid) notify=\(notifyChar!.uuid)")
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        if let error { log("RX-ERR \(characteristic.uuid) \(error.localizedDescription)"); return }
        guard let data = characteristic.value, !data.isEmpty else { return }
        log("RX \(characteristic.uuid.uuidString) len=\(data.count) \(Self.hex(data))")
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        if let error { log("TX-ERR \(characteristic.uuid) \(error.localizedDescription)"); return }
        log("TX-OK \(characteristic.uuid.uuidString)")
    }

    // MARK: - 手动发送（v1 协议就绪前用于实验性写入）

    func send(_ hexString: String) {
        guard let chr = writeChar, let data = Data(hexString: hexString) else {
            log("SEND-REJECT not-ready or bad-hex: \(hexString)"); return
        }
        log("TX \(chr.uuid.uuidString) len=\(data.count) \(Self.hex(data))")
        sensor?.writeValue(data, for: chr, type: .withResponse)
    }
}

extension Data {
    init?(hexString: String) {
        let cleaned = hexString.replacingOccurrences(of: " ", with: "")
        guard cleaned.count % 2 == 0, !cleaned.isEmpty else { return nil }
        var data = Data(capacity: cleaned.count / 2)
        var index = cleaned.startIndex
        while index < cleaned.endIndex {
            let next = cleaned.index(index, offsetBy: 2)
            guard let byte = UInt8(cleaned[index..<next], radix: 16) else { return nil }
            data.append(byte)
            index = next
        }
        self = data
    }
}

@main
struct CGMReaderApp: App {
    @StateObject private var logger: BLELogger

    init() {
        // 关键：@StateObject 的 wrappedValue 是 autoclosure 惰性求值；
        // headless exec 时 SwiftUI 场景可能永不实例化 body，导致 BLELogger
        // 从未构造、openLog 从未执行。这里在 App.init 中显式急切构造。
        let instance = BLELogger()
        _logger = StateObject(wrappedValue: instance)
        print("[CGMReader] App.init eager logger constructed target=\(instance.targetName)"); fflush(stdout)
    }

    var body: some Scene {
        WindowGroup {
            VStack(alignment: .leading, spacing: 8) {
                Text("CGM Reader v0 (frame logger)").font(.headline)
                Text("设备: \(logger.targetName)").font(.caption)
                Text("状态: \(logger.status)").font(.subheadline)
                ScrollView {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(logger.events.enumerated()), id: \.offset) { _, line in
                            Text(line).font(.system(size: 10, design: .monospaced))
                        }
                    }
                }
            }
            .padding()
        }
    }
}
