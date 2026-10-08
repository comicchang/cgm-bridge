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
        Class bd = objc_getClass("BlueDeviceModel");
        Class gl = objc_getClass("GlucoseModel");
        Class bdd = objc_getClass("BlueDataDispose");

        // Phase 1: 全量预检（无副作用）——三个类与全部五个选择子必须同时在场
        Method bd_gm = bd ? class_getInstanceMethod(bd, @selector(deviceStatus)) : NULL;
        Method bd_sm = bd ? class_getInstanceMethod(bd, @selector(setDeviceStatus:)) : NULL;
        Method gl_gm = gl ? class_getInstanceMethod(gl, @selector(deviceStatus)) : NULL;
        Method gl_sm = gl ? class_getInstanceMethod(gl, @selector(setDeviceStatus:)) : NULL;
        Method bdd_sm = bdd ? class_getInstanceMethod(bdd, @selector(saveAlarmStatusAndDeviceStatusWithGlucoseModel:alarmStatus:deviceStatus:)) : NULL;

        BOOL ok = (bd != nil && gl != nil && bdd != nil &&
                   bd_gm != NULL && bd_sm != NULL &&
                   gl_gm != NULL && gl_sm != NULL && bdd_sm != NULL);
        if (!ok) {
            os_log(OS_LOG_DEFAULT, "StatusMask: precheck failed (attempt %d) bd=%p gl=%p bdd=%p bd_gm=%p bd_sm=%p gl_gm=%p gl_sm=%p bdd_sm=%p",
                   attempt, bd, gl, bdd, bd_gm, bd_sm, gl_gm, gl_sm, bdd_sm);
        } else {
            // Phase 2: 预检全部通过后才执行 IMP 交换——要么全部生效，要么零副作用
            if (method_getImplementation(bd_gm) != (IMP)hook_bd_getter) {
                orig_bd_getter = (NSString *(*)(id, SEL))method_setImplementation(bd_gm, (IMP)hook_bd_getter);
            }
            if (method_getImplementation(bd_sm) != (IMP)hook_bd_setter) {
                orig_bd_setter = (void (*)(id, SEL, NSString *))method_setImplementation(bd_sm, (IMP)hook_bd_setter);
            }
            if (method_getImplementation(gl_gm) != (IMP)hook_gl_getter) {
                orig_gl_getter = (NSString *(*)(id, SEL))method_setImplementation(gl_gm, (IMP)hook_gl_getter);
            }
            if (method_getImplementation(gl_sm) != (IMP)hook_gl_setter) {
                orig_gl_setter = (void (*)(id, SEL, NSString *))method_setImplementation(gl_sm, (IMP)hook_gl_setter);
            }
            if (method_getImplementation(bdd_sm) != (IMP)hook_save) {
                orig_save = (void (*)(id, SEL, id, id, id))method_setImplementation(bdd_sm, (IMP)hook_save);
            }
        }

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
