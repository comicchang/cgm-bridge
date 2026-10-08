// ECOStatusMask.m — 最小化状态掩码 dylib v3
// 职责单一：掩蔽 deviceStatus 的 '2'/'4' 写入与读取（探头失效/过期 gate），
// 使 App 的重连扫描与数据处理不被探头自报警产生的 '4' 态阻断。
// 与 P5 二进制补丁分工：P5=常量/BLE包闸/GrowingIO（权威层）；本 dylib=仅状态掩码（动态缺口层）。
// 安装位置：/var/jb/Library/MobileSubstrate/DynamicLibraries/（不触碰 App 容器 → 无重绑定/登录丢失风险）
// 卸载：移除 dylib+plist 后重启 App 即回原生行为（无需 respring，Filter 仅匹配 com.sisensing.eco）。

#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <os/log.h>

static BOOL g_installed = NO;

// 原始 IMP 保存
static NSString *(*orig_bd_getter)(id, SEL) = NULL;
static void (*orig_bd_setter)(id, SEL, NSString *) = NULL;
static NSString *(*orig_gl_getter)(id, SEL) = NULL;
static void (*orig_gl_setter)(id, SEL, NSString *) = NULL;
static void (*orig_save)(id, SEL, id, id, id) = NULL;

static inline NSString *mask_status(NSString *s) {
    if ([s isKindOfClass:[NSString class]] && ([s isEqualToString:@"2"] || [s isEqualToString:@"4"])) {
        return @"1";
    }
    return s;
}

// BlueDeviceModel.deviceStatus getter —— 重连门禁 reConnectWithDB 读取点
static NSString *hook_bd_getter(id self, SEL _cmd) {
    return mask_status(orig_bd_getter ? orig_bd_getter(self, _cmd) : @"1");
}
// BlueDeviceModel.setDeviceStatus: —— 持久化写入点（入参掩码）
static void hook_bd_setter(id self, SEL _cmd, NSString *v) {
    if (orig_bd_setter) orig_bd_setter(self, _cmd, mask_status(v));
}
// GlucoseModel.deviceStatus getter/setter（行级状态读写）
static NSString *hook_gl_getter(id self, SEL _cmd) {
    return mask_status(orig_gl_getter ? orig_gl_getter(self, _cmd) : @"1");
}
static void hook_gl_setter(id self, SEL _cmd, NSString *v) {
    if (orig_gl_setter) orig_gl_setter(self, _cmd, mask_status(v));
}
// BlueDataDispose saveAlarmStatusAndDeviceStatusWithGlucoseModel:alarmStatus:deviceStatus:
// 第3参数 deviceStatus 掩码；alarmStatus 透传（保持厂商语义）
static void hook_save(id self, SEL _cmd, id model, id alarm, id status) {
    NSString *masked = [status isKindOfClass:[NSString class]] ? mask_status((NSString *)status) : (NSString *)status;
    if (orig_save) orig_save(self, _cmd, model, alarm, masked);
}

static BOOL try_install(int attempt) {
    @autoreleasepool {
        BOOL ok = YES;
        Class bd = objc_getClass("BlueDeviceModel");
        Class gl = objc_getClass("GlucoseModel");
        Class bdd = objc_getClass("BlueDataDispose");

        if (bd) {
            Method gm = class_getInstanceMethod(bd, @selector(deviceStatus));
            Method sm = class_getInstanceMethod(bd, @selector(setDeviceStatus:));
            if (!gm || !sm) { ok = NO; os_log(OS_LOG_DEFAULT, "StatusMask: BlueDeviceModel methods missing gm=%p sm=%p (attempt %d)", gm, sm, attempt); }
            if (gm && method_getImplementation(gm) != (IMP)hook_bd_getter) {
                orig_bd_getter = (NSString *(*)(id, SEL))method_setImplementation(gm, (IMP)hook_bd_getter);
            }
            if (sm && method_getImplementation(sm) != (IMP)hook_bd_setter) {
                orig_bd_setter = (void (*)(id, SEL, NSString *))method_setImplementation(sm, (IMP)hook_bd_setter);
            }
        } else { ok = NO; os_log(OS_LOG_DEFAULT, "StatusMask: BlueDeviceModel missing (attempt %d)", attempt); }

        if (gl) {
            Method gm = class_getInstanceMethod(gl, @selector(deviceStatus));
            Method sm = class_getInstanceMethod(gl, @selector(setDeviceStatus:));
            if (!gm || !sm) { ok = NO; os_log(OS_LOG_DEFAULT, "StatusMask: GlucoseModel methods missing gm=%p sm=%p (attempt %d)", gm, sm, attempt); }
            if (gm && method_getImplementation(gm) != (IMP)hook_gl_getter) {
                orig_gl_getter = (NSString *(*)(id, SEL))method_setImplementation(gm, (IMP)hook_gl_getter);
            }
            if (sm && method_getImplementation(sm) != (IMP)hook_gl_setter) {
                orig_gl_setter = (void (*)(id, SEL, NSString *))method_setImplementation(sm, (IMP)hook_gl_setter);
            }
        } else { ok = NO; os_log(OS_LOG_DEFAULT, "StatusMask: GlucoseModel missing (attempt %d)", attempt); }

        if (bdd) {
            Method sm = class_getInstanceMethod(bdd, @selector(saveAlarmStatusAndDeviceStatusWithGlucoseModel:alarmStatus:deviceStatus:));
            if (!sm) { ok = NO; os_log(OS_LOG_DEFAULT, "StatusMask: BlueDataDispose save method missing (attempt %d)", attempt); }
            if (sm && method_getImplementation(sm) != (IMP)hook_save) {
                orig_save = (void (*)(id, SEL, id, id, id))method_setImplementation(sm, (IMP)hook_save);
            }
        } else { ok = NO; os_log(OS_LOG_DEFAULT, "StatusMask: BlueDataDispose missing (attempt %d)", attempt); }

        os_log(OS_LOG_DEFAULT, "StatusMask: install attempt=%d ok=%d", attempt, ok);
        if (ok) { g_installed = YES; }
        else if (attempt < 2) {
            int64_t delay = (attempt == 0) ? 50 * NSEC_PER_MSEC : 200 * NSEC_PER_MSEC;
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, delay), dispatch_get_main_queue(), ^{
                try_install(attempt + 1);
            });
        }
        return ok;
    }
}

__attribute__((constructor))
static void init_status_mask(void) {
    if (g_installed) return;
    try_install(0);
}
