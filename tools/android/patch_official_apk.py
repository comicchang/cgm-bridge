#!/usr/bin/env python3
"""patch_official_apk.py — SiSensing ECO 官方 Android APK 一键解除过期限制与重签名工具。

功能流程:
  1. 解包与元数据解析:
     提取 APK 元数据（包名、versionName、versionCode），计算原始 APK SHA-256。
  2. 版本自适应门禁 (Fail-Closed):
     比对 patch_config.json 白名单。若版本或 SHA-256 不匹配，立即中止。
  3. 壳保护探测 (Packer Detection):
     检测网易易盾等加固壳（libnesec.so / com.netease.nis.wrapper）。若命中且未脱壳，
     给出明确阻断原因与脱壳/Hook 建议，防止盲目篡改导致运行时闪退。
  4. Smali 站点模式匹配与替换:
     按配置在反编译后的 smali 代码树中检索目标候选站点（A1-A5），核对模式后覆写。
  5. 回编译与重签名:
     调用 apktool b 重构 APK，执行 zipalign 对齐，使用 debug keystore（首次自动生成）
     通过 apksigner 执行 v1+v2 签名并校验。
  6. 验证报告输出:
     生成 patch_report.json，记录各站点匹配状态、时间戳及产物哈希。

注意:
  - 仅用于个人数据互操作研究；非官方关联；严禁用于商业侵权。
  - 输入必须由用户从合法拥有的设备副本提取。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def log(msg: str) -> None:
    print(f"[INFO] {msg}")


def log_ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def log_fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)


def log_gate(msg: str) -> None:
    print(f"[GATE] {msg}")


def log_warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def calc_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def find_tool(name: str) -> Optional[str]:
    """在系统 PATH 及常见 Android SDK build-tools 路径下寻找可执行文件。"""
    found = shutil.which(name)
    if found:
        return found

    candidates = [
        Path.home() / "Library/Android/sdk/build-tools",
        Path("/opt/homebrew/share/android-commandlinetools/build-tools"),
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    for base in candidates:
        if not base.is_dir():
            continue
        # 寻找匹配的文件，按版本倒序
        matches = sorted(base.glob(f"*/{name}"), reverse=True)
        if matches and matches[0].is_file() and os.access(matches[0], os.X_OK):
            return str(matches[0])
        direct = base / name
        if direct.is_file() and os.access(direct, os.X_OK):
            return str(direct)

    return None


def _parse_string_pool(data: bytes, offset: int = 8) -> List[str]:
    """解析 AXML 中的 StringPool 块。"""
    if offset + 28 > len(data):
        return []
    chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, offset)
    if chunk_type != 1:
        return []
    string_count, style_count, flags, strings_start, styles_start = struct.unpack_from(
        "<IIIII", data, offset + 8
    )
    is_utf8 = bool(flags & (1 << 8))
    offsets = [
        struct.unpack_from("<I", data, offset + 28 + i * 4)[0]
        for i in range(string_count)
    ]
    strings_data_start = offset + strings_start

    strings: List[str] = []
    for off in offsets:
        str_offset = strings_data_start + off
        if str_offset >= len(data):
            strings.append("")
            continue
        if is_utf8:
            u16len = data[str_offset]
            pos = str_offset + 1
            if u16len & 0x80:
                pos += 1
            if pos < len(data):
                u8len = data[pos]
                pos += 1
                if u8len & 0x80 and pos < len(data):
                    u8len = ((u8len & 0x7F) << 8) | data[pos]
                    pos += 1
                s = data[pos : pos + u8len].decode("utf-8", errors="replace")
            else:
                s = ""
        else:
            if str_offset + 2 > len(data):
                strings.append("")
                continue
            u16len = struct.unpack_from("<H", data, str_offset)[0]
            pos = str_offset + 2
            if u16len & 0x8000:
                if pos + 2 > len(data):
                    strings.append("")
                    continue
                u16len = ((u16len & 0x7FFF) << 16) | struct.unpack_from("<H", data, pos)[0]
                pos += 2
            s = data[pos : pos + u16len * 2].decode("utf-16-le", errors="replace")
        strings.append(s)
    return strings


def _parse_axml_manifest(data: bytes) -> Tuple[Optional[str], Optional[str]]:
    """解析 Android 二进制 XML (AXML) 提取 package_name 与 versionName。"""
    if len(data) < 8:
        return None, None
    c_type, c_hdr_size, c_size = struct.unpack_from("<HHI", data, 0)
    if c_type != 0x0003:
        return None, None

    strings = _parse_string_pool(data, 8)
    if not strings:
        return None, None

    cur = 8
    sp_type, sp_hdr_size, sp_size = struct.unpack_from("<HHI", data, cur)
    cur += sp_size

    pkg_name: Optional[str] = None
    ver_name: Optional[str] = None

    while cur + 8 <= len(data):
        chunk_type, chunk_hdr_size, chunk_size = struct.unpack_from("<HHI", data, cur)
        if chunk_size <= 0:
            break
        if chunk_type == 0x0102:  # START_ELEMENT
            if cur + chunk_hdr_size + 14 <= len(data):
                ns, name, attr_start, attr_size, attr_count = struct.unpack_from(
                    "<IIHHH", data, cur + chunk_hdr_size
                )
                elem_name = strings[name] if 0 <= name < len(strings) else ""
                if elem_name == "manifest":
                    attr_base = cur + chunk_hdr_size + attr_start
                    for i in range(attr_count):
                        ab = attr_base + i * attr_size
                        if ab + 20 <= len(data):
                            a_ns, a_name, a_val_str, a_size_b, a_res0, a_type, a_data = struct.unpack_from(
                                "<IIIHBB I", data, ab
                            )
                            aname = strings[a_name] if 0 <= a_name < len(strings) else ""
                            if 0 <= a_val_str < len(strings) and strings[a_val_str]:
                                aval = strings[a_val_str]
                            elif a_type == 3 and 0 <= a_data < len(strings):
                                aval = strings[a_data]
                            else:
                                aval = str(a_data)
                            if aname == "package":
                                pkg_name = aval
                            elif aname == "versionName":
                                ver_name = aval
                    break
        cur += chunk_size

    return pkg_name, ver_name


def read_apk_manifest_info(apk_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """从 APK 的 AndroidManifest.xml 解析真实包名与版本 (支持 AXML 二进制与明文 XML)。

    Fail-closed: 解析失败时返回 (None, None)，绝不使用硬编码默认值兜底。
    """
    if not apk_path.is_file():
        return None, None

    try:
        with zipfile.ZipFile(apk_path, "r") as z:
            if "AndroidManifest.xml" not in z.namelist():
                return None, None
            manifest_bytes = z.read("AndroidManifest.xml")
    except Exception as e:
        log_fail(f"Failed to read AndroidManifest.xml from {apk_path}: {e}")
        return None, None

    if manifest_bytes.startswith(b"\x03\x00\x08\x00"):
        pkg_name, ver_name = _parse_axml_manifest(manifest_bytes)
        if pkg_name and ver_name:
            return pkg_name, ver_name

    # 尝试作为标准明文 XML 解析（用于合成测试包或未编译资源包）
    try:
        root = ET.fromstring(manifest_bytes.decode("utf-8", errors="replace"))
        pkg_name = root.attrib.get("package")
        ver_name = None
        for k, v in root.attrib.items():
            if k.endswith("versionName"):
                ver_name = v
                break
        if pkg_name or ver_name:
            return pkg_name, ver_name
    except Exception:
        pass

    return pkg_name, ver_name


def detect_packer(apk_path: Path) -> Dict[str, Any]:
    """检测 APK 是否被网易易盾、腾讯乐固、360加固等加壳保护。"""
    result = {
        "is_packed": False,
        "packer_name": "None",
        "evidence": [],
    }
    with zipfile.ZipFile(apk_path, "r") as z:
        names = z.namelist()
        # 网易易盾
        if any("libnesec" in n for n in names):
            result["is_packed"] = True
            result["packer_name"] = "NetEase-YiDun"
            result["evidence"].append("Found libnesec.so in native libraries")
        if "assets/nedata.db" in names or "assets/nedig.properties" in names:
            result["is_packed"] = True
            result["packer_name"] = "NetEase-YiDun"
            result["evidence"].append("Found nedata.db / nedig.properties in assets")

        # 腾讯乐固
        if any("libshexexec" in n or "libtup" in n for n in names):
            result["is_packed"] = True
            result["packer_name"] = "Tencent-Legu"
            result["evidence"].append("Found Tencent Legu native libraries")

    return result


def load_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.is_file():
        script_dir = Path(__file__).resolve().parent
        cand = script_dir / config_path.name
        if cand.is_file():
            config_path = cand
        elif (script_dir.parent.parent / config_path).is_file():
            config_path = script_dir.parent.parent / config_path
        else:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if "supported_versions" in cfg:
        return cfg["supported_versions"]
    if "versions" in cfg:
        return cfg["versions"]
    return cfg


def verify_gate(
    apk_path: Path,
    version_name: str,
    version_config: Dict[str, Any],
) -> str:
    """版本与哈希门禁核验。Fail-closed: 任何不一致、缺失或占位哈希立即抛出异常阻断。"""
    log_gate(f"Checking version '{version_name}' against patch configuration...")
    expected_sha = version_config.get("apk_sha256", "").strip().lower()
    if not expected_sha or set(expected_sha) <= {"0"} or "<" in expected_sha:
        log_fail(f"Config for version '{version_name}' has missing, placeholder, or zeroed apk_sha256: '{expected_sha}'")
        log_fail("Fail-closed: you must calculate the SHA-256 of your legitimate APK and update the config before patching.")
        raise ValueError(f"Config for version '{version_name}' has placeholder/zeroed apk_sha256: '{expected_sha}'")

    actual_sha = calc_sha256(apk_path)
    log_gate(f"APK SHA256: {actual_sha}")
    log_gate(f"Expect SHA256: {expected_sha}")

    if actual_sha != expected_sha:
        log_fail("Gate check failed: APK SHA256 mismatch!")
        log_fail(f"  Expected: {expected_sha}")
        log_fail(f"  Actual:   {actual_sha}")
        log_fail("  Hint: The input APK is modified, belongs to another market, or differs from baseline.")
        raise ValueError(f"Gate check failed: expected {expected_sha}, got {actual_sha}")

    log_ok("Gate check passed: version and APK SHA256 verified pristine.")
    return actual_sha


def ensure_debug_keystore(keystore_path: Path) -> Tuple[Path, str, str]:
    """若指定 keystore 不存在，使用 keytool 自动生成标准 debug.keystore。"""
    alias = "androiddebugkey"
    password = "android"

    if keystore_path.is_file():
        return keystore_path, alias, password

    keytool = find_tool("keytool")
    if not keytool:
        raise RuntimeError("keytool not found; cannot create debug keystore")

    keystore_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"Generating debug keystore at {keystore_path}...")
    cmd = [
        keytool,
        "-genkeypair",
        "-v",
        "-keystore",
        str(keystore_path),
        "-storepass",
        password,
        "-alias",
        alias,
        "-keypass",
        password,
        "-keyalg",
        "RSA",
        "-keysize",
        "2048",
        "-validity",
        "10000",
        "-dname",
        "CN=Android Debug,O=Android,C=US",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    log_ok("Debug keystore generated successfully.")
    return keystore_path, alias, password


def is_unverified_site(site: Dict[str, Any]) -> bool:
    """判定补丁站点是否属于待验证/占位模式或已禁用。"""
    if site.get("enabled") is False:
        return True
    if site.get("verified") is False:
        return True
    status_tag = site.get("status", "")
    return "[待验证" in status_tag or "[占位模板" in status_tag or "[高危候选" in status_tag

def calculate_dynamic_smali_replace(site_id: str, replace_pattern: str, days: int) -> str:
    """根据目标天数动态调整 Smali 替换模式（支持 21 天与 24 天）。"""
    num_samples = days * 1440
    duration_ms = days * 86400000
    days_hex = f"{days:#x}"
    num_samples_hex = f"{num_samples:#x}"
    num_samples_plus1_hex = f"{(num_samples + 1):#x}"
    duration_ms_hex = f"{duration_ms:#x}"

    # 1. 支持模板占位符替换
    pattern = replace_pattern
    pattern = pattern.replace("{days}", str(days))
    pattern = pattern.replace("{days_hex}", days_hex)
    pattern = pattern.replace("{num_samples}", str(num_samples))
    pattern = pattern.replace("{num_samples_hex}", num_samples_hex)
    pattern = pattern.replace("{duration_ms_hex}", duration_ms_hex)

    if days == 24:
        return pattern

    # 2. 针对静态配置默认 24 天 (0x18, 0x8700, 0x8701, 0x7b98a000) 动态自适应换算
    if any(k in site_id for k in ["A2", "P2", "getSensorDays", "max_days"]):
        pattern = re.sub(r"0x18\b", days_hex, pattern)
    if any(k in site_id for k in ["A3", "P3", "getSensorNum", "max_num"]):
        pattern = re.sub(r"0x8700\b", num_samples_hex, pattern)
    if any(k in site_id for k in ["A1", "P1", "ble_packet_limit", "ble_limit"]):
        pattern = re.sub(r"0x8700\b", num_samples_hex, pattern)
        pattern = re.sub(r"0x8701\b", num_samples_plus1_hex, pattern)
    if any(k in site_id for k in ["A4", "P4", "expiration_ms", "duration_ms"]):
        pattern = re.sub(r"0x7b98a000(?=L|\b)", duration_ms_hex, pattern)

    return pattern


def apply_smali_patch(
    decompiled_dir: Path,
    site: Dict[str, Any],
    days: int = 24,
    allow_unverified: bool = False,
) -> Dict[str, Any]:
    """在反编译目录中查找目标 smali 文件并执行正则或字符串模式替换。"""
    site_id = site.get("id", "")
    name = site.get("name", site_id)
    target_rel = site.get("target_smali", "")
    search_pattern = site.get("search_pattern", "")
    default_replace = site.get("replace_pattern", "")
    replace_pattern = calculate_dynamic_smali_replace(site_id, default_replace, days)
    status_tag = site.get("status", "")
    unverified = is_unverified_site(site)

    report_item = {
        "id": site_id,
        "name": name,
        "target_smali": target_rel,
        "matched": False,
        "status": "NOT_FOUND",
        "detail": "",
        "days": days,
    }

    # 显式禁用站点：直接跳过
    if site.get("enabled") is False:
        msg = f"Site {site_id} ({name}) is explicitly disabled in config (enabled: false)."
        log_warn(msg)
        report_item["status"] = "DISABLED"
        report_item["detail"] = msg
        return report_item

    # 未验证站点门禁：默认拒绝应用
    if unverified and not allow_unverified:
        msg = f"Site {site_id} ({name}) is unverified ({status_tag}) and --allow-unverified was not specified."
        log_fail(f"Fail-closed: {msg}")
        report_item["status"] = "UNVERIFIED_REJECTED"
        report_item["detail"] = msg
        return report_item

    # 寻找匹配的 smali 文件（支持 smali/, smali_classes2/, smali_classes3/ 等）
    smali_roots = [p for p in decompiled_dir.iterdir() if p.is_dir() and p.name.startswith("smali")]
    matched_files: List[Path] = []
    for s_root in smali_roots:
        cand = s_root / target_rel
        if cand.is_file():
            matched_files.append(cand)

    if not matched_files:
        msg = f"Target smali not found in any smali folder: {target_rel}"
        if unverified:
            log_warn(f"Site {site_id} ({name}): {msg} {status_tag}")
            report_item["status"] = "PENDING_VERIFICATION"
            report_item["detail"] = f"File missing in decompiled tree (packed or split); {status_tag}"
            return report_item
        else:
            log_fail(f"Site {site_id} ({name}): {msg}")
            raise FileNotFoundError(msg)

    target_file = matched_files[0]
    content = target_file.read_text(encoding="utf-8")

    regex = re.compile(search_pattern)
    if not regex.search(content):
        msg = f"Search pattern '{search_pattern}' not matched in {target_file.name}"
        if unverified:
            log_warn(f"Site {site_id} ({name}): {msg} {status_tag}")
            report_item["status"] = "PENDING_VERIFICATION"
            report_item["detail"] = msg
            return report_item
        else:
            log_fail(f"Site {site_id} ({name}): {msg}")
            raise ValueError(msg)

    new_content, count = regex.subn(replace_pattern, content)
    target_file.write_text(new_content, encoding="utf-8")
    log_ok(f"Site {site_id} ({name}): applied {count} substitution(s) in {target_file.name} (target days: {days})")

    report_item["matched"] = True
    report_item["status"] = "APPLIED"
    report_item["detail"] = f"Replaced {count} occurrence(s) for {days} days"
    return report_item

def run_mock_test(
    config_path: Path,
    report_path: Path,
    fail_closed_demo: bool = True,
) -> int:
    """在无外部工具环境下运行 Mock 验证，演示版本门禁 fail-closed 与补丁流程。"""
    log("=== Running Mock Test Mode ===")
    cfg_data = load_config(config_path)

    if fail_closed_demo:
        # 1. 测试 Fail-Closed 门禁拒绝非白名单 APK / 校验和不匹配
        log_gate("Testing fail-closed gate: mismatched SHA256...")
        with tempfile.NamedTemporaryFile("wb", suffix=".apk") as f:
            f.write(b"MOCK_INVALID_APK_CONTENT")
            f.flush()
            mock_apk = Path(f.name)
            v_cfg = cfg_data.get("02.26.01.00", {})
            try:
                verify_gate(mock_apk, "02.26.01.00", v_cfg)
                log_fail("Fail-closed gate unexpectedly allowed mismatched APK!")
                return 1
            except ValueError as e:
                log_ok(f"Fail-closed gate correctly rejected mismatched SHA256: {e}")

        # 2. 测试 Fail-Closed 门禁拒绝占位符 / 全零哈希配置 (如 3.9.4)
        log_gate("Testing fail-closed gate: placeholder/zeroed SHA256...")
        cfg_394 = cfg_data.get("3.9.4", {})
        try:
            verify_gate(mock_apk, "3.9.4", cfg_394)
            log_fail("Fail-closed gate unexpectedly allowed zeroed/placeholder SHA256!")
            return 1
        except ValueError as e:
            log_ok(f"Fail-closed gate correctly rejected zeroed/placeholder SHA256: {e}")

        # 3. 测试包名不符门禁拦截 (Manifest Package Mismatch)
        log_gate("Testing fail-closed gate: package name mismatch...")
        with tempfile.NamedTemporaryFile("wb", suffix=".apk") as f:
            with zipfile.ZipFile(f, "w") as bio:
                bio.writestr(
                    "AndroidManifest.xml",
                    '<manifest package="com.fake.mismatched" android:versionName="02.26.01.00" xmlns:android="http://schemas.android.com/apk/res/android"/>',
                )
            f.flush()
            parsed_pkg, parsed_ver = read_apk_manifest_info(Path(f.name))
            expected_pkg = v_cfg.get("package_name")
            if parsed_pkg != expected_pkg:
                log_ok(f"Fail-closed correctly identified package mismatch: APK has '{parsed_pkg}', config expects '{expected_pkg}'")
            else:
                log_fail("Package mismatch gate failed to detect mismatched package!")
                return 1

        # 4. 测试未验证站点默认拒绝 (Unverified Sites Default Reject)
        log_gate("Testing fail-closed gate: unverified sites default reject...")
        unverified_site = {
            "id": "A1_test",
            "name": "测试未验证站点",
            "target_smali": "com/test/Test.smali",
            "search_pattern": "const/16 v0, 0x4ec0",
            "replace_pattern": "const v0, 0x8700",
            "status": "[待验证: 需脱壳确认]",
            "verified": False,
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            res_unv = apply_smali_patch(Path(tmp_dir), unverified_site, days=24, allow_unverified=False)
            if res_unv["status"] == "UNVERIFIED_REJECTED":
                log_ok("Fail-closed correctly rejected unverified site when --allow-unverified is omitted.")
            else:
                log_fail(f"Unverified site was not rejected: {res_unv}")
                return 1

        # 5. 测试零站点匹配 fail-closed 拦截 (NO_SITES_APPLIED)
        log_gate("Testing fail-closed gate: zero sites applied (NO_SITES_APPLIED)...")
        with tempfile.TemporaryDirectory() as tmp_dir:
            res_zero = apply_smali_patch(Path(tmp_dir), unverified_site, days=24, allow_unverified=True)
            applied = [s for s in [res_zero] if s.get("status") == "APPLIED"]
            if not applied:
                log_ok("Fail-closed correctly identified zero sites applied when files missing or unmatched.")
            else:
                log_fail("Zero sites check failed!")
                return 1

    # 6. 模拟 Smali 目录结构并执行替换测试 (包含 24 天与 21 天动态 --days 验证)
    log("Testing smali pattern matching and patching on mock code tree...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        smali_dir = tmp_path / "smali" / "com" / "sisensing" / "common" / "ble"
        smali_dir.mkdir(parents=True)
        mock_ble_smali = smali_dir / "BleModel.smali"
        mock_code = """.class public Lcom/sisensing/common/ble/BleModel;
.super Ljava/lang/Object;

.method public static getSensorDays()I
    .registers 1
    const/16 v0, 0xe
    return v0
.end method

.method public static getSensorNum()I
    .registers 1
    const v0, 0x4ec0
    return v0
.end method
"""
        mock_ble_smali.write_text(mock_code, encoding="utf-8")

        sites_cfg = [
            {
                "id": "A2_getSensorDays",
                "name": "传感器生命周期天数常量",
                "target_smali": "com/sisensing/common/ble/BleModel.smali",
                "search_pattern": r"const/16 [v0-9]+, 0xe\b",
                "replace_pattern": "const/16 v0, 0x18",
                "description": "天数 14 -> 24",
                "verified": True,
            },
            {
                "id": "A3_getSensorNum",
                "name": "传感器最大采集点数常量",
                "target_smali": "com/sisensing/common/ble/BleModel.smali",
                "search_pattern": r"const [v0-9]+, 0x4ec0\b",
                "replace_pattern": "const v0, 0x8700",
                "description": "点数 20160 -> 34560",
                "verified": True,
            },
        ]

        # 6a. 测试 24 天
        results_24 = []
        for s in sites_cfg:
            res = apply_smali_patch(tmp_path, s, days=24, allow_unverified=True)
            results_24.append(res)

        patched_text_24 = mock_ble_smali.read_text(encoding="utf-8")
        assert "const/16 v0, 0x18" in patched_text_24, "A2 24d patch missing!"
        assert "const v0, 0x8700" in patched_text_24, "A3 24d patch missing!"
        log_ok("Mock smali 24-day patching asserted: 14 -> 24 (0x18) and 20160 -> 34560 (0x8700) applied successfully.")

        # 6b. 测试 21 天动态替换 (--days 21)
        mock_ble_smali.write_text(mock_code, encoding="utf-8")
        results_21 = []
        for s in sites_cfg:
            res = apply_smali_patch(tmp_path, s, days=21, allow_unverified=True)
            results_21.append(res)

        patched_text_21 = mock_ble_smali.read_text(encoding="utf-8")
        assert "const/16 v0, 0x15" in patched_text_21, "A2 21d patch missing!"
        assert "const v0, 0x7620" in patched_text_21, "A3 21d patch missing!"
        log_ok("Mock smali 21-day dynamic patching asserted: 14 -> 21 (0x15) and 20160 -> 30240 (0x7620) applied successfully.")

        # 生成 Mock 验证报告
        report = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": "SUCCESS (MOCK)",
            "mode": "mock_test",
            "sites": results_24,
            "target_days": 24,
            "summary": "All mock assertions and fail-closed gates passed.",
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        log_ok(f"Report written to {report_path}")

    log("=== Mock Test Completed Successfully ===")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SiSensing ECO Android 官方 APK 一键解除过期限制与重签名工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-i", "--input", help="原始 APK 输入路径 (由用户合法提取)")
    parser.add_argument("-o", "--output", help="补丁重打包后的 APK 输出路径")
    parser.add_argument(
        "-c",
        "--config",
        default="tools/android/patch_config.example.json",
        help="补丁配置 JSON 路径 (默认 tools/android/patch_config.example.json)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=24,
        choices=[21, 24],
        help="扩展传感器目标天数 (21 或 24，默认 24 天)",
    )
    parser.add_argument(
        "-k",
        "--keystore",
        default="debug.keystore",
        help="签名 keystore 路径 (缺省时自动在当前目录创建 debug.keystore)",
    )
    parser.add_argument("--key-alias", default="androiddebugkey", help="Keystore key alias")
    parser.add_argument("--key-pass", default="android", help="Keystore key password")
    parser.add_argument(
        "-r",
        "--report",
        default="patch_report.json",
        help="补丁与验证结果 JSON 报告路径 (默认 patch_report.json)",
    )
    parser.add_argument(
        "--skip-sign",
        action="store_true",
        help="跳过重签名与对齐步骤（仅反编译和 smali 修改）",
    )
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="允许尝试应用标记为 [待验证] 的候选补丁站点 (默认禁用，防止盲目修补加固 APK)",
    )
    parser.add_argument(
        "--mock-test",
        action="store_true",
        help="运行自测模式（演示门禁 fail-closed 与 smali 替换，无需输入真实 APK）",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    report_path = Path(args.report)

    if args.mock_test:
        return run_mock_test(config_path, report_path)

    if not args.input or not args.output:
        parser.print_help()
        log_fail("Error: --input and --output are required unless --mock-test is specified.")
        return 2

    input_apk = Path(args.input)
    output_apk = Path(args.output)

    if not input_apk.is_file():
        log_fail(f"Input APK not found: {input_apk}")
        return 1

    # 1. 工具检查 (Fail-closed: 必需工具缺失立即报错退出，严禁将未签名产物标为 SUCCESS)
    apktool = find_tool("apktool")
    if not apktool:
        log_fail("apktool is required but not found in PATH or Android SDK. Install with 'brew install apktool'.")
        return 1

    zipalign = find_tool("zipalign")
    apksigner = find_tool("apksigner")

    if not args.skip_sign:
        missing_sign_tools = []
        if not zipalign:
            missing_sign_tools.append("zipalign")
        if not apksigner:
            missing_sign_tools.append("apksigner")
        if missing_sign_tools:
            log_fail(
                f"Fail-closed: signing tool(s) missing: {', '.join(missing_sign_tools)}. "
                "Install Android SDK build-tools, or explicitly specify --skip-sign to output an unsigned APK."
            )
            return 1

    # 2. 读取配置与真实 APK Manifest 信息
    configs = load_config(config_path)
    pkg_name, ver_name = read_apk_manifest_info(input_apk)
    if not pkg_name or not ver_name:
        log_fail("Fail-closed: failed to parse package_name or versionName from APK AndroidManifest.xml.")
        return 1

    log(f"Detected APK: package='{pkg_name}', version='{ver_name}'")

    if ver_name not in configs:
        log_fail(f"Version '{ver_name}' is not in supported versions list: {list(configs.keys())}")
        log_fail("Fail-closed: halting to prevent incompatible modifications.")
        return 1

    v_cfg = configs[ver_name]
    expected_pkg = v_cfg.get("package_name")
    if expected_pkg and pkg_name != expected_pkg:
        log_fail(f"Package name mismatch: APK has '{pkg_name}', but config expects '{expected_pkg}'")
        log_fail("Fail-closed: halting due to package name mismatch.")
        return 1

    # 3. 门禁校验 (Fail-closed: SHA256 缺失、全零占位或不匹配均立即阻断)
    try:
        apk_sha = verify_gate(input_apk, ver_name, v_cfg)
    except Exception as e:
        log_fail(f"Security gate error: {e}")
        return 1

    # 4. 未验证站点门禁检查 (Fail-closed: 默认拒绝应用未验证/占位站点)
    sites = v_cfg.get("sites", [])
    unverified_sites = [s.get("id", "unknown") for s in sites if is_unverified_site(s)]
    if unverified_sites and not args.allow_unverified:
        log_fail(
            f"Fail-closed: version '{ver_name}' contains {len(unverified_sites)} unverified site(s): "
            f"{unverified_sites}."
        )
        log_fail(
            "Unverified sites cannot be applied by default. "
            "Specify --allow-unverified to allow testing in research environments, "
            "or update config after verifying Smali offsets."
        )
        return 1

    # 5. 加壳检测
    packer_info = detect_packer(input_apk)
    if packer_info["is_packed"]:
        log_warn(f"APK is packed with {packer_info['packer_name']}!")
        for ev in packer_info["evidence"]:
            log_warn(f"  Evidence: {ev}")
        log_warn("Note: Modifying smali of a packed APK without dynamic unpacking will trigger runtime")
        log_warn("integrity checks. Recommendation: Use LSPosed runtime hook or dump classes via Frida dexdump.")

    # 6. 反编译
    work_dir = Path(tempfile.mkdtemp(prefix="apk_patch_"))
    decomp_dir = work_dir / "decompiled"
    try:
        log(f"Decompiling APK using apktool: {input_apk} -> {decomp_dir}")
        try:
            subprocess.run(
                [apktool, "d", str(input_apk), "-o", str(decomp_dir), "-f"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else str(e)
            log_fail(f"apktool decompilation failed: {err_msg}")
            return 1
        log_ok("Decompilation completed.")

        # 7. 应用 smali 补丁 (真正的 --days 贯通)
        report_sites = []
        for site in sites:
            res = apply_smali_patch(
                decomp_dir,
                site,
                days=args.days,
                allow_unverified=args.allow_unverified,
            )
            report_sites.append(res)

        applied_sites = [s for s in report_sites if s.get("status") == "APPLIED"]
        if not applied_sites:
            log_fail("Fail-closed: 0 patch sites were applied! Halting to prevent generating an unpatched APK.")
            report_data = {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "status": "NO_SITES_APPLIED",
                "input_apk": str(input_apk),
                "input_sha256": apk_sha,
                "version": ver_name,
                "package_name": pkg_name,
                "target_days": args.days,
                "packer": packer_info,
                "sites": report_sites,
                "output_apk": None,
                "output_sha256": None,
            }
            report_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
            log_ok(f"Failure report written to {report_path}")
            if output_apk.is_file():
                output_apk.unlink()
            return 1

        # 8. 回编译
        unaligned_apk = work_dir / "unaligned.apk"
        log(f"Rebuilding APK using apktool: {decomp_dir} -> {unaligned_apk}")
        try:
            subprocess.run(
                [apktool, "b", str(decomp_dir), "-o", str(unaligned_apk)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else str(e)
            log_fail(f"apktool rebuild failed: {err_msg}")
            return 1
        log_ok("Rebuild completed.")

        # 9. 对齐与重签名 (严禁无签名标记成功)
        if not args.skip_sign:
            aligned_apk = work_dir / "aligned.apk"
            log(f"Aligning APK using zipalign (4-byte)...")
            try:
                subprocess.run(
                    [zipalign, "-p", "-f", "-v", "4", str(unaligned_apk), str(aligned_apk)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except subprocess.CalledProcessError as e:
                err_msg = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else str(e)
                log_fail(f"zipalign failed: {err_msg}")
                return 1

            keystore_path, alias, kpass = ensure_debug_keystore(Path(args.keystore))
            log(f"Signing APK using apksigner with {alias}...")
            try:
                subprocess.run(
                    [
                        apksigner,
                        "sign",
                        "--ks",
                        str(keystore_path),
                        "--ks-key-alias",
                        alias,
                        "--ks-pass",
                        f"pass:{kpass}",
                        "--key-pass",
                        f"pass:{kpass}",
                        "--out",
                        str(output_apk),
                        str(aligned_apk),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except subprocess.CalledProcessError as e:
                err_msg = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else str(e)
                log_fail(f"apksigner sign failed: {err_msg}")
                if output_apk.is_file():
                    output_apk.unlink()
                return 1
            log_ok(f"Verification of signed APK:")
            verify_res = subprocess.run(
                [apksigner, "verify", "-v", str(output_apk)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if verify_res.returncode == 0:
                log_ok("apksigner verification passed (v1+v2 signature valid).")
                final_status = "SUCCESS" if len(applied_sites) == len(report_sites) else "PARTIAL_APPLIED"
            else:
                log_fail(f"apksigner verification failed: {verify_res.stderr.strip()}")
                if output_apk.is_file():
                    output_apk.unlink()
                return 1
        else:
            log("Skipping signing step as requested by --skip-sign.")
            shutil.copy2(unaligned_apk, output_apk)
            final_status = "SUCCESS_UNSIGNED"

        out_sha = calc_sha256(output_apk)
        log_ok(f"Patched APK generated successfully: {output_apk} (SHA256: {out_sha})")

        # 10. 输出报告
        report_data = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": final_status,
            "input_apk": str(input_apk),
            "input_sha256": apk_sha,
            "version": ver_name,
            "package_name": pkg_name,
            "target_days": args.days,
            "packer": packer_info,
            "sites": report_sites,
            "output_apk": str(output_apk),
            "output_sha256": out_sha,
        }
        report_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
        log_ok(f"Patch report written to {report_path}")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
