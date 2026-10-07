// eco-launch.m — 无人值守启动诊断/启动工具（部署于 /var/jb/usr/bin/eco-launch）
// uiopen 静默失败（rc=0 无输出）时无法得知 frontboard 拒绝原因；本工具直接调
// LSApplicationWorkspace 并打印 NSError，既是诊断器也是潜在的无人值守启动入口。
// 用法: eco-launch <bundle-id>
#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <objc/message.h>
#import <objc/runtime.h>

typedef void *LSWS;
typedef LSWS (*GETWS)(void);
typedef BOOL (*OPENWITHERR)(LSWS *, SEL, NSString *, id, NSError **);
typedef BOOL (*OPENPLAIN)(LSWS *, SEL, NSString *);

int main(int argc, char *argv[]) {
    @autoreleasepool {
        if (argc < 2) {
            fprintf(stderr, "usage: eco-launch <bundle-id>\n");
            return 2;
        }
        NSString *bid = [NSString stringWithUTF8String:argv[1]];

        void *h = dlopen("/System/Library/Frameworks/MobileCoreServices.framework/MobileCoreServices", RTLD_NOW);
        if (!h) h = dlopen("/System/Library/CoreServices/MobileCoreServices", RTLD_NOW);
        GETWS getWS = (GETWS)dlsym(h ?: RTLD_DEFAULT, "LSApplicationWorkspace.defaultWorkspace");
        // ObjC 类方法符号 mangling: 直接用 objc runtime 更稳
        Class wcls = objc_getClass("LSApplicationWorkspace");
        if (!wcls) {
            fprintf(stderr, "[eco-launch] LSApplicationWorkspace class not found\n");
            return 3;
        }
        id ws = ((id (*)(id, SEL))objc_msgSend)((id)wcls, sel_registerName("defaultWorkspace"));
        if (!ws) {
            fprintf(stderr, "[eco-launch] defaultWorkspace nil\n");
            return 4;
        }

        // 优先带 NSError 的变体
        SEL selErr = sel_registerName("openApplicationWithBundleID:withOptions:error:");
        if ([ws respondsToSelector:selErr]) {
            NSError *err = nil;
            OPENWITHERR fn = (OPENWITHERR)objc_msgSend;
            BOOL ok = fn(ws, selErr, bid, nil, &err);
            fprintf(stdout, "[eco-launch] open(withError) bid=%s ok=%d err=%s\n",
                    bid.UTF8String, ok,
                    err ? err.localizedDescription.UTF8String : "(nil)");
            return ok ? 0 : 1;
        }
        SEL selPlain = sel_registerName("openApplicationWithBundleID:");
        if ([ws respondsToSelector:selPlain]) {
            OPENPLAIN fn = (OPENPLAIN)objc_msgSend;
            BOOL ok = fn(ws, selPlain, bid);
            fprintf(stdout, "[eco-launch] open(plain) bid=%s ok=%d\n", bid.UTF8String, ok);
            return ok ? 0 : 1;
        }
        fprintf(stderr, "[eco-launch] no open selector found\n");
        return 5;
    }
}
