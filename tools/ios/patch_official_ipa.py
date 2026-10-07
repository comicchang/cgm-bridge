#!/usr/bin/env python3
"""patch_official_ipa.py — SiSensing ECO 官方 IPA 一键解除过期限制与重签名工具。

流程:
  1. 解包: 提取解密 IPA，保留 POSIX 文件执行权限与符号链接结构。
  2. 版本自适应门禁: 读取 Info.plist 版本号、Bundle ID 及主二进制 SHA256，与 patch_config.json
     白名单严格比对；不匹配则 fail-closed 中止。
  3. 字节模式打补丁: 按 config 校验原始字节并覆写（P1 BLE包闸 / P2 maxDay / P3 maxNum /
     P4 默认天数 dword / P5a-c GrowingIO RET 防后台崩溃）。写回时严格保留执行权限，
     并校验补丁后 SHA256 与 config.patched_sha256 一致（fail-closed）。
  4. ldid 重签名: 在打包前对已具备正确 POSIX 执行权限的可执行文件执行，使用包含 TEAMID 占位符的
     entitlements 模板注入指定的 --team-id，重签名主二进制及扩展/框架。
  5. 打包输出: 校验主二进制执行位（fail-closed），以 zip symlink 保留软链结构，重新压缩为 IPA 格式并生成 patch_report.json。

注意:
  - 仅用于个人数据互操作研究。
  - 输入必须为合法拥有的解密版 IPA（由越狱设备 Dump 或砸壳工具提取）。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 主程序 Entitlements 模板（以 TEAMID 占位）
MAIN_ENTITLEMENTS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>com.apple.developer.siri</key>
	<true/>
	<key>com.apple.developer.team-identifier</key>
	<string>{TEAM_ID}</string>
	<key>application-identifier</key>
	<string>{TEAM_ID}.com.sisensing.eco</string>
	<key>aps-environment</key>
	<string>production</string>
	<key>com.apple.developer.associated-domains</key>
	<array>
		<string>applinks:protocol.sisensing.com/</string>
	</array>
	<key>com.apple.security.application-groups</key>
	<array>
		<string>group.com.sisensing.eco</string>
	</array>
</dict>
</plist>
"""

# Mach-O Magic 常量
MACHO_MAGICS = (
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
)


def log(msg: str) -> None:
    print(f"[INFO] {msg}")


def log_ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def log_fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)


def log_gate(msg: str) -> None:
    print(f"[GATE] {msg}")


def parse_offset(val: Any) -> int:
    """支持十进制或十六进制字符串（如 0xB5A3DC）转为整数偏移。"""
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        return int(val, 0)
    raise ValueError(f"Invalid offset format: {val}")


def check_tool(name: str) -> bool:
    return shutil.which(name) is not None


def unpack_ipa(ipa_path: Path, dest_dir: Path) -> Path:
    """解压 IPA 并显式还原文件的 POSIX 权限与符号链接（防止 Mach-O 执行权限丢失与链接拍平）。"""
    if not ipa_path.is_file():
        raise FileNotFoundError(f"Input IPA not found: {ipa_path}")
    log(f"Unpacking IPA: {ipa_path} -> {dest_dir}")
    with zipfile.ZipFile(ipa_path, "r") as z:
        for member in z.infolist():
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                link_target = z.read(member).decode("utf-8")
                dest = dest_dir / member.filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_symlink() or dest.exists():
                    dest.unlink()
                os.symlink(link_target, dest)
            else:
                target = z.extract(member, dest_dir)
                if mode:
                    try:
                        os.chmod(target, mode)
                    except OSError:
                        pass
    payload_dir = dest_dir / "Payload"
    if not payload_dir.is_dir():
        raise RuntimeError("Malformed IPA: 'Payload' directory missing")
    app_bundles = [p for p in payload_dir.iterdir() if p.suffix == ".app" and p.is_dir()]
    if not app_bundles:
        raise RuntimeError("Malformed IPA: no '.app' bundle found inside Payload")
    return app_bundles[0]


def read_app_info(app_dir: Path) -> Tuple[str, str, str]:
    """读取 Info.plist 返回 (version, executable_name, bundle_id)。"""
    plist_path = app_dir / "Info.plist"
    if not plist_path.is_file():
        raise FileNotFoundError(f"Info.plist missing: {plist_path}")
    with open(plist_path, "rb") as f:
        data = plistlib.load(f)
    version = data.get("CFBundleShortVersionString") or data.get("CFBundleVersion") or ""
    executable = data.get("CFBundleExecutable") or ""
    bundle_id = data.get("CFBundleIdentifier") or ""
    if not executable:
        raise ValueError(f"CFBundleExecutable not found in {plist_path}")
    return str(version), str(executable), str(bundle_id)


def calc_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def load_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.is_file():
        raise FileNotFoundError(f"Patch configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if "supported_versions" in cfg:
        return cfg["supported_versions"]
    if "versions" in cfg:
        return cfg["versions"]
    return cfg


def verify_gate(
    version: str,
    executable_path: Path,
    version_config: Dict[str, Any],
    bundle_id: Optional[str] = None,
) -> str:
    """版本、Bundle ID 与二进制哈希门禁比对。不匹配则立即中止（fail-closed）。"""
    log_gate(f"Checking version '{version}' against configuration...")

    expected_bundle_id = version_config.get("bundle_id", "").strip()
    if expected_bundle_id:
        if not bundle_id:
            raise ValueError(f"Config expects bundle_id '{expected_bundle_id}', but none provided to gate")
        log_gate(f"Bundle ID:  {bundle_id}")
        log_gate(f"Expect ID:  {expected_bundle_id}")
        if bundle_id != expected_bundle_id:
            log_fail(f"Gate check failed: Bundle ID mismatch for version '{version}'")
            log_fail(f"  Expected: {expected_bundle_id}")
            log_fail(f"  Actual:   {bundle_id}")
            raise ValueError(
                f"Gate assertion failed: Bundle ID '{bundle_id}' != expected '{expected_bundle_id}'"
            )

    expected_executable = version_config.get("executable", "").strip()
    if expected_executable and executable_path.name != expected_executable:
        log_fail(f"Gate check failed: Executable name mismatch for version '{version}'")
        log_fail(f"  Expected: {expected_executable}")
        log_fail(f"  Actual:   {executable_path.name}")
        raise ValueError(
            f"Gate assertion failed: executable '{executable_path.name}' != expected '{expected_executable}'"
        )

    expected_sha = version_config.get("binary_sha256", "").strip().lower()
    if not expected_sha:
        raise ValueError(f"Config for version '{version}' missing 'binary_sha256'")

    actual_sha = calc_sha256(executable_path)
    log_gate(f"Binary SHA256: {actual_sha}")
    log_gate(f"Expect SHA256: {expected_sha}")

    if actual_sha != expected_sha:
        log_fail(f"Gate check failed: SHA256 mismatch for {executable_path.name}")
        log_fail(f"  Expected: {expected_sha}")
        log_fail(f"  Actual:   {actual_sha}")
        log_fail("  Hint: The input binary might already be modified, re-signed, or belongs to another build.")
        raise ValueError(
            f"Gate assertion failed: binary SHA256 '{actual_sha}' != expected '{expected_sha}'"
        )

    log_ok("Gate check passed: version, bundle_id and binary SHA256 verified pristine.")
    return actual_sha


def calculate_dynamic_patch(
    site_id: str,
    default_after: str,
    days: int,
) -> bytes:
    """支持根据 --days (21/24) 动态微调 P2/P3/P4 字节；默认 24 天直接采用配置中的经过验证的 after 字节。"""
    if days == 24 or not site_id.startswith(("P2", "P3", "P4")):
        return bytes.fromhex(default_after)

    if site_id.startswith("P2"):
        # MOV W0, #days; RET
        opcode = 0x52800000 | ((days & 0xFFFF) << 5)
        return opcode.to_bytes(4, "little") + bytes.fromhex("C0035FD6")
    elif site_id.startswith("P3"):
        # MOV W0, #(days * 1440); RET
        num = days * 1440
        if num > 0xFFFF:
            raise ValueError(f"Days {days} leads to number {num} exceeding 16-bit immediate")
        opcode = 0x52800000 | ((num & 0xFFFF) << 5)
        return opcode.to_bytes(4, "little") + bytes.fromhex("C0035FD6")
    elif site_id.startswith("P4"):
        # dword days
        return days.to_bytes(4, "little")

    return bytes.fromhex(default_after)


def apply_patch_sites(
    binary_path: Path,
    sites: List[Dict[str, Any]],
    days: int = 24,
) -> Tuple[List[Dict[str, Any]], str]:
    """对主二进制应用 7 站点补丁，每站前置断言原始字节，记录 before/after。"""
    data = bytearray(binary_path.read_bytes())
    bin_size = len(data)
    log(f"Applying patches to {binary_path.name} (size: {bin_size} bytes, target days: {days})")

    report_sites: List[Dict[str, Any]] = []

    for site in sites:
        site_id = site.get("id", "")
        name = site.get("name", site_id)
        offset = parse_offset(site["offset"])
        before_hex = site["before"].strip().lower()
        default_after_hex = site["after"].strip().lower()
        desc = site.get("description", "")

        before_bytes = bytes.fromhex(before_hex)
        after_bytes = calculate_dynamic_patch(site_id, default_after_hex, days)
        after_hex = after_bytes.hex().lower()

        req_len = max(len(before_bytes), len(after_bytes))
        if offset + req_len > bin_size:
            log_fail(f"Site {name} offset 0x{offset:X} exceeds binary size {bin_size}")
            raise IndexError(f"Patch site {name} out of bounds")

        actual_current = bytes(data[offset : offset + len(before_bytes)])
        if actual_current != before_bytes:
            cur_hex = actual_current.hex().lower()
            log_fail(f"Site {name} @ 0x{offset:X} mismatch!")
            log_fail(f"  Expected before: {before_hex}")
            log_fail(f"  Actual found:    {cur_hex}")
            if cur_hex == after_hex:
                log_fail(f"  Hint: bytes at 0x{offset:X} match patch target; input is already patched!")
            raise ValueError(f"Site verification failed: {name} @ 0x{offset:X}")

        # 应用补丁
        data[offset : offset + len(after_bytes)] = after_bytes
        log_ok(f"Patched {name:26} @ 0x{offset:06X}: {before_hex} -> {after_hex}")

        report_sites.append(
            {
                "id": site_id,
                "name": name,
                "offset": f"0x{offset:X}",
                "before": before_hex,
                "after": after_hex,
                "status": "ok",
                "description": desc,
            }
        )

    # 原子写回并严格保留原有文件权限模式（防止 umask 导致可执行位 S_IXUSR 丢失）
    orig_mode = binary_path.stat().st_mode
    tmp_bin = binary_path.with_name(f"{binary_path.name}.tmp.{os.getpid()}")
    tmp_bin.write_bytes(bytes(data))
    os.chmod(tmp_bin, orig_mode)
    os.replace(tmp_bin, binary_path)

    new_sha = calc_sha256(binary_path)
    log_ok(f"All {len(sites)} sites applied successfully. Patched SHA256: {new_sha}")
    return report_sites, new_sha


def is_macho(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with open(path, "rb") as f:
            hdr = f.read(4)
        return hdr in MACHO_MAGICS
    except OSError:
        return False


def resign_bundle(app_dir: Path, team_id: str, main_executable_name: str) -> None:
    """使用 ldid 级联重签 App Bundle 内全部 Mach-O 二进制。"""
    if not check_tool("ldid"):
        raise RuntimeError("ldid executable not found in PATH. Please install ldid (e.g. brew install ldid)")

    log(f"Re-signing Mach-O binaries in {app_dir.name} using Team ID '{team_id}'...")

    main_binary = app_dir / main_executable_name
    main_ent_content = MAIN_ENTITLEMENTS_TEMPLATE.format(TEAM_ID=team_id).strip()

    # 递归查找所有 Mach-O
    macho_list: List[Path] = []
    for root, dirs, files in os.walk(app_dir):
        for fname in files:
            p = Path(root) / fname
            if is_macho(p):
                macho_list.append(p)

    log(f"Found {len(macho_list)} Mach-O binaries to sign")

    with tempfile.TemporaryDirectory() as ent_td:
        for macho in macho_list:
            is_main = macho.resolve() == main_binary.resolve()

            if is_main:
                ent_path = Path(ent_td) / "main_entitlements.plist"
                ent_path.write_text(main_ent_content, encoding="utf-8")
                cmd = ["ldid", f"-S{ent_path}", str(macho)]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    raise RuntimeError(f"ldid failed on main binary {macho.name}: {res.stderr}")
                log_ok(f"Signed main executable: {macho.name} (with custom entitlements)")
            else:
                # 检查原有二进制是否附带 entitlements
                dump = subprocess.run(["ldid", "-e", str(macho)], capture_output=True, text=True)
                existing_ent = dump.stdout.strip()
                if existing_ent and "<plist" in existing_ent:
                    # 动态替换其中的 Team ID（包括 10 位 Apple Team ID 或原有 TEAMID）
                    subbed_ent = re.sub(
                        r"([A-Z0-9]{10}|TEAMID)(?=\.com\.sisensing|\</string\>)",
                        team_id,
                        existing_ent,
                    )
                    sub_path = Path(ent_td) / f"{macho.name}_ent.plist"
                    sub_path.write_text(subbed_ent, encoding="utf-8")
                    cmd = ["ldid", f"-S{sub_path}", str(macho)]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    if res.returncode != 0:
                        raise RuntimeError(f"ldid failed on {macho.name}: {res.stderr}")
                    log_ok(f"Signed extension binary: {macho.name} (preserved & re-keyed entitlements)")
                else:
                    # 无 entitlements（如动态库/Framework），采用 ad-hoc 签名
                    cmd = ["ldid", "-s", str(macho)]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    if res.returncode != 0:
                        raise RuntimeError(f"ldid failed on {macho.name}: {res.stderr}")
                    log_ok(f"Signed framework/lib binary: {macho.name} (ad-hoc)")


def pack_ipa(extracted_root: Path, output_ipa: Path) -> None:
    """将 Payload 重新封装为 IPA，保留原有 UNIX 权限与符号链接结构。"""
    log(f"Packaging output IPA: {output_ipa}")
    output_ipa.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = output_ipa.with_name(f"{output_ipa.name}.tmp.{os.getpid()}")

    with zipfile.ZipFile(tmp_out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(extracted_root, followlinks=False):
            # 处理目录列表中的符号链接（目录链接不递归进入，记录为 zip 符号链接条目）
            symlink_dirs = []
            for d in list(dirs):
                full_d = Path(root) / d
                if os.path.islink(full_d):
                    symlink_dirs.append(full_d)
                    dirs.remove(d)

            for dsym in symlink_dirs:
                rel_path = dsym.relative_to(extracted_root)
                link_target = os.readlink(dsym)
                zinfo = zipfile.ZipInfo(str(rel_path))
                zinfo.create_system = 3  # Unix
                zinfo.external_attr = 0o120777 << 16
                z.writestr(zinfo, link_target)

            for f in files:
                full_path = Path(root) / f
                rel_path = full_path.relative_to(extracted_root)

                if os.path.islink(full_path):
                    # 符号链接文件：以 zip symlink 形式保留（模式 0o120777，内容为链接目标）
                    link_target = os.readlink(full_path)
                    zinfo = zipfile.ZipInfo(str(rel_path))
                    zinfo.create_system = 3  # Unix
                    zinfo.external_attr = 0o120777 << 16
                    z.writestr(zinfo, link_target)
                else:
                    # 常规文件：保留 POSIX 文件权限模式
                    st = full_path.stat()
                    zinfo = zipfile.ZipInfo.from_file(full_path, arcname=str(rel_path))
                    zinfo.create_system = 3  # Unix
                    zinfo.external_attr = (st.st_mode & 0xFFFF) << 16
                    with open(full_path, "rb") as fp:
                        z.writestr(zinfo, fp.read(), compress_type=zipfile.ZIP_DEFLATED)

    os.replace(tmp_out, output_ipa)
    log_ok(f"Output IPA packaged successfully ({output_ipa.stat().st_size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SiSensing ECO Official IPA Patcher (24-Day Expiration Bypass & Resigning)"
    )
    parser.add_argument("input", type=Path, help="Input decrypted IPA file")
    parser.add_argument("output", type=Path, help="Output patched & resigned IPA file")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent / "patch_config.json",
        help="Path to patch_config.json (default: patch_config.json adjacent to script)",
    )
    parser.add_argument(
        "--days",
        type=int,
        choices=[21, 24],
        default=24,
        help="Target expiration days (default: 24)",
    )
    parser.add_argument(
        "--team-id",
        type=str,
        default="TEAMID",
        help="Apple Team ID for re-signing entitlements (default: TEAMID)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("patch_report.json"),
        help="Path to output patch_report.json (default: patch_report.json)",
    )
    parser.add_argument(
        "--no-resign",
        action="store_true",
        help="Skip ldid code signing stage",
    )

    args = parser.parse_args()

    input_ipa: Path = args.input.resolve()
    output_ipa: Path = args.output.resolve()
    config_path: Path = args.config.resolve()
    report_path: Path = args.report.resolve()

    if not input_ipa.is_file():
        log_fail(f"Input file not found: {input_ipa}")
        return 1

    try:
        config_data = load_config(config_path)
    except Exception as e:
        log_fail(f"Failed to load patch config: {e}")
        return 1

    with tempfile.TemporaryDirectory() as td:
        extract_root = Path(td)
        try:
            app_dir = unpack_ipa(input_ipa, extract_root)
        except Exception as e:
            log_fail(f"Failed to unpack IPA: {e}")
            return 1

        try:
            version, exec_name, bundle_id = read_app_info(app_dir)
            log(f"Detected App: {bundle_id} version {version} (binary: {exec_name})")
        except Exception as e:
            log_fail(f"Failed to parse app metadata: {e}")
            return 1

        # 门禁检查：版本是否在配置中
        if version not in config_data:
            log_fail(
                f"Gate check failed: Version '{version}' is not in whitelist. "
                f"Supported versions: {list(config_data.keys())}"
            )
            return 1

        v_config = config_data[version]
        main_binary = app_dir / exec_name
        if not main_binary.is_file():
            log_fail(f"Executable binary not found: {main_binary}")
            return 1

        # 门禁检查：主二进制 SHA256 与 Bundle ID 比对
        try:
            sha_before = verify_gate(version, main_binary, v_config, bundle_id=bundle_id)
        except Exception as e:
            log_fail(str(e))
            return 1

        # 应用补丁
        sites_config = v_config.get("sites", [])
        if not sites_config:
            log_fail(f"No patch sites defined for version '{version}' in config")
            return 1

        try:
            report_sites, sha_after = apply_patch_sites(
                main_binary,
                sites_config,
                days=args.days,
            )
        except Exception as e:
            log_fail(f"Patching failed: {e}")
            return 1

        # 门禁检查：补丁后主二进制 SHA256 比对（若配置提供了 patched_sha256）
        expected_patched_sha = v_config.get("patched_sha256", "").strip().lower()
        if expected_patched_sha:
            default_days = v_config.get("default_days", 24)
            if args.days == default_days:
                log_gate(f"Checking patched binary SHA256 against configuration...")
                log_gate(f"Patched SHA256: {sha_after}")
                log_gate(f"Expect SHA256:  {expected_patched_sha}")
                if sha_after != expected_patched_sha:
                    log_fail(f"Gate check failed: Patched binary SHA256 mismatch for {main_binary.name}")
                    log_fail(f"  Expected: {expected_patched_sha}")
                    log_fail(f"  Actual:   {sha_after}")
                    return 1
                log_ok(f"Gate check passed: patched binary SHA256 matches whitelist: {sha_after}")
            else:
                log(f"Notice: Dynamic days target ({args.days}d != default {default_days}d); skipping static patched_sha256 check")

        # 重签名（在打包前对已恢复正确权限的可执行文件执行，确保对已具备执行位的二进制签名）
        if not args.no_resign:
            try:
                resign_bundle(app_dir, args.team_id, exec_name)
            except Exception as e:
                log_fail(f"Resigning failed: {e}")
                return 1
        else:
            log("Skipping code resigning (--no-resign specified)")

        # 门禁断言：打包前确保主二进制保留可执行权限 (S_IXUSR，fail-closed)
        main_mode = main_binary.stat().st_mode
        if not (main_mode & stat.S_IXUSR):
            log_fail(
                f"Gate check failed: Main executable {main_binary.name} missing execution bit "
                f"(mode: {oct(main_mode)}). Refusing to package broken IPA."
            )
            return 1
        # 打包输出 IPA
        try:
            pack_ipa(extract_root, output_ipa)
        except Exception as e:
            log_fail(f"Repackaging failed: {e}")
            return 1

        # 写入报告
        report = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "input_ipa": str(input_ipa),
            "output_ipa": str(output_ipa),
            "app_version": version,
            "bundle_id": bundle_id,
            "binary_name": exec_name,
            "binary_sha256_before": sha_before,
            "binary_sha256_after": sha_after,
            "team_id": args.team_id,
            "days": args.days,
            "status": "success",
            "sites_total": len(sites_config),
            "sites_patched": len(report_sites),
            "sites": report_sites,
        }

        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            log_ok(f"Patch report generated at: {report_path}")
        except Exception as e:
            log_fail(f"Failed to write patch report: {e}")
            return 1

    log_ok("All operations completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
